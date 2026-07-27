"""2026-07-27 코드 검토에서 실측으로 확인된 반례에 대한 회귀 테스트.

각 테스트는 '수정 전에는 통과하지 못하던 입력'을 고정한다.
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from webtest_agent.datacheck import compare, normalize_cell
from webtest_agent.history import find_previous_run
from webtest_agent.report import summarize
from webtest_agent.models import FAIL, PASS, WARN, ScenarioResult
from webtest_agent.safety import (DESTRUCTIVE_PATTERNS, HARD_BLOCK_PATTERNS,
                                  find_destructive, find_hard_block,
                                  validate_read_only_sql)


# ── NULL 정규화 ────────────────────────────────────────────────

def test_db_null_matches_empty_ui_cell():
    """DB NULL은 화면에서 빈 칸으로 그려지는 것이 정상 — 불일치로 보면 안 된다."""
    assert normalize_cell(None) == normalize_cell("") == ""


def test_none_string_is_not_confused_with_null():
    """실제 값이 'None' 문자열인 경우까지 지우면 안 된다."""
    assert normalize_cell("None") == "None"
    assert normalize_cell("None") != normalize_cell(None)


def test_nullable_column_compare_passes():
    result = compare(["주문번호", "비고"], [["1", ""]], ["주문번호", "비고"],
                     ["id", "note"], [(1, None)])
    assert result.matched is True, result.note


# ── UI 헤더 중복 ───────────────────────────────────────────────

def test_duplicate_ui_header_is_rejected_not_silently_first_column():
    """같은 헤더가 둘이면 어느 컬럼을 비교할지 확정할 수 없다."""
    result = compare(["금액", "금액"], [["100", "200"]], ["금액"], ["금액"], [(100,)])
    assert result.matched is False
    assert "같은 이름" in result.note


def test_distinct_headers_still_compare():
    result = compare(["단가", "합계"], [["100", "200"]], ["합계"], ["합계"], [(200,)])
    assert result.matched is True, result.note


# ── 조회 전용 SQL 가드 ──────────────────────────────────────────

@pytest.mark.parametrize("sql", [
    "SELECT * FROM orders INTO OUTFILE '/tmp/dump.csv'",   # MySQL 파일 쓰기
    "SELECT * INTO backup_orders FROM orders",             # PostgreSQL 테이블 생성
    "SELECT * FROM orders LIMIT 1 INTO DUMPFILE '/tmp/a'",
    "SELECT LOAD_FILE('/etc/passwd')",                     # 밑줄 함수명 우회
    "SELECT sys_exec('id')",
    "SELECT lo_export(oid, '/tmp/x') FROM t",
    "SELECT pg_read_file('/etc/passwd')",
    "SELECT pg_sleep(600)",
])
def test_read_only_sql_rejects_file_and_command_paths(sql):
    with pytest.raises(ValueError):
        validate_read_only_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT id, customer FROM orders WHERE status = 'shipped'",
    "SELECT 'delete' AS label FROM orders",                # 문자열 안의 단어는 무관
    "WITH cte AS (SELECT 1 AS x) SELECT x FROM cte",
    "select count(*) from orders",
])
def test_read_only_sql_allows_plain_queries(sql):
    assert validate_read_only_sql(sql) == sql


# ── 위험 클릭 차단 ─────────────────────────────────────────────

@pytest.mark.parametrize("text,selector,href", [
    ("제거", "#x", None),
    ("초기화", "#x", None),
    ("전송", "#x", None),
    ("보내기", "#x", None),
    ("해지", "#x", None),
    ("Purge account", "#x", None),
    ("Reset data", "#x", None),
    ("", "#btn-delete", None),
    ("자세히", "#a", "/orders/1/delete"),
])
def test_destructive_labels_are_blocked(text, selector, href):
    assert find_hard_block(text, selector, href) is not None


@pytest.mark.parametrize("label", ["삭제", "제거", "초기화", "결제", "발송", "초대", "배포"])
def test_destructive_subset_applies_to_explicit_steps(label):
    assert find_destructive(label) is not None


@pytest.mark.parametrize("label", ["저장", "save", "등록", "submit", "수정", "로그아웃"])
def test_normal_write_labels_are_sweep_only(label):
    """로그인 submit처럼 정상 흐름에 필요한 동작은 명시 스텝에서 막지 않는다."""
    assert find_destructive(label) is None
    assert find_hard_block(label) is not None


def test_destructive_is_subset_of_hard_block():
    assert set(DESTRUCTIVE_PATTERNS) <= set(HARD_BLOCK_PATTERNS)


# ── flaky 강등이 통과를 만들지 않는다 ────────────────────────────

def test_flaky_downgrade_is_counted_and_not_a_pass():
    flaky = ScenarioResult(name="간헐", kind="spec_check", page="/",
                           status=WARN, flaky=True)
    summary = summarize([ScenarioResult(name="정상", kind="spec_check", page="/",
                                        status=PASS), flaky])
    assert summary["fail"] == 0
    assert summary["flaky"] == 1
    # cli가 종료코드를 정하는 조건과 같은 식
    unresolved = any(r.status == FAIL or r.flaky for r in [flaky])
    assert unresolved is True


def test_clean_run_has_no_unresolved():
    results = [ScenarioResult(name="정상", kind="spec_check", page="/", status=PASS)]
    assert summarize(results)["flaky"] == 0
    assert any(r.status == FAIL or r.flaky for r in results) is False


# ── 실행 이력 비교는 같은 대상끼리만 ─────────────────────────────

def _write_run(root, name, base_url, config_path="configs/demo.yaml"):
    run = root / name
    run.mkdir(parents=True)
    (run / "report.json").write_text(
        json.dumps({"meta": {"base_url": base_url, "config_path": config_path},
                    "scenarios": []}), encoding="utf-8")
    return run


def test_previous_run_must_be_same_target(tmp_path):
    _write_run(tmp_path, "20260101-000000", "http://a.example")
    other = _write_run(tmp_path, "20260102-000000", "http://b.example")
    current = tmp_path / "20260103-000000"
    current.mkdir()

    assert find_previous_run(
        tmp_path, current, identity=("http://b.example", "configs/demo.yaml")) == other
    # 같은 대상의 실행이 없으면 비교하지 않는다 (다른 앱과 대조 금지)
    assert find_previous_run(
        tmp_path, current, identity=("http://c.example", "configs/demo.yaml")) is None


def test_previous_run_must_be_same_config(tmp_path):
    """같은 호스트라도 설정이 다르면 시나리오 묶음이 달라 비교가 무의미하다."""
    _write_run(tmp_path, "20260101-000000", "http://a.example", "configs/demo-auth.yaml")
    current = tmp_path / "20260102-000000"
    current.mkdir()
    assert find_previous_run(
        tmp_path, current, identity=("http://a.example", "configs/demo.yaml")) is None


def test_previous_run_without_filter_keeps_legacy_behaviour(tmp_path):
    _write_run(tmp_path, "20260101-000000", "http://a.example")
    latest = _write_run(tmp_path, "20260102-000000", "http://b.example")
    current = tmp_path / "20260103-000000"
    current.mkdir()
    assert find_previous_run(tmp_path, current) == latest


# ── 쓰기 변화량 정밀도 ──────────────────────────────────────────

def test_write_delta_survives_large_integers():
    """float였다면 2^53 초과 구간에서 변화량 1이 0으로 사라진다."""
    pre, post = Decimal("9007199254740992"), Decimal("9007199254740993")
    assert post - pre == 1
    assert float(post) - float(pre) != 1     # 수정의 근거 (대조군)
