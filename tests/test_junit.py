"""JUnit XML 출력(CI 통합) 회귀 테스트 — well-formed·이스케이프·집계·infra."""
from __future__ import annotations

import xml.dom.minidom as minidom

from webtest_agent.models import FAIL, PASS, WARN, RunMeta, ScenarioResult
from webtest_agent.report import _junit_xml


def _meta(status="failed", error="", duration_ms=42000):
    return RunMeta(title="t", base_url="http://x", app_version="", config_path="c",
                   started_at="s", duration_ms=duration_ms, status=status, error=error)


def _summary(total, p, w, f):
    return {"total": total, "pass": p, "warn": w, "fail": f, "flaky": 0}


def test_wellformed_and_counts():
    results = [
        ScenarioResult(name="a", kind="data_check", page="/", status=PASS, duration_ms=1000),
        ScenarioResult(name="b", kind="spec_check", page="/", status=FAIL, duration_ms=2000,
                       reasons=["콘솔 에러"]),
        ScenarioResult(name="c", kind="visual_check", page="/", status=WARN, duration_ms=500,
                       reasons=["미승인 기준선"]),
    ]
    xml = _junit_xml(_meta(), _summary(3, 1, 1, 1), results)
    dom = minidom.parseString(xml)   # well-formed 아니면 예외
    ts = dom.getElementsByTagName("testsuite")[0]
    assert ts.getAttribute("tests") == "3"
    assert ts.getAttribute("failures") == "1"
    assert ts.getAttribute("errors") == "0"
    assert len(dom.getElementsByTagName("testcase")) == 3
    assert len(dom.getElementsByTagName("failure")) == 1
    assert len(dom.getElementsByTagName("system-out")) == 1   # 경고


def test_xml_special_chars_escaped():
    results = [ScenarioResult(name='이름 <t> & "q"', kind="spec_check", page="/",
                              status=FAIL, duration_ms=1,
                              reasons=['사유 <bad> & "x"'])]
    xml = _junit_xml(_meta(), _summary(1, 0, 0, 1), results)
    minidom.parseString(xml)   # 이스케이프 안 되면 파싱 실패
    body = xml.split("?>", 1)[1]
    assert "<bad>" not in body
    assert '& "x"' not in body
    assert "&lt;bad&gt;" in body


def test_infra_error_is_suite_error():
    xml = _junit_xml(_meta("infra_error", "대상 접속 불가"), _summary(0, 0, 0, 0), [])
    dom = minidom.parseString(xml)
    ts = dom.getElementsByTagName("testsuite")[0]
    assert ts.getAttribute("errors") == "1"
    assert len(dom.getElementsByTagName("error")) == 1


def test_all_pass_no_failure_tags():
    results = [ScenarioResult(name="a", kind="data_check", page="/", status=PASS, duration_ms=1)]
    xml = _junit_xml(_meta("passed"), _summary(1, 1, 0, 0), results)
    dom = minidom.parseString(xml)
    assert dom.getElementsByTagName("testsuite")[0].getAttribute("failures") == "0"
    assert len(dom.getElementsByTagName("failure")) == 0


def test_write_reports_emits_xml(tmp_path):
    from webtest_agent.report import write_reports
    results = [ScenarioResult(name="a", kind="data_check", page="/", status=PASS, duration_ms=1)]
    write_reports(tmp_path, _meta("passed"), results, [], [])
    xml_path = tmp_path / "report.xml"
    assert xml_path.exists()
    minidom.parseString(xml_path.read_text(encoding="utf-8"))
