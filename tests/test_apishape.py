"""정답원 응답에서 설정 초안 뽑기.

설정 작업에서 가장 오래 걸리는 부분이다. 화면을 보고 아는 것이 아니라 응답을
뜯어야 알 수 있어서 discover로도 대신할 수 없었고, 사람이 `curl | jq`로 눈으로
훑어 rows_path와 점 표기 경로를 만들어야 했다.

이 도구는 **선택지를 보여줄 뿐 고르지 않는다.** 어느 배열이 목록인지, 어느
필드를 대조할지는 사람이 정한다.
"""
from webtest_agent.apishape import find_row_arrays, render

# OpenMetadata 화면 A의 실제 응답 모양 — 단건 객체 안에 columns 배열이 있다
TABLE = {
    "name": "bank_contract_table",
    "version": 0.1,
    "columns": [{
        "name": "bank_contract_column",
        "dataType": "VARCHAR",
        "extension": {"attributeName": "contractAttribute",
                      "instanceName": "contract1", "infoType": "PII"},
    }],
    "owners": [{"displayName": "admin", "id": "u1"}],
}

# 화면 B — 목록 응답
LIST = {"data": [
    {"name": "검사1", "testCaseResult": {"testCaseStatus": "Failed"},
     "owners": [{"displayName": "admin"}]},
    {"name": "검사2", "testCaseResult": {"testCaseStatus": "Success"},
     "owners": [{"displayName": "kim"}]},
], "paging": {"total": 11}}


def test_finds_nested_row_array():
    """단건 객체 안의 배열도 행 배열이다 — OpenMetadata 화면 A가 이 모양이다."""
    paths = {c["rows_path"] for c in find_row_arrays(TABLE)}
    assert "columns" in paths


def test_builds_dot_notation_for_nested_fields():
    """`extension.attributeName` 같은 경로를 손으로 만들 필요가 없어야 한다."""
    cols = next(c for c in find_row_arrays(TABLE) if c["rows_path"] == "columns")
    names = [n for n, _, _ in cols["fields"]]
    assert "extension.attributeName" in names
    assert "extension.infoType" in names


def test_builds_index_notation_for_arrays_of_objects():
    """`owners.0.displayName` — query.api.columns가 쓰는 표기와 같아야 한다."""
    rows = next(c for c in find_row_arrays(LIST) if c["rows_path"] == "data")
    names = [n for n, _, _ in rows["fields"]]
    assert "owners.0.displayName" in names
    assert "testCaseResult.testCaseStatus" in names


def test_shows_every_candidate_without_choosing():
    """어느 배열이 목록인지는 앱마다 다르다. 하나를 골라 버리면 틀렸을 때
    사람이 되돌릴 방법이 없다."""
    paths = {c["rows_path"] for c in find_row_arrays(TABLE)}
    assert paths == {"columns", "owners"}, "후보를 빠뜨리거나 임의로 고르면 안 된다"


def test_single_object_response_says_so():
    """단건 조회는 표와 행 단위로 대조할 수 없다.

    OpenMetadata 화면 B에서 실제로 이 상황이 나왔다 — 받은 API가 단건이라
    목록 대조에 쓸 수 없었다.
    """
    out = render({"name": "t", "version": 0.1}, "/api/v1/tables/name/x")
    assert "행 배열을 찾지 못했습니다" in out
    assert "단건 조회" in out


def test_bare_array_response():
    out = render([{"id": 1}, {"id": 2}], "/api/items")
    assert "응답 자체가 배열" in out


def test_empty_array_is_still_a_candidate_but_has_no_fields():
    """0건 응답도 rows_path 후보다 — 지금 비어 있을 뿐 목록은 목록이다."""
    got = find_row_arrays({"data": []})
    assert got == [] or got[0]["count"] == 0


def test_draft_uses_the_largest_candidate():
    out = render(LIST, "/api/v1/x")
    assert "rows_path: data" in out
    assert "columns: [" in out


def test_draft_reminds_that_order_matters():
    """columns 순서를 화면 표와 맞추지 않으면 조용히 엉뚱한 열을 비교한다."""
    out = render(LIST, "/api/v1/x")
    assert "같은 순서로" in out
    assert "무엇을 대조할지는 정해 주세요" in out
