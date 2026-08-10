"""`discover`의 표 추출 — 실제 Chromium에서 JS를 돌려 검증한다.

이 부분은 파이썬이 아니라 페이지 안에서 도는 JS다. 파이썬만 테스트하면
JS에 오타가 나도 476건이 전부 통과한다. 그래서 여기서는 브라우저를 띄우고
직접 만든 HTML에 JS_INVENTORY를 실행해 결과를 본다.

지키려는 것은 두 가지다.

  세는 것을 정확히 센다   헤더 줄을 행으로 세거나, 안쪽 표의 행을 바깥 표에
                        더하면 "표본 1행"을 "표본 5행"으로 착각한다.
  빼놓지 않는다          안 보이는 표·0행 표를 목록에서 지우면 "그런 표는
                        없다"로 읽힌다. 표시만 하고 남긴다.
"""
from __future__ import annotations

import pytest

from webtest_agent.discovery import JS_INVENTORY

playwright_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture(scope="module")
def inventory():
    """HTML 한 조각을 넣으면 JS_INVENTORY 결과를 준다."""
    with playwright_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()

        def run(html: str) -> dict:
            page.set_content(f"<!doctype html><html lang='ko'><body>{html}</body></html>")
            return page.evaluate(JS_INVENTORY)

        yield run
        browser.close()


def tables(inventory, html: str) -> list[dict]:
    return inventory(html)["tables"]


# ── 기본 ────────────────────────────────────────────────────────

def test_headers_and_row_count(inventory):
    """thead의 th는 헤더, tbody의 tr만 행."""
    found = tables(inventory, """
      <table id="orders">
        <thead><tr><th>주문번호</th><th>고객</th><th>상태</th></tr></thead>
        <tbody>
          <tr><td>1001</td><td>김</td><td>배송중</td></tr>
          <tr><td>1002</td><td>이</td><td>완료</td></tr>
        </tbody>
      </table>
    """)
    assert len(found) == 1
    assert found[0]["selector"] == "#orders"
    assert found[0]["headers"] == ["주문번호", "고객", "상태"]
    assert found[0]["row_count"] == 2, "헤더 줄을 행으로 세면 안 된다"


def test_header_row_without_th_is_used_as_headers(inventory):
    """th를 안 쓰는 표도 많다. 첫 줄을 헤더로 보여준다."""
    found = tables(inventory, """
      <table id="t">
        <tr><td>이름</td><td>값</td></tr>
        <tr><td>가</td><td>1</td></tr>
      </table>
    """)
    assert found[0]["headers"] == ["이름", "값"]
    # th가 없으므로 첫 줄도 데이터 행으로 센다. 이건 추측이 아니라 사실이고,
    # 헤더로 쓴 줄인지 아닌지는 화면을 본 사람이 정한다.
    assert found[0]["row_count"] == 2


def test_row_header_cell_does_not_drop_the_row(inventory):
    """행 머리글(줄 맨 앞 th)이 있는 행은 데이터 행이다."""
    found = tables(inventory, """
      <table id="t">
        <thead><tr><th>항목</th><th>값</th></tr></thead>
        <tbody>
          <tr><th>매출</th><td>100</td></tr>
          <tr><th>비용</th><td>40</td></tr>
        </tbody>
      </table>
    """)
    assert found[0]["row_count"] == 2, "th가 섞였다고 행을 버리면 표본이 0이 된다"


# ── 셀렉터 ──────────────────────────────────────────────────────

def test_testid_beats_positional_path(inventory):
    """nth-of-type 경로는 화면이 조금만 바뀌어도 깨진다."""
    found = tables(inventory, """
      <div><section><table data-testid="entity-table">
        <thead><tr><th>Name</th></tr></thead>
        <tbody><tr><td>a</td></tr></tbody>
      </table></section></div>
    """)
    assert found[0]["selector"] == '[data-testid="entity-table"]'


def test_id_beats_testid(inventory):
    found = tables(inventory, """
      <table id="orders" data-testid="entity-table"><tr><td>a</td></tr></table>
    """)
    assert found[0]["selector"] == "#orders"


def test_unsafe_testid_value_falls_back_to_path(inventory):
    """따옴표·공백이 든 값을 그대로 넣으면 셀렉터가 깨진 채 나간다."""
    found = tables(inventory, """
      <table data-testid='entity "main" table'><tr><td>a</td></tr></table>
    """)
    assert "data-testid" not in found[0]["selector"]
    assert "table" in found[0]["selector"]


# ── 빠뜨리지 않는다 ─────────────────────────────────────────────

def test_hidden_table_is_reported_not_dropped(inventory):
    """탭을 안 열어 안 보이는 표를 지우면 '그런 표는 없다'로 읽힌다."""
    found = tables(inventory, """
      <table id="visible"><tr><td>a</td></tr></table>
      <table id="hidden" style="display:none"><tr><td>b</td></tr></table>
    """)
    by_id = {t["selector"]: t for t in found}
    assert by_id["#visible"]["visible"] is True
    assert by_id["#hidden"]["visible"] is False, "숨은 표도 목록에는 남아야 한다"


def test_empty_table_is_reported(inventory):
    """0행 표는 지울 대상이 아니라 알아야 할 사실이다."""
    found = tables(inventory, """
      <table id="t"><thead><tr><th>이름</th></tr></thead><tbody></tbody></table>
    """)
    assert len(found) == 1
    assert found[0]["row_count"] == 0
    assert found[0]["headers"] == ["이름"]


def test_thin_sample_is_visible_before_the_config_is_written(inventory):
    """1행짜리 표를 정답원과 대조해봐야 증명되는 게 거의 없다.

    그 사실이 설정을 쓰기 전에 보여야 한다. 다 돌리고 리포트를 열어야
    알게 되면 이미 늦다.
    """
    found = tables(inventory, """
      <table data-testid="entity-table">
        <thead><tr><th>No</th><th>Name</th><th>Type</th></tr></thead>
        <tbody><tr><td>1</td><td>a</td><td>INT</td></tr></tbody>
      </table>
    """)
    assert found[0]["row_count"] == 1


# ── 섞이지 않는다 ───────────────────────────────────────────────

def test_nested_table_rows_do_not_inflate_the_outer_count(inventory):
    """안쪽 표의 행을 바깥 표에 더하면 표본 크기가 부풀려진다."""
    found = tables(inventory, """
      <table id="outer">
        <thead><tr><th>바깥</th></tr></thead>
        <tbody><tr><td>
          <table id="inner">
            <thead><tr><th>안쪽</th></tr></thead>
            <tbody><tr><td>x</td></tr><tr><td>y</td></tr><tr><td>z</td></tr></tbody>
          </table>
        </td></tr></tbody>
      </table>
    """)
    by_id = {t["selector"]: t for t in found}
    assert by_id["#outer"]["row_count"] == 1
    assert by_id["#outer"]["headers"] == ["바깥"], "안쪽 헤더가 섞이면 안 된다"
    assert by_id["#inner"]["row_count"] == 3
    assert by_id["#inner"]["headers"] == ["안쪽"]


def test_role_grid_is_found(inventory):
    """table 태그를 안 쓰고 div로 표를 그리는 앱이 많다."""
    found = tables(inventory, """
      <div role="grid" data-testid="dq-grid">
        <div role="row"><div role="columnheader">규칙</div><div role="columnheader">결과</div></div>
        <div role="row"><div role="gridcell">널검사</div><div role="gridcell">통과</div></div>
        <div role="row"><div role="gridcell">범위</div><div role="gridcell">실패</div></div>
      </div>
    """)
    assert len(found) == 1
    assert found[0]["selector"] == '[data-testid="dq-grid"]'
    assert found[0]["headers"] == ["규칙", "결과"]
    assert found[0]["row_count"] == 2


def test_no_tables_gives_empty_list_not_missing_key(inventory):
    """키가 없으면 읽는 쪽이 '옛날 discovery.json인가'를 매번 따져야 한다."""
    assert inventory("<p>표 없음</p>")["tables"] == []
