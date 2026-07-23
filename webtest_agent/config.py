"""설정(YAML) 로딩과 검증."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(ValueError):
    """설정 파일 오류."""


STEP_ACTIONS = {"goto", "click", "fill", "select", "check", "press", "wait_for", "wait_ms"}
_NEEDS_SELECTOR = {"click", "fill", "select", "check", "press", "wait_for"}
_NEEDS_VALUE = {"goto", "fill", "select", "press", "wait_ms"}


@dataclass
class Step:
    action: str
    selector: str | None = None
    value: str | None = None

    @classmethod
    def from_dict(cls, d: dict, where: str) -> "Step":
        if not isinstance(d, dict):
            raise ConfigError(f"{where}: 스텝은 매핑이어야 합니다 (예: {{action: click, selector: '#btn'}})")
        action = d.get("action")
        if action not in STEP_ACTIONS:
            raise ConfigError(f"{where}: 지원하지 않는 action '{action}' (가능: {sorted(STEP_ACTIONS)})")
        selector = d.get("selector")
        value = d.get("value")
        if action in _NEEDS_SELECTOR and not selector:
            raise ConfigError(f"{where}: action '{action}'에는 selector가 필요합니다")
        if action in _NEEDS_VALUE and value is None:
            raise ConfigError(f"{where}: action '{action}'에는 value가 필요합니다")
        return cls(action=action, selector=selector, value=None if value is None else str(value))


@dataclass
class TargetConfig:
    base_url: str
    settle_ms: int = 400
    nav_timeout_ms: int = 10000
    scenario_timeout_ms: int = 60000
    app_version: str = ""


@dataclass
class CrawlConfig:
    enabled: bool = True
    max_pages: int = 8
    max_depth: int = 2
    exclude_patterns: list[str] = field(default_factory=list)


DEFAULT_AVOID = ["삭제", "delete", "remove", "탈퇴", "결제", "pay", "logout", "로그아웃"]


@dataclass
class SweepConfig:
    enabled: bool = True
    include_links: bool = True
    max_per_page: int = 30
    avoid_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_AVOID))


@dataclass
class UITableSpec:
    selector: str
    columns: list[str] | None = None
    count_selector: str | None = None


@dataclass
class QuerySpec:
    db: str
    sql: str
    order_matters: bool = False


@dataclass
class DataCheckSpec:
    name: str
    page: str = "/"
    description: str = ""
    steps: list[Step] = field(default_factory=list)
    ui_table: UITableSpec | None = None
    query: QuerySpec | None = None


@dataclass
class ReportConfig:
    title: str = "웹 자동 테스트 리포트"
    video: bool = True
    trace: bool = True


@dataclass
class AgentConfig:
    target: TargetConfig
    crawl: CrawlConfig
    sweep: SweepConfig
    data_checks: list[DataCheckSpec]
    report: ReportConfig
    output_dir: str = "runs"
    config_path: str = ""


def _sub(data: dict, key: str) -> dict:
    v = data.get(key) or {}
    if not isinstance(v, dict):
        raise ConfigError(f"'{key}' 섹션은 매핑이어야 합니다")
    return v


def load_config(path: str | Path) -> AgentConfig:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"설정 파일을 찾을 수 없습니다: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError("설정 파일 최상위는 매핑이어야 합니다")

    t = _sub(data, "target")
    if not t.get("base_url"):
        raise ConfigError("target.base_url은 필수입니다")
    target = TargetConfig(
        base_url=str(t["base_url"]).rstrip("/"),
        settle_ms=int(t.get("settle_ms", 400)),
        nav_timeout_ms=int(t.get("nav_timeout_ms", 10000)),
        scenario_timeout_ms=int(t.get("scenario_timeout_ms", 60000)),
        app_version=str(t.get("app_version", "")),
    )

    c = _sub(data, "crawl")
    crawl = CrawlConfig(
        enabled=bool(c.get("enabled", True)),
        max_pages=int(c.get("max_pages", 8)),
        max_depth=int(c.get("max_depth", 2)),
        exclude_patterns=[str(x) for x in c.get("exclude_patterns", [])],
    )

    s = _sub(data, "button_sweep")
    sweep = SweepConfig(
        enabled=bool(s.get("enabled", True)),
        include_links=bool(s.get("include_links", True)),
        max_per_page=int(s.get("max_per_page", 30)),
        avoid_patterns=[str(x) for x in s.get("avoid_patterns", DEFAULT_AVOID)],
    )

    checks: list[DataCheckSpec] = []
    for i, raw in enumerate(data.get("data_checks") or []):
        where = f"data_checks[{i}]"
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ConfigError(f"{where}: name이 필요합니다")
        name = str(raw["name"])
        steps = [Step.from_dict(sd, f"{where}.steps[{j}]") for j, sd in enumerate(raw.get("steps") or [])]

        ut_raw = raw.get("ui_table")
        if not ut_raw or not ut_raw.get("selector"):
            raise ConfigError(f"{where}: ui_table.selector가 필요합니다")
        columns = ut_raw.get("columns")
        ui_table = UITableSpec(
            selector=str(ut_raw["selector"]),
            columns=[str(x) for x in columns] if columns else None,
            count_selector=str(ut_raw["count_selector"]) if ut_raw.get("count_selector") else None,
        )

        q_raw = raw.get("query")
        if not q_raw or not q_raw.get("db") or not q_raw.get("sql"):
            raise ConfigError(f"{where}: query.db와 query.sql이 필요합니다")
        query = QuerySpec(
            db=str(q_raw["db"]),
            sql=str(q_raw["sql"]),
            order_matters=bool(q_raw.get("order_matters", False)),
        )

        checks.append(DataCheckSpec(
            name=name,
            page=str(raw.get("page", "/")),
            description=str(raw.get("description", "")),
            steps=steps,
            ui_table=ui_table,
            query=query,
        ))

    r = _sub(data, "report")
    report = ReportConfig(
        title=str(r.get("title", "웹 자동 테스트 리포트")),
        video=bool(r.get("video", True)),
        trace=bool(r.get("trace", True)),
    )

    return AgentConfig(
        target=target,
        crawl=crawl,
        sweep=sweep,
        data_checks=checks,
        report=report,
        output_dir=str(data.get("output_dir", "runs")),
        config_path=str(p),
    )
