"""잘못된 성공 종료를 막는 실행 가드 회귀 테스트."""
import argparse
import json
import sys
import time
import types
from types import SimpleNamespace

import pytest

# 실행 가드 테스트는 실제 브라우저를 띄우지 않는다. 개발 환경에 Playwright가
# 아직 설치되지 않은 경우에도 CLI 모듈의 타입 import만 통과하도록 최소 스텁을 둔다.
try:
    import playwright.sync_api  # noqa: F401
except ModuleNotFoundError:
    playwright_module = types.ModuleType("playwright")
    sync_api_module = types.ModuleType("playwright.sync_api")
    sync_api_module.Browser = object
    sync_api_module.BrowserContext = object
    sync_api_module.Dialog = object
    sync_api_module.Page = object
    sync_api_module.sync_playwright = lambda: None
    playwright_module.sync_api = sync_api_module
    sys.modules["playwright"] = playwright_module
    sys.modules["playwright.sync_api"] = sync_api_module

from webtest_agent import cli


class _FakeContext:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _FakePage:
    def __init__(self, error=None):
        self.error = error
        self.goto_args = None

    def goto(self, *args, **kwargs):
        self.goto_args = (args, kwargs)
        if self.error:
            raise self.error


class _FakeSession:
    version = "fake"

    def __init__(self, headless=True, page_error=None):
        self.headless = headless
        self.ctx = _FakeContext()
        self.page = _FakePage(page_error)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def new_context(self, base_url):
        return self.ctx, self.page, object()

    def configure_storage_state(self, path, preserve=False):
        self.pending_storage_state = path
        self.preserve_storage_state = preserve

    def activate_storage_state(self):
        self.storage_state = self.pending_storage_state


def _empty_config(tmp_path):
    config = tmp_path / "empty.yaml"
    output = tmp_path / "runs"
    config.write_text(
        f"""
target:
  base_url: http://127.0.0.1:59999
crawl:
  enabled: false
button_sweep:
  enabled: false
report:
  title: 빈 테스트
  video: false
  trace: false
output_dir: {output}
""",
        encoding="utf-8",
    )
    return config, output


def _args(config):
    return argparse.Namespace(
        config=str(config),
        out=None,
        headed=False,
        update_baselines=False,
    )


def _only_report(output):
    reports = list(output.glob("*/report.json"))
    assert len(reports) == 1
    return json.loads(reports[0].read_text(encoding="utf-8"))


def test_target_guard_turns_navigation_error_into_infrastructure_error():
    session = _FakeSession(page_error=RuntimeError("connection refused"))
    cfg = SimpleNamespace(
        target=SimpleNamespace(base_url="http://127.0.0.1:59999", nav_timeout_ms=1000)
    )

    with pytest.raises(cli.RunInfrastructureError, match="접속할 수 없습니다"):
        cli._check_target_available(session, cfg)

    assert session.ctx.closed is True


def test_zero_scenarios_is_not_success(tmp_path, monkeypatch):
    config, output = _empty_config(tmp_path)
    monkeypatch.setattr(cli, "BrowserSession", _FakeSession)
    monkeypatch.setattr(cli, "_check_target_available", lambda *args, **kwargs: None)

    assert cli.cmd_run(_args(config)) == 2

    report = _only_report(output)
    assert report["meta"]["status"] == "infra_error"
    assert "시나리오가 0개" in report["meta"]["error"]
    assert report["summary"]["total"] == 0


def test_unreachable_target_is_not_success(tmp_path, monkeypatch):
    config, output = _empty_config(tmp_path)
    monkeypatch.setattr(cli, "BrowserSession", _FakeSession)

    def unavailable(*args, **kwargs):
        raise cli.RunInfrastructureError("대상 앱에 접속할 수 없습니다")

    monkeypatch.setattr(cli, "_check_target_available", unavailable)

    assert cli.cmd_run(_args(config)) == 2

    report = _only_report(output)
    assert report["meta"]["status"] == "infra_error"
    assert "접속할 수 없습니다" in report["meta"]["error"]


def test_write_checks_are_blocked_without_explicit_approval(tmp_path, monkeypatch):
    config = tmp_path / "write.yaml"
    output = tmp_path / "runs"
    config.write_text(
        f"""
target:
  base_url: http://example.test
crawl:
  enabled: false
button_sweep:
  enabled: false
write_checks:
  - name: 주문 등록
    page: /new
    steps: [{{action: click, selector: "#save"}}]
    query:
      db: sqlite:///{tmp_path / "never-open.db"}
      sql: "SELECT COUNT(*) FROM orders"
    expect_delta: 1
output_dir: {output}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "BrowserSession", _FakeSession)
    monkeypatch.setattr(cli, "_check_target_available", lambda *args, **kwargs: None)

    assert cli.cmd_run(_args(config)) == 2

    report = _only_report(output)
    assert report["summary"]["total"] == 0
    assert report["blocked_elements"][0]["text"] == "주문 등록"
    assert "--allow-write-checks" in report["blocked_elements"][0]["pattern"]


def test_run_deadline_is_infrastructure_error_not_partial_success(tmp_path, monkeypatch):
    config = tmp_path / "deadline.yaml"
    output = tmp_path / "runs"
    config.write_text(
        f"""
target:
  base_url: http://example.test
  run_timeout_ms: 1
crawl:
  enabled: false
button_sweep:
  enabled: false
spec_checks:
  - name: 실행 예정 테스트
    page: /
    steps: [{{action: assert_visible, selector: "#ready"}}]
output_dir: {output}
""",
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "BrowserSession", _FakeSession)

    def slow_precheck(*args, **kwargs):
        time.sleep(0.01)

    monkeypatch.setattr(cli, "_check_target_available", slow_precheck)

    assert cli.cmd_run(_args(config)) == 2

    report = _only_report(output)
    assert report["meta"]["status"] == "infra_error"
    assert "전체 실행 제한 시간" in report["meta"]["error"]
    assert report["summary"]["total"] == 0
