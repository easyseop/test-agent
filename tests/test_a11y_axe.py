"""axe-core 접근성 점검의 회귀 테스트.

가장 중요한 계약: **'위반 0건'과 '검사를 못 했다'는 다른 사건이다.**
CSP가 스크립트 주입을 막거나 페이지가 죽으면 axe는 아무것도 못 돌린다. 이때
0건으로 보고하면 '접근성 문제 없음'이라는 뜻이 되어 조용한 거짓 통과가 된다.
아래 테스트의 절반은 그 한 가지를 지키기 위한 것이다.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from webtest_agent.a11y import (IMPACT_ORDER, A11yPageResult, at_or_above,
                                axe_available, count_by_impact, run_axe,
                                summarize_impacts)
from webtest_agent.cli import _a11y_scenario_result
from webtest_agent.models import FAIL, PASS, WARN


# ── 번들 ────────────────────────────────────────────────────────

def test_axe_bundle_is_vendored():
    """CDN을 쓰면 실행 시점마다 규칙이 달라져 판정이 흔들린다(결정성 위반)."""
    assert axe_available(), "axe-core 번들이 저장소에 없다"


def test_korean_locale_bundled():
    from webtest_agent.a11y import _AXE_LOCALE_PATH
    data = json.loads(_AXE_LOCALE_PATH.read_text(encoding="utf-8"))
    assert data.get("lang") == "ko"
    assert data["rules"]["image-alt"]["help"]           # 번역이 실제로 들어있다


# ── 주입·실행 실패는 0건이 아니다 ────────────────────────────────

class _Page:
    """add_script_tag / evaluate를 흉내내는 가짜 페이지."""

    def __init__(self, inject_error=None, result=None, eval_error=None):
        self.url = "http://x/"
        self._inject_error = inject_error
        self._result = result
        self._eval_error = eval_error

    def add_script_tag(self, content=None):
        if self._inject_error:
            raise RuntimeError(self._inject_error)

    def evaluate(self, script, arg=None):
        if "contentType" in script:
            return "text/html"          # 기본은 HTML 문서
        if self._eval_error:
            raise RuntimeError(self._eval_error)
        return self._result


def test_csp_injection_failure_is_not_zero_violations():
    page = _Page(inject_error="Refused to execute inline script because it violates CSP")
    res = run_axe(page)
    assert res.checked is False
    assert res.issues == []
    assert "주입 실패" in res.error


def test_evaluate_failure_is_not_zero_violations():
    res = run_axe(_Page(eval_error="Execution context was destroyed"))
    assert res.checked is False
    assert "실행 실패" in res.error


def test_axe_timeout_is_reported_as_error():
    res = run_axe(_Page(result={"error": "axe 실행 시간 초과"}))
    assert res.checked is False
    assert "시간 초과" in res.error


def test_zero_rules_run_is_treated_as_not_checked():
    """규칙이 하나도 안 돌았는데 0건이면 '깨끗한 페이지'가 아니라 '검사 안 된 페이지'다."""
    res = run_axe(_Page(result={"violations": [], "rulesRun": 0}))
    assert res.checked is False
    assert res.error


def test_clean_page_with_rules_run_is_checked():
    res = run_axe(_Page(result={"violations": [], "rulesRun": 62}))
    assert res.checked is True
    assert res.issues == []
    assert res.error == ""


def test_violations_are_normalized():
    res = run_axe(_Page(result={"rulesRun": 60, "violations": [
        {"id": "image-alt", "impact": "critical", "help": "이미지는 대체텍스트가 필요합니다",
         "helpUrl": "http://h", "nodes": [{"target": "img", "html": "<img>"}], "nodeCount": 3},
    ]}))
    assert res.checked is True
    issue = res.issues[0]
    assert issue["type"] == "image-alt"
    assert issue["impact"] == "critical"
    assert issue["node_count"] == 3


def test_unknown_impact_falls_back_to_minor():
    res = run_axe(_Page(result={"rulesRun": 10, "violations": [
        {"id": "x", "impact": "catastrophic", "nodes": [], "nodeCount": 0}]}))
    assert res.issues[0]["impact"] == "minor"


# ── 판정: 점검 실패는 통과를 주지 않는다 ────────────────────────

def _page(issues, error=""):
    return SimpleNamespace(a11y=issues, a11y_error=error, path="/p")


def _issue(rule="image-alt", impact="critical"):
    return {"type": rule, "impact": impact, "detail": "d", "node_count": 1}


@pytest.mark.parametrize("severity,expected", [("warn", WARN), ("fail", FAIL)])
def test_unchecked_page_never_passes(severity, expected):
    """이 테스트가 깨지면 CSP 걸린 사이트가 '접근성 이상 없음'으로 통과한다."""
    res = _a11y_scenario_result([_page([], error="axe 주입 실패(CSP 등)")],
                                severity)
    assert res.status == expected
    assert "확인 불가" in res.reasons[0]


def test_mixed_checked_and_unchecked_does_not_pass():
    """일부 페이지만 점검됐는데 위반이 없다고 통과시키면 나머지가 묻힌다."""
    res = _a11y_scenario_result(
        [_page([]), _page([], error="주입 실패")], "warn")
    assert res.status == WARN


def test_all_checked_and_clean_passes():
    res = _a11y_scenario_result([_page([]), _page([])], "fail")
    assert res.status == PASS


# ── min_impact 게이팅 ───────────────────────────────────────────

def test_min_impact_filters_lower_severity():
    """규칙 100여 개를 한꺼번에 실패로 걸면 기존 운영이 전부 빨개진다."""
    pages = [_page([_issue("minor-rule", "minor"), _issue("mod", "moderate")])]
    res = _a11y_scenario_result(pages, "fail", min_impact="serious")
    assert res.status == PASS, "임계값 미만 이슈가 게이팅에 셌다"


def test_min_impact_counts_at_threshold_and_above():
    pages = [_page([_issue("a", "serious"), _issue("b", "critical"),
                    _issue("c", "minor")])]
    res = _a11y_scenario_result(pages, "fail", min_impact="serious")
    assert res.status == FAIL
    assert "2건" in res.reasons[0]


def test_min_impact_does_not_apply_to_builtin_engine():
    """간이 엔진 이슈에는 impact가 없다 — 임계값으로 걸러버리면 전부 사라진다."""
    pages = [_page([{"type": "img-alt", "detail": "x"}])]
    res = _a11y_scenario_result(pages, "fail", engine="builtin",
                                min_impact="critical")
    assert res.status == FAIL


# ── 심각도 유틸 ─────────────────────────────────────────────────

def test_impact_ordering():
    assert at_or_above("critical", "minor") is True
    assert at_or_above("minor", "critical") is False
    assert at_or_above("serious", "serious") is True
    assert at_or_above("존재하지-않음", "minor") is False


def test_count_and_summary():
    issues = [_issue(impact="critical"), _issue(impact="critical"),
              _issue(impact="minor")]
    assert count_by_impact(issues)["critical"] == 2
    summary = summarize_impacts(issues)
    assert "critical 2건" in summary and "minor 1건" in summary
    assert summarize_impacts([]) == "없음"


def test_impact_order_is_ascending():
    assert IMPACT_ORDER == ("minor", "moderate", "serious", "critical")


# ── 설정 검증 ───────────────────────────────────────────────────

def _cfg(tmp_path, body):
    from webtest_agent.config import load_config
    p = tmp_path / "c.yaml"
    p.write_text("target: {base_url: 'http://x'}\n" + body, encoding="utf-8")
    return load_config(str(p))


def test_engine_defaults_to_axe(tmp_path):
    assert _cfg(tmp_path, "a11y: {enabled: true}\n").a11y.engine == "axe"


def test_severity_defaults_to_info(tmp_path):
    """도입 첫날 빨간 화면을 보면 팀은 점검 자체를 꺼버린다."""
    cfg = _cfg(tmp_path, "a11y: {enabled: true}\n").a11y
    assert cfg.severity == "info" and cfg.min_impact == "minor"


def test_unknown_engine_rejected(tmp_path):
    from webtest_agent.config import ConfigError
    with pytest.raises(ConfigError, match="a11y.engine"):
        _cfg(tmp_path, "a11y: {enabled: true, engine: pa11y}\n")


def test_unknown_min_impact_rejected(tmp_path):
    from webtest_agent.config import ConfigError
    with pytest.raises(ConfigError, match="min_impact"):
        _cfg(tmp_path, "a11y: {enabled: true, min_impact: blocker}\n")


def test_rules_exclude_and_tags_load(tmp_path):
    cfg = _cfg(tmp_path,
               "a11y:\n  enabled: true\n  rules_exclude: [color-contrast]\n"
               "  tags: [wcag2a]\n").a11y
    assert cfg.rules_exclude == ["color-contrast"] and cfg.tags == ["wcag2a"]


def test_run_options_disable_excluded_rules():
    from webtest_agent.a11y import _run_options
    opts = _run_options(None, ["color-contrast"])
    assert opts["rules"]["color-contrast"]["enabled"] is False
    assert "wcag2aa" in opts["runOnly"]["values"]


def test_default_tags_exclude_best_practice():
    """best-practice는 법적 기준이 아니라 권고 — 기본에 넣으면 노이즈가 된다."""
    from webtest_agent.a11y import _run_options
    assert "best-practice" not in _run_options(None, None)["runOnly"]["values"]


# ── 비-HTML 문서는 점검 대상이 아니다 ───────────────────────────

class _TypedPage(_Page):
    """document.contentType을 흉내내는 가짜 페이지."""

    def __init__(self, content_type, **kw):
        super().__init__(**kw)
        self._content_type = content_type

    def evaluate(self, script, arg=None):
        if "contentType" in script:
            return self._content_type
        if self._eval_error:
            raise RuntimeError(self._eval_error)
        return self._result


@pytest.mark.parametrize("ctype", ["text/csv", "application/json", "text/plain"])
def test_non_html_document_is_skipped_not_violated(ctype):
    """엔진마다 다운로드 처리가 달라(WebKit은 /export.csv를 화면에 띄운다)
    CSV가 '<title> 없음'으로 잡히는 거짓 위반이 생겼다 — 실측으로 발견."""
    res = run_axe(_TypedPage(ctype, result={"violations": [], "rulesRun": 5}))
    assert res.skipped, "비-HTML 문서인데 점검을 강행했다"
    assert res.issues == []
    assert res.error == "", "점검 대상 아님은 '실패'가 아니다"


@pytest.mark.parametrize("ctype", ["text/html", "application/xhtml+xml", ""])
def test_html_document_is_checked(ctype):
    res = run_axe(_TypedPage(ctype, result={"violations": [], "rulesRun": 62}))
    assert not res.skipped
    assert res.checked is True
