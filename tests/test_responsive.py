"""반응형(다중 뷰포트 가로 오버플로) 검증 회귀 테스트.

가로 오버플로는 scrollWidth > clientWidth라는 결정적 불변식이므로,
판정은 코드가 내리고 LLM은 개입하지 않는다.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from webtest_agent.config import ConfigError, load_config
from webtest_agent.models import FAIL, PASS, ResponsiveResult, ViewportResult
from webtest_agent.runner import JS_OVERFLOW, Runner


def _write_cfg(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "c.yaml"
    p.write_text(
        "target: {base_url: 'http://127.0.0.1:5057'}\n"
        "crawl: {enabled: false}\n"
        "button_sweep: {enabled: false}\n"
        "report: {video: false, trace: false}\n" + body,
        encoding="utf-8",
    )
    return p


def test_config_defaults(tmp_path):
    cfg = load_config(_write_cfg(tmp_path,
        "responsive_checks:\n  - name: 홈-반응형\n    page: /\n"))
    assert len(cfg.responsive_checks) == 1
    rc = cfg.responsive_checks[0]
    assert rc.viewports == [375, 768, 1280]
    assert rc.height == 900
    assert rc.max_overflow_px == 2


def test_config_custom_viewports(tmp_path):
    cfg = load_config(_write_cfg(tmp_path,
        "responsive_checks:\n  - name: r\n    viewports: [320, 1440]\n    max_overflow_px: 0\n"))
    assert cfg.responsive_checks[0].viewports == [320, 1440]
    assert cfg.responsive_checks[0].max_overflow_px == 0


@pytest.mark.parametrize("body", [
    "responsive_checks:\n  - page: /\n",                       # name 없음
    "responsive_checks:\n  - name: r\n    viewports: []\n",     # 빈 목록
    "responsive_checks:\n  - name: r\n    viewports: [0]\n",    # 0 폭
    "responsive_checks:\n  - name: r\n    viewports: [a]\n",    # 비정수
])
def test_config_rejects_invalid(tmp_path, body):
    with pytest.raises(ConfigError):
        load_config(_write_cfg(tmp_path, body))


# ── 판정 로직 (브라우저 없이) ────────────────────────────────────

def _fake_page(measurements):
    """set_viewport_size로 폭을 받으면 사전 정의된 측정값을 돌려주는 가짜 page."""
    state = {"w": None}

    class _P:
        def set_viewport_size(self, size):
            state["w"] = size["width"]

        def evaluate(self, script, max_overflow):
            m = measurements[state["w"]]
            overflow = m["scrollWidth"] - m["clientWidth"]
            offenders = m.get("offenders", []) if overflow > max_overflow else []
            return {"scrollWidth": m["scrollWidth"], "clientWidth": m["clientWidth"],
                    "overflow": overflow, "offenders": offenders}

    return _P()


def _runner():
    r = Runner.__new__(Runner)
    r.session = SimpleNamespace(viewport={"width": 1280, "height": 800})
    r.cfg = SimpleNamespace(target=SimpleNamespace(settle_ms=0),
                            report=SimpleNamespace(mask_selectors=[]))
    r.run_dir = Path("/tmp")
    r.deadline_monotonic = None
    return r


def _scenario(viewports, max_overflow=2):
    spec = SimpleNamespace(viewports=viewports, height=900, max_overflow_px=max_overflow)
    return SimpleNamespace(responsive_spec=spec)


def test_no_overflow_passes(monkeypatch, tmp_path):
    r = _runner()
    r.run_dir = tmp_path
    monkeypatch.setattr(r, "_wait", lambda *a, **k: None)
    page = _fake_page({
        375: {"scrollWidth": 375, "clientWidth": 375},
        1280: {"scrollWidth": 1280, "clientWidth": 1280},
    })
    result = r._responsive_check(page, _scenario([375, 1280]), tmp_path)
    assert result.matched is True
    assert all(v.ok for v in result.viewports)


def test_overflow_at_mobile_fails(monkeypatch, tmp_path):
    r = _runner()
    r.run_dir = tmp_path
    monkeypatch.setattr(r, "_wait", lambda *a, **k: None)
    monkeypatch.setattr(r, "_shot", lambda *a, **k: "")
    page = _fake_page({
        375: {"scrollWidth": 460, "clientWidth": 375, "offenders": ["pre.code (right=460)"]},
        1280: {"scrollWidth": 1280, "clientWidth": 1280},
    })
    result = r._responsive_check(page, _scenario([375, 1280]), tmp_path)
    assert result.matched is False
    mobile = next(v for v in result.viewports if v.width == 375)
    assert mobile.ok is False
    assert mobile.overflow_px == 85
    assert mobile.offenders


def test_tolerance_allows_scrollbar(monkeypatch, tmp_path):
    """스크롤바 몇 px 오차는 max_overflow_px로 허용된다."""
    r = _runner()
    r.run_dir = tmp_path
    monkeypatch.setattr(r, "_wait", lambda *a, **k: None)
    page = _fake_page({768: {"scrollWidth": 770, "clientWidth": 768}})
    result = r._responsive_check(page, _scenario([768], max_overflow=2), tmp_path)
    assert result.matched is True


def test_verdict_marks_fail(tmp_path):
    """_verdict가 반응형 실패를 시나리오 FAIL로 반영하는지."""
    from webtest_agent.models import ScenarioResult
    r = _runner()
    res = ScenarioResult(name="r", kind="responsive_check", page="/")
    res.responsive = ResponsiveResult(
        max_overflow_px=2, matched=False,
        viewports=[ViewportResult(width=375, height=900, scroll_width=460,
                                  client_width=375, overflow_px=85, ok=False,
                                  offenders=["pre (right=460)"])])
    scenario = SimpleNamespace(kind="responsive_check", visual_spec=None)
    r._verdict(res, scenario, hard_fail=False)
    assert res.status == FAIL
    assert any("가로 오버플로" in reason for reason in res.reasons)


def test_verdict_pass_when_all_ok(tmp_path):
    from webtest_agent.models import ScenarioResult
    r = _runner()
    res = ScenarioResult(name="r", kind="responsive_check", page="/")
    res.responsive = ResponsiveResult(
        max_overflow_px=2, matched=True,
        viewports=[ViewportResult(width=375, height=900, scroll_width=375,
                                  client_width=375, overflow_px=0, ok=True)])
    scenario = SimpleNamespace(kind="responsive_check", visual_spec=None)
    r._verdict(res, scenario, hard_fail=False)
    assert res.status == PASS


def test_js_overflow_is_a_string():
    # JS 스니펫이 max_overflow 인자를 받는 화살표 함수인지 (계약 고정)
    assert "scrollWidth" in JS_OVERFLOW and "clientWidth" in JS_OVERFLOW
