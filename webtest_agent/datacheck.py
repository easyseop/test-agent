"""UI↔정답원(DB/API) 데이터 정합성: 쿼리·API 실행, 테이블 추출, 정규화·비교."""
from __future__ import annotations

import json
import re
import sqlite3
import urllib.request
from collections import Counter
from decimal import Decimal

from .config import ApiSpec
from .models import DataCheckResult
from .safety import validate_read_only_sql

_NUM_STRIP = re.compile(r"[,\s₩원$]")
_WS = re.compile(r"\s+")
_NUMERIC = re.compile(r"-?\d+(\.\d+)?")


def normalize_cell(value) -> str:
    """표현 차이 정규화: 공백 압축, 통화 표기 제거 후 숫자로 해석되면 수치 표준형."""
    s = _WS.sub(" ", str(value).strip())
    candidate = _NUM_STRIP.sub("", s)
    if _NUMERIC.fullmatch(candidate):
        negative = candidate.startswith("-")
        unsigned = candidate[1:] if negative else candidate
        whole, dot, fraction = unsigned.partition(".")
        whole = whole.lstrip("0") or "0"
        fraction = fraction.rstrip("0") if dot else ""
        if whole == "0" and not fraction:
            return "0"
        canonical = whole + (f".{fraction}" if fraction else "")
        # float/Decimal 연산 없이 문자열만 다뤄 자릿수 제한 없이 정확히 보존한다.
        return f"-{canonical}" if negative else canonical
    return s


def run_query(db_url: str, sql: str) -> tuple[list[str], list[tuple]]:
    """정답 쿼리 실행.

    - sqlite:///<경로> : 내장 sqlite3, 읽기 전용(read-only) 접속
    - 그 외 SQLAlchemy URL (postgresql://…, mysql+pymysql://… 등) : sqlalchemy 필요
    """
    validate_read_only_sql(sql)

    if db_url.startswith("sqlite:///"):
        path = db_url[len("sqlite:///"):]
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cur = conn.execute(sql)
            cols = [d[0] for d in cur.description or []]
            rows = cur.fetchall()
        finally:
            conn.close()
        return cols, rows

    if "://" in db_url:
        try:
            from sqlalchemy import create_engine, text
        except ImportError as err:
            raise ValueError(
                "SQLite 외 DB 대조에는 sqlalchemy가 필요합니다 — "
                "pip install sqlalchemy 와 드라이버(PostgreSQL: psycopg2-binary, MySQL: pymysql)"
            ) from err
        engine = create_engine(db_url)
        try:
            with engine.connect() as conn:
                result = conn.execute(text(sql))
                cols = list(result.keys())
                rows = [tuple(r) for r in result.fetchall()]
        finally:
            engine.dispose()
        return cols, rows

    raise ValueError(f"지원하지 않는 DB URL 형식입니다: {db_url}")


def _pick_field(obj, path: str):
    """행 객체에서 점 표기 경로로 값을 꺼낸다 (없으면 빈 문자열)."""
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return ""
    return "" if cur is None else cur


def extract_api_rows(data, rows_path: str, columns: list[str]) -> list[tuple]:
    """API 응답 JSON에서 행 배열을 찾아 columns 순서의 튜플로 변환한다."""
    rows_obj = data
    if rows_path:
        for part in rows_path.split("."):
            if not isinstance(rows_obj, dict) or part not in rows_obj:
                raise ValueError(f"응답에서 rows_path '{rows_path}'를 찾을 수 없습니다")
            rows_obj = rows_obj[part]
    if not isinstance(rows_obj, list):
        raise ValueError(f"rows_path '{rows_path}' 위치가 배열이 아닙니다 ({type(rows_obj).__name__})")
    return [tuple(_pick_field(row, c) for c in columns) for row in rows_obj]


def run_api_query(api: ApiSpec, base_url: str) -> tuple[list[str], list[tuple]]:
    """REST API를 정답원으로 조회 — 화면이 백엔드 응답을 올바르게 표시하는지(표시 계층) 검증용."""
    url = api.url if "://" in api.url else base_url.rstrip("/") + api.url
    req = urllib.request.Request(url, headers=api.headers or {})
    with urllib.request.urlopen(req, timeout=15) as resp:
        # JSON 소수도 binary float로 바꾸지 않아 긴 숫자의 유효 자릿수를 보존한다.
        data = json.loads(resp.read().decode("utf-8"), parse_float=Decimal)
    return list(api.columns), extract_api_rows(data, api.rows_path, api.columns)


def run_scalar_query(db_url: str, sql: str) -> float:
    """단일 수치를 반환하는 쿼리 실행 (쓰기 검증의 사전/사후 측정용)."""
    cols, rows = run_query(db_url, sql)
    if not rows or not rows[0]:
        raise ValueError("쿼리 결과가 비어 있습니다 (단일 수치가 필요)")
    try:
        return float(rows[0][0])
    except (TypeError, ValueError) as err:
        raise ValueError(f"쿼리 첫 값이 수치가 아닙니다: {rows[0][0]!r}") from err


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
