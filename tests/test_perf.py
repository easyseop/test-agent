"""성능 예산 검사 회귀 테스트 — 판정은 결정적(타이밍 주입 없이 측정값 고정)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from webtest_agent.config import ConfigError, load_config
from webtest_agent.models import FAIL, PASS, PerfResult, ScenarioResult
from webtest_agent.runner import JS_PERF, Runner


def _runner():
    r = Runner.__new__(Runner)
    r.session = SimpleNamespace()
    r.cfg = SimpleNamespace()
    r.run_dir = Path("/tmp")
    r.deadline_monotonic = None
    return r


def _fake_page(value, note=""):
    class _P:
        def evaluate(self, script, metric):
            return {"value": value, "note": note}
    return _P()


def _scenario(metric="load", budget=3000):
    return SimpleNamespace(perf_spec=SimpleNamespace(metric=metric, budget_ms=budget))


def test_within_budget_matches():
    res = _runner()._perf_check(_fake_page(1200.4), _scenario(budget=3000))
    assert res.matched is True
    assert res.measured_ms == 1200.4
    assert res.budget_ms == 3000


def test_over_budget_fails():
    res = _runner()._perf_check(_fake_page(4200.0), _scenario(budget=3000))
    assert res.matched is False
    assert res.measured_ms == 4200.0


def test_boundary_equal_is_within():
    res = _runner()._perf_check(_fake_page(3000.0), _scenario(budget=3000))
    assert res.matched is True


def test_unmeasurable_metric_is_note():
    res = _runner()._perf_check(_fake_page(-1, "FCP 미측정"), _scenario(metric="fcp"))
    assert res.matched is False
    assert "FCP" in res.note


def test_verdict_over_budget_is_fail():
    r = _runner()
    res = ScenarioResult(name="p", kind="perf_check", page="/")
    res.perf = PerfResult(metric="load", measured_ms=4200, budget_ms=3000, matched=False)
    scenario = SimpleNamespace(kind="perf_check", visual_spec=None)
    r._verdict(res, scenario, hard_fail=False)
    assert res.status == FAIL
    assert any("성능 예산 초과" in reason for reason in res.reasons)


def test_verdict_within_budget_is_pass():
    r = _runner()
    res = ScenarioResult(name="p", kind="perf_check", page="/")
    res.perf = PerfResult(metric="load", measured_ms=800, budget_ms=3000, matched=True)
    scenario = SimpleNamespace(kind="perf_check", visual_spec=None)
    r._verdict(res, scenario, hard_fail=False)
    assert res.status == PASS


def test_js_perf_covers_metrics():
    for m in ("load", "dcl", "response", "fcp"):
        assert m in JS_PERF or m == "fcp"   # fcp는 paint 분기로 처리


# ── 설정 검증 ────────────────────────────────────────────────────

def _cfg(tmp_path, body):
    p = tmp_path / "c.yaml"
    p.write_text("target: {base_url: 'http://127.0.0.1:5057'}\n"
                 "crawl: {enabled: false}\nbutton_sweep: {enabled: false}\n"
                 "report: {video: false, trace: false}\n" + body, encoding="utf-8")
    return p


def test_config_defaults(tmp_path):
    cfg = load_config(_cfg(tmp_path, "perf_checks:\n  - {name: 홈로드, budget_ms: 2500}\n"))
    assert cfg.perf_checks[0].metric == "load"
    assert cfg.perf_checks[0].budget_ms == 2500


@pytest.mark.parametrize("body", [
    "perf_checks:\n  - {name: x}\n",                          # budget_ms 없음
    "perf_checks:\n  - {name: x, budget_ms: 0}\n",            # 0
    "perf_checks:\n  - {name: x, budget_ms: 1000, metric: cpu}\n",  # 잘못된 metric
    "perf_checks:\n  - {budget_ms: 1000}\n",                  # name 없음
])
def test_config_rejects_invalid(tmp_path, body):
    with pytest.raises(ConfigError):
        load_config(_cfg(tmp_path, body))
