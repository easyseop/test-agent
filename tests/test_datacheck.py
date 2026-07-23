"""정규화·비교·쿼리 로직 유닛 테스트 (브라우저 불필요)."""
import sqlite3

import pytest

from webtest_agent.datacheck import compare, normalize_cell, parse_count, run_query


def _make_db(tmp_path):
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (id INTEGER, name TEXT)")
    conn.execute("INSERT INTO t VALUES (1, '김민준')")
    conn.commit()
    conn.close()
    return db


def test_run_query_sqlite(tmp_path):
    db = _make_db(tmp_path)
    cols, rows = run_query(f"sqlite:///{db}", "SELECT id, name FROM t")
    assert cols == ["id", "name"] and rows == [(1, "김민준")]


def test_run_query_sqlalchemy_url(tmp_path):
    pytest.importorskip("sqlalchemy")
    db = _make_db(tmp_path)
    cols, rows = run_query(f"sqlite+pysqlite:///{db}", "SELECT id, name FROM t")
    assert cols == ["id", "name"] and rows == [(1, "김민준")]


def test_run_query_unsupported_url():
    with pytest.raises(ValueError, match="지원하지 않는"):
        run_query("not-a-url", "SELECT 1")


def test_normalize_number_formats():
    assert normalize_cell("12,300원") == "12300"
    assert normalize_cell(" 12,300 ") == "12300"
    assert normalize_cell("₩5,000") == "5000"
    assert normalize_cell(12300) == "12300"


def test_normalize_decimal():
    assert normalize_cell("3.50") == "3.5"
    assert normalize_cell("3.5") == normalize_cell("3.50")


def test_normalize_non_numeric():
    assert normalize_cell("2026-06-01") == "2026-06-01"
    assert normalize_cell("  홍  길동 ") == "홍 길동"
    assert normalize_cell("주문-3") == "주문-3"


def test_parse_count():
    assert parse_count("검색 결과 1,234건") == 1234
    assert parse_count("0건") == 0
    assert parse_count("없음") is None


HEADERS = ["주문번호", "고객", "금액"]
DB_COLS = ["id", "customer", "amount"]


def test_compare_exact_match():
    r = compare(HEADERS, [["1001", "김민준", "12,300"]], None, DB_COLS, [(1001, "김민준", 12300)])
    assert r.matched and r.ui_count == 1 and r.db_count == 1


def test_compare_order_insensitive_by_default():
    ui = [["1002", "이서연", "500"], ["1001", "김민준", "300"]]
    db = [(1001, "김민준", 300), (1002, "이서연", 500)]
    assert compare(HEADERS, ui, None, DB_COLS, db).matched


def test_compare_order_matters():
    ui = [["1002", "이서연", "500"], ["1001", "김민준", "300"]]
    db = [(1001, "김민준", 300), (1002, "이서연", 500)]
    assert not compare(HEADERS, ui, None, DB_COLS, db, order_matters=True).matched


def test_compare_missing_in_ui():
    r = compare(HEADERS, [["1001", "김민준", "300"]], None, DB_COLS,
                [(1001, "김민준", 300), (1002, "이서연", 500)])
    assert not r.matched
    assert r.missing_total == 1
    assert r.missing_in_ui == [["1002", "이서연", "500"]]


def test_compare_unexpected_in_ui():
    r = compare(HEADERS, [["1001", "김민준", "300"], ["9999", "유령", "1"]], None, DB_COLS,
                [(1001, "김민준", 300)])
    assert not r.matched
    assert r.unexpected_total == 1


def test_compare_duplicates_counted():
    ui = [["1001", "김민준", "300"], ["1001", "김민준", "300"]]
    db = [(1001, "김민준", 300)]
    assert not compare(HEADERS, ui, None, DB_COLS, db).matched


def test_compare_column_subset():
    headers = ["주문번호", "고객", "상태", "금액"]
    ui = [["1001", "김민준", "shipped", "9,000"]]
    r = compare(headers, ui, ["주문번호", "금액"], ["id", "amount"], [(1001, 9000)])
    assert r.matched and r.columns == ["주문번호", "금액"]


def test_compare_unknown_column():
    r = compare(HEADERS, [], ["없는컬럼"], DB_COLS, [])
    assert not r.matched and "없는컬럼" in r.note


def test_compare_column_count_mismatch():
    r = compare(HEADERS, [["1001", "김민준", "300"]], None, ["id", "amount"], [(1001, 300)])
    assert not r.matched and "1:1" in r.note
