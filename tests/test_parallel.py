"""병렬 실행의 결정성·안전 불변식 회귀 테스트.

병렬화해도 (1) 판정은 그대로 (2) 리포트 순서는 입력 순서 (3) 쓰기 검증은
직렬·최후 여야 한다. 이 파일은 브라우저 없이 분리·정렬·flaky 로직만 검증한다.
"""
from __future__ import annotations

from types import SimpleNamespace

from webtest_agent.cli import (order_results, split_scenarios,
                               _run_scenario_with_flaky)
from webtest_agent.models import FAIL, PASS, WARN


def _sc(kind):
    return SimpleNamespace(kind=kind, name=kind)


def test_write_checks_excluded_from_parallel():
    scenarios = [_sc("data_check"), _sc("write_check"), _sc("spec_check"),
                 _sc("write_check"), _sc("sweep_button")]
    parallel, serial = split_scenarios(scenarios)
    assert [i for i, _ in parallel] == [1, 3, 5]
    assert [i for i, _ in serial] == [2, 4]
    assert all(sc.kind != "write_check" for _, sc in parallel)
    assert all(sc.kind == "write_check" for _, sc in serial)


def test_indices_are_one_based_and_cover_all():
    scenarios = [_sc("data_check"), _sc("visual_check"), _sc("responsive_check")]
    parallel, serial = split_scenarios(scenarios)
    covered = sorted(i for i, _ in parallel) + sorted(i for i, _ in serial)
    assert sorted(covered) == [1, 2, 3]


def test_results_ordered_by_input_not_completion():
    # 완료 순서가 뒤죽박죽이어도 인덱스 순서로 정렬돼야 한다
    collected = {3: "c", 1: "a", 2: "b", 5: "e", 4: "d"}
    assert order_results(collected) == ["a", "b", "c", "d", "e"]


class _FakeRunner:
    """run()이 미리 정해진 status 시퀀스를 돌려주는 가짜 러너 (flaky 검증용)."""

    def __init__(self, statuses):
        self._statuses = list(statuses)
        self.calls = 0

    def run(self, sc, index, suffix=""):
        status = self._statuses[self.calls]
        self.calls += 1
        return SimpleNamespace(status=status, flaky=False, reasons=[],
                               video="", trace="")


def _cfg(flaky_recheck):
    return SimpleNamespace(target=SimpleNamespace(flaky_recheck=flaky_recheck))


def test_flaky_recheck_downgrades_but_marks():
    runner = _FakeRunner([FAIL, PASS])   # 최초 실패, 재실행 통과
    res = _run_scenario_with_flaky(runner, _sc("spec_check"), 1, _cfg(True))
    assert runner.calls == 2
    assert res.status == WARN
    assert res.flaky is True
    assert any("간헐" in r for r in res.reasons)


def test_flaky_recheck_disabled_keeps_fail():
    runner = _FakeRunner([FAIL])
    res = _run_scenario_with_flaky(runner, _sc("spec_check"), 1, _cfg(False))
    assert runner.calls == 1
    assert res.status == FAIL
    assert res.flaky is False


def test_pass_scenario_not_rechecked():
    runner = _FakeRunner([PASS])
    res = _run_scenario_with_flaky(runner, _sc("data_check"), 1, _cfg(True))
    assert runner.calls == 1
    assert res.status == PASS


# ── 스윕 커버리지 집계 (조용한 상한 방지) ─────────────────────────

def _discovery(buttons=(), links=()):
    from webtest_agent.discovery import Discovery, PageInfo
    p = PageInfo(url="http://a/", path="/", title="t", screenshot="",
                 elements={"buttons": list(buttons), "links": list(links)})
    d = Discovery(); d.pages = [p]; return d


def _sweep_cfg(max_per_page=30):
    from webtest_agent.config import (AgentConfig, TargetConfig, CrawlConfig,
                                      SweepConfig, ReportConfig)
    from webtest_agent.safety import merge_avoid_patterns
    return AgentConfig(
        target=TargetConfig(base_url="http://a"), crawl=CrawlConfig(),
        sweep=SweepConfig(max_per_page=max_per_page, avoid_patterns=merge_avoid_patterns([])),
        data_checks=[], spec_checks=[], write_checks=[], visual_checks=[],
        responsive_checks=[], perf_checks=[], report=ReportConfig())


def test_coverage_counts_tested_and_capped():
    from webtest_agent.scenarios import build_sweep
    buttons = [{"selector": f"#b{i}", "text": f"버튼{i}"} for i in range(5)]
    scen, blocked, cov = build_sweep(_discovery(buttons=buttons), _sweep_cfg(max_per_page=2))
    assert cov["found"] == 5
    assert cov["tested"] == 2
    assert cov["capped"] == 3
    assert len(scen) == 2


def test_coverage_counts_duplicate_and_disabled():
    from webtest_agent.scenarios import build_sweep
    buttons = [{"selector": "#b", "text": "x"}, {"selector": "#b", "text": "x"},   # 중복
               {"selector": "#c", "text": "y", "disabled": True}]                   # 비활성
    scen, blocked, cov = build_sweep(_discovery(buttons=buttons), _sweep_cfg())
    assert cov["skipped_duplicate"] == 1
    assert cov["skipped_disabled"] == 1
    assert cov["tested"] == 1


def test_coverage_counts_blocked():
    from webtest_agent.scenarios import build_sweep
    buttons = [{"selector": "#del", "text": "삭제"}, {"selector": "#ok", "text": "조회"}]
    scen, blocked, cov = build_sweep(_discovery(buttons=buttons), _sweep_cfg())
    assert cov["blocked"] == 1
    assert cov["tested"] == 1


def test_coverage_counts_nonhttp_links():
    from webtest_agent.scenarios import build_sweep
    links = [{"selector": "#m", "text": "메일", "href": "mailto:a@b.c"},
             {"selector": "#h", "text": "홈", "href": "/home"}]
    scen, blocked, cov = build_sweep(_discovery(links=links), _sweep_cfg())
    assert cov["skipped_nonhttp"] == 1
    assert cov["tested"] == 1
