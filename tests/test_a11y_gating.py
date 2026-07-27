"""접근성 선택적 게이팅(info|warn|fail) 회귀 테스트."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from webtest_agent.cli import _a11y_scenario_result
from webtest_agent.config import ConfigError, load_config
from webtest_agent.models import FAIL, PASS, WARN


def _page(issues):
    return SimpleNamespace(a11y=issues)


def test_no_issues_passes():
    res = _a11y_scenario_result([_page([]), _page([])], "fail")
    assert res.status == PASS
    assert res.kind == "a11y_check"


def test_issues_fail_when_severity_fail():
    pages = [_page([{"type": "img-alt", "detail": "<img>"}]),
             _page([{"type": "no-main", "detail": ""}])]
    res = _a11y_scenario_result(pages, "fail")
    assert res.status == FAIL
    assert "접근성 위반 2건" in res.reasons[0]


def test_issues_warn_when_severity_warn():
    pages = [_page([{"type": "img-alt", "detail": "<img>"}])]
    res = _a11y_scenario_result(pages, "warn")
    assert res.status == WARN


def test_reason_aggregates_by_type():
    pages = [_page([{"type": "img-alt", "detail": "a"},
                    {"type": "img-alt", "detail": "b"},
                    {"type": "dup-id", "detail": "#x x2"}])]
    res = _a11y_scenario_result(pages, "warn")
    reason = res.reasons[0]
    assert "대체 텍스트(alt) 없는 이미지 2건" in reason
    assert "중복 id 1건" in reason


def _write_cfg(tmp_path, a11y_body):
    p = tmp_path / "c.yaml"
    p.write_text(
        "target: {base_url: 'http://127.0.0.1:5057'}\n"
        "crawl: {enabled: true}\n"
        "button_sweep: {enabled: false}\n"
        "report: {video: false, trace: false}\n" + a11y_body,
        encoding="utf-8")
    return p


def test_config_severity_default_info(tmp_path):
    cfg = load_config(_write_cfg(tmp_path, "a11y: {enabled: true}\n"))
    assert cfg.a11y.severity == "info"


@pytest.mark.parametrize("sev", ["info", "warn", "fail"])
def test_config_severity_valid(tmp_path, sev):
    cfg = load_config(_write_cfg(tmp_path, f"a11y: {{enabled: true, severity: {sev}}}\n"))
    assert cfg.a11y.severity == sev


def test_config_severity_invalid_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_write_cfg(tmp_path, "a11y: {enabled: true, severity: strict}\n"))
