"""쓰기 검증의 사후조건을 여러 개 건다.

건수 하나만 보는 검사는 통과해도 증명하는 게 거의 없다. "주문이 1건 늘었다"는
확인되지만 품목이 같이 저장됐는지, 감사 로그가 남았는지, 엉뚱한 표가 같이 늘지
않았는지는 확인되지 않는다.

여기서 고정하는 계약:

    하나라도 어긋나면 실패    '과반'도 '첫 조건만'도 아니다. 여러 개를 거는
                            이유 자체가 하나만 봐서는 증명이 안 되기 때문이다.
    못 잰 것은 결함이 아니다  정답원을 못 읽은 것을 제품 결함으로 보고하면
                            멀쩡한 코드를 뒤지게 된다 → 판정 불가로 흘린다.
    지우면 지문이 바뀐다      조건을 빼서 검사를 약하게 만들면 checks_sha256이
                            달라져야 한다. 아니면 조용히 약해진다.
    전부 보고한다            어긋난 조건을 하나만 적으면 나머지는 고치고 다시
                            돌려야 알게 된다.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from webtest_agent.config import ConfigError, load_config, post_conditions
from webtest_agent.fingerprint import checks_sha256
from webtest_agent.models import FAIL, PASS, ScenarioResult
from webtest_agent.runner import Runner


# ── 설정 파싱 ───────────────────────────────────────────────────

def _write_config(tmp_path, body: str):
    path = tmp_path / "c.yaml"
    path.write_text(f"""
target:
  base_url: http://127.0.0.1:5057
{body}
""", encoding="utf-8")
    return path


def _db(tmp_path):
    db = tmp_path / "seed.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER)")
    con.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER)")
    con.commit()
    con.close()
    return f"sqlite:///{db}"


def test_multiple_post_conditions_load(tmp_path):
    cfg = load_config(_write_config(tmp_path, f"""
write_checks:
  - name: 주문등록
    page: /new
    steps:
      - {{action: click, selector: "#save"}}
    query:
      db: {_db(tmp_path)}
    expect:
      - {{label: 주문, sql: "SELECT COUNT(*) FROM orders", delta: 1}}
      - {{label: 품목, sql: "SELECT COUNT(*) FROM items", delta: 2}}
"""))
    conds = post_conditions(cfg.write_checks[0])
    assert [c.label for c in conds] == ["주문", "품목"]
    assert [c.expected_delta for c in conds] == [1, 2]


def test_old_single_form_still_works(tmp_path):
    """예전 설정이 그대로 돌아야 한다 — 경로가 둘이면 한쪽만 고치는 사고가 난다."""
    cfg = load_config(_write_config(tmp_path, f"""
write_checks:
  - name: 주문등록
    steps:
      - {{action: click, selector: "#save"}}
    query:
      db: {_db(tmp_path)}
      sql: "SELECT COUNT(*) FROM orders"
    expect_delta: 1
"""))
    conds = post_conditions(cfg.write_checks[0])
    assert len(conds) == 1
    assert conds[0].expected_delta == 1
    assert conds[0].sql == "SELECT COUNT(*) FROM orders"


def test_mixing_both_forms_is_rejected(tmp_path):
    """어느 쪽이 검사인지 갈리면 조용히 한쪽만 적용된다."""
    with pytest.raises(ConfigError, match="함께 쓸 수 없습니다"):
        load_config(_write_config(tmp_path, f"""
write_checks:
  - name: x
    steps: [{{action: click, selector: "#a"}}]
    query: {{db: {_db(tmp_path)}}}
    expect_delta: 1
    expect:
      - {{sql: "SELECT COUNT(*) FROM orders", delta: 1}}
"""))


def test_expect_rejects_a_writing_statement(tmp_path):
    """사후조건도 조회 전용이어야 한다. 확인하러 가서 바꾸면 안 된다."""
    with pytest.raises(ConfigError):
        load_config(_write_config(tmp_path, f"""
write_checks:
  - name: x
    steps: [{{action: click, selector: "#a"}}]
    query: {{db: {_db(tmp_path)}}}
    expect:
      - {{sql: "DELETE FROM orders", delta: 0}}
"""))


def test_expect_rejects_string_interpolation_in_sql(tmp_path):
    """SQL 본문에 값을 이어붙이면 주입 위험이 생긴다."""
    with pytest.raises(ConfigError, match="변수"):
        load_config(_write_config(tmp_path, f"""
write_checks:
  - name: x
    steps:
      - {{action: extract, selector: "#no", store_as: order_no}}
      - {{action: click, selector: "#a"}}
    query: {{db: {_db(tmp_path)}}}
    expect:
      - {{sql: "SELECT COUNT(*) FROM orders WHERE id = {{{{order_no}}}}", delta: 1}}
"""))


def test_expect_requires_delta(tmp_path):
    with pytest.raises(ConfigError, match="delta"):
        load_config(_write_config(tmp_path, f"""
write_checks:
  - name: x
    steps: [{{action: click, selector: "#a"}}]
    query: {{db: {_db(tmp_path)}}}
    expect:
      - {{sql: "SELECT COUNT(*) FROM orders"}}
"""))


def test_param_must_come_from_a_step(tmp_path):
    with pytest.raises(ConfigError, match="extract"):
        load_config(_write_config(tmp_path, f"""
write_checks:
  - name: x
    steps: [{{action: click, selector: "#a"}}]
    query: {{db: {_db(tmp_path)}}}
    expect:
      - {{sql: "SELECT COUNT(*) FROM orders WHERE id = :no", delta: 1,
          params: {{no: "{{{{order_no}}}}"}}}}
"""))


# ── 지문: 조건을 지우면 값이 달라져야 한다 ──────────────────────

def _cfg_with(tmp_path, expect_body):
    return load_config(_write_config(tmp_path, f"""
write_checks:
  - name: 주문등록
    steps: [{{action: click, selector: "#save"}}]
    query: {{db: {_db(tmp_path)}}}
    expect:
{expect_body}
"""))


def test_dropping_a_condition_changes_the_fingerprint(tmp_path):
    """조건을 빼서 검사를 약하게 만들면 지문이 달라져야 한다."""
    two = _cfg_with(tmp_path, """      - {sql: "SELECT COUNT(*) FROM orders", delta: 1}
      - {sql: "SELECT COUNT(*) FROM items", delta: 2}""")
    one = _cfg_with(tmp_path, """      - {sql: "SELECT COUNT(*) FROM orders", delta: 1}""")
    assert checks_sha256(two) != checks_sha256(one)


def test_changing_an_expected_delta_changes_the_fingerprint(tmp_path):
    a = _cfg_with(tmp_path, """      - {sql: "SELECT COUNT(*) FROM orders", delta: 1}""")
    b = _cfg_with(tmp_path, """      - {sql: "SELECT COUNT(*) FROM orders", delta: 2}""")
    assert checks_sha256(a) != checks_sha256(b)


def test_same_conditions_give_the_same_fingerprint(tmp_path):
    body = """      - {sql: "SELECT COUNT(*) FROM orders", delta: 1}
      - {sql: "SELECT COUNT(*) FROM items", delta: 2}"""
    assert checks_sha256(_cfg_with(tmp_path, body)) == checks_sha256(_cfg_with(tmp_path, body))


# ── 판정 ────────────────────────────────────────────────────────

class _FakeRunner(Runner):
    """DB 대신 미리 정한 값을 돌려준다. 판정 논리만 본다."""

    def __init__(self, values):
        self._values = list(values)
        self._calls = 0

    def _resolve(self, text):
        return text


def _spec(*deltas, labels=None):
    labels = labels or [f"조건 {i + 1}" for i in range(len(deltas))]
    conds = [SimpleNamespace(sql=f"SELECT {i}", expected_delta=d, params={},
                             label=labels[i])
             for i, d in enumerate(deltas)]
    return SimpleNamespace(query=SimpleNamespace(db="sqlite:///x"), expect=conds,
                           expect_delta=0)


def _verdict(monkeypatch, spec, pre, post):
    """사전값 pre, 사후값 post로 판정을 낸다."""
    calls = iter(post)
    monkeypatch.setattr("webtest_agent.runner.run_scalar_query",
                        lambda db, sql, params: next(calls))
    return _FakeRunner([])._write_verdict(spec, pre)


def test_all_conditions_matching_passes(monkeypatch):
    result = _verdict(monkeypatch, _spec(1, 2), [10, 20], [11, 22])
    assert result.matched is True
    assert [c.matched for c in result.conditions] == [True, True]


def test_one_broken_condition_fails_the_whole_check(monkeypatch):
    """'과반'도 '첫 조건만'도 아니다. 하나라도 어긋나면 실패다."""
    result = _verdict(monkeypatch, _spec(1, 2, 3), [10, 20, 30], [11, 22, 30])
    assert result.matched is False
    assert [c.matched for c in result.conditions] == [True, True, False]


def test_a_broken_first_condition_is_not_hidden_by_later_passes(monkeypatch):
    result = _verdict(monkeypatch, _spec(1, 2), [10, 20], [10, 22])
    assert result.matched is False


def test_an_unreadable_condition_becomes_not_proven_not_a_defect(monkeypatch):
    """정답원을 못 읽은 것을 제품 결함으로 보고하면 멀쩡한 코드를 뒤지게 된다."""
    def boom(db, sql, params):
        raise RuntimeError("no such table: items")
    monkeypatch.setattr("webtest_agent.runner.run_scalar_query", boom)
    result = _FakeRunner([])._write_verdict(_spec(1), [10])
    assert result.matched is False
    assert result.note, "판정 불가 사유가 남아야 한다"
    assert "사후 측정 실패" in result.note


def test_scalar_summary_mirrors_the_first_condition(monkeypatch):
    result = _verdict(monkeypatch, _spec(1, 5), [10, 20], [11, 25])
    assert (result.pre, result.post, result.delta, result.expected_delta) == (10, 11, 1, 1)


def test_every_broken_condition_is_reported(monkeypatch):
    """하나만 적으면 나머지는 고치고 다시 돌려야 알게 된다."""
    result = _verdict(monkeypatch, _spec(1, 1, 1, labels=["주문", "품목", "감사로그"]),
                      [0, 0, 0], [0, 0, 0])
    res = ScenarioResult(name="w", kind="write_check", page="/")
    res.write_check = result
    runner = _FakeRunner([])
    runner.cfg = SimpleNamespace(
        target=SimpleNamespace(ignore_http_error_patterns=[], ignore_console_patterns=[]))
    runner._verdict(res, SimpleNamespace(kind="write_check", steps=[]), hard_fail=False)
    text = " ".join(res.reasons)
    for label in ("주문", "품목", "감사로그"):
        assert label in text, f"{label} 조건이 보고에서 빠졌다"
    assert res.status == FAIL
