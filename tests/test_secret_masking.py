"""비밀값이 증거 산출물(리포트·절차서)에 남지 않는지 검증.

${환경변수} 치환의 목적은 설정 파일에 평문을 두지 않는 것이다. 치환된 값이
report.json·report.html·walkthrough.md에 다시 나타나면 그 목적이 무의미해진다.
"""
from __future__ import annotations

import json

import pytest

from webtest_agent.config import MASK, Step
from webtest_agent.models import RunMeta, ScenarioResult, StepResult
from webtest_agent.report import write_reports
from webtest_agent.runner import _scrub, describe_step

SECRET = "S3cr3t-Real-Password!"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SITE_PASSWORD", SECRET)


def _step(**over) -> Step:
    payload = {"action": "fill", "selector": "#pw", "value": "${SITE_PASSWORD}"}
    payload.update(over)
    return Step.from_dict(payload, "spec_checks[0].steps[0]")


def test_env_expansion_marks_step_secret():
    step = _step()
    assert step.secret is True
    assert step.value == SECRET          # 실행에는 실제 값을 쓴다
    assert step.log_value == MASK        # 기록에는 마스킹된 값을 쓴다


def test_password_selector_masked_without_env_expansion():
    """치환을 쓰지 않고 평문을 적어도 비밀번호 입력이면 가린다."""
    step = _step(selector="#password", value="plain-text-pw")
    assert step.secret is True
    assert step.log_value == MASK


@pytest.mark.parametrize("selector", ["#password", "#passwd", "#user_token",
                                      "input[name=apiKey]", "#api-key", "#otp",
                                      "#credential", "[data-secret]"])
def test_secret_selector_variants(selector):
    assert _step(selector=selector, value="v").secret is True


@pytest.mark.parametrize("selector", ["#username", "#status", "#q",
                                      "#compass", "#spinner", "#tokenizer"])
def test_non_secret_selector_keeps_value(selector):
    """다른 단어에 우연히 포함된 경우까지 가리면 리포트가 못 쓰게 된다."""
    step = _step(selector=selector, value="visible")
    assert step.secret is False
    assert step.log_value == "visible"


def test_describe_step_does_not_leak():
    assert SECRET not in describe_step(_step())
    assert MASK in describe_step(_step())


def test_scrub_removes_secret_from_error_text():
    step = _step()
    assert SECRET not in _scrub(f"timeout while filling {SECRET}", step)
    # 비밀이 아닌 스텝의 메시지는 그대로 둔다
    plain = _step(selector="#username", value="alice")
    assert _scrub("timeout while filling alice", plain) == "timeout while filling alice"


def test_reports_never_contain_secret(tmp_path):
    """4개 산출물 전부에 원래 값이 없어야 한다."""
    step = _step()
    sr = StepResult(index=1, action=step.action, selector=step.selector,
                    value=step.log_value, description=describe_step(step),
                    status="fail", error=_scrub(f"failed with {SECRET}", step))
    res = ScenarioResult(name="로그인-명세", kind="spec_check", page="/login",
                         status="fail", steps=[sr],
                         reasons=[f"스텝 1 실패 — {describe_step(step)}: {sr.error}"])
    meta = RunMeta(title="t", base_url="http://127.0.0.1:1", app_version="",
                   config_path="c.yaml", started_at="2026-07-27 00:00:00")
    write_reports(tmp_path, meta, [res], [], [])

    for name in ("report.json", "report.md", "walkthrough.md", "report.html"):
        text = (tmp_path / name).read_text(encoding="utf-8")
        assert SECRET not in text, f"{name}에 비밀값이 남았습니다"

    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload["scenarios"][0]["steps"][0]["value"] == MASK
