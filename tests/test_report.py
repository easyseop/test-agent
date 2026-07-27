"""리포트 생성 유닛 테스트."""
import json

from webtest_agent.models import (DataCheckResult, HttpFailure, RunMeta,
                                  ScenarioResult, StepResult)
from webtest_agent.report import write_reports


def _sample_results():
    ok = ScenarioResult(
        name="전체목록", kind="data_check", page="/", status="pass",
        steps=[StepResult(index=0, action="goto", description="/ 페이지에 접속한다")],
        data_check=DataCheckResult(matched=True, ui_count=3, db_count=3,
                                   columns=["주문번호"], count_display=3, count_display_ok=True),
        duration_ms=1200,
    )
    bad = ScenarioResult(
        name="상태필터-shipped", kind="data_check", page="/", status="fail",
        reasons=["UI↔DB 불일치: 화면 5건 vs DB 3건 (화면에 누락 0건, 화면에 초과 2건)"],
        steps=[StepResult(index=0, action="goto", description="/ 페이지에 접속한다"),
               StepResult(index=1, action="click", selector="#apply", description="'#apply' 요소를 클릭한다")],
        data_check=DataCheckResult(matched=False, ui_count=5, db_count=3,
                                   columns=["주문번호", "상태"],
                                   unexpected_in_ui=[["1010", "delivered"]], unexpected_total=2),
        http_failures=[HttpFailure(url="http://x/api", status=500)],
        duration_ms=2100,
    )
    warn = ScenarioResult(
        name="버튼점검 요약", kind="sweep_button", page="/", status="warn",
        reasons=["클릭 후 관찰 가능한 변화가 없음 (죽은 버튼 후보)"], effect="무반응",
        duration_ms=900,
    )
    return [ok, bad, warn]


def test_write_reports(tmp_path):
    meta = RunMeta(title="테스트 리포트", base_url="http://127.0.0.1:5057", app_version="v1",
                   config_path="configs/demo.yaml", started_at="2026-07-23 10:00:00",
                   finished_at="2026-07-23 10:01:00", duration_ms=60000,
                   browser_version="139.0", playwright_version="1.50",
                   python_version="3.11", agent_version="0.1.0")
    diff = {"prev_run": "20260722-090000", "new_failures": ["상태필터-shipped"],
            "fixed": [], "still_failing": [], "added": [], "removed": []}
    summary = write_reports(tmp_path, meta, _sample_results(), [], [], diff=diff)

    assert summary == {"total": 3, "pass": 1, "warn": 1, "fail": 1, "flaky": 0}
    for name in ("report.json", "report.md", "report.html", "walkthrough.md"):
        assert (tmp_path / name).exists(), name

    html = (tmp_path / "report.html").read_text(encoding="utf-8")
    assert "상태필터-shipped" in html and "통과" in html and "실패" in html
    assert "전회차 대비" in html
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "UI↔DB 불일치" in md and "신규 실패" in md
    walkthrough = (tmp_path / "walkthrough.md").read_text(encoding="utf-8")
    assert "페이지에 접속한다" in walkthrough


def test_infrastructure_error_is_visible_in_all_reports(tmp_path):
    meta = RunMeta(
        title="실행 불가 리포트",
        base_url="http://127.0.0.1:59999",
        app_version="",
        config_path="configs/offline.yaml",
        started_at="2026-07-25 10:00:00",
        finished_at="2026-07-25 10:00:01",
        status="infra_error",
        error="대상 앱에 접속할 수 없습니다",
    )

    write_reports(tmp_path, meta, [], [], [])

    payload = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert payload["meta"]["status"] == "infra_error"
    assert "접속할 수 없습니다" in payload["meta"]["error"]
    assert "실행 불가" in (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "테스트를 실행하지 못했습니다" in (
        tmp_path / "report.html"
    ).read_text(encoding="utf-8")
