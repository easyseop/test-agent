"""단위 B — 쓰기 검증 전 데이터 초기화.

초기화는 대상 앱의 데이터를 지운다. 그래서 검증할 것이 세 가지다.

1. 승인(`--allow-write-checks`) 없이는 절대 돌지 않는다.
2. 실패는 판정이 아니라 실행 불가다.
3. command는 셸을 거치지 않아 연쇄 실행이 불가능하다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from webtest_agent.config import ConfigError, WriteResetConfig, load_config
from webtest_agent.reset import ResetFailed, run_write_reset

WRITE_BLOCK = """
write_checks:
  - name: 주문등록
    page: /new
    steps:
      - {action: click, selector: '#submit'}
    expect_delta: 1
    query: {db: 'sqlite:///x.db', sql: 'SELECT COUNT(*) FROM orders'}
"""


def _write_cfg(tmp_path: Path, reset_block: str, with_writes: bool = True) -> Path:
    p = tmp_path / "c.yaml"
    p.write_text(
        "target: {base_url: 'http://127.0.0.1:1'}\n"
        + (WRITE_BLOCK if with_writes else "")
        + reset_block,
        encoding="utf-8")
    return p


# ── 설정 검증 ────────────────────────────────────────────────────

def test_reset_without_write_checks_is_rejected(tmp_path):
    """쓰기 검증이 없는데 초기화만 있는 설정은 데이터만 지우는 꼴이다."""
    p = _write_cfg(tmp_path, "write_reset: {command: ['echo', 'x']}", with_writes=False)
    with pytest.raises(ConfigError, match="write_checks가 없는데"):
        load_config(p)


def test_reset_needs_exactly_one_mechanism(tmp_path):
    both = "write_reset: {command: ['echo','x'], http: {url: '/r'}}"
    with pytest.raises(ConfigError, match="정확히 하나"):
        load_config(_write_cfg(tmp_path, both))
    with pytest.raises(ConfigError, match="정확히 하나"):
        load_config(_write_cfg(tmp_path, "write_reset: {timeout_ms: 100}"))


def test_command_must_be_a_list_not_a_shell_string(tmp_path):
    """문자열을 받으면 셸을 거치게 되고 '; rm -rf /' 같은 연쇄가 가능해진다."""
    p = _write_cfg(tmp_path, "write_reset: {command: 'python seed.py; rm -rf /'}")
    with pytest.raises(ConfigError, match="인자 목록"):
        load_config(p)


def test_http_method_limited_to_state_changing_verbs(tmp_path):
    p = _write_cfg(tmp_path, "write_reset: {http: {url: '/reset', method: GET}}")
    with pytest.raises(ConfigError, match="POST, PUT, DELETE"):
        load_config(p)


def test_valid_reset_is_parsed(tmp_path):
    p = _write_cfg(tmp_path,
                   "write_reset: {http: {url: '/__test__/reset', method: POST}}")
    cfg = load_config(p)
    assert cfg.write_reset.http_url == "/__test__/reset"
    assert cfg.write_reset.http_method == "POST"


def test_reset_env_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("RESET_TOKEN", "tok-123")
    p = _write_cfg(
        tmp_path,
        "write_reset: {http: {url: '/r', headers: {Authorization: 'Bearer ${RESET_TOKEN}'}}}")
    cfg = load_config(p)
    assert cfg.write_reset.http_headers["Authorization"] == "Bearer tok-123"


# ── 승인 경계 ────────────────────────────────────────────────────

def test_reset_does_not_run_without_approval(tmp_path):
    """설정에 초기화가 있다는 이유만으로 데이터를 지우면 안 된다."""
    marker = tmp_path / "ran.txt"
    cfg = WriteResetConfig(command=[sys.executable, "-c",
                                    f"open({str(marker)!r},'w').write('x')"])
    outcome = run_write_reset(cfg, allow_write_checks=False,
                              base_url="http://x", config_dir=tmp_path)
    assert not outcome.performed
    assert "allow-write-checks" in outcome.skipped_reason
    assert not marker.exists()          # 실제로 돌지 않았다


def test_reset_runs_with_approval(tmp_path):
    marker = tmp_path / "ran.txt"
    cfg = WriteResetConfig(command=[sys.executable, "-c",
                                    f"open({str(marker)!r},'w').write('x')"])
    outcome = run_write_reset(cfg, allow_write_checks=True,
                              base_url="http://x", config_dir=tmp_path)
    assert outcome.performed
    assert marker.read_text() == "x"


def test_no_reset_config_is_a_noop(tmp_path):
    outcome = run_write_reset(None, allow_write_checks=True,
                              base_url="http://x", config_dir=tmp_path)
    assert not outcome.performed and not outcome.skipped_reason


# ── 실패는 실행 불가 ─────────────────────────────────────────────

def test_failing_command_raises_reset_failed(tmp_path):
    cfg = WriteResetConfig(command=[sys.executable, "-c", "import sys; sys.exit(3)"])
    with pytest.raises(ResetFailed, match="종료코드 3"):
        run_write_reset(cfg, allow_write_checks=True,
                        base_url="http://x", config_dir=tmp_path)


def test_missing_command_raises_reset_failed(tmp_path):
    cfg = WriteResetConfig(command=["definitely-not-a-real-binary-xyz"])
    with pytest.raises(ResetFailed, match="찾을 수 없습니다"):
        run_write_reset(cfg, allow_write_checks=True,
                        base_url="http://x", config_dir=tmp_path)


def test_unreachable_http_raises_reset_failed(tmp_path):
    cfg = WriteResetConfig(http_url="http://127.0.0.1:1/reset", timeout_ms=2000)
    with pytest.raises(ResetFailed, match="보내지 못했습니다"):
        run_write_reset(cfg, allow_write_checks=True,
                        base_url="http://127.0.0.1:1", config_dir=tmp_path)


def test_command_does_not_go_through_a_shell(tmp_path):
    """셸을 거친다면 ';'로 두 번째 명령이 실행됐을 것이다."""
    marker = tmp_path / "pwned.txt"
    cfg = WriteResetConfig(command=[
        sys.executable, "-c", "pass", ";",
        sys.executable, "-c", f"open({str(marker)!r},'w').write('x')",
    ])
    run_write_reset(cfg, allow_write_checks=True,
                    base_url="http://x", config_dir=tmp_path)
    assert not marker.exists()


def test_command_runs_relative_to_the_config_folder(tmp_path):
    (tmp_path / "seed.py").write_text("open('done.txt','w').write('1')", encoding="utf-8")
    cfg = WriteResetConfig(command=[sys.executable, "seed.py"])
    run_write_reset(cfg, allow_write_checks=True,
                    base_url="http://x", config_dir=tmp_path)
    assert (tmp_path / "done.txt").exists()
