"""OpenMetadata 파일럿 실측 발견 3건의 회귀 테스트.

실제 OM 1.13.2에 붙여본 제3자 파일럿에서 나온 통합 이슈다. 데모앱은
IndexedDB를 쓰지 않고 env도 다 갖춰져 있어 여기까지 오지 못했다 —
실전에 붙여봐야만 나오는 결함이라 반드시 테스트로 고정한다.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from webtest_agent.config import ConfigError, load_config
from webtest_agent.runner import Runner


def _cfg_file(tmp_path: Path, data: dict) -> str:
    p = tmp_path / "c.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return str(p)


BASE = {"target": {"base_url": "http://x"}}
API_CHECK = {
    "name": "t", "ui_table": {"selector": "#t"},
    "query": {"api": {"url": "/api/v1/x", "columns": ["a"],
                      "headers": {"Authorization": "Bearer ${OM_JWT_TEST}"}}},
}
AUTH = {"steps": [{"action": "fill", "selector": "#password",
                   "value": "${OM_PW_TEST}"}]}


# ── 발견 1: IndexedDB 인증 세션 ─────────────────────────────────
#
# OpenMetadata는 토큰을 IndexedDB(AppDataStore)에 둔다. Playwright의 기본
# storage_state()는 쿠키+localStorage만 담아서, 복원된 컨텍스트에 토큰이 없다.
# 증상은 "로그인은 성공했다는데 모든 화면이 로그인 창"이라 원인을 찾기 어렵다.
# 3엔진(chromium·firefox·webkit) 실측에서 indexed_db=True 일 때만 토큰이 살아남았다.

def test_indexed_db_defaults_on(tmp_path):
    cfg = load_config(_cfg_file(tmp_path, dict(
        BASE, auth={"steps": [{"action": "click", "selector": "#a"}]})))
    assert cfg.auth.indexed_db is True


def test_indexed_db_can_be_disabled(tmp_path):
    cfg = load_config(_cfg_file(tmp_path, dict(
        BASE, auth={"steps": [{"action": "click", "selector": "#a"}],
                    "indexed_db": False})))
    assert cfg.auth.indexed_db is False


class _Ctx:
    """storage_state 호출을 기록하는 가짜 컨텍스트."""

    def __init__(self, supports_idb=True):
        self.calls: list[dict] = []
        self._supports = supports_idb

    def storage_state(self, path=None, **kw):
        if "indexed_db" in kw and not self._supports:
            raise TypeError("unexpected keyword argument 'indexed_db'")
        self.calls.append({"path": path, **kw})


def _runner(indexed_db=True, has_auth=True):
    r = Runner.__new__(Runner)
    auth = SimpleNamespace(indexed_db=indexed_db) if has_auth else None
    r.cfg = SimpleNamespace(auth=auth)
    return r


def test_auth_state_saved_with_indexed_db(tmp_path):
    ctx = _Ctx()
    _runner()._save_storage_state(ctx, tmp_path / "s.json")
    assert ctx.calls[0].get("indexed_db") is True, "IndexedDB 토큰이 저장되지 않는다"


def test_indexed_db_off_uses_plain_save(tmp_path):
    ctx = _Ctx()
    _runner(indexed_db=False)._save_storage_state(ctx, tmp_path / "s.json")
    assert "indexed_db" not in ctx.calls[0]


def test_missing_auth_config_still_saves(tmp_path):
    """auth 설정이 없는 경로(테스트용 가짜 cfg 등)에서도 죽지 않아야 한다."""
    ctx = _Ctx()
    _runner(has_auth=False)._save_storage_state(ctx, tmp_path / "s.json")
    assert ctx.calls


def test_old_playwright_warns_instead_of_silently_downgrading(tmp_path):
    """조용히 넘기면 '로그인이 안 된다'가 원인 불명으로 되살아난다."""
    ctx = _Ctx(supports_idb=False)
    with pytest.warns(RuntimeWarning, match="indexed_db"):
        _runner()._save_storage_state(ctx, tmp_path / "s.json")
    assert ctx.calls and "indexed_db" not in ctx.calls[-1], "폴백 저장이 안 됐다"


# ── 발견 3: discover가 안 쓰는 env에 막히지 않아야 ──────────────
#
# fail-closed 자체는 이 프로젝트 철학이지만, 첫 발디딤(discover)이 막히면
# 셀렉터를 얻을 수 없어 진행 자체가 잠긴다.

def test_run_still_fails_closed_on_missing_env(tmp_path):
    """완화는 discover 전용이다. run이 이걸 잃으면 안 된다."""
    with pytest.raises(ConfigError, match="OM_JWT_TEST"):
        load_config(_cfg_file(tmp_path, dict(BASE, data_checks=[API_CHECK])))


def test_discover_defers_unused_check_env(tmp_path):
    cfg = load_config(_cfg_file(tmp_path, dict(BASE, data_checks=[API_CHECK])),
                      defer_check_env=True)
    assert any("OM_JWT_TEST" in name for name in cfg.deferred_env)


def test_deferred_value_is_left_unsubstituted(tmp_path):
    """치환되지 않은 `${VAR}`로 남아야 진짜 값으로 오인될 수 없다."""
    cfg = load_config(_cfg_file(tmp_path, dict(BASE, data_checks=[API_CHECK])),
                      defer_check_env=True)
    assert "${OM_JWT_TEST}" in cfg.data_checks[0].query.api.headers["Authorization"]


def test_auth_env_is_never_deferred(tmp_path):
    """discover도 로그인을 수행한다. 비밀번호가 비면 `${VAR}`가 그대로 입력돼
    로그인이 조용히 실패하므로 여기서 막아야 한다."""
    with pytest.raises(ConfigError, match="OM_PW_TEST"):
        load_config(_cfg_file(tmp_path, dict(BASE, auth=AUTH,
                                             data_checks=[API_CHECK])),
                    defer_check_env=True)


def test_present_env_substitutes_normally(tmp_path):
    os.environ["OM_JWT_TEST"] = "real-jwt"
    try:
        cfg = load_config(_cfg_file(tmp_path, dict(BASE, data_checks=[API_CHECK])),
                          defer_check_env=True)
        assert cfg.deferred_env == []
        assert "real-jwt" in cfg.data_checks[0].query.api.headers["Authorization"]
    finally:
        del os.environ["OM_JWT_TEST"]


def test_failed_deferred_load_does_not_poison_next_strict_load(tmp_path):
    """완화 모드가 전역에 남으면 그다음 run이 fail-closed를 잃는다."""
    strict = _cfg_file(tmp_path, dict(BASE, data_checks=[API_CHECK]))
    both = tmp_path / "both.yaml"
    both.write_text(yaml.safe_dump(dict(BASE, auth=AUTH, data_checks=[API_CHECK])),
                    encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(both), defer_check_env=True)   # auth에서 예외
    with pytest.raises(ConfigError, match="OM_JWT_TEST"):
        load_config(strict)


# ── 발견 2: OpenMetadata 로그인 셀렉터 ──────────────────────────

def test_openmetadata_login_uses_input_selectors():
    """[data-testid="email"]은 입력이 아니라 래퍼라 fill이 실패한다(실측)."""
    text = Path("configs/openmetadata.yaml").read_text(encoding="utf-8")
    assert "selector: '#email'" in text
    assert "selector: '#password'" in text
    assert '[data-testid="email"]' not in text
