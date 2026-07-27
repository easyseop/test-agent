"""브라우저·환경 실패가 제품 실패(exit 1)로 위장되지 않고 infra_error(exit 2)가 되는지.

계약: 실제로 판정하지 못한 실행은 절대 통과(0)나 제품 실패(1)로 표시하지 않는다.
브라우저 기동 실패·실행 중 브라우저 사망은 판정 불가이므로 infra_error다.
"""
from __future__ import annotations

import json

import pytest

from webtest_agent import cli
from webtest_agent.runner import BrowserGoneError, _is_browser_gone


def _base_config(tmp_path, out):
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "target: {base_url: 'http://127.0.0.1:59999'}\n"
        "crawl: {enabled: false}\n"
        "button_sweep: {enabled: false}\n"
        "spec_checks:\n"
        "  - name: 더미\n"
        "    page: /\n"
        "    steps: [{action: assert_url, value: '/'}]\n"
        "report: {video: false, trace: false}\n"
        f"output_dir: {out}\n",
        encoding="utf-8",
    )
    return cfg


class _Args:
    def __init__(self, config, out):
        self.config = config
        self.out = out
        self.headed = False
        self.update_baselines = False
        self.allow_write_checks = False
        self.preserve_auth_state = False


class _ExplodingSession:
    """BrowserSession을 흉내 내되 start()에서 브라우저 기동 실패를 던진다."""

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        raise RuntimeError(
            "BrowserType.launch: Target page, context or browser has been closed")

    def __exit__(self, *exc):
        return False


def test_browser_launch_failure_is_infra_error_with_report(tmp_path, monkeypatch):
    out = tmp_path / "runs"
    args = _Args(str(_base_config(tmp_path, out)), str(out))
    monkeypatch.setattr(cli, "BrowserSession", _ExplodingSession)

    code = cli.cmd_run(args)

    assert code == 2, "브라우저 기동 실패는 종료코드 2(infra_error)여야 한다"
    run_dirs = list(out.iterdir())
    assert run_dirs, "실행 불가여도 진단 리포트를 남겨야 한다"
    report = json.loads((run_dirs[0] / "report.json").read_text(encoding="utf-8"))
    assert report["meta"]["status"] == "infra_error"
    assert report["meta"]["error"]


@pytest.mark.parametrize("message", [
    "BrowserType.launch: Target page, context or browser has been closed",
    "playwright._impl._errors.TargetClosedError: ...",
    "Browser closed unexpectedly",
    "Page crashed",
])
def test_browser_gone_detected(message):
    assert _is_browser_gone(Exception(message)) is True


@pytest.mark.parametrize("message", [
    "요소 '#btn'가 화면에 보이지 않습니다",
    "Timeout 5000ms exceeded waiting for selector",
    "AssertionError: 기대 텍스트 없음",
    # 아래는 앱(제품) 쪽 오류다 — 넓은 마커였다면 infra로 오분류됐을 실제 반례
    "Dialog has been closed by the page",
    "WebSocket has been closed",
    "The popup has been closed",
    "Assertion failed: modal has been closed",
])
def test_ordinary_failures_are_not_browser_gone(message):
    assert _is_browser_gone(Exception(message)) is False


def test_browser_gone_error_is_infra_not_product():
    assert issubclass(BrowserGoneError, RuntimeError)
