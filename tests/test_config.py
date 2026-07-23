"""설정 로딩·검증 유닛 테스트."""
from pathlib import Path

import pytest

from webtest_agent.config import ConfigError, load_config

ROOT = Path(__file__).resolve().parents[1]


def test_load_demo_config():
    cfg = load_config(ROOT / "configs" / "demo.yaml")
    assert cfg.target.base_url.startswith("http://127.0.0.1")
    assert len(cfg.data_checks) == 5
    check = cfg.data_checks[1]
    assert check.name == "상태필터-shipped"
    assert check.ui_table.columns == ["주문번호", "고객", "상태", "금액"]
    assert check.query.sql.strip().lower().startswith("select")
    assert "삭제" in cfg.sweep.avoid_patterns
    assert len(cfg.spec_checks) == 2
    assert cfg.spec_checks[0].steps[1].action == "assert_visible"
    assert cfg.target.flaky_recheck is False


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
    steps: [{action: hover, selector: "#a"}]
    ui_table: {selector: "#t"}
    query: {db: "sqlite:///x.db", sql: "SELECT 1"}
""",
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="hover"):
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
