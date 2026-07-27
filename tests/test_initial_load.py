"""시나리오 최초 page.goto 오류 판정 회귀 테스트."""
import time
from types import SimpleNamespace

from webtest_agent.models import FAIL, PASS, HttpFailure
from webtest_agent.runner import Runner
from webtest_agent.scenarios import Scenario


class _Context:
    def close(self):
        return None


class _Page:
    def __init__(self, monitor):
        self.monitor = monitor
        self.url = "http://example.test/"

    def set_default_timeout(self, timeout):
        return None

    def goto(self, path, **kwargs):
        self.url = f"http://example.test{path}"
        load_console = getattr(self.monitor, "load_console", [])
        self.monitor.console_errors.extend(load_console)
        # 실제 PageMonitor와 같은 길이의 URL 목록을 유지한다 (허용 패턴 필터용)
        self.monitor.console_error_urls.extend(
            getattr(self.monitor, "load_console_urls", [""] * len(load_console)))
        self.monitor.page_errors.extend(getattr(self.monitor, "load_page_errors", []))
        self.monitor.http_failures.extend(getattr(self.monitor, "load_http", []))

    def wait_for_timeout(self, timeout):
        return None

    def evaluate(self, script):
        return 100

    def screenshot(self, **kwargs):
        return None


class _Session:
    def __init__(self, monitor):
        self.monitor = monitor

    def new_context(self, *args, **kwargs):
        return _Context(), _Page(self.monitor), self.monitor


def _monitor(**load_signals):
    return SimpleNamespace(
        console_errors=[],
        console_error_urls=[],
        page_errors=[],
        http_failures=[],
        dialogs=[],
        downloads=[],
        popups=[],
        navigations=0,
        **load_signals,
    )


def _cfg(ignore_http=None):
    return SimpleNamespace(
        target=SimpleNamespace(
            base_url="http://example.test",
            settle_ms=0,
            nav_timeout_ms=1000,
            scenario_timeout_ms=5000,
            run_timeout_ms=100,
            ignore_http_error_patterns=ignore_http or [],
            flaky_recheck=False,
        ),
        report=SimpleNamespace(video=False, trace=False, mask_selectors=[]),
    )


def _scenario():
    return Scenario(name="첫 화면", kind="spec_check", page="/", steps=[])


def test_initial_page_load_signals_fail_scenario(tmp_path):
    monitor = _monitor(
        load_console=["load console error"],
        load_page_errors=["load page exception"],
        load_http=[HttpFailure(url="http://example.test/app.js", status=500)],
    )
    runner = Runner(_Session(monitor), _cfg(), tmp_path)

    result = runner.run(_scenario(), 1)

    assert result.status == FAIL
    assert result.console_errors == ["load console error"]
    assert result.page_errors == ["load page exception"]
    assert result.http_failures[0].status == 500


def test_allowed_initial_http_error_pattern_is_filtered(tmp_path):
    monitor = _monitor(
        load_http=[HttpFailure(url="http://example.test/optional-widget.png", status=404)],
    )
    runner = Runner(
        _Session(monitor),
        _cfg(ignore_http=[r"/optional-widget\.png$"]),
        tmp_path,
    )

    result = runner.run(_scenario(), 1)

    assert result.status == PASS
    assert result.http_failures == []


def test_runner_reports_global_deadline_in_plain_language(tmp_path):
    monitor = _monitor()
    runner = Runner(
        _Session(monitor),
        _cfg(),
        tmp_path,
        deadline_monotonic=time.monotonic() - 1,
    )

    result = runner.run(_scenario(), 1)

    assert result.status == FAIL
    assert result.reasons == ["전체 실행 제한 시간 초과 (100ms)"]
