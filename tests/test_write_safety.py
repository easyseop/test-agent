"""자동 탐색과 쓰기 검증의 안전 경계 회귀 테스트."""
from types import SimpleNamespace

from webtest_agent.config import Step
from webtest_agent.discovery import Discovery, PageInfo
from webtest_agent.models import FAIL
from webtest_agent.runner import Runner
from webtest_agent.scenarios import Scenario, build_sweep


def _cfg(avoid_patterns=None):
    return SimpleNamespace(
        sweep=SimpleNamespace(
            include_links=True,
            max_per_page=30,
            avoid_patterns=avoid_patterns or [],
        )
    )


def test_sweep_hard_blocks_write_actions_even_when_config_is_empty():
    discovery = Discovery(pages=[
        PageInfo(
            url="http://example.test/",
            path="/",
            title="테스트",
            screenshot="",
            elements={
                "buttons": [
                    {"selector": "#search", "text": "조회", "disabled": False},
                    {"selector": "#save", "text": "저장", "disabled": False},
                    {"selector": "#mail", "text": "이메일 발송", "disabled": False},
                ],
                "links": [],
            },
        )
    ])

    scenarios, blocked = build_sweep(discovery, _cfg())

    assert [scenario.element_text for scenario in scenarios] == ["조회"]
    assert {item.text for item in blocked} == {"저장", "이메일 발송"}
    assert all("자동 탐색" in item.reason for item in blocked)


def test_sweep_hard_block_also_checks_selector():
    discovery = Discovery(pages=[
        PageInfo(
            url="http://example.test/",
            path="/",
            title="테스트",
            screenshot="",
            elements={
                "buttons": [
                    {"selector": "#delete-account", "text": "확인", "disabled": False},
                ],
                "links": [],
            },
        )
    ])

    scenarios, blocked = build_sweep(discovery, _cfg())

    assert scenarios == []
    assert blocked[0].pattern == "delete"


def test_runner_refuses_unapproved_write_check_before_opening_browser(tmp_path):
    class NeverOpenSession:
        def new_context(self, *args, **kwargs):
            raise AssertionError("브라우저를 열면 안 됨")

    runner = Runner(NeverOpenSession(), SimpleNamespace(), tmp_path)
    scenario = Scenario(
        name="주문 등록",
        kind="write_check",
        page="/new",
        steps=[Step(action="click", selector="#save")],
    )

    result = runner.run(scenario, 1)

    assert result.status == FAIL
    assert "--allow-write-checks" in result.reasons[0]


def test_runner_runtime_guard_refuses_unsafe_sweep_if_planner_is_bypassed(tmp_path):
    class NeverOpenSession:
        def new_context(self, *args, **kwargs):
            raise AssertionError("브라우저를 열면 안 됨")

    runner = Runner(NeverOpenSession(), SimpleNamespace(), tmp_path)
    scenario = Scenario(
        name="강제 생성 클릭",
        kind="sweep_button",
        page="/",
        steps=[Step(action="click", selector="#create")],
        element_text="확인",
    )

    result = runner.run(scenario, 1)

    assert result.status == FAIL
    assert "안전 차단" in result.reasons[0]
