"""설정(YAML) 로딩과 검증."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .safety import (HARD_BLOCK_PATTERNS, merge_avoid_patterns,
                     validate_read_only_sql)


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
    run_timeout_ms: int = 1800000
    app_version: str = ""
    flaky_recheck: bool = False  # 실패 시 1회 재실행해 간헐(flaky) 여부 표시
    ignore_http_error_patterns: list[str] = field(default_factory=list)


@dataclass
class CrawlConfig:
    enabled: bool = True
    max_pages: int = 8
    max_depth: int = 2
    exclude_patterns: list[str] = field(default_factory=list)


DEFAULT_AVOID = list(HARD_BLOCK_PATTERNS)


@dataclass
class SweepConfig:
    enabled: bool = True
    include_links: bool = True
    max_per_page: int = 30
    avoid_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_AVOID))


@dataclass
class PaginationSpec:
    """표가 여러 페이지로 나뉠 때 '다음' 버튼을 순회하며 전체 행을 수집한다."""
    next_selector: str
    max_pages: int = 20


@dataclass
class UITableSpec:
    selector: str
    columns: list[str] | None = None
    count_selector: str | None = None
    pagination: PaginationSpec | None = None


@dataclass
class ApiSpec:
    """REST API 응답을 정답원으로 쓰는 대조 — 화면이 백엔드 응답을 올바르게 표시하는지(표시 계층) 검증."""
    url: str                      # 상대 경로면 target.base_url 기준
    columns: list[str] = field(default_factory=list)   # 행 객체에서 뽑을 필드 (점 표기 지원: owner.name)
    rows_path: str = ""           # 응답 JSON에서 행 배열 위치 (점 표기, 빈값 = 루트가 배열)
    headers: dict[str, str] = field(default_factory=dict)  # 예: Authorization — ${환경변수} 치환 지원


@dataclass
class QuerySpec:
    db: str = ""
    sql: str = ""
    order_matters: bool = False
    api: ApiSpec | None = None    # db+sql 대신 API를 정답원으로 사용


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
class WriteCheckSpec:
    """쓰기(상태 전이) 검증 — 스텝 실행 전후의 DB 스칼라 값 변화량을 검증한다.

    query.sql은 단일 수치를 반환해야 한다 (예: SELECT COUNT(*) FROM orders).
    반드시 스테이징/시드 DB에서만 사용할 것.
    """
    name: str
    page: str = "/"
    description: str = ""
    steps: list[Step] = field(default_factory=list)
    query: QuerySpec | None = None
    expect_delta: int = 0


@dataclass
class VisualCheckSpec:
    """시각 회귀(기준선 대조) — 첫 실행 시 기준선 생성, 이후 픽셀 비교.

    의도된 UI 변경은 `run --update-baselines`로 기준선을 갱신(승인)한다.
    """
    name: str
    page: str = "/"
    description: str = ""
    steps: list[Step] = field(default_factory=list)
    selector: str = ""            # 지정 시 해당 요소만 비교 (기본: 뷰포트)
    full_page: bool = False
    threshold: float = 0.01       # 허용되는 달라진 픽셀 비율 (1%)
    severity: str = "warn"        # warn(기본) | fail — 불일치 시 판정


@dataclass
class NotifyConfig:
    """실행 후 웹훅 알림 (Slack Incoming Webhook 호환 JSON POST)."""
    webhook_url: str
    on: str = "fail"              # fail(기본: 실패 있을 때만) | always


@dataclass
class A11yConfig:
    """접근성 기본 점검(간이·내장) — 크롤링한 페이지에 정보성으로 보고 (판정에 영향 없음)."""
    enabled: bool = False


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
    write_checks: list[WriteCheckSpec]
    visual_checks: list[VisualCheckSpec]
    report: ReportConfig
    a11y: A11yConfig = field(default_factory=A11yConfig)
    auth: AuthConfig | None = None
    notify: NotifyConfig | None = None
    baselines_dir: str = "baselines"
    output_dir: str = "runs"
    config_path: str = ""


def _sub(data: dict, key: str) -> dict:
    v = data.get(key) or {}
    if not isinstance(v, dict):
        raise ConfigError(f"'{key}' 섹션은 매핑이어야 합니다")
    return v


def _regex_list(values, where: str) -> list[str]:
    patterns = [str(value) for value in (values or [])]
    for index, pattern in enumerate(patterns):
        try:
            re.compile(pattern)
        except re.error as err:
            raise ConfigError(f"{where}[{index}]: 잘못된 정규식 '{pattern}' ({err})") from err
    return patterns


def _parse_query(q_raw, where: str, allow_api: bool) -> QuerySpec:
    """query 파싱 — (db+sql) 또는 api 중 정확히 하나."""
    if not q_raw:
        raise ConfigError(f"{where}: query가 필요합니다 (db+sql 또는 api)")
    has_db = bool(q_raw.get("db") or q_raw.get("sql"))
    api_raw = q_raw.get("api")
    if has_db and api_raw:
        raise ConfigError(f"{where}: query는 db+sql 또는 api 중 하나만 지정하세요")
    if api_raw:
        if not allow_api:
            raise ConfigError(f"{where}: 이 검증에는 api 정답원을 쓸 수 없습니다 (db+sql만 가능)")
        if not api_raw.get("url") or not api_raw.get("columns"):
            raise ConfigError(f"{where}: query.api에는 url과 columns가 필요합니다")
        api = ApiSpec(
            url=_expand_env(str(api_raw["url"]), f"{where}.query.api.url"),
            columns=[str(c) for c in api_raw["columns"]],
            rows_path=str(api_raw.get("rows_path", "")),
            headers={str(k): _expand_env(str(v), f"{where}.query.api.headers.{k}")
                     for k, v in (api_raw.get("headers") or {}).items()},
        )
        return QuerySpec(api=api, order_matters=bool(q_raw.get("order_matters", False)))
    if not q_raw.get("db") or not q_raw.get("sql"):
        raise ConfigError(f"{where}: query.db와 query.sql이 필요합니다")
    sql = str(q_raw["sql"])
    try:
        validate_read_only_sql(sql)
    except ValueError as err:
        raise ConfigError(f"{where}.query.sql: {err}") from err
    return QuerySpec(
        db=_expand_env(str(q_raw["db"]), f"{where}.query.db"),
        sql=sql,
        order_matters=bool(q_raw.get("order_matters", False)),
    )


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
        run_timeout_ms=int(t.get("run_timeout_ms", 1800000)),
        app_version=str(t.get("app_version", "")),
        flaky_recheck=bool(t.get("flaky_recheck", False)),
        ignore_http_error_patterns=_regex_list(
            t.get("ignore_http_error_patterns"),
            "target.ignore_http_error_patterns",
        ),
    )
    if target.run_timeout_ms <= 0:
        raise ConfigError("target.run_timeout_ms는 1ms 이상이어야 합니다")

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
        avoid_patterns=merge_avoid_patterns(s.get("avoid_patterns", [])),
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
        pagination = None
        pg_raw = ut_raw.get("pagination")
        if pg_raw:
            if not pg_raw.get("next_selector"):
                raise ConfigError(f"{where}: pagination.next_selector가 필요합니다")
            pagination = PaginationSpec(
                next_selector=str(pg_raw["next_selector"]),
                max_pages=int(pg_raw.get("max_pages", 20)),
            )
        ui_table = UITableSpec(
            selector=str(ut_raw["selector"]),
            columns=[str(x) for x in columns] if columns else None,
            count_selector=str(ut_raw["count_selector"]) if ut_raw.get("count_selector") else None,
            pagination=pagination,
        )

        query = _parse_query(raw.get("query"), where, allow_api=True)

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

    writes: list[WriteCheckSpec] = []
    for i, raw in enumerate(data.get("write_checks") or []):
        where = f"write_checks[{i}]"
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ConfigError(f"{where}: name이 필요합니다")
        w_steps = [Step.from_dict(sd, f"{where}.steps[{j}]")
                   for j, sd in enumerate(raw.get("steps") or [])]
        if not w_steps:
            raise ConfigError(f"{where}: steps가 최소 1개 필요합니다")
        if "expect_delta" not in raw:
            raise ConfigError(f"{where}: expect_delta가 필요합니다 (예: 1)")
        writes.append(WriteCheckSpec(
            name=str(raw["name"]),
            page=str(raw.get("page", "/")),
            description=str(raw.get("description", "")),
            steps=w_steps,
            query=_parse_query(raw.get("query"), where, allow_api=False),
            expect_delta=int(raw["expect_delta"]),
        ))

    visuals: list[VisualCheckSpec] = []
    for i, raw in enumerate(data.get("visual_checks") or []):
        where = f"visual_checks[{i}]"
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ConfigError(f"{where}: name이 필요합니다")
        severity = str(raw.get("severity", "warn"))
        if severity not in ("warn", "fail"):
            raise ConfigError(f"{where}: severity는 warn 또는 fail이어야 합니다")
        visuals.append(VisualCheckSpec(
            name=str(raw["name"]),
            page=str(raw.get("page", "/")),
            description=str(raw.get("description", "")),
            steps=[Step.from_dict(sd, f"{where}.steps[{j}]")
                   for j, sd in enumerate(raw.get("steps") or [])],
            selector=str(raw.get("selector", "")),
            full_page=bool(raw.get("full_page", False)),
            threshold=float(raw.get("threshold", 0.01)),
            severity=severity,
        ))

    notify: NotifyConfig | None = None
    n_raw = data.get("notify")
    if n_raw:
        if not n_raw.get("webhook_url"):
            raise ConfigError("notify: webhook_url이 필요합니다")
        on = str(n_raw.get("on", "fail"))
        if on not in ("fail", "always"):
            raise ConfigError("notify: on은 fail 또는 always여야 합니다")
        notify = NotifyConfig(
            webhook_url=_expand_env(str(n_raw["webhook_url"]), "notify.webhook_url"),
            on=on,
        )

    a11y = A11yConfig(enabled=bool(_sub(data, "a11y").get("enabled", False)))

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
        write_checks=writes,
        visual_checks=visuals,
        report=report,
        a11y=a11y,
        auth=auth,
        notify=notify,
        baselines_dir=str(data.get("baselines_dir", "baselines")),
        output_dir=str(data.get("output_dir", "runs")),
        config_path=str(p),
    )
