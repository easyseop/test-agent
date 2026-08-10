"""판정 불가를 리포트 표면에서 제품 결함과 구분한다.

`not_proven`은 모델과 종료코드에는 이미 있었지만 `summary`에 없었다. 그래서
리포트를 읽는 쪽(Console·CI)에서는 정답원이 죽어 판정하지 못한 시나리오가
제품 결함과 똑같이 '실패 1건'으로만 보였다. 멀쩡한 코드를 뒤지게 만드는 차이다.

여기서 고정하는 계약은 두 가지다.

    부분집합이다        판정 불가는 fail 안에 든다. 별도 칸으로 빼면
                       pass+warn+fail이 전체와 어긋나 Console 파서가 거부하고,
                       무엇보다 '통과가 아님'이 흐려진다.
    사라지지 않는다     failures + errors 는 언제나 실패 총수와 같다.
                       어느 쪽에도 안 들어간 시나리오가 있으면 안 된다.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from webtest_agent.models import FAIL, PASS, WARN, RunMeta, ScenarioResult
from webtest_agent.report import _junit_xml, summarize, unproven


def _scenario(name, status=PASS, not_proven=False, reason="", kind="spec_check"):
    return ScenarioResult(
        name=name, kind=kind, page="/", status=status,
        reasons=[reason] if reason else [],
        not_proven=not_proven,
        not_proven_reason=reason if not_proven else "",
    )


def _meta(status="failed"):
    return RunMeta(title="t", base_url="http://127.0.0.1:1", app_version="v",
                   config_path="c.yaml", started_at="2026-08-10 00:00:00",
                   duration_ms=1000, status=status)


# ── 부분집합 계약 ───────────────────────────────────────────────

def test_not_proven_is_counted_inside_fail():
    """따로 빼면 pass+warn+fail이 전체와 어긋나 Console이 리포트를 거부한다."""
    results = [
        _scenario("정상"),
        _scenario("정답원죽음", FAIL, not_proven=True, reason="정답원 조회 실패"),
        _scenario("진짜결함", FAIL, reason="UI↔DB 불일치"),
    ]
    s = summarize(results)
    assert s["fail"] == 2, "판정 불가도 실패에서 빠지면 안 된다"
    assert s["not_proven"] == 1
    assert s["pass"] + s["warn"] + s["fail"] == s["total"]


def test_not_proven_never_exceeds_fail():
    results = [_scenario(f"s{i}", FAIL, not_proven=True, reason="정답원 조회 실패")
               for i in range(3)]
    s = summarize(results)
    assert s["not_proven"] <= s["fail"]
    assert s["not_proven"] == 3


def test_a_passing_scenario_is_never_counted_as_unproven():
    """통과에 표시가 붙어도 세지 않는다 — 세면 부분집합이 깨진다."""
    results = [_scenario("이상한통과", PASS, not_proven=True, reason="x")]
    s = summarize(results)
    assert s["not_proven"] == 0
    assert s["not_proven"] <= s["fail"]
    assert unproven(results) == []


def test_warn_is_not_counted_as_unproven():
    results = [_scenario("경고", WARN, not_proven=True, reason="x")]
    assert summarize(results)["not_proven"] == 0


def test_clean_run_reports_zero():
    s = summarize([_scenario("a"), _scenario("b")])
    assert s["not_proven"] == 0
    assert s["fail"] == 0


# ── JUnit: 돌린 실패와 못 돌린 것을 나눈다 ──────────────────────

def _suite(results, status="failed"):
    xml = _junit_xml(_meta(status), summarize(results), results)
    return ET.fromstring(xml)


def test_junit_separates_failures_from_errors():
    """`<failure>`는 '돌렸는데 달랐다', `<error>`는 '돌리지 못했다'."""
    results = [
        _scenario("정상"),
        _scenario("정답원죽음", FAIL, not_proven=True, reason="정답원 조회 실패"),
        _scenario("진짜결함", FAIL, reason="UI↔DB 불일치"),
    ]
    suite = _suite(results, status="infra_error")
    assert suite.get("failures") == "1"
    assert suite.get("errors") == "1"
    assert suite.get("tests") == "3"

    by_name = {case.get("name"): case for case in suite.findall("testcase")}
    assert by_name["정답원죽음"].find("error") is not None
    assert by_name["정답원죽음"].find("failure") is None
    assert by_name["진짜결함"].find("failure") is not None
    assert by_name["진짜결함"].find("error") is None
    assert list(by_name["정상"]) == []


def test_junit_loses_no_failed_scenario():
    """failures + errors 는 언제나 실패 총수와 같다."""
    results = [
        _scenario("a", FAIL, reason="x"),
        _scenario("b", FAIL, not_proven=True, reason="정답원 조회 실패"),
        _scenario("c", FAIL, not_proven=True, reason="정답원 조회 실패"),
        _scenario("d"),
    ]
    suite = _suite(results, status="infra_error")
    assert int(suite.get("failures")) + int(suite.get("errors")) == summarize(results)["fail"]
    marked = sum(1 for case in suite.findall("testcase")
                 if case.find("failure") is not None or case.find("error") is not None)
    assert marked == 3, "실패한 시나리오가 어느 칸에도 안 들어가면 안 된다"


def test_a_collapsed_run_still_reports_suite_level_error():
    """브라우저가 죽거나 대상이 안 뜨면 시나리오별 결과를 내지 않는다.

    중간까지의 통과를 늘어놓으면 실행이 끝난 것처럼 읽힌다. 판정 불가가
    시나리오 단위로 설명되지 않는 경우가 바로 그것이다.
    """
    results = [_scenario("도중까지통과")]
    suite = _suite(results, status="infra_error")
    assert suite.get("tests") == "1"
    assert suite.get("errors") == "1"
    assert suite.get("failures") == "0"
    case = suite.find("testcase")
    assert case.get("name") == "실행 불가(infra_error)"
    assert case.find("error") is not None


def test_an_ordinary_failing_run_is_unchanged():
    """판정 불가가 없으면 예전과 똑같이 failures만 쓴다."""
    results = [_scenario("a", FAIL, reason="x"), _scenario("b")]
    suite = _suite(results, status="failed")
    assert suite.get("failures") == "1"
    assert suite.get("errors") == "0"


def test_error_message_states_why_it_could_not_be_judged():
    """CI 화면에서 '왜 못 돌렸는지'가 바로 보여야 한다."""
    results = [_scenario("정답원죽음", FAIL, not_proven=True,
                         reason="정답원(DB/API) 조회 실패: HTTP Error 503")]
    suite = _suite(results, status="infra_error")
    error = suite.find("testcase").find("error")
    assert "정답원" in error.get("message")
