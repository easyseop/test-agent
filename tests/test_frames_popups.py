"""단위 C·D — iframe·새 창과 마우스·파일 동작.

결제창·주소검색·소셜 로그인은 iframe이나 별도 창으로 뜬다. 그 안에 손이 닿아야
하지만, 닿는다고 해서 안전 경계가 느슨해지면 안 된다. 프레임 안의 '삭제'도
바깥의 '삭제'와 똑같이 막혀야 한다.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from webtest_agent.config import ConfigError, Step
from webtest_agent.runner import _GUARDED_ACTIONS, Runner, describe_step
from webtest_agent.safety import find_destructive


def _step(**kwargs):
    return Step.from_dict(kwargs, "t")


def _runner(config_path: str = "") -> Runner:
    r = Runner.__new__(Runner)
    r.deadline_monotonic = None
    if config_path:
        from types import SimpleNamespace
        r.cfg = SimpleNamespace(config_path=config_path)
    return r


# ── 설정 검증 ────────────────────────────────────────────────────

def test_frame_is_accepted_on_element_actions():
    s = _step(action="click", selector="#pay", frame="iframe#pg")
    assert s.frame == "iframe#pg"


def test_frame_rejected_where_meaningless():
    for action, extra in [("goto", {"value": "/x"}),
                          ("wait_popup", {}),
                          ("close_popup", {}),
                          ("assert_url", {"value": "/x"})]:
        with pytest.raises(ConfigError, match="frame을 쓸 수 없습니다"):
            _step(action=action, frame="iframe#pg", **extra)


def test_empty_frame_rejected():
    with pytest.raises(ConfigError, match="빈 값"):
        _step(action="click", selector="#a", frame="   ")


def test_popup_actions_need_nothing_else():
    assert _step(action="wait_popup").action == "wait_popup"
    assert _step(action="close_popup").action == "close_popup"


def test_new_actions_require_their_arguments():
    with pytest.raises(ConfigError, match="selector"):
        _step(action="hover")
    with pytest.raises(ConfigError, match="value"):
        _step(action="upload", selector="#f")
    with pytest.raises(ConfigError, match="value"):
        _step(action="drag", selector="#a")


def test_describe_step_mentions_the_frame():
    text = describe_step(_step(action="click", selector="#pay", frame="iframe#pg"))
    assert "iframe#pg" in text and "클릭" in text


def test_describe_step_covers_every_action():
    """설명이 없는 동작이 생기면 리포트를 만들다 KeyError로 죽는다."""
    from webtest_agent.config import STEP_ACTIONS
    samples = {
        "goto": {"value": "/x"}, "wait_ms": {"value": "10"},
        "assert_url": {"value": "/x"}, "wait_popup": {}, "close_popup": {},
        "upload": {"selector": "#f", "value": "a.txt"},
        "drag": {"selector": "#a", "value": "#b"},
        "extract": {"selector": "#a", "store_as": "v"},
    }
    for action in sorted(STEP_ACTIONS):
        kwargs = samples.get(action)
        if kwargs is None:
            kwargs = {"selector": "#a"}
            if action in ("fill", "select", "press", "assert_text", "assert_not_text"):
                kwargs["value"] = "x"
        assert describe_step(_step(action=action, **kwargs))


# ── 안전 경계는 프레임·팝업 안에서도 유효 ────────────────────────

def test_guarded_actions_cover_click_and_drag():
    assert _GUARDED_ACTIONS == {"click", "drag"}


def test_destructive_detected_in_frame_selector():
    """프레임 이름에 파괴적 어휘가 있어도 걸러야 한다."""
    assert find_destructive(None, None, "iframe#delete-panel")


def test_destructive_detected_in_drag_target():
    """끌어다 놓는 목적지가 휴지통이면 막아야 한다."""
    assert find_destructive("#row", "#trash-delete", None)


def test_harmless_actions_are_not_guarded():
    """hover·scroll_to는 상태를 바꾸지 않으므로 승인 대상이 아니다."""
    assert "hover" not in _GUARDED_ACTIONS
    assert "scroll_to" not in _GUARDED_ACTIONS


# ── 업로드 경로 제한 ─────────────────────────────────────────────

def test_upload_rejects_absolute_path(tmp_path):
    r = _runner(str(tmp_path / "c.yaml"))
    with pytest.raises(AssertionError, match="상대경로"):
        r._upload_path("/etc/passwd")


def test_upload_rejects_escaping_the_config_folder(tmp_path):
    r = _runner(str(tmp_path / "c.yaml"))
    with pytest.raises(AssertionError, match="벗어납니다"):
        r._upload_path("../../../etc/passwd")


def test_upload_reports_missing_file(tmp_path):
    r = _runner(str(tmp_path / "c.yaml"))
    with pytest.raises(AssertionError, match="파일이 없습니다"):
        r._upload_path("nope.txt")


def test_upload_accepts_file_next_to_the_config(tmp_path):
    (tmp_path / "receipt.txt").write_text("x", encoding="utf-8")
    r = _runner(str(tmp_path / "c.yaml"))
    assert r._upload_path("receipt.txt") == str(tmp_path / "receipt.txt")


def test_upload_accepts_subfolder(tmp_path):
    sub = tmp_path / "fixtures"
    sub.mkdir()
    (sub / "a.png").write_bytes(b"x")
    r = _runner(str(tmp_path / "c.yaml"))
    assert r._upload_path("fixtures/a.png") == str(sub / "a.png")


# ── 페이지 스택 ──────────────────────────────────────────────────

class _FakePage:
    def __init__(self, name):
        self.name = name
        self.closed = False

    def close(self):
        self.closed = True


class _FakeMonitor:
    def __init__(self, popups=()):
        self.hold_popups = False
        self.captured_popups = list(popups)

    def take_popup(self):
        return self.captured_popups.pop(0) if self.captured_popups else None


def test_active_page_is_the_base_page_without_popups():
    r = _runner()
    base = _FakePage("base")
    assert r._active_page(base) is base


def test_close_popup_without_wait_popup_fails():
    r = _runner()
    with pytest.raises(AssertionError, match="닫을 새 창이 없습니다"):
        r._exec_close_popup()


def test_scenario_cleanup_closes_leftover_popups():
    """시나리오가 창을 열어둔 채 끝나도 다음 시나리오로 새지 않아야 한다."""
    r = _runner()
    r._ensure_pages()
    opened = _FakePage("popup")
    r._page_stack.append(opened)
    stray = _FakePage("stray")
    monitor = _FakeMonitor([stray])
    monitor.hold_popups = True

    r._close_popups(monitor)

    assert opened.closed and stray.closed
    assert r._page_stack == []
    assert monitor.captured_popups == []
    assert monitor.hold_popups is False


def test_cleanup_survives_a_monitor_without_popup_fields():
    """정리 경로에서 예외가 나면 원래 실패 원인을 덮어쓴다."""
    from types import SimpleNamespace
    r = _runner()
    r._close_popups(SimpleNamespace())      # 예외가 없으면 통과


def test_cleanup_survives_a_popup_that_refuses_to_close():
    class _Stubborn:
        def close(self):
            raise RuntimeError("이미 닫힘")

    r = _runner()
    r._ensure_pages()
    r._page_stack.append(_Stubborn())
    r._close_popups(None)
    assert r._page_stack == []
