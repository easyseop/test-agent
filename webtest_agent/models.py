"""실행 결과 모델."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

PASS, WARN, FAIL = "pass", "warn", "fail"
STATUS_LABEL = {PASS: "통과", WARN: "경고", FAIL: "실패"}


@dataclass
class StepResult:
    index: int
    action: str
    description: str
    selector: str | None = None
    value: str | None = None
    status: str = "ok"          # ok | fail
    error: str = ""
    screenshot: str = ""        # run_dir 기준 상대 경로
    duration_ms: int = 0


@dataclass
class HttpFailure:
    url: str
    status: int | None
    detail: str = ""


@dataclass
class DataCheckResult:
    matched: bool = False
    ui_count: int = 0
    db_count: int = 0
    columns: list[str] = field(default_factory=list)
    missing_in_ui: list[list[str]] = field(default_factory=list)     # DB에는 있는데 UI에 없음
    unexpected_in_ui: list[list[str]] = field(default_factory=list)  # UI에는 있는데 DB에 없음
    missing_total: int = 0
    unexpected_total: int = 0
    count_display: int | None = None    # 화면의 건수 표기 값
    count_display_ok: bool | None = None
    note: str = ""


@dataclass
class ScenarioResult:
    name: str
    kind: str                   # sweep_button | sweep_link | data_check
    page: str
    description: str = ""
    status: str = PASS
    reasons: list[str] = field(default_factory=list)
    steps: list[StepResult] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    http_failures: list[HttpFailure] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)
    downloads: list[str] = field(default_factory=list)
    effect: str = ""            # 스윕: 관찰된 효과 요약
    data_check: DataCheckResult | None = None
    video: str = ""
    trace: str = ""
    flaky: bool = False          # 실패 후 재실행에서 통과 → 간헐 의심
    duration_ms: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BlockedElement:
    page: str
    text: str
    pattern: str


@dataclass
class RunMeta:
    title: str
    base_url: str
    app_version: str
    config_path: str
    started_at: str
    finished_at: str = ""
    duration_ms: int = 0
    browser_version: str = ""
    playwright_version: str = ""
    python_version: str = ""
    agent_version: str = ""
