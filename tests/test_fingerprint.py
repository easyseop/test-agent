"""실행 증거 지문 — 테스트가 조용히 약해지는 것을 잡는다.

리포트에 "통과 1 · 실패 0"만 남으면, 대조할 열을 넷에서 하나로 줄여도
지난주와 똑같아 보인다. 실제로 OpenMetadata 검증에서 컬럼 이름이 대조에서
빠진 것을 report.json을 뜯어보고서야 발견했다.
"""
import pathlib

from webtest_agent.config import load_config
from webtest_agent.fingerprint import checks_sha256, file_sha256

BASE = """
target:
  base_url: http://x.test
  settle_ms: 400
data_checks:
  - name: 목록
    page: /
    steps: [{action: wait_for, selector: "#t"}]
    ui_table: {selector: "#t", columns: [주문번호, 고객, 금액]}
    query: {db: "sqlite:///a.db", sql: "SELECT id, customer, amount FROM orders"}
report:
  title: 원래 제목
"""


def _sha(tmp_path, text, name="c.yaml"):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return checks_sha256(load_config(str(p)))


def test_unrelated_edits_do_not_change_the_fingerprint(tmp_path):
    """주석·제목처럼 판정과 무관한 수정에는 안 바뀐다.

    바뀌면 경보가 매번 울리고, 곧 아무도 보지 않게 된다.
    """
    base = _sha(tmp_path, BASE)
    assert _sha(tmp_path, "# 주석\n" + BASE, "b.yaml") == base
    assert _sha(tmp_path, BASE.replace("원래 제목", "다른 제목"), "c2.yaml") == base


def test_dropping_a_compared_column_changes_the_fingerprint(tmp_path):
    """검사를 약하게 만드는 수정은 반드시 드러나야 한다."""
    base = _sha(tmp_path, BASE)
    weakened = _sha(tmp_path,
                    BASE.replace("[주문번호, 고객, 금액]", "[주문번호]"), "w.yaml")
    assert weakened != base


def test_changing_the_oracle_changes_the_fingerprint(tmp_path):
    base = _sha(tmp_path, BASE)
    assert _sha(tmp_path, BASE.replace("FROM orders", "FROM orders WHERE 1=1"),
                "o.yaml") != base


def test_locale_is_part_of_the_fingerprint(tmp_path):
    """화면 언어가 바뀌면 무엇을 본 것인지가 달라진다."""
    base = _sha(tmp_path, BASE)
    assert _sha(tmp_path, BASE.replace("settle_ms: 400",
                                       "settle_ms: 400\n  locale: en-US"),
                "l.yaml") != base


def test_secrets_are_not_hashed_in_the_clear(tmp_path, monkeypatch):
    """`${환경변수}`는 설정을 읽는 시점에 실제 값으로 바뀐다.

    원본 value를 그대로 해싱하면 비밀번호가 지문 계산에 들어간다. 같은
    비밀번호를 쓰는 두 설정이 같은 지문을 갖는 것 자체가 정보가 되므로,
    마스킹된 값을 쓴다 — 비밀번호가 달라도 지문은 같아야 한다.
    """
    text = (BASE + "auth:\n  steps:\n"
            "    - {action: fill, selector: '#password', value: '${PW}'}\n")
    monkeypatch.setenv("PW", "hunter2")
    one = _sha(tmp_path, text, "s1.yaml")
    monkeypatch.setenv("PW", "완전히-다른-비밀번호")
    two = _sha(tmp_path, text, "s2.yaml")
    assert one == two


def test_file_hash_reflects_any_byte(tmp_path):
    """파일 해시는 주석까지 포함해 무엇이든 바뀌면 달라진다 — 용도가 다르다."""
    p = tmp_path / "f.yaml"
    p.write_text(BASE, encoding="utf-8")
    first = file_sha256(p)
    p.write_text("# 주석\n" + BASE, encoding="utf-8")
    assert file_sha256(p) != first


def test_missing_file_returns_empty(tmp_path):
    assert file_sha256(tmp_path / "없음.yaml") == ""


def test_diff_flags_a_changed_definition(tmp_path):
    """직전 실행과 검사 정의가 다르면 결과 비교가 같은 조건이 아니다."""
    import json
    from webtest_agent.history import diff_for

    prev = tmp_path / "20260101-000000"
    prev.mkdir()
    (prev / "report.json").write_text(json.dumps({
        "meta": {"base_url": "http://x", "config_path": "c.yaml",
                 "browser": "chromium", "checks_sha256": "AAA"},
        "scenarios": [{"name": "목록", "status": "pass"}]}), encoding="utf-8")
    cur = tmp_path / "20260102-000000"
    cur.mkdir()

    ident = ("http://x", "c.yaml", "chromium")
    same = diff_for(tmp_path, cur, {"목록": "pass"}, identity=ident,
                    cur_checks_sha256="AAA")
    assert same["checks_changed"] is False

    changed = diff_for(tmp_path, cur, {"목록": "pass"}, identity=ident,
                       cur_checks_sha256="BBB")
    # 통과 건수는 똑같은데 검사가 달라진 상황 — 결과만 보면 알 수 없다.
    assert changed["checks_changed"] is True
    assert changed["new_failures"] == [] and changed["fixed"] == []
