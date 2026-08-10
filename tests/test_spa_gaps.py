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
    return r._console_errors(monitor)[0]


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


def test_ignored_console_errors_are_reported_not_discarded():
    """무시한 에러는 판정에서만 빠지고 기록에는 남는다.

    조용히 버리면 무시 목록을 넓게 적어 통과시킨 실행과 진짜로 깨끗한 실행이
    리포트에서 똑같아 보인다. 그러면 무시 목록이 곧 은폐 수단이 된다.
    """
    r = Runner.__new__(Runner)
    r.cfg = _cfg(ignore_console=[r"i18next"])
    monitor = SimpleNamespace(
        console_errors=["i18next::translator: missingKey", "TypeError: boom"],
        console_error_urls=["", ""],
    )
    kept, ignored = r._console_errors(monitor)
    assert kept == ["TypeError: boom"]
    assert ignored == ["i18next::translator: missingKey"]


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


# ------------------------------------------------- 오류 응답이 기대값인 검사

def test_fetch_expect_status_allows_error_responses(tmp_path):
    """권한 없는 요청이 401을 돌려주는지 — 오류 응답이 곧 기대값인 경우."""
    p = tmp_path / "f.yaml"
    p.write_text(
        "target:\n  base_url: http://x.test\n"
        "spec_checks:\n  - name: 권한없음-401\n    steps:\n"
        "      - {action: fetch, value: /api/v1/secret, expect_status: 401}\n",
        encoding="utf-8",
    )
    step = load_config(str(p)).spec_checks[0].steps[0]
    assert step.expect_status == 401
    # 상태코드만 볼 때는 본문을 담을 필요가 없다.
    assert step.store_as is None


def test_fetch_without_store_as_or_expect_status_is_rejected(tmp_path):
    """아무것도 확인하지 않는 fetch는 통과시켜 봐야 의미가 없다."""
    p = tmp_path / "g.yaml"
    p.write_text(
        "target:\n  base_url: http://x.test\n"
        "spec_checks:\n  - name: 빈fetch\n    steps:\n"
        "      - {action: fetch, value: /api/v1/x}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="store_as"):
        load_config(str(p))


def test_expect_status_rejected_on_other_actions(tmp_path):
    p = tmp_path / "h.yaml"
    p.write_text(
        "target:\n  base_url: http://x.test\n"
        "spec_checks:\n  - name: 잘못된사용\n    steps:\n"
        "      - {action: click, selector: '#b', expect_status: 401}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="fetch"):
        load_config(str(p))


def test_expect_status_out_of_range_is_rejected(tmp_path):
    p = tmp_path / "i.yaml"
    p.write_text(
        "target:\n  base_url: http://x.test\n"
        "spec_checks:\n  - name: 범위밖\n    steps:\n"
        "      - {action: fetch, value: /x, expect_status: 999}\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="상태코드"):
        load_config(str(p))


# --------------------------------------------------- 대조한 행 수를 드러낸다

def _res(ui, db):
    from webtest_agent.models import DataCheckResult
    dc = DataCheckResult()
    dc.ui_count, dc.db_count = ui, db
    return SimpleNamespace(data_check=dc)


def test_rows_note_shows_the_number_not_a_judgement():
    """몇 행인지만 말하고 '얇다·충분하다'를 판단하지 않는다.

    임계값을 도구가 정하면 근거 없는 판단이 판정 옆에 붙는다. 숫자는 사실이므로
    표시하되, 해석은 읽는 사람에게 넘긴다.
    """
    from webtest_agent.cli import _rows_note
    assert _rows_note(_res(1, 1)) == " (대조 1행)"
    assert _rows_note(_res(250, 250)) == " (대조 250행)"
    # 어떤 행 수에도 경고 문구가 붙지 않는다
    for n in (1, 2, 5, 100):
        assert "얇" not in _rows_note(_res(n, n))
        assert "⚠" not in _rows_note(_res(n, n))


def test_zero_row_comparison_is_called_out():
    """0행 대 0행은 '일치'지만 아무것도 증명하지 못한다.

    이건 임계값이 아니라 논리 문제다 — 비교한 값이 하나도 없다.
    실패로 만들지는 않는다. '0건 검색이면 표도 비어야 한다'처럼 0행이
    정답인 검사가 실제로 있기 때문이다.
    """
    from webtest_agent.cli import _rows_note
    assert _rows_note(_res(0, 0)) == " (양쪽 0행 — 비교한 값 없음)"


def test_no_note_for_scenarios_without_a_data_check():
    from webtest_agent.cli import _rows_note
    assert _rows_note(SimpleNamespace(data_check=None)) == ""
    assert _rows_note(SimpleNamespace()) == ""


def test_run_level_total_counts_every_comparison():
    from webtest_agent.cli import _compared_rows
    got = _compared_rows([_res(1, 1), _res(11, 11), _res(0, 0),
                          SimpleNamespace(data_check=None)])
    assert got == {"checks": 3, "ui": 12, "db": 12, "empty": 1}


# ------------------------------- 설정 검증·실행 폴더·이력 identity (경쟁 분석 지적)

def test_assert_text_exact_requires_selector_and_value(tmp_path):
    """액션을 추가할 때 필수값 검증 집합에 넣지 않으면, 잘못된 YAML이
    설정 단계를 통과해 실행 중에야 터진다. 실제로 그 상태로 커밋된 적이 있다."""
    for step in ("{action: assert_text_exact}",
                 "{action: assert_text_exact, selector: '#a'}",
                 "{action: assert_text_exact, value: 'x'}"):
        p = tmp_path / "x.yaml"
        p.write_text(
            "target:\n  base_url: http://x.test\n"
            f"spec_checks:\n  - name: t\n    steps:\n      - {step}\n",
            encoding="utf-8")
        with pytest.raises(ConfigError):
            load_config(str(p))


def test_run_dir_never_reuses_an_existing_folder(tmp_path):
    """같은 초에 시작한 두 실행이 한 폴더를 쓰면 앞선 실행의 증거가 사라진다."""
    from webtest_agent.cli import _make_run_dir
    cfg = SimpleNamespace(output_dir=str(tmp_path))
    dirs = [_make_run_dir(cfg, None) for _ in range(3)]
    assert len({str(d) for d in dirs}) == 3, "같은 폴더를 다시 내줬다"
    assert all(d.exists() for d in dirs)


def test_history_identity_separates_browsers(tmp_path):
    """같은 설정을 다른 엔진으로 돌린 결과가 한 추이에 섞이면,
    '어제는 통과했는데 오늘 깨졌다'가 실은 엔진 차이일 수 있다."""
    import json
    from webtest_agent.history import _identity_of
    run = tmp_path / "20260101-000000"
    run.mkdir()
    (run / "report.json").write_text(json.dumps({
        "meta": {"base_url": "http://x", "config_path": "c.yaml", "browser": "firefox"},
        "scenarios": []}), encoding="utf-8")
    assert _identity_of(run) == ("http://x", "c.yaml", "firefox")


# ----------------------------------------- 트레이스 자격증명 (실측으로 확인됨)

def _report_cfg(mode):
    from webtest_agent.config import ReportConfig
    return ReportConfig(trace=mode)


def test_trace_defaults_to_on_failure(tmp_path):
    """기본값이 true면 통과한 실행마다 자격증명이 담긴 파일이 쌓인다.

    trace에는 네트워크 요청이 통째로 들어가고, 로그인한 세션이면 Authorization
    헤더의 토큰이 평문으로 담긴다. 실제 실행 산출물에서 718자 JWT를 확인했다.
    """
    p = tmp_path / "a.yaml"
    p.write_text("target:\n  base_url: http://x.test\n", encoding="utf-8")
    cfg = load_config(str(p))
    assert cfg.report.trace == "on-failure"
    assert cfg.report.trace_enabled is True        # 기록은 해야 실패 시 남긴다
    assert cfg.report.trace_only_on_failure is True


def test_trace_modes(tmp_path):
    for raw, enabled, only_fail in (("true", True, False),
                                    ("false", False, False),
                                    ("on-failure", True, True)):
        p = tmp_path / f"{raw}.yaml"
        p.write_text(f"target:\n  base_url: http://x.test\nreport:\n  trace: {raw}\n",
                     encoding="utf-8")
        r = load_config(str(p)).report
        assert r.trace_enabled is enabled and r.trace_only_on_failure is only_fail


def test_invalid_trace_mode_is_rejected(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("target:\n  base_url: http://x.test\nreport:\n  trace: sometimes\n",
                 encoding="utf-8")
    with pytest.raises(ConfigError, match="on-failure"):
        load_config(str(p))


def _runner_for_trace(mode, tmp_path):
    r = Runner.__new__(Runner)
    r.cfg = SimpleNamespace(report=_report_cfg(mode))
    r.run_dir = tmp_path
    return r


def _result_with_trace(tmp_path, status):
    from webtest_agent.models import ScenarioResult
    (tmp_path / "traces").mkdir(exist_ok=True)
    (tmp_path / "traces" / "01_x.zip").write_bytes(b"trace-with-token")
    res = ScenarioResult(name="x", kind="spec_check", page="/")
    res.status = status
    res.trace = "traces/01_x.zip"
    return res


def test_passing_scenario_trace_is_removed(tmp_path):
    from webtest_agent.models import PASS as P
    r = _runner_for_trace("on-failure", tmp_path)
    res = _result_with_trace(tmp_path, P)
    r._prune_trace(res)
    assert res.trace == ""
    assert not (tmp_path / "traces" / "01_x.zip").exists()


def test_failing_scenario_trace_is_kept(tmp_path):
    """실패는 원인을 찾아야 하므로 남긴다 — 여기서 지우면 도구가 쓸모없어진다."""
    from webtest_agent.models import FAIL as F
    r = _runner_for_trace("on-failure", tmp_path)
    res = _result_with_trace(tmp_path, F)
    r._prune_trace(res)
    assert res.trace == "traces/01_x.zip"
    assert (tmp_path / "traces" / "01_x.zip").exists()


def test_trace_true_keeps_everything(tmp_path):
    from webtest_agent.models import PASS as P
    r = _runner_for_trace(True, tmp_path)
    res = _result_with_trace(tmp_path, P)
    r._prune_trace(res)
    assert res.trace == "traces/01_x.zip"


def test_report_warns_when_traces_remain():
    """무엇이 들어 있는지 모르고 공유하는 상태가 가장 나쁘다."""
    from webtest_agent.report import _trace_warning_html
    kept = SimpleNamespace(trace="traces/01_x.zip")
    none_kept = SimpleNamespace(trace="")
    assert "자격증명" not in _trace_warning_html([none_kept])
    warning = _trace_warning_html([kept, none_kept])
    assert "1건" in warning and "토큰" in warning


# ------------------------------- 정답원 장애 ≠ 제품 실패 (경쟁 분석 8.2)

def _verdict_runner():
    r = Runner.__new__(Runner)
    r.cfg = SimpleNamespace(report=SimpleNamespace(trace=False))
    return r


def _scn(kind="data_check"):
    from webtest_agent.scenarios import Scenario
    return Scenario(name="t", kind=kind, page="/", steps=[])


def _res_with(dc=None, steps=None, console=None):
    from webtest_agent.models import ScenarioResult, StepResult
    res = ScenarioResult(name="t", kind="data_check", page="/")
    res.data_check = dc
    res.steps = steps or []
    res.console_errors = console or []
    return res


def test_dead_oracle_is_marked_not_proven():
    """정답원이 죽으면 화면이 맞는지 틀린지 알 수 없다.

    이걸 제품 결함으로 보고하면 멀쩡한 코드를 뒤지게 만든다. 통과로 세지도
    않으므로 상태는 FAIL이되, 종류를 구분한다.
    """
    from webtest_agent.models import DataCheckResult, FAIL
    dc = DataCheckResult(note="정답원(DB/API) 조회 실패: connection refused")
    res = _res_with(dc=dc)
    _verdict_runner()._verdict(res, _scn(), hard_fail=False)
    assert res.status == FAIL          # 통과로 세지 않는다
    assert res.not_proven is True
    assert "정답원" in res.not_proven_reason


def test_real_mismatch_is_not_marked_not_proven():
    """값이 실제로 다른 것은 제품 결함이다 — 판정 불가로 눙치면 안 된다."""
    from webtest_agent.models import DataCheckResult, FAIL
    dc = DataCheckResult(matched=False, ui_count=3, db_count=5, missing_total=2)
    res = _res_with(dc=dc)
    _verdict_runner()._verdict(res, _scn(), hard_fail=False)
    assert res.status == FAIL
    assert res.not_proven is False


def test_real_failure_wins_over_not_proven():
    """둘 다 있으면 제품 결함이 우선이다. 결함을 가리면 안 된다."""
    from webtest_agent.models import DataCheckResult, FAIL
    dc = DataCheckResult(note="정답원 조회 실패")
    res = _res_with(dc=dc, console=["TypeError: boom"])
    _verdict_runner()._verdict(res, _scn(), hard_fail=False)
    assert res.status == FAIL
    assert res.not_proven is False, "진짜 결함이 있는데 판정 불가로 표시되면 결함이 묻힌다"


def test_passing_check_is_untouched():
    from webtest_agent.models import DataCheckResult, PASS
    dc = DataCheckResult(matched=True, ui_count=3, db_count=3)
    res = _res_with(dc=dc)
    _verdict_runner()._verdict(res, _scn(), hard_fail=False)
    assert res.status == PASS and res.not_proven is False
