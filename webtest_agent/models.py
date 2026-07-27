"""실행 결과 모델."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal

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
class WriteCheckResult:
    # binary float를 쓰면 큰 정수·금액 소수에서 변화량이 어긋나므로 Decimal로 다룬다.
    # JSON 출력에서는 문자열로 직렬화된다 (report._write_json의 default=str).
    pre: Decimal = Decimal(0)
    post: Decimal = Decimal(0)
    delta: Decimal = Decimal(0)
    expected_delta: int = 0
    matched: bool = False
    note: str = ""


@dataclass
class ViewportResult:
    """한 뷰포트 폭에서의 가로 오버플로 측정."""
    width: int
    height: int
    scroll_width: int = 0
    client_width: int = 0
    overflow_px: int = 0
    ok: bool = True
    offenders: list[str] = field(default_factory=list)   # 화면 밖으로 나간 요소 샘플
    screenshot: str = ""          # 오버플로 시 증거 캡처 (run_dir 기준)


@dataclass
class ResponsiveResult:
    """반응형 점검 — 여러 뷰포트에서 가로 스크롤(오버플로) 발생 여부.

    scrollWidth > clientWidth는 결정적 불변식이므로 판정을 코드가 내린다.
    """
    max_overflow_px: int = 2
    matched: bool = True
    viewports: list[ViewportResult] = field(default_factory=list)
    note: str = ""


@dataclass
class PerfResult:
    """성능 예산 — 페이지 로드 지표를 예산(ms)과 비교. 초과 시 실패(결정적)."""
    metric: str = "load"          # load | dcl | fcp | response
    measured_ms: float = 0.0
    budget_ms: int = 0
    matched: bool = True
    note: str = ""


@dataclass
class VisualResult:
    matched: bool = False
    ratio: float = 0.0            # 달라진 픽셀 비율
    threshold: float = 0.01
    baseline_created: bool = False
    baseline_updated: bool = False
    baseline: str = ""            # 기준선 파일 경로 (CWD 기준)
    current: str = ""             # 이번 실행 캡처 (run_dir 기준 상대)
    diff: str = ""                # diff 이미지 (run_dir 기준 상대)
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
    write_check: WriteCheckResult | None = None
    visual: VisualResult | None = None
    responsive: ResponsiveResult | None = None
    perf: PerfResult | None = None
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
    reason: str = ""


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
    status: str = "running"       # running | passed | failed | infra_error
    error: str = ""               # 실행 불가 사유 (infra_error일 때)
