"""설정(YAML) 로딩과 검증."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml


class ConfigError(ValueError):
    """설정 파일 오류."""


_ENV_RX = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env(value: str, where: str) -> str:
    """'${VAR}' 형태를 환경변수로 치환 — 비밀번호·DB 접속정보의 평문 저장 방지용."""
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in os.environ:
            raise ConfigError(f"{where}: 환경변수 {name}가 설정되어 있지 않습니다 (${{{name}}} 치환 실패)")
        return os.environ[name]
    return _ENV_RX.sub(repl, value)


STEP_ACTIONS = {
    "goto", "click", "fill", "select", "check", "press", "wait_for", "wait_ms",
    "assert_visible", "assert_text", "assert_url",
}
_NEEDS_SELECTOR = {"click", "fill", "select", "check", "press", "wait_for",
                   "assert_visible", "assert_text"}
_NEEDS_VALUE = {"goto", "fill", "select", "press", "wait_ms", "assert_text", "assert_url"}


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
        if value is not None:
            value = _expand_env(str(value), where)
        return cls(action=action, selector=selector, value=value)


@dataclass
class TargetConfig:
    base_url: str
    settle_ms: int = 400
    nav_timeout_ms: int = 10000
    scenario_timeout_ms: int = 60000
    app_version: str = ""
    flaky_recheck: bool = False  # 실패 시 1회 재실행해 간헐(flaky) 여부 표시


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
class SpecCheckSpec:
    """명세 기반 단언 시나리오 — 스텝(액션+단언)만으로 구성."""
    name: str
    page: str = "/"
    description: str = ""
    steps: list[Step] = field(default_factory=list)


@dataclass
class AuthConfig:
    """로그인 시나리오 — 실행 시작 시 1회 수행 후 세션(storage_state)을 전 시나리오가 재사용."""
    steps: list[Step] = field(default_factory=list)


@dataclass
class ReportConfig:
    title: str = "웹 자동 테스트 리포트"
    video: bool = True
    trace: bool = True
    mask_selectors: list[str] = field(default_factory=list)  # 스크린샷에서 가릴 요소(개인정보 등)


@dataclass
class AgentConfig:
    target: TargetConfig
    crawl: CrawlConfig
    sweep: SweepConfig
    data_checks: list[DataCheckSpec]
    spec_checks: list[SpecCheckSpec]
    report: ReportConfig
    auth: AuthConfig | None = None
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
        flaky_recheck=bool(t.get("flaky_recheck", False)),
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
            db=_expand_env(str(q_raw["db"]), f"{where}.query.db"),
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

    specs: list[SpecCheckSpec] = []
    for i, raw in enumerate(data.get("spec_checks") or []):
        where = f"spec_checks[{i}]"
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ConfigError(f"{where}: name이 필요합니다")
        spec_steps = [Step.from_dict(sd, f"{where}.steps[{j}]")
                      for j, sd in enumerate(raw.get("steps") or [])]
        if not spec_steps:
            raise ConfigError(f"{where}: steps가 최소 1개 필요합니다")
        specs.append(SpecCheckSpec(
            name=str(raw["name"]),
            page=str(raw.get("page", "/")),
            description=str(raw.get("description", "")),
            steps=spec_steps,
        ))

    auth: AuthConfig | None = None
    a_raw = data.get("auth")
    if a_raw:
        auth_steps = [Step.from_dict(sd, f"auth.steps[{j}]")
                      for j, sd in enumerate(a_raw.get("steps") or [])]
        if not auth_steps:
            raise ConfigError("auth: steps가 최소 1개 필요합니다")
        auth = AuthConfig(steps=auth_steps)

    r = _sub(data, "report")
    report = ReportConfig(
        title=str(r.get("title", "웹 자동 테스트 리포트")),
        video=bool(r.get("video", True)),
        trace=bool(r.get("trace", True)),
        mask_selectors=[str(x) for x in r.get("mask_selectors", [])],
    )

    return AgentConfig(
        target=target,
        crawl=crawl,
        sweep=sweep,
        data_checks=checks,
        spec_checks=specs,
        report=report,
        auth=auth,
        output_dir=str(data.get("output_dir", "runs")),
        config_path=str(p),
    )
