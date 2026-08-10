"""`check-config` — 못 찾은 셀렉터를 한 번에 모아 보여준다.

이 명령이 지켜야 할 한 가지는 **'확인 못 함'을 '못 찾음'에 섞지 않는 것**이다.

앞 스텝이 실패하면 그 뒤 화면은 원래 나와야 할 상태가 아니다. 그 상태에서
본 결과를 '설정이 틀렸다'로 보고하면 멀쩡한 줄을 고치게 되고, 반대로 '찾았다'로
보고하면 앞을 고친 뒤에도 맞다는 보장이 없는데 확인된 것처럼 읽힌다.
둘 다 하지 않는다.
"""
from __future__ import annotations

from webtest_agent.config import Step
from webtest_agent.precheck import (INFO, MISS, OK, UNCHECKED, PrecheckResult,
                                    Probe, ScenarioProbe, classify,
                                    destructive_hit, exit_code,
                                    has_unresolved_var, missing_columns, render,
                                    step_label)


# ── 판정 구분 ───────────────────────────────────────────────────

def test_found_is_found():
    assert classify(3, blocked=False, needs_selector=True) == OK


def test_zero_before_any_failure_is_a_real_miss():
    """앞이 다 성공한 상태에서 0개면 셀렉터가 틀린 것이다."""
    assert classify(0, blocked=False, needs_selector=True) == MISS


def test_zero_after_a_failure_is_not_reported_as_a_miss():
    """화면이 거기까지 못 간 것을 설정 결함으로 보고하면 멀쩡한 줄을 고치게 된다."""
    assert classify(0, blocked=True, needs_selector=True) == UNCHECKED


def test_found_after_a_failure_is_not_counted_as_confirmed():
    """막힌 화면에서 찾았다고 앞을 고친 뒤에도 맞다는 보장은 없다."""
    assert classify(2, blocked=True, needs_selector=True) == UNCHECKED


def test_optional_selector_miss_is_not_a_config_error():
    """셀렉터가 필수가 아닌 액션은 0개여도 틀렸다고 할 수 없다."""
    assert classify(0, blocked=False, needs_selector=False) == UNCHECKED


def test_runtime_variable_selector_cannot_be_judged():
    """`{{주문번호}}`는 실행해야 값이 정해진다. 추측해서 0개로 세면 없는 결함이 생긴다."""
    assert has_unresolved_var("#order-{{order_no}}")
    assert not has_unresolved_var("#order-123")
    assert not has_unresolved_var(None)


# ── 안전 경계 ───────────────────────────────────────────────────
#
# 이 명령은 셀렉터만 보는 게 아니라 스텝을 실제로 실행한다. run이 승인 없이
# 누르지 않는 동작을 여기서 누르면 안전 경계가 이 명령 하나로 뚫린다.

def test_destructive_click_is_not_executed():
    assert destructive_hit(Step(action="click", selector="#delete-account"))


def test_drag_to_a_destructive_target_is_caught():
    """끌어다 놓는 위치가 휴지통일 수 있어 목적지까지 본다."""
    assert destructive_hit(Step(action="drag", selector="#row-1", value="#trash-delete"))


def test_ordinary_click_is_executed():
    """정상 흐름에 필요한 동작까지 막으면 뒤 스텝을 하나도 확인 못 한다."""
    assert destructive_hit(Step(action="click", selector="#apply")) is None
    assert destructive_hit(Step(action="click", selector="#submit-order")) is None


def test_non_clicking_actions_are_not_guarded():
    """단언·대기는 화면을 바꾸지 않는다."""
    assert destructive_hit(Step(action="assert_visible", selector="#delete-btn")) is None


def test_guard_looks_inside_frames_too():
    """결제창 안의 버튼이라고 예외가 되면 안 된다."""
    assert destructive_hit(
        Step(action="click", selector="#ok", frame="iframe#delete-dialog"))


# ── 열 이름 ─────────────────────────────────────────────────────

def test_missing_columns_found_before_the_run():
    """로케일이 바뀌면 헤더 글자가 통째로 달라진다."""
    assert missing_columns(["주문번호", "Status"], ["주문번호", "상태"]) == ["Status"]


def test_all_columns_present():
    assert missing_columns(["고객", "상태"], ["주문번호", "고객", "상태"]) == []


def test_empty_columns_means_all_headers_and_is_not_a_miss():
    """columns를 안 적으면 화면 헤더를 그대로 쓴다 — 빠진 열이 있을 수 없다."""
    assert missing_columns([], ["a", "b"]) == []


# ── 종료코드 ────────────────────────────────────────────────────

def _result(*scenarios, fatal="", skipped=()):
    return PrecheckResult(scenarios=list(scenarios), skipped=list(skipped), fatal=fatal)


def test_exit_zero_only_when_everything_was_found():
    sp = ScenarioProbe("가", "spec_check", [Probe("1. click #a", OK, "1개")])
    assert exit_code(_result(sp)) == 0


def test_miss_gives_exit_one():
    sp = ScenarioProbe("가", "spec_check", [Probe("1. click #a", MISS)])
    assert exit_code(_result(sp)) == 1


def test_scenario_that_could_not_start_is_not_a_pass():
    """페이지에 못 갔는데 0으로 끝내면 '확인 완료'로 읽힌다."""
    sp = ScenarioProbe("가", "spec_check", error="타임아웃")
    assert exit_code(_result(sp)) == 1


def test_unchecked_alone_is_not_a_failure():
    """실행 시점에 정해지는 셀렉터만 남았다면 고칠 것이 없다."""
    sp = ScenarioProbe("가", "spec_check",
                       [Probe("1. click #a", OK, "1개"),
                        Probe("2. click #b-{{id}}", UNCHECKED, "실행해야 정해짐")])
    assert exit_code(_result(sp)) == 0


def test_target_down_gives_exit_two():
    """설정이 틀린 것과 사이트가 안 뜨는 것은 다른 사건이다."""
    assert exit_code(_result(fatal="대상 사이트에 접속할 수 없습니다")) == 2


# ── 출력 ────────────────────────────────────────────────────────

def test_report_separates_the_two_kinds():
    sp = ScenarioProbe("주문목록", "data_check", [
        Probe("1. select #status", MISS),
        Probe("2. click #apply", UNCHECKED, "1개"),
    ])
    text = render(_result(sp))
    assert "못 찾음 1개" in text
    assert "확인 못 함 1개" in text
    assert "틀렸다는 뜻도, 맞다는 뜻도 아닙니다" in text


def test_scenario_with_unchecked_lines_does_not_get_a_checkmark():
    """✓를 달면 '이 시나리오는 다 봤다'로 읽힌다."""
    clean = ScenarioProbe("가", "spec_check", [Probe("1. click #a", OK, "1개")])
    partial = ScenarioProbe("나", "spec_check", [
        Probe("1. click #a", UNCHECKED, "안전 차단"),
    ])
    text = render(_result(clean, partial))
    assert "✓ [spec_check] 가" in text
    assert "✓ [spec_check] 나" not in text


def test_passing_report_says_what_it_did_not_check():
    """'전부 통과'로 읽히면 기대값까지 확인된 줄 안다."""
    sp = ScenarioProbe("가", "spec_check", [Probe("1. click #a", OK, "1개")])
    text = render(_result(sp))
    assert "기대값이 맞는지는" in text


def test_skipped_write_checks_are_named_not_silently_dropped():
    """조용히 빼면 '다 확인했다'로 읽힌다."""
    sp = ScenarioProbe("가", "spec_check", [Probe("1. click #a", OK, "1개")])
    text = render(_result(sp, skipped=["write_checks 1개 — 데이터를 바꾸므로 실행하지 않습니다"]))
    assert "건너뜀" in text and "write_checks" in text


def test_matching_selectors_are_not_printed_line_by_line():
    """맞은 것까지 다 찍으면 틀린 게 안 보인다."""
    sp = ScenarioProbe("가", "spec_check", [
        Probe("1. click #a", OK, "1개"),
        Probe("2. click #b", MISS),
    ])
    text = render(_result(sp))
    assert "#b" in text
    assert "#a" not in text


def test_scenario_that_never_started_says_so():
    text = render(_result(ScenarioProbe("가", "data_check", error="net::ERR_CONNECTION_REFUSED")))
    assert "시작하지 못함" in text
    assert "ERR_CONNECTION_REFUSED" in text


def test_fatal_report_does_not_claim_scenarios_were_checked():
    text = render(_result(fatal="로그인 실패 — #dashboard 없음"))
    assert "점검 불가" in text
    assert "시나리오 0개" not in text, "점검을 못 했는데 집계를 내면 안 된다"


def test_step_label_survives_a_missing_selector():
    assert step_label(2, "wait_ms", None) == "2. wait_ms"
    assert step_label(1, "click", "#go") == "1. click  #go"


def test_info_probe_is_not_counted_as_a_problem():
    """헤더 목록 같은 정보 줄이 결함 수에 섞이면 안 된다."""
    sp = ScenarioProbe("가", "data_check", [Probe("헤더 6개 · 123행", INFO, "주문번호 | 고객")])
    result = _result(sp)
    assert result.miss_total == 0
    assert result.unchecked_total == 0
    assert exit_code(result) == 0
