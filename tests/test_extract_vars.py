"""단위 A — 화면 값 추출과 변수 재사용.

화면에 뜬 주문번호를 뽑아 다음 스텝과 정답 쿼리에 넘길 수 있어야 한다.
동시에 그 값이 SQL 구조를 바꾸거나, 시나리오 밖으로 새거나, 리포트에 평문으로
남는 일이 없어야 한다.
"""
from __future__ import annotations

import sqlite3

import pytest

from webtest_agent.config import (ConfigError, Step, load_config,
                                  substitute_vars, validate_step_vars, var_refs)
from webtest_agent.datacheck import run_query
from webtest_agent.runner import Runner, describe_step


def _runner():
    r = Runner.__new__(Runner)
    r.deadline_monotonic = None
    return r


def _step(**kwargs):
    return Step.from_dict(kwargs, "t")


# ── 설정 검증 ────────────────────────────────────────────────────

def test_extract_requires_store_as():
    with pytest.raises(ConfigError, match="store_as"):
        _step(action="extract", selector="#no")


def test_store_as_rejects_odd_names():
    with pytest.raises(ConfigError, match="영문자"):
        _step(action="extract", selector="#no", store_as="주문 번호")


def test_pattern_must_have_exactly_one_group():
    with pytest.raises(ConfigError, match="그룹이 정확히 1개"):
        _step(action="extract", selector="#no", store_as="v", pattern=r"(\d+)-(\d+)")
    with pytest.raises(ConfigError, match="그룹이 정확히 1개"):
        _step(action="extract", selector="#no", store_as="v", pattern=r"\d+")


def test_store_as_and_pattern_rejected_on_other_actions():
    with pytest.raises(ConfigError, match="extract"):
        _step(action="click", selector="#b", store_as="v")
    with pytest.raises(ConfigError, match="extract"):
        _step(action="click", selector="#b", pattern="(x)")


def test_variable_must_be_extracted_before_use():
    steps = [_step(action="goto", value="/orders/{{order_no}}")]
    with pytest.raises(ConfigError, match="먼저 만들어야"):
        validate_step_vars(steps, "spec_checks[0]")


def test_variable_use_after_extract_is_fine():
    steps = [
        _step(action="extract", selector="#no", store_as="order_no"),
        _step(action="goto", value="/orders/{{order_no}}"),
        _step(action="assert_text", selector="{{order_no}}", value="x"),
    ]
    validate_step_vars(steps, "spec_checks[0]")   # 예외 없으면 통과


def test_double_extract_of_same_name_rejected():
    steps = [
        _step(action="extract", selector="#a", store_as="v"),
        _step(action="extract", selector="#b", store_as="v"),
    ]
    with pytest.raises(ConfigError, match="두 번 만듭니다"):
        validate_step_vars(steps, "spec_checks[0]")


def test_var_refs_and_substitute():
    assert var_refs("/orders/{{ a }}/{{b}}") == ["a", "b"]
    assert substitute_vars("/orders/{{a}}", {"a": "42"}) == "/orders/42"
    with pytest.raises(KeyError):
        substitute_vars("{{missing}}", {})


# ── SQL 주입 차단 ────────────────────────────────────────────────

def _cfg_text(sql: str, params_block: str = "") -> str:
    return f"""
target: {{base_url: 'http://127.0.0.1:1'}}
data_checks:
  - name: c
    page: /
    steps:
      - {{action: extract, selector: '#no', store_as: order_no}}
    ui_table: {{selector: '#t'}}
    query:
      db: 'sqlite:///x.db'
      sql: "{sql}"
{params_block}
"""


def test_var_in_sql_body_is_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(_cfg_text("SELECT a FROM t WHERE id = '{{order_no}}'"), encoding="utf-8")
    with pytest.raises(ConfigError, match="query.params"):
        load_config(p)


def test_params_require_matching_placeholder(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(_cfg_text("SELECT a FROM t",
                           "      params: {order_no: '{{order_no}}'}"), encoding="utf-8")
    with pytest.raises(ConfigError, match=":order_no 자리가 없습니다"):
        load_config(p)


def test_params_var_must_be_extracted(tmp_path):
    p = tmp_path / "c.yaml"
    text = _cfg_text("SELECT a FROM t WHERE id = :order_no",
                     "      params: {order_no: '{{nope}}'}")
    p.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="만들지 않았습니다"):
        load_config(p)


def test_params_accepted_when_wired_correctly(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(_cfg_text("SELECT a FROM t WHERE id = :order_no",
                           "      params: {order_no: '{{order_no}}'}"), encoding="utf-8")
    cfg = load_config(p)
    assert cfg.data_checks[0].query.params == {"order_no": "{{order_no}}"}


def test_bound_value_cannot_change_query_shape(tmp_path):
    """추출값이 SQL 조각처럼 생겨도 데이터로만 취급되어야 한다."""
    db = tmp_path / "app.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE orders (order_no TEXT, customer TEXT)")
    conn.executemany("INSERT INTO orders VALUES (?, ?)",
                     [("1001", "김"), ("1002", "이")])
    conn.commit()
    conn.close()

    sql = "SELECT customer FROM orders WHERE order_no = :order_no"
    attack = "1001' OR '1'='1"
    cols, rows = run_query(f"sqlite:///{db}", sql, {"order_no": attack})
    assert rows == []          # 주입이 통했다면 2행이 나왔을 것이다

    _, ok = run_query(f"sqlite:///{db}", sql, {"order_no": "1001"})
    assert [r[0] for r in ok] == ["김"]


# ── 실행 ─────────────────────────────────────────────────────────

class _Locator:
    def __init__(self, tag="div", text="", value="", missing=False):
        self._tag, self._text, self._value, self._missing = tag, text, value, missing
        self.first = self

    def wait_for(self, **kwargs):
        if self._missing:
            raise RuntimeError("Timeout: not attached")

    def evaluate(self, _script):
        return self._tag.upper()

    def inner_text(self, **kwargs):
        return self._text

    def input_value(self, **kwargs):
        return self._value


class _Page:
    def __init__(self, locator):
        self._locator = locator
        self.url = "http://x/"

    def locator(self, _selector):
        return self._locator


def test_extract_reads_text_and_stores():
    r = _runner()
    page = _Page(_Locator(text="  주문번호 9007199254740993  "))
    r._exec_step(page, _step(action="extract", selector="#no", store_as="order_no"))
    assert r._vars["order_no"] == "주문번호 9007199254740993"


def test_extract_applies_pattern():
    r = _runner()
    page = _Page(_Locator(text="주문번호 9007199254740993 입니다"))
    r._exec_step(page, _step(action="extract", selector="#no",
                             store_as="order_no", pattern=r"([0-9]{6,})"))
    # 2^53을 넘는 값이 문자열 그대로 보존돼야 한다 (float로 뭉개지면 안 됨)
    assert r._vars["order_no"] == "9007199254740993"


def test_extract_reads_input_value_not_text():
    r = _runner()
    page = _Page(_Locator(tag="input", text="", value="INV-77"))
    r._exec_step(page, _step(action="extract", selector="#f", store_as="v"))
    assert r._vars["v"] == "INV-77"


def test_extract_fails_on_empty_value():
    r = _runner()
    page = _Page(_Locator(text="   "))
    with pytest.raises(AssertionError, match="비어 있습니다"):
        r._exec_step(page, _step(action="extract", selector="#no", store_as="v"))


def test_extract_fails_when_pattern_does_not_match():
    r = _runner()
    page = _Page(_Locator(text="주문 없음"))
    with pytest.raises(AssertionError, match="패턴"):
        r._exec_step(page, _step(action="extract", selector="#no",
                                 store_as="v", pattern=r"([0-9]+)"))


def test_extract_fails_when_element_missing():
    r = _runner()
    page = _Page(_Locator(missing=True))
    with pytest.raises(AssertionError, match="값을 읽지 못했습니다"):
        r._exec_step(page, _step(action="extract", selector="#no", store_as="v"))


def test_using_unextracted_variable_fails_at_runtime():
    """정적 검증을 우회해 Runner를 직접 부르더라도 조용히 통과하지 않는다."""
    r = _runner()
    with pytest.raises(AssertionError, match="아직 추출되지 않았습니다"):
        r._resolve("/orders/{{order_no}}")


def test_vars_do_not_leak_across_scenarios():
    r = _runner()
    page = _Page(_Locator(text="A1"))
    r._exec_step(page, _step(action="extract", selector="#no", store_as="v"))
    assert r._vars["v"] == "A1"
    r._reset_vars()
    with pytest.raises(AssertionError, match="아직 추출되지 않았습니다"):
        r._resolve("{{v}}")


def test_secret_extract_is_masked_in_messages():
    r = _runner()
    # selector가 비밀 입력을 가리키면 뽑은 값도 비밀로 다룬다
    page = _Page(_Locator(tag="input", value="s3cr3t-value"))
    r._exec_step(page, _step(action="extract", selector="#otp", store_as="code"))
    assert r._vars["code"] == "s3cr3t-value"
    assert "code" in r._secret_vars
    assert r._mask_vars("응답에 s3cr3t-value 가 있었다") == "응답에 *** 가 있었다"


def test_describe_step_mentions_variable():
    text = describe_step(_step(action="extract", selector="#no", store_as="order_no"))
    assert "order_no" in text
