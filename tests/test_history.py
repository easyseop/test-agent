"""전회차 diff 로직 유닛 테스트."""
import json

from webtest_agent.history import compute_diff, diff_for, find_previous_run


def test_compute_diff_changes():
    prev = {"A": "pass", "B": "fail", "C": "fail", "D": "pass"}
    cur = {"A": "fail", "B": "pass", "C": "fail", "E": "fail"}
    d = compute_diff("run-1", prev, cur)
    assert d["prev_run"] == "run-1"
    assert d["new_failures"] == ["A", "E"]   # 신규 시나리오 E의 즉시 실패 포함
    assert d["fixed"] == ["B"]
    assert d["still_failing"] == ["C"]
    assert d["added"] == ["E"]
    assert d["removed"] == ["D"]


def test_compute_diff_no_change():
    cur = {"A": "pass", "B": "warn"}
    d = compute_diff("run-1", dict(cur), cur)
    assert not any(d[k] for k in ("new_failures", "fixed", "still_failing", "added", "removed"))


def _make_run(root, name, statuses):
    d = root / name
    d.mkdir(parents=True)
    payload = {"scenarios": [{"name": n, "status": s} for n, s in statuses.items()]}
    (d / "report.json").write_text(json.dumps(payload), encoding="utf-8")
    return d


def test_find_previous_and_diff_for(tmp_path):
    old = _make_run(tmp_path, "20260101-000000", {"A": "pass", "B": "fail"})
    current = tmp_path / "20260102-000000"
    current.mkdir()

    assert find_previous_run(tmp_path, current) == old

    d = diff_for(tmp_path, current, {"A": "fail", "B": "pass"})
    assert d["prev_run"] == "20260101-000000"
    assert d["new_failures"] == ["A"] and d["fixed"] == ["B"]


def test_diff_for_without_previous(tmp_path):
    current = tmp_path / "20260102-000000"
    current.mkdir()
    assert diff_for(tmp_path, current, {"A": "pass"}) is None
