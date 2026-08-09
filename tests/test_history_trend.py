"""단위 G — 최근 실행들의 추이.

직전 1회 비교로는 "오늘 처음 깨졌다"와 "지난주부터 계속 깨져 있다"를 구분할 수
없다. 시나리오 × 실행 표를 만들어 언제부터 깨졌는지, 간헐적으로 흔들리는지를
보이게 한다.
"""
from __future__ import annotations

import json
from pathlib import Path

from webtest_agent.history import build_trend, collect_runs
from webtest_agent.models import FAIL, PASS, WARN

BASE = "http://app"
CONFIG = "configs/x.yaml"


def _make_run(root: Path, name: str, statuses: dict[str, str],
              status: str = "failed", base_url: str = BASE, config: str = CONFIG) -> Path:
    run = root / name
    run.mkdir(parents=True)
    (run / "report.json").write_text(json.dumps({
        "meta": {"base_url": base_url, "config_path": config,
                 "started_at": f"2026-08-08 {name}", "status": status},
        "scenarios": [{"name": n, "status": s} for n, s in statuses.items()],
    }), encoding="utf-8")
    return run


def test_trend_shows_when_a_scenario_broke(tmp_path):
    _make_run(tmp_path, "01", {"로그인": PASS, "주문조회": PASS}, status="passed")
    _make_run(tmp_path, "02", {"로그인": PASS, "주문조회": FAIL})
    _make_run(tmp_path, "03", {"로그인": PASS, "주문조회": FAIL})

    trend = build_trend(tmp_path, identity=(BASE, CONFIG))
    rows = {r["name"]: r for r in trend["scenarios"]}

    assert rows["주문조회"]["history"] == [PASS, FAIL, FAIL]
    assert rows["주문조회"]["failures"] == 2
    assert rows["주문조회"]["flips"] == 1        # 한 번 깨진 뒤 계속 깨져 있다
    assert rows["로그인"]["failures"] == 0


def test_flapping_scenario_is_distinguishable(tmp_path):
    """간헐적으로 흔들리는 검사는 '계속 깨진 것'과 다르게 보여야 한다."""
    for i, status in enumerate([PASS, FAIL, PASS, FAIL, PASS], start=1):
        _make_run(tmp_path, f"{i:02d}", {"흔들림": status})
    row = build_trend(tmp_path, identity=(BASE, CONFIG))["scenarios"][0]
    assert row["failures"] == 2
    assert row["flips"] == 4                  # 매번 뒤집힌다


def test_infra_error_runs_are_excluded(tmp_path):
    """실행 불가는 판정이 아니다.

    대상이 꺼져 있어 아무것도 못 한 실행을 '실패'로 세면 제품 품질이 나빠
    보이고, '통과'로 세면 그보다 나쁘다. 아예 통계에서 뺀다.
    """
    _make_run(tmp_path, "01", {"로그인": PASS}, status="passed")
    _make_run(tmp_path, "02", {}, status="infra_error")
    _make_run(tmp_path, "03", {"로그인": PASS}, status="passed")

    trend = build_trend(tmp_path, identity=(BASE, CONFIG))
    assert [r["run"] for r in trend["runs"]] == ["01", "03"]
    assert trend["skipped_infra_runs"] == ["02"]
    assert trend["scenarios"][0]["history"] == [PASS, PASS]


def test_runs_from_other_targets_are_not_mixed(tmp_path):
    """대상·설정이 다른 실행이 섞이면 '언제부터 깨졌나'가 무의미해진다."""
    _make_run(tmp_path, "01", {"로그인": PASS}, status="passed")
    _make_run(tmp_path, "02", {"로그인": FAIL}, base_url="http://other")
    _make_run(tmp_path, "03", {"로그인": FAIL}, config="configs/other.yaml")

    trend = build_trend(tmp_path, identity=(BASE, CONFIG))
    assert [r["run"] for r in trend["runs"]] == ["01"]
    assert trend["scenarios"][0]["failures"] == 0


def test_scenario_absent_from_a_run_is_marked_not_failed(tmp_path):
    """그 실행에 없던 시나리오를 실패로 세면 안 된다."""
    _make_run(tmp_path, "01", {"기존": PASS}, status="passed")
    _make_run(tmp_path, "02", {"기존": PASS, "신규": PASS}, status="passed")

    rows = {r["name"]: r for r in build_trend(tmp_path, identity=(BASE, CONFIG))["scenarios"]}
    assert rows["신규"]["history"] == ["", PASS]
    assert rows["신규"]["runs"] == 1          # 실제로 판정된 실행만 센다
    assert rows["신규"]["failures"] == 0


def test_warning_is_kept_separate_from_failure(tmp_path):
    _make_run(tmp_path, "01", {"시각회귀": WARN})
    row = build_trend(tmp_path, identity=(BASE, CONFIG))["scenarios"][0]
    assert row["history"] == [WARN]
    assert row["failures"] == 0
    assert row["current"] == WARN


def test_limit_keeps_the_most_recent_runs(tmp_path):
    for i in range(1, 6):
        _make_run(tmp_path, f"{i:02d}", {"x": PASS}, status="passed")
    assert [d.name for d in collect_runs(tmp_path, (BASE, CONFIG), limit=2)] == ["04", "05"]


def test_empty_root_is_not_an_error(tmp_path):
    trend = build_trend(tmp_path / "없음", identity=(BASE, CONFIG))
    assert trend == {"runs": [], "skipped_infra_runs": [], "scenarios": []}


def test_broken_report_is_skipped(tmp_path):
    _make_run(tmp_path, "01", {"로그인": PASS}, status="passed")
    bad = tmp_path / "02"
    bad.mkdir()
    (bad / "report.json").write_text("{망가진 JSON", encoding="utf-8")

    trend = build_trend(tmp_path, identity=(BASE, CONFIG))
    assert [r["run"] for r in trend["runs"]] == ["01"]
