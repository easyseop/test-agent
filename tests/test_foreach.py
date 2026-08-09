"""단위 E — 반복(foreach).

"목록의 각 항목마다 같은 검사"를 실행 중 반복이 아니라 **계획 단계에서 펼쳐**
N개의 독립 시나리오로 만든다. 펼쳐 두면 리포트에 각각 한 줄로 남고, 병렬
실행과 직전 실행 비교도 개별로 된다.

조건분기(if/else)는 일부러 넣지 않는다 — 실행마다 다른 길을 타는 테스트는
실패를 숨긴다.
"""
from __future__ import annotations

import json

import pytest

from webtest_agent.config import MAX_FOREACH_ITEMS, ConfigError, load_config

HEAD = "target: {base_url: 'http://127.0.0.1:1'}\n"


def _cfg(tmp_path, body: str, name: str = "c.yaml"):
    p = tmp_path / name
    p.write_text(HEAD + body, encoding="utf-8")
    return load_config(p)


BASIC = """
spec_checks:
  - name: "상품카드-{{item}}"
    description: "{{item}} 카드가 보여야 한다"
    foreach: {var: item, in: [셔츠, 바지, 신발]}
    page: /products
    steps:
      - {action: assert_text, selector: ".card", value: "{{item}}"}
"""


def test_foreach_expands_into_independent_scenarios(tmp_path):
    cfg = _cfg(tmp_path, BASIC)
    assert len(cfg.spec_checks) == 3
    assert [s.name for s in cfg.spec_checks] == ["상품카드-셔츠", "상품카드-바지", "상품카드-신발"]
    # 값이 이름·설명·스텝 전부에 들어가야 한다
    assert cfg.spec_checks[0].description == "셔츠 카드가 보여야 한다"
    assert cfg.spec_checks[0].steps[0].value == "셔츠"
    assert cfg.spec_checks[2].steps[0].value == "신발"


def test_expanded_scenarios_do_not_share_step_objects(tmp_path):
    """한 시나리오를 고치면 다른 시나리오가 같이 바뀌면 안 된다."""
    cfg = _cfg(tmp_path, BASIC)
    first, second = cfg.spec_checks[0], cfg.spec_checks[1]
    assert first.steps[0] is not second.steps[0]
    assert first.steps[0].value != second.steps[0].value


def test_checks_without_foreach_are_untouched(tmp_path):
    cfg = _cfg(tmp_path, """
spec_checks:
  - name: 그냥검사
    page: /
    steps:
      - {action: assert_visible, selector: "#x"}
""")
    assert [s.name for s in cfg.spec_checks] == ["그냥검사"]


def test_foreach_works_on_every_check_type(tmp_path):
    cfg = _cfg(tmp_path, """
perf_checks:
  - name: "로드-{{page}}"
    foreach: {var: page, in: [a, b]}
    page: "/{{page}}"
    metric: load
    budget_ms: 3000
""")
    assert [c.name for c in cfg.perf_checks] == ["로드-a", "로드-b"]
    assert [c.page for c in cfg.perf_checks] == ["/a", "/b"]


# ── 잘못 쓰면 조용히 넘어가지 않는다 ─────────────────────────────

def test_duplicate_expanded_names_are_rejected(tmp_path):
    """이름이 겹치면 리포트에서 어느 항목이 깨졌는지 구분할 수 없다."""
    with pytest.raises(ConfigError, match="겹칩니다"):
        _cfg(tmp_path, """
spec_checks:
  - name: 고정이름
    foreach: {var: item, in: [a, b]}
    page: /
    steps:
      - {action: assert_text, selector: "#x", value: "{{item}}"}
""")


def test_foreach_needs_var(tmp_path):
    with pytest.raises(ConfigError, match="var가 필요"):
        _cfg(tmp_path, """
spec_checks:
  - name: "x-{{item}}"
    foreach: {in: [a]}
    page: /
    steps: [{action: assert_visible, selector: "#x"}]
""")


def test_foreach_needs_exactly_one_source(tmp_path):
    with pytest.raises(ConfigError, match="정확히 하나"):
        _cfg(tmp_path, """
spec_checks:
  - name: "x-{{i}}"
    foreach: {var: i, in: [a], in_file: items.json}
    page: /
    steps: [{action: assert_visible, selector: "#x"}]
""")


def test_empty_list_is_rejected(tmp_path):
    """빈 목록을 허용하면 시나리오 0개가 조용히 통과로 보고된다."""
    with pytest.raises(ConfigError, match="비어 있지 않은"):
        _cfg(tmp_path, """
spec_checks:
  - name: "x-{{i}}"
    foreach: {var: i, in: []}
    page: /
    steps: [{action: assert_visible, selector: "#x"}]
""")


def test_too_many_items_rejected(tmp_path):
    items = json.dumps(list(range(MAX_FOREACH_ITEMS + 1)))
    with pytest.raises(ConfigError, match="시나리오는"):
        _cfg(tmp_path, f"""
spec_checks:
  - name: "x-{{{{i}}}}"
    foreach: {{var: i, in: {items}}}
    page: /
    steps: [{{action: assert_visible, selector: "#x"}}]
""")


def test_nested_values_rejected(tmp_path):
    with pytest.raises(ConfigError, match="문자열·숫자"):
        _cfg(tmp_path, """
spec_checks:
  - name: "x-{{i}}"
    foreach: {var: i, in: [{a: 1}]}
    page: /
    steps: [{action: assert_visible, selector: "#x"}]
""")


# ── in_file ─────────────────────────────────────────────────────

def test_in_file_reads_the_list(tmp_path):
    (tmp_path / "items.json").write_text(json.dumps(["가", "나"]), encoding="utf-8")
    cfg = _cfg(tmp_path, """
spec_checks:
  - name: "항목-{{i}}"
    foreach: {var: i, in_file: items.json}
    page: /
    steps: [{action: assert_text, selector: "#x", value: "{{i}}"}]
""")
    assert [s.name for s in cfg.spec_checks] == ["항목-가", "항목-나"]


def test_in_file_cannot_escape_the_config_folder(tmp_path):
    with pytest.raises(ConfigError, match="벗어납니다"):
        _cfg(tmp_path, """
spec_checks:
  - name: "x-{{i}}"
    foreach: {var: i, in_file: "../../../etc/passwd"}
    page: /
    steps: [{action: assert_visible, selector: "#x"}]
""")


def test_in_file_absolute_path_rejected(tmp_path):
    with pytest.raises(ConfigError, match="상대경로"):
        _cfg(tmp_path, """
spec_checks:
  - name: "x-{{i}}"
    foreach: {var: i, in_file: "/etc/passwd"}
    page: /
    steps: [{action: assert_visible, selector: "#x"}]
""")


def test_missing_in_file_reported(tmp_path):
    with pytest.raises(ConfigError, match="파일이 없습니다"):
        _cfg(tmp_path, """
spec_checks:
  - name: "x-{{i}}"
    foreach: {var: i, in_file: nope.json}
    page: /
    steps: [{action: assert_visible, selector: "#x"}]
""")


# ── 단위 A(화면 값 추출)와 같이 쓰기 ─────────────────────────────

def test_foreach_var_and_extract_var_coexist(tmp_path):
    """foreach 값은 미리 박히고, extract 변수는 실행 중에 채워진다."""
    cfg = _cfg(tmp_path, """
spec_checks:
  - name: "상세-{{item}}"
    foreach: {var: item, in: [셔츠, 바지]}
    page: /products
    steps:
      - {action: click, selector: "a:has-text('{{item}}')"}
      - {action: extract, selector: "#code", store_as: code}
      - {action: assert_text, selector: "#title", value: "{{code}}"}
""")
    assert len(cfg.spec_checks) == 2
    first = cfg.spec_checks[0]
    # foreach 값은 이미 문자열로 박혔다
    assert first.steps[0].selector == "a:has-text('셔츠')"
    # extract 변수는 실행 중에 채워지므로 그대로 남아 있어야 한다
    assert first.steps[2].value == "{{code}}"


def test_foreach_does_not_bypass_the_extract_rule(tmp_path):
    """foreach를 썼다고 '뽑기 전에 쓰기'가 허용되면 안 된다."""
    with pytest.raises(ConfigError, match="먼저 만들어야"):
        _cfg(tmp_path, """
spec_checks:
  - name: "x-{{item}}"
    foreach: {var: item, in: [a, b]}
    page: /
    steps:
      - {action: assert_text, selector: "#x", value: "{{notyet}}"}
""")


def test_foreach_does_not_bypass_the_safety_boundary(tmp_path):
    """펼친 시나리오도 파괴적 동작 검사를 그대로 받아야 한다."""
    from webtest_agent.runner import _GUARDED_ACTIONS
    from webtest_agent.safety import find_destructive
    cfg = _cfg(tmp_path, """
spec_checks:
  - name: "삭제-{{item}}"
    foreach: {var: item, in: [a, b]}
    page: /
    steps:
      - {action: click, selector: "#delete-{{item}}"}
""")
    for check in cfg.spec_checks:
        step = check.steps[0]
        assert step.action in _GUARDED_ACTIONS
        assert find_destructive(step.selector)      # 실행 시 차단 대상이 된다
