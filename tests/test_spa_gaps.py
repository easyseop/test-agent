"""실전 SPA에서 관측된 한계 5가지에 대한 회귀 검증.

OpenMetadata 검증 중 "화면은 멀쩡한데 도구가 통과를 못 시킨다"로 드러난
항목들이다. 각 항목마다 고친 동작뿐 아니라 **고치지 않았어야 할 동작**도
같이 검증한다 — 잡음을 거르는 기능은 진짜 결함까지 같이 거르기 쉽다.
"""
from types import SimpleNamespace

import pytest

from webtest_agent.config import ConfigError, load_config
from webtest_agent.datacheck import compare
from webtest_agent.runner import Runner


# ---------------------------------------------------------------- 콘솔 잡음

def _console(cfg, errors, urls):
    r = Runner.__new__(Runner)
    r.cfg = cfg
    monitor = SimpleNamespace(console_errors=list(errors),
                              console_error_urls=list(urls))
    return r._console_errors(monitor)


def _cfg(ignore_console=None, ignore_http=None):
    return SimpleNamespace(target=SimpleNamespace(
        ignore_http_error_patterns=ignore_http or [],
        ignore_console_patterns=ignore_console or [],
    ))


def test_url_less_console_error_can_be_ignored_by_message():
    """위치 URL이 없는 프레임워크 경고를 메시지 본문으로 거른다.

    이것이 OpenMetadata에서 초록불을 막은 최종 원인이었다. URL 기반 필터만
    있던 때에는 어떤 패턴을 적어도 이런 메시지가 걸러지지 않았다.
    """
    kept = _console(
        _cfg(ignore_console=[r"i18next::translator"]),
        ["i18next::translator: missingKey ko translation label.foo"],
        [""],
    )
    assert kept == []


def test_real_error_survives_message_filter():
    """무시 목록에 없는 에러는 그대로 남는다 — 필터가 전부를 삼키면 안 된다."""
    kept = _console(
        _cfg(ignore_console=[r"i18next::translator"]),
        ["i18next::translator: missingKey", "TypeError: x is not a function"],
        ["", ""],
    )
    assert kept == ["TypeError: x is not a function"]


def test_url_filter_still_works():
    """메시지 필터를 더해도 기존 URL 기반 필터가 죽지 않는다."""
    kept = _console(
        _cfg(ignore_http=[r"/optional\.png$"]),
        ["failed to load resource"],
        ["http://x.test/optional.png"],
    )
    assert kept == []


def test_console_pattern_is_validated_at_load(tmp_path):
    """깨진 정규식은 실행 중이 아니라 설정을 읽는 시점에 걸린다."""
    p = tmp_path / "c.yaml"
    p.write_text(
        "target:\n  base_url: http://x.test\n"
        "  ignore_console_patterns: ['[unclosed']\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(str(p))


# ------------------------------------------------------------------ 로케일

def test_locale_defaults_to_korean_and_is_overridable(tmp_path):
    p = tmp_path / "a.yaml"
    p.write_text("target:\n  base_url: http://x.test\n", encoding="utf-8")
    assert load_config(str(p)).target.locale == "ko-KR"

    q = tmp_path / "b.yaml"
    q.write_text("target:\n  base_url: http://x.test\n  locale: en-US\n",
                 encoding="utf-8")
    assert load_config(str(q)).target.locale == "en-US"


def test_blank_locale_is_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("target:\n  base_url: http://x.test\n  locale: '  '\n",
                 encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(str(p))


# --------------------------------------------------------------- 값 표현 차이

def test_value_map_bridges_localized_ui_and_enum_oracle():
    """화면은 번역된 상태를, 정답원은 원값을 준다. 데이터는 같다."""
    result = compare(
        ["대상", "상태"],
        [["col_a", "실패"], ["col_b", "성공"]],
        ["대상", "상태"],
        ["target", "status"],
        [("col_a", "Failed"), ("col_b", "Success")],
        value_map={"실패": "Failed", "성공": "Success"},
    )
    assert result.matched
    assert result.missing_total == 0 and result.unexpected_total == 0


def test_value_map_does_not_hide_a_real_mismatch():
    """번역만 이어줄 뿐, 값이 실제로 다르면 여전히 불일치다.

    value_map이 대조를 무르게 만들면 이 기능 자체가 검증을 무의미하게 만든다.
    """
    result = compare(
        ["대상", "상태"],
        [["col_a", "성공"]],                      # 화면은 성공이라 하고
        ["대상", "상태"],
        ["target", "status"],
        [("col_a", "Failed")],                    # 정답원은 실패라 한다
        value_map={"실패": "Failed", "성공": "Success"},
    )
    assert not result.matched
    assert result.unexpected_total == 1


def test_without_value_map_localized_values_do_not_match():
    """매핑을 안 주면 예전처럼 불일치 — 기본 동작이 느슨해지지 않았다."""
    result = compare(
        ["상태"], [["실패"]], ["상태"], ["status"], [("Failed",)],
    )
    assert not result.matched


# ------------------------------------------------------- 표 추출: 유령 행

def test_value_map_only_touches_ui_side():
    """정답원 값은 건드리지 않는다 — 기준을 바꾸면 대조가 뒤집힌다."""
    result = compare(
        ["상태"], [["Failed"]], ["상태"], ["status"], [("실패",)],
        value_map={"실패": "Failed"},
    )
    assert not result.matched


# ------------------------------------------------------- 컨텍스트별 재로그인

def test_per_context_defaults_off(tmp_path):
    p = tmp_path / "d.yaml"
    p.write_text(
        "target:\n  base_url: http://x.test\n"
        "auth:\n  steps:\n    - {action: goto, value: /login}\n",
        encoding="utf-8",
    )
    assert load_config(str(p)).auth.per_context is False


def test_per_context_can_be_enabled(tmp_path):
    p = tmp_path / "e.yaml"
    p.write_text(
        "target:\n  base_url: http://x.test\n"
        "auth:\n  per_context: true\n  steps:\n    - {action: goto, value: /login}\n",
        encoding="utf-8",
    )
    assert load_config(str(p)).auth.per_context is True


class _RecordingPage:
    def __init__(self):
        self.calls = []

    def set_default_timeout(self, ms):
        pass


def _runner_for_reauth(per_context, steps):
    r = Runner.__new__(Runner)
    r.cfg = SimpleNamespace(
        auth=SimpleNamespace(per_context=per_context, steps=steps),
        target=SimpleNamespace(settle_ms=0),
    )
    r._exec_step = lambda page, step: page.calls.append(step)
    r._wait = lambda page, ms: None
    r._bounded_timeout = lambda ms: ms
    return r


def test_reauth_runs_auth_steps_in_the_scenario_context():
    page = _RecordingPage()
    _runner_for_reauth(True, ["step1", "step2"])._reauth_if_needed(page)
    assert page.calls == ["step1", "step2"]


def test_reauth_is_skipped_when_disabled():
    page = _RecordingPage()
    _runner_for_reauth(False, ["step1"])._reauth_if_needed(page)
    assert page.calls == []


def test_reauth_clears_only_login_noise():
    """로그인 화면의 잡음은 검사 대상 화면의 결함이 아니다."""
    page = _RecordingPage()
    monitor = SimpleNamespace(
        console_errors=["login page warning"], console_error_urls=[""],
        page_errors=["boom"], http_failures=["401"],
    )
    _runner_for_reauth(True, ["step1"])._reauth_if_needed(page, monitor)
    assert monitor.console_errors == []
    assert monitor.page_errors == []
    assert monitor.http_failures == []


def test_reauth_without_monitor_does_not_crash():
    page = _RecordingPage()
    _runner_for_reauth(True, ["step1"])._reauth_if_needed(page, None)
    assert page.calls == ["step1"]


def test_missing_auth_attribute_is_tolerated():
    """auth가 아예 없는 설정 객체에서도 넘어간다."""
    r = Runner.__new__(Runner)
    r.cfg = SimpleNamespace(target=SimpleNamespace(settle_ms=0))
    r._reauth_if_needed(_RecordingPage())
