"""부정 단언(assert_not_visible / assert_not_text) 회귀 테스트.

부재 검증: 로그아웃 후 메뉴 사라짐, 오류 안 뜸 등 필수 패턴.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from webtest_agent.config import ConfigError, Step
from webtest_agent.runner import Runner, describe_step


def _runner():
    r = Runner.__new__(Runner)
    r.deadline_monotonic = None
    return r


# ── config 검증 ──────────────────────────────────────────────────

def test_assert_not_visible_needs_selector_only():
    s = Step.from_dict({"action": "assert_not_visible", "selector": "#m"}, "t")
    assert s.action == "assert_not_visible"


def test_assert_not_text_needs_selector_and_value():
    s = Step.from_dict({"action": "assert_not_text", "selector": "#m", "value": "오류"}, "t")
    assert s.value == "오류"


@pytest.mark.parametrize("bad", [
    {"action": "assert_not_visible"},                       # selector 없음
    {"action": "assert_not_text", "selector": "#x"},        # value 없음
])
def test_invalid_rejected(bad):
    with pytest.raises(ConfigError):
        Step.from_dict(bad, "t")


def test_describe_step():
    assert "보이지 않는지" in describe_step(
        Step.from_dict({"action": "assert_not_visible", "selector": "#m"}, "t"))
    assert "없는지" in describe_step(
        Step.from_dict({"action": "assert_not_text", "selector": "#m", "value": "x"}, "t"))


# ── _exec_step 판정 (가짜 page) ──────────────────────────────────

class _Page:
    def __init__(self, hidden_ok=True, text=""):
        self._hidden_ok = hidden_ok
        self._text = text

    def wait_for_selector(self, selector, state=None, timeout=None):
        if state == "hidden" and not self._hidden_ok:
            raise TimeoutError("still visible")
        return None

    def locator(self, selector):
        page = self

        class _Loc:
            @property
            def first(self):
                return self

            def wait_for(self, state=None, timeout=None):
                # 프레임 안에서도 쓸 수 있도록 Runner가 locator.wait_for를 쓴다.
                if state == "hidden" and not page._hidden_ok:
                    raise TimeoutError("still visible")
                return None

            def inner_text(self, timeout=None):
                return page._text
        return _Loc()


def _step(action, selector="#x", value=None):
    d = {"action": action, "selector": selector}
    if value is not None:
        d["value"] = value
    return Step.from_dict(d, "t")


def test_not_visible_passes_when_hidden():
    _runner()._exec_step(_Page(hidden_ok=True), _step("assert_not_visible"))  # 예외 없으면 통과


def test_not_visible_fails_when_shown():
    with pytest.raises(AssertionError):
        _runner()._exec_step(_Page(hidden_ok=False), _step("assert_not_visible"))


def test_not_text_passes_when_absent():
    _runner()._exec_step(_Page(text="다른 내용"), _step("assert_not_text", value="오류"))


def test_not_text_fails_when_present():
    with pytest.raises(AssertionError):
        _runner()._exec_step(_Page(text="치명 오류 발생"), _step("assert_not_text", value="오류"))


def test_not_text_passes_when_element_absent():
    """요소 자체가 없으면 텍스트도 없는 것 → 통과."""
    class _NoEl:
        def locator(self, selector):
            class _Loc:
                @property
                def first(self):
                    return self

                def inner_text(self, timeout=None):
                    raise RuntimeError("no element")
            return _Loc()
    _runner()._exec_step(_NoEl(), _step("assert_not_text", value="오류"))
