"""설정 로딩·검증 유닛 테스트."""
from pathlib import Path

import pytest

from webtest_agent.config import ConfigError, load_config

ROOT = Path(__file__).resolve().parents[1]


def test_load_demo_config():
    cfg = load_config(ROOT / "configs" / "demo.yaml")
    assert cfg.target.base_url.startswith("http://127.0.0.1")
    assert len(cfg.data_checks) == 7
    check = cfg.data_checks[1]
    assert check.name == "상태필터-shipped"
    assert check.ui_table.columns == ["주문번호", "고객", "상태", "금액"]
    assert check.query.sql.strip().lower().startswith("select")
    assert "삭제" in cfg.sweep.avoid_patterns
    assert len(cfg.spec_checks) == 2
    assert cfg.spec_checks[0].steps[1].action == "assert_visible"
    assert cfg.target.flaky_recheck is False
    # API 오라클
    api_check = next(c for c in cfg.data_checks if c.name == "API대조-상태필터")
    assert api_check.query.api is not None
    assert api_check.query.api.rows_path == "orders"
    assert api_check.query.api.columns == ["id", "customer", "status", "amount"]
    # 페이지네이션
    pag_check = next(c for c in cfg.data_checks if c.name == "페이지네이션-전체목록")
    assert pag_check.ui_table.pagination.next_selector == "#next-page"
    # 쓰기 검증 / a11y
    assert len(cfg.write_checks) == 1 and cfg.write_checks[0].expect_delta == 1
    assert cfg.a11y.enabled is True


def test_hard_sweep_blocks_cannot_be_removed_by_yaml(tmp_path):
    p = tmp_path / "safe.yaml"
    p.write_text(
        """
target: {base_url: http://x}
button_sweep:
  avoid_patterns: [custom-danger]
""",
        encoding="utf-8",
    )

    cfg = load_config(p)

    assert "custom-danger" in cfg.sweep.avoid_patterns
    assert "삭제" in cfg.sweep.avoid_patterns
    assert "save" in cfg.sweep.avoid_patterns


def test_invalid_ignored_http_error_regex_is_rejected(tmp_path):
    p = tmp_path / "bad-regex.yaml"
    p.write_text(
        """
target:
  base_url: http://x
  ignore_http_error_patterns: ["["]
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="잘못된 정규식"):
        load_config(p)


def test_run_timeout_must_be_positive(tmp_path):
    p = tmp_path / "bad-timeout.yaml"
    p.write_text(
        """
target:
  base_url: http://x
  run_timeout_ms: 0
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="run_timeout_ms"):
        load_config(p)


def test_query_db_and_api_mutually_exclusive(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        """
target: {base_url: http://x}
data_checks:
  - name: t
    ui_table: {selector: "#t"}
    query:
      db: "sqlite:///x.db"
      sql: "SELECT 1"
      api: {url: /api, columns: [a]}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="하나만"):
        load_config(p)


def test_query_sql_must_be_a_single_read_only_statement(tmp_path):
    p = tmp_path / "unsafe-query.yaml"
    p.write_text(
        """
target: {base_url: http://x}
data_checks:
  - name: unsafe
    ui_table: {selector: "#t"}
    query:
      db: "postgresql://example.invalid/db"
      sql: "SELECT * FROM orders; DELETE FROM orders"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="단일 문장"):
        load_config(p)


def test_write_check_requires_expect_delta(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        """
target: {base_url: http://x}
write_checks:
  - name: t
    steps: [{action: click, selector: "#save"}]
    query: {db: "sqlite:///x.db", sql: "SELECT COUNT(*) FROM t"}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="expect_delta"):
        load_config(p)


def test_write_check_rejects_api_oracle(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        """
target: {base_url: http://x}
write_checks:
  - name: t
    steps: [{action: click, selector: "#save"}]
    query: {api: {url: /api, columns: [a]}}
    expect_delta: 1
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="db\\+sql"):
        load_config(p)


def test_assert_step_requires_value(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        """
target: {base_url: http://x}
spec_checks:
  - name: t
    steps: [{action: assert_text, selector: "#a"}]
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="value"):
        load_config(p)


def test_spec_check_requires_steps(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        """
target: {base_url: http://x}
spec_checks:
  - name: t
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="steps"):
        load_config(p)


def test_missing_base_url(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("crawl: {enabled: true}\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="base_url"):
        load_config(p)


def test_unknown_step_action(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        """
target: {base_url: http://x}
data_checks:
  - name: t
    steps: [{action: teleport, selector: "#a"}]
    ui_table: {selector: "#t"}
    query: {db: "sqlite:///x.db", sql: "SELECT 1"}
""",
        encoding="utf-8",
    )
    # hover는 이제 지원하는 동작이므로 '없는 동작'의 예로 쓸 수 없다.
    with pytest.raises(ConfigError, match="teleport"):
        load_config(p)


def test_load_auth_config(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", "demo1234")
    cfg = load_config(ROOT / "configs" / "demo-auth.yaml")
    assert cfg.auth is not None
    assert cfg.auth.steps[2].value == "demo1234"  # ${DEMO_PASSWORD} 치환 확인
    assert cfg.report.mask_selectors == ["#orders-table td:nth-of-type(2)"]
    assert "/logout" in cfg.crawl.exclude_patterns


def test_env_expand_missing_var(tmp_path, monkeypatch):
    monkeypatch.delenv("NO_SUCH_VAR_XYZ", raising=False)
    p = tmp_path / "bad.yaml"
    p.write_text(
        """
target: {base_url: http://x}
auth:
  steps:
    - {action: fill, selector: "#pw", value: "${NO_SUCH_VAR_XYZ}"}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="NO_SUCH_VAR_XYZ"):
        load_config(p)


def test_data_check_requires_query(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text(
        """
target: {base_url: http://x}
data_checks:
  - name: t
    ui_table: {selector: "#t"}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="query"):
        load_config(p)
