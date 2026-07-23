"""UI↔DB 데이터 정합성: 쿼리 실행, 테이블 추출, 정규화·비교."""
from __future__ import annotations

import re
import sqlite3
from collections import Counter

from .models import DataCheckResult

_NUM_STRIP = re.compile(r"[,\s₩원$]")
_WS = re.compile(r"\s+")
_NUMERIC = re.compile(r"-?\d+(\.\d+)?")


def normalize_cell(value) -> str:
    """표현 차이 정규화: 공백 압축, 통화 표기 제거 후 숫자로 해석되면 수치 표준형."""
    s = _WS.sub(" ", str(value).strip())
    candidate = _NUM_STRIP.sub("", s)
    if _NUMERIC.fullmatch(candidate):
        f = float(candidate)
        return str(int(f)) if f.is_integer() else repr(f)
    return s


def run_query(db_url: str, sql: str) -> tuple[list[str], list[tuple]]:
    """정답 쿼리 실행. v1은 sqlite:///<경로>만 지원 (상대 경로는 CWD 기준)."""
    if not db_url.startswith("sqlite:///"):
        raise ValueError(f"v1은 'sqlite:///<경로>' 형식만 지원합니다 (받은 값: {db_url})")
    path = db_url[len("sqlite:///"):]
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cur = conn.execute(sql)
        cols = [d[0] for d in cur.description or []]
        rows = cur.fetchall()
    finally:
        conn.close()
    return cols, rows


JS_EXTRACT_TABLE = """(table) => {
  const clean = (s) => (s || '').trim();
  let headers = Array.from(table.querySelectorAll('thead th')).map(th => clean(th.innerText));
  let rowEls = Array.from(table.querySelectorAll('tbody tr'));
  if (headers.length === 0) {
    const all = Array.from(table.querySelectorAll('tr'));
    if (all.length) {
      headers = Array.from(all[0].querySelectorAll('th,td')).map(c => clean(c.innerText));
      rowEls = all.slice(1);
    }
  }
  const rows = rowEls
    .map(tr => Array.from(tr.querySelectorAll('td,th')).map(c => clean(c.innerText)))
    .filter(r => r.length > 0);
  return { headers, rows };
}"""


def extract_table(page, selector: str) -> tuple[list[str], list[list[str]]]:
    el = page.query_selector(selector)
    if el is None:
        raise ValueError(f"테이블을 찾을 수 없습니다: {selector}")
    data = el.evaluate(JS_EXTRACT_TABLE)
    return data["headers"], data["rows"]


def parse_count(text: str) -> int | None:
    m = re.search(r"([\d,]+)", text or "")
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def compare(
    headers: list[str],
    ui_rows: list[list[str]],
    columns: list[str] | None,
    db_cols: list[str],
    db_rows: list[tuple],
    order_matters: bool = False,
) -> DataCheckResult:
    """정규화 후 UI 행과 DB 행을 비교한다. 기본은 순서 무시(멀티셋)."""
    result = DataCheckResult()

    if columns:
        missing_cols = [c for c in columns if c not in headers]
        if missing_cols:
            result.note = f"UI 헤더에 없는 컬럼: {missing_cols} (실제 헤더: {headers})"
            return result
        indices = [headers.index(c) for c in columns]
        result.columns = list(columns)
    else:
        indices = list(range(len(headers)))
        result.columns = list(headers)

    if len(db_cols) != len(indices):
        result.note = (
            f"쿼리 SELECT 컬럼 수({len(db_cols)})와 비교할 UI 컬럼 수({len(indices)})가 다릅니다. "
            "query.sql의 SELECT 순서를 ui_table.columns와 1:1로 맞춰주세요."
        )
        return result

    ui_norm = [tuple(normalize_cell(row[i]) if i < len(row) else "" for i in indices) for row in ui_rows]
    db_norm = [tuple(normalize_cell(v) for v in row) for row in db_rows]
    result.ui_count, result.db_count = len(ui_norm), len(db_norm)

    if order_matters:
        result.matched = ui_norm == db_norm
        missing = [r for r in db_norm if r not in ui_norm]
        unexpected = [r for r in ui_norm if r not in db_norm]
    else:
        counter_ui, counter_db = Counter(ui_norm), Counter(db_norm)
        result.matched = counter_ui == counter_db
        missing = list((counter_db - counter_ui).elements())
        unexpected = list((counter_ui - counter_db).elements())

    result.missing_total = len(missing)
    result.unexpected_total = len(unexpected)
    result.missing_in_ui = [list(r) for r in missing[:10]]
    result.unexpected_in_ui = [list(r) for r in unexpected[:10]]
    return result
