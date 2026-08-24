"""설정(YAML) 로딩과 검증."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .a11y import IMPACT_ORDER
from .safety import (HARD_BLOCK_PATTERNS, merge_avoid_patterns,
                     validate_read_only_sql)


class ConfigError(ValueError):
    """설정 파일 오류."""


_ENV_RX = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# 실행 중 화면에서 뽑은 값을 참조하는 자리표시자. `${VAR}`(설정 로딩 시점의
# 환경변수)와 문법을 일부러 다르게 뒀다 — 비밀 주입과 화면 값 재사용은 성격이
# 다르므로 YAML만 봐도 구분되어야 한다.
_VAR_RX = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_VAR_NAME_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# 2단계 인증 코드. `${VAR}`와 달리 **설정을 읽는 시점에 값을 만들지 않는다** —
# 코드는 30초마다 바뀌므로 로딩 때 만들어 두면 실행 시점에는 이미 만료된다.
# 여기서는 비밀키가 있는지만 확인하고, 실제 코드는 스텝을 실행할 때 만든다.
_TOTP_RX = re.compile(r"\$\{TOTP:([A-Za-z_][A-Za-z0-9_]*)\}")


def var_refs(text: str | None) -> list[str]:
    """문자열이 참조하는 `{{변수}}` 이름들."""
    return [] if text is None else _VAR_RX.findall(text)


def substitute_vars(text: str, values: dict[str, str]) -> str:
    """`{{변수}}`를 실제 값으로 바꾼다. 없는 변수는 KeyError."""
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in values:
            raise KeyError(name)
        return values[name]
    return _VAR_RX.sub(repl, text)


def validate_step_vars(steps: list["Step"], where: str) -> None:
    """변수는 같은 시나리오 안에서 '먼저 추출한 뒤에만' 쓸 수 있다.

    시나리오 사이로 값이 넘어가면 실행 순서에 의존이 생겨 병렬 실행과 독립성이
    깨진다. 그래서 범위를 시나리오 하나로 못박고, 여기서 정적으로 검사한다.
    """
    known: set[str] = set()
    for i, step in enumerate(steps, start=1):
        for name in (var_refs(step.selector) + var_refs(step.value)
                     + var_refs(step.frame)):
            if name not in known:
                raise ConfigError(
                    f"{where}.steps[{i - 1}]: 변수 '{{{{{name}}}}}'를 쓰기 전에 "
                    f"extract·fetch로 먼저 만들어야 합니다 "
                    "(변수는 같은 시나리오 안에서만 유효)")
        # 변수를 만드는 동작은 extract(화면)와 fetch(앱 바깥 정답원) 둘이다.
        if step.store_as:
            if step.store_as in known:
                raise ConfigError(
                    f"{where}.steps[{i - 1}]: 변수 '{step.store_as}'를 두 번 만듭니다 "
                    "(덮어쓰면 앞 단계의 기대값이 조용히 바뀝니다)")
            known.add(step.store_as)


# 값이 증거(리포트·절차서)에 남으면 안 되는 입력을 가리키는 selector 힌트.
# 환경변수 치환 여부와 무관하게 이 셀렉터에 입력한 값은 기록에서 가린다.
# 앞뒤가 알파벳이면 다른 단어의 일부다 (#compass의 'pass', #spinner의 'pin' 등).
_SECRET_SELECTOR_RX = re.compile(
    r"(?<![A-Za-z])(pass(word|wd)?|secret|token|api[-_]?key|otp|pin|credential)"
    r"(?![A-Za-z])",
    re.IGNORECASE,
)
MASK = "***"


# 이 명령이 실행하지 않는 섹션의 미설정 환경변수를 모아두는 곳.
# None이면 평소대로 즉시 오류(fail-closed). 목록이면 이름만 적어두고 `${VAR}`를
# 그대로 남긴다 — 치환되지 않은 채로 남으므로 진짜 값으로 오인될 수 없다.
_DEFERRED_ENV: list[str] | None = None


def _expand_env(value: str, where: str) -> str:
    """'${VAR}' 형태를 환경변수로 치환 — 비밀번호·DB 접속정보의 평문 저장 방지용."""
    def repl(m: re.Match) -> str:
        name = m.group(1)
        if name not in os.environ:
            if _DEFERRED_ENV is not None:
                # discover처럼 이 섹션을 실행하지 않는 명령. 여기서 막으면 첫
                # 발디딤(셀렉터 수집)이 잠겨 진행 자체가 불가능해진다.
                _DEFERRED_ENV.append(f"{name} ({where})")
                return m.group(0)
            raise ConfigError(f"{where}: 환경변수 {name}가 설정되어 있지 않습니다 (${{{name}}} 치환 실패)")
        return os.environ[name]
    return _ENV_RX.sub(repl, value)


def totp_refs(text: str | None) -> list[str]:
    """`${TOTP:이름}`이 참조하는 환경변수 이름들."""
    return [] if text is None else _TOTP_RX.findall(text)


def _check_totp_refs(value: str, where: str) -> None:
    """비밀키가 준비됐는지 로딩 때 미리 본다.

    실행 도중 로그인 단계에서야 "환경변수가 없다"를 알면 이미 브라우저를 띄우고
    시간을 쓴 뒤다. 여기서 먼저 세운다.
    """
    from .totp import TotpError, generate
    for name in totp_refs(value):
        if name not in os.environ:
            raise ConfigError(
                f"{where}: 환경변수 {name}가 설정되어 있지 않습니다 "
                f"(${{TOTP:{name}}} — 2단계 인증 비밀키)")
        try:
            generate(os.environ[name])
        except TotpError as err:
            raise ConfigError(f"{where}: {name} — {err}") from err


def _is_secret_step(raw_value: str | None, selector: str | None) -> bool:
    """이 스텝의 값을 증거에 남기면 안 되는가.

    ① `${VAR}` 치환이 일어났다 — 설정에 평문으로 두지 않으려 한 값이므로 비밀로 본다.
    ② selector가 비밀번호·토큰 입력을 가리킨다 — 치환을 쓰지 않았어도 가린다.
    """
    if raw_value is not None and (_ENV_RX.search(raw_value) or _TOTP_RX.search(raw_value)):
        return True
    return bool(selector and _SECRET_SELECTOR_RX.search(selector))


STEP_ACTIONS = {
    "goto", "click", "fill", "select", "check", "press", "wait_for", "wait_ms",
    "assert_visible", "assert_text", "assert_text_exact", "assert_url",
    "assert_not_visible", "assert_not_text",
    "extract",
    # 테스트용 메일함 같은 '앱 바깥 정답원'에서 값을 가져온다
    "fetch",
    # 새 창 — 결제창·소셜 로그인처럼 별도 창으로 뜨는 흐름
    "wait_popup", "close_popup",
    # 마우스·파일 조작
    "upload", "hover", "scroll_to", "drag",
    # 한글 조합 입력 — fill은 값을 통째로 꽂아 조합 자체가 일어나지 않는다.
    # 조합 중에 글자가 유실되는 결함은 이 액션으로만 재현된다.
    "type_ime",
}
_NEEDS_SELECTOR = {"click", "fill", "select", "check", "press", "wait_for",
                   "assert_visible", "assert_text", "assert_text_exact",
                   "assert_not_visible", "assert_not_text",
                   "extract",
                   "upload", "hover", "scroll_to", "drag"}
_NEEDS_VALUE = {"goto", "fill", "select", "press", "wait_ms",
                "assert_text", "assert_text_exact", "assert_url",
                "assert_not_text",
                "upload", "drag", "fetch"}
# 새 창 안에서는 프레임 지정이 의미가 없거나(창 전환 자체) 대상이 없다.
_NO_FRAME = {"wait_popup", "close_popup", "goto", "wait_ms", "assert_url", "fetch"}


@dataclass
class Step:
    action: str
    selector: str | None = None
    value: str | None = None
    secret: bool = False        # True면 값을 리포트·절차서에 남기지 않는다
    store_as: str | None = None  # extract 전용 — 뽑은 값을 담을 변수 이름
    pattern: str | None = None   # extract 전용 — 뽑은 텍스트에서 1개 그룹만 취함
    # 이 스텝이 조작할 iframe. 중첩은 'iframe#a >> iframe#b'.
    # 결제창·주소검색처럼 화면 안의 다른 문서를 다룰 때 쓴다.
    frame: str | None = None
    # fetch 전용 — JSON 응답에서 꺼낼 위치. 'html' 또는 'messages.0.html'.
    # 테스트 메일함은 대개 JSON을 주는데, 그 안의 따옴표가 이스케이프돼 있어
    # 본문에 정규식을 바로 걸면 맞지 않는다.
    json_path: str | None = None
    # fetch 전용 — 이 상태코드가 나와야 정상. 권한 검증("토큰 없이 부르면 401")처럼
    # 오류 응답이 곧 기대값인 경우가 있다. 이게 없으면 400 이상은 전부 실패로
    # 처리돼, 정상 동작을 확인하는 수단이 아예 없다.
    expect_status: int | None = None

    @property
    def log_value(self) -> str | None:
        """증거에 기록해도 되는 값 (비밀이면 마스킹)."""
        if self.value is None:
            return None
        return MASK if self.secret else self.value

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

        store_as = d.get("store_as")
        pattern = d.get("pattern")
        expect_status = d.get("expect_status")
        if expect_status is not None:
            if action != "fetch":
                raise ConfigError(f"{where}: expect_status는 fetch에서만 쓸 수 있습니다")
            try:
                expect_status = int(expect_status)
            except (TypeError, ValueError):
                raise ConfigError(
                    f"{where}.expect_status: 정수여야 합니다 (예: 401)") from None
            if not 100 <= expect_status <= 599:
                raise ConfigError(
                    f"{where}.expect_status: HTTP 상태코드 범위(100~599)를 벗어났습니다")

        if action in ("extract", "fetch"):
            # 상태코드만 확인하는 fetch는 본문을 담을 필요가 없다.
            if not store_as and not (action == "fetch" and expect_status is not None):
                raise ConfigError(f"{where}: action '{action}'에는 store_as가 필요합니다")
            if store_as:
                store_as = str(store_as)
                if not _VAR_NAME_RX.fullmatch(store_as):
                    raise ConfigError(
                        f"{where}: store_as '{store_as}'는 영문자·숫자·밑줄만 쓸 수 있습니다")
            else:
                # str(None)을 그대로 쓰면 'None'이라는 이름의 변수가 생긴다.
                store_as = None
            if pattern is not None:
                pattern = str(pattern)
                try:
                    compiled = re.compile(pattern)
                except re.error as err:
                    raise ConfigError(f"{where}.pattern: 정규식 오류 — {err}") from err
                if compiled.groups != 1:
                    raise ConfigError(
                        f"{where}.pattern: 그룹이 정확히 1개여야 합니다 "
                        f"(현재 {compiled.groups}개). 예: '주문번호 ([0-9]+)'")
            if action == "fetch" and not str(value).lower().startswith(("http://", "https://", "/")):
                raise ConfigError(
                    f"{where}: fetch의 value는 http(s) 주소이거나 '/'로 시작하는 "
                    "경로여야 합니다")
        else:
            if store_as is not None:
                raise ConfigError(f"{where}: store_as는 action 'extract'·'fetch'에서만 씁니다")
            if pattern is not None:
                raise ConfigError(f"{where}: pattern은 action 'extract'·'fetch'에서만 씁니다")

        json_path = d.get("json_path")
        if json_path is not None:
            if action != "fetch":
                raise ConfigError(f"{where}: json_path는 action 'fetch'에서만 씁니다")
            json_path = str(json_path).strip()
            if not json_path:
                raise ConfigError(f"{where}.json_path: 빈 값은 쓸 수 없습니다")

        frame = d.get("frame")
        if frame is not None:
            frame = str(frame).strip()
            if not frame:
                raise ConfigError(f"{where}.frame: 빈 값은 쓸 수 없습니다")
            if action in _NO_FRAME:
                raise ConfigError(
                    f"{where}: action '{action}'에는 frame을 쓸 수 없습니다 "
                    f"(프레임 안에서 의미가 없는 동작)")

        raw_value = None if value is None else str(value)
        secret = _is_secret_step(raw_value, selector)
        if raw_value is not None:
            _check_totp_refs(raw_value, where)
            value = _expand_env(raw_value, where)
        return cls(action=action, selector=selector, value=value, secret=secret,
                   store_as=store_as, pattern=pattern, frame=frame,
                   json_path=json_path, expect_status=expect_status)


@dataclass
class TargetConfig:
    base_url: str
    settle_ms: int = 400
    nav_timeout_ms: int = 10000
    scenario_timeout_ms: int = 60000
    run_timeout_ms: int = 1800000
    app_version: str = ""
    browser: str = "chromium"    # chromium · firefox · webkit
    flaky_recheck: bool = False  # 실패 시 1회 재실행해 간헐(flaky) 여부 표시
    ignore_http_error_patterns: list[str] = field(default_factory=list)
    # 브라우저 로케일. 화면 표기 언어를 정하므로 기대 텍스트와 반드시 같아야 한다.
    # 정답원이 영어 enum(Failed/Success)을 주는 API라면 여기를 en-US로 두는 편이
    # 값 매핑을 손으로 적는 것보다 정확하다 — 번역표를 우리가 관리하지 않게 된다.
    locale: str = "ko-KR"
    # 무시할 콘솔 에러 메시지 정규식.
    #
    # ignore_http_error_patterns는 발생 위치 URL로 거른다. 그런데 프레임워크가
    # 뿜는 경고(i18next "key not found" 등)에는 위치 URL이 없어서 그 방식으로는
    # 영원히 걸러지지 않는다. 콘솔 에러 1건이면 시나리오가 실패하므로, 무해한
    # 잡음을 뿜는 앱은 화면이 멀쩡해도 통과할 수 없었다. 메시지 본문으로 거른다.
    ignore_console_patterns: list[str] = field(default_factory=list)


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
    # 화면 표기 → 정답원 표기 변환 (예: {"실패": "Failed"}). 상세는 compare() 참고.
    value_map: dict[str, str] = field(default_factory=dict)


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
    # 화면에서 뽑은 값을 SQL에 넘기는 유일한 통로. 값은 바인딩 파라미터로만
    # 전달하며 SQL 본문에는 절대 이어붙이지 않는다 — 추출값이 SQL 조각이어도
    # 명령이 아니라 데이터로만 취급되게 하기 위해서다.
    params: dict[str, str] = field(default_factory=dict)


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
class PostCondition:
    """쓰기 후 확인할 값 하나. sql은 단일 수치를 돌려줘야 한다."""
    sql: str
    expected_delta: int
    params: dict[str, str] = field(default_factory=dict)
    label: str = ""      # 리포트에서 어느 조건인지 알아보게 하는 이름


@dataclass
class WriteCheckSpec:
    """쓰기(상태 전이) 검증 — 스텝 실행 전후의 DB 값 변화량을 검증한다.

    사후조건은 **여러 개**를 걸 수 있다. 건수 하나만 보면 "주문이 1건 늘었다"는
    확인되지만, 품목이 같이 저장됐는지·감사 로그가 남았는지·엉뚱한 표가 같이
    늘지 않았는지는 확인되지 않는다. 그런 검사는 통과해도 증명하는 게 거의 없다.

    반드시 스테이징/시드 DB에서만 사용할 것.
    """
    name: str
    page: str = "/"
    description: str = ""
    steps: list[Step] = field(default_factory=list)
    query: QuerySpec | None = None
    # 하위호환 — 사후조건이 하나뿐일 때 쓰던 형태. 로딩 시 expect로 정규화되므로
    # 실행·판정·리포트는 expect 하나만 본다(경로가 둘이면 한쪽만 고치는 사고가 난다).
    expect_delta: int = 0
    expect: list[PostCondition] = field(default_factory=list)


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
class ResponsiveCheckSpec:
    """반응형 점검 — 여러 뷰포트 폭에서 가로 오버플로(가로 스크롤) 발생 여부.

    scrollWidth > clientWidth + max_overflow_px 이면 실패. 판정은 결정적 코드가 한다.
    """
    name: str
    page: str = "/"
    description: str = ""
    steps: list[Step] = field(default_factory=list)
    viewports: list[int] = field(default_factory=lambda: [375, 768, 1280])
    height: int = 900
    max_overflow_px: int = 2      # 스크롤바 등 미세 오차 허용


_PERF_METRICS = {"load", "dcl", "fcp", "response"}


@dataclass
class PerfCheckSpec:
    """성능 예산 — 페이지 로드 지표가 예산(ms) 이내인지 검사(결정적).

    metric: load(loadEventEnd 기본) | dcl(DOMContentLoaded) | fcp(First Contentful
    Paint) | response(responseEnd).
    """
    name: str
    page: str = "/"
    description: str = ""
    steps: list[Step] = field(default_factory=list)
    metric: str = "load"
    budget_ms: int = 3000


@dataclass
class NotifyConfig:
    """실행 후 웹훅 알림 (Slack Incoming Webhook 호환 JSON POST)."""
    webhook_url: str
    on: str = "fail"              # fail(기본: 실패 있을 때만) | always


@dataclass
class LinkCheckConfig:
    """깨진 링크 검사 — 크롤링으로 발견한 링크의 HTTP 상태를 전수 점검.

    include_external=False면 같은 출처(same-origin) 링크만 본다(외부 사이트의
    일시적 오류를 우리 앱 실패로 보지 않도록). severity로 판정 게이팅.
    """
    enabled: bool = False
    include_external: bool = False
    severity: str = "fail"        # info | warn | fail
    timeout_ms: int = 10000
    ignore_patterns: list[str] = field(default_factory=list)


@dataclass
class A11yConfig:
    """접근성 점검 — 크롤링한 페이지 대상.

    engine: axe(기본, 동봉한 axe-core 4.x — WCAG 2.1 AA 규칙 100여 개) |
            builtin(간이 내장 규칙 9종 — axe를 못 쓰는 환경의 대비책)
    severity: info(기본, 정보성 — 판정 영향 없음) | warn(경고) | fail(실패)
    min_impact: 게이팅에 셀 최소 심각도(minor/moderate/serious/critical).
        severity가 warn/fail일 때만 의미가 있다. 규칙 100여 개를 한꺼번에
        실패로 걸면 기존 운영이 전부 빨개지므로, 심각한 것부터 올리라는 뜻.
    rules_exclude: 끄고 싶은 axe 규칙 id 목록(예: color-contrast).

    기본이 info인 이유: 도입 첫날 빨간 화면을 보면 팀은 점검 자체를 꺼버린다.
    먼저 보이게 하고, 합의된 규칙부터 fail로 승격하는 편이 실제로 고쳐진다.
    """
    enabled: bool = False
    engine: str = "axe"
    severity: str = "info"
    min_impact: str = "minor"
    rules_exclude: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)   # 비우면 WCAG 2.1 A/AA 기본


@dataclass
class AuthConfig:
    """로그인 시나리오 — 실행 시작 시 1회 수행 후 세션(storage_state)을 전 시나리오가 재사용.

    per_context=True면 시나리오마다 컨텍스트 안에서 로그인을 다시 수행한다.

    storage_state는 기본적으로 쿠키와 localStorage만 담는다. 토큰을
    **IndexedDB**에 두는 SPA(OpenMetadata 등)는 그대로면 세션이 옮겨지지 않아,
    로그인은 성공해도 검사할 화면마다 미인증 상태가 된다(로그인 화면으로 튕김).
    증상이 "로그인이 안 된다"로 보여서 원인을 찾기 어렵다.

    indexed_db=True(기본)면 IndexedDB까지 담는다. Chromium·Firefox·WebKit
    3엔진 실측에서 이 옵션이 있어야만 토큰이 살아남았다. 끄면 파일은 작아지지만
    IndexedDB를 쓰는 앱에서 위 증상이 되살아난다.

    토큰을 sessionStorage나 메모리에만 두는 앱은 여전히 옮겨지지 않는다 —
    그런 앱은 per_context=True로 매번 다시 로그인하는 편이 느리지만 정확하다.

    **인증 상태 파일은 비밀값이다.** IndexedDB를 담으면 토큰이 그대로 들어가므로
    0600 권한·기본 자동 삭제 정책이 특히 중요하다.
    """
    steps: list[Step] = field(default_factory=list)
    per_context: bool = False
    indexed_db: bool = True


def _mask_patterns(raw) -> list[str]:
    """리포트 본문 마스킹 패턴. 잘못된 정규식은 실행 도중이 아니라 여기서 잡는다."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ConfigError("report.mask_patterns: 목록이어야 합니다")
    out: list[str] = []
    for i, item in enumerate(raw):
        text = str(item)
        try:
            re.compile(text)
        except re.error as err:
            raise ConfigError(
                f"report.mask_patterns[{i}]: 정규식이 잘못됐습니다 ({err})") from err
        out.append(text)
    return out


def _trace_mode(raw) -> str | bool:
    """trace: true | false | on-failure."""
    if isinstance(raw, bool):
        return raw
    text = str(raw).strip().lower()
    if text in ("on-failure", "on_failure"):
        return "on-failure"
    if text in ("true", "yes", "1"):
        return True
    if text in ("false", "no", "0"):
        return False
    raise ConfigError(
        f"report.trace는 true·false·on-failure 중 하나여야 합니다 (받은 값: {raw!r})")


@dataclass
class ReportConfig:
    """리포트·증적 설정.

    trace는 되감기 기록이다. 실패 원인을 찾을 때 가장 강력하지만, **네트워크
    요청이 통째로 들어간다.** 로그인한 세션으로 검사하면 Authorization 헤더의
    토큰과 쿠키가 그대로 담긴다 — 실제로 확인했다. 목 데이터 환경이라도 토큰은
    진짜로 발급된 값이라, trace 파일을 공유하면 자격증명을 함께 넘기는 셈이다.

    그래서 기본을 on-failure로 둔다. 실패한 시나리오만 남기면 파일 수가 줄고,
    통과한 실행을 그대로 공유해도 새는 것이 없다. 전부 남기려면 true로 둔다.
    """
    title: str = "웹 자동 테스트 리포트"
    video: bool = True
    trace: str = "on-failure"     # "on-failure" | true | false
    mask_selectors: list[str] = field(default_factory=list)  # 스크린샷에서 가릴 요소(개인정보 등)
    # 리포트 **본문**에서 가릴 문자열 패턴(정규식). mask_selectors는 화면 캡처만
    # 덮는다. 실제 데이터를 대조하면 불일치 표본·추출값·단언 메시지에 이름·전화번호
    # 같은 값이 그대로 남는데, 캡처만 가리면 가려졌다고 착각하게 된다.
    #
    # 판정이 끝난 뒤 리포트를 쓰는 시점에만 적용한다. 대조 전에 가리면 서로 다른
    # 값이 같은 ***로 바뀌어 불일치가 일치로 둔갑한다.
    mask_patterns: list[str] = field(default_factory=list)

    @property
    def trace_enabled(self) -> bool:
        """기록을 시작할지. on-failure도 일단 켜야 실패했을 때 남길 수 있다."""
        return self.trace is not False

    @property
    def trace_only_on_failure(self) -> bool:
        return self.trace == "on-failure"


@dataclass
class WriteResetConfig:
    """쓰기 검증 전 데이터 되돌리기.

    주문을 넣으면 데이터가 남아 다음 실행의 건수 검사가 거짓으로 깨진다.
    초기화 없이는 쓰기 검증을 반복할 수 없다.

    데이터를 지우는 동작이므로 `--allow-write-checks` 승인 없이는 실행하지
    않는다. 실패는 판정이 아니라 실행 불가(infra_error)다 — 초기화가 안 된
    상태에서 합격·불합격을 말하면 안 되기 때문이다.
    """
    http_url: str = ""
    http_method: str = "POST"
    http_headers: dict[str, str] = field(default_factory=dict)
    command: list[str] = field(default_factory=list)
    timeout_ms: int = 30000

    @property
    def describe(self) -> str:
        if self.http_url:
            return f"{self.http_method} {self.http_url}"
        return " ".join(self.command)


@dataclass
class AgentConfig:
    target: TargetConfig
    crawl: CrawlConfig
    sweep: SweepConfig
    data_checks: list[DataCheckSpec]
    spec_checks: list[SpecCheckSpec]
    write_checks: list[WriteCheckSpec]
    visual_checks: list[VisualCheckSpec]
    responsive_checks: list[ResponsiveCheckSpec]
    perf_checks: list[PerfCheckSpec]
    report: ReportConfig
    a11y: A11yConfig = field(default_factory=A11yConfig)
    link_check: LinkCheckConfig = field(default_factory=LinkCheckConfig)
    auth: AuthConfig | None = None
    notify: NotifyConfig | None = None
    write_reset: WriteResetConfig | None = None
    baselines_dir: str = "baselines"
    output_dir: str = "runs"
    # defer_check_env로 넘긴 미설정 환경변수 목록. 비어 있지 않으면 이 설정은
    # 아직 `run` 할 수 없다 — 호출부가 반드시 사용자에게 알려야 한다.
    deferred_env: list[str] = field(default_factory=list)
    config_path: str = ""


def _sub(data: dict, key: str) -> dict:
    v = data.get(key) or {}
    if not isinstance(v, dict):
        raise ConfigError(f"'{key}' 섹션은 매핑이어야 합니다")
    return v


def _browser_name(value) -> str:
    """엔진 이름 검증. 오타를 조용히 chromium으로 넘기면 어느 엔진으로 판정했는지
    아무도 모르게 된다 — 설정 단계에서 막는다."""
    from .browser import UnsupportedEngineError, normalize_engine
    try:
        return normalize_engine(None if value is None else str(value))
    except UnsupportedEngineError as err:
        raise ConfigError(f"target.browser: {err}") from err


def _regex_list(values, where: str) -> list[str]:
    patterns = [str(value) for value in (values or [])]
    for index, pattern in enumerate(patterns):
        try:
            re.compile(pattern)
        except re.error as err:
            raise ConfigError(f"{where}[{index}]: 잘못된 정규식 '{pattern}' ({err})") from err
    return patterns


def _parse_steps(raw_steps, where: str) -> list[Step]:
    """스텝 목록 파싱 + 변수 사용 순서 검증.

    파싱과 검증을 한 곳에 묶어 둔다. 시나리오 종류가 늘 때 검증을 빠뜨리면
    "쓰기 전에 추출" 규칙이 조용히 무너지기 때문이다.
    """
    steps = [Step.from_dict(sd, f"{where}.steps[{j}]")
             for j, sd in enumerate(raw_steps or [])]
    validate_step_vars(steps, where)
    return steps


def _validate_query_vars(query: QuerySpec | None, steps: list[Step], where: str) -> None:
    """정답 쿼리 파라미터가 참조하는 변수도 스텝에서 먼저 추출돼야 한다."""
    if query is None:
        return
    extracted = {s.store_as for s in steps if s.store_as}
    for name, raw in query.params.items():
        for ref in var_refs(raw):
            if ref not in extracted:
                raise ConfigError(
                    f"{where}.query.params.{name}: 변수 '{{{{{ref}}}}}'를 이 시나리오의 "
                    "스텝에서 extract·fetch로 만들지 않았습니다")


def _parse_post_conditions(raw_expect, query: QuerySpec, steps: list[Step],
                           where: str) -> list[PostCondition]:
    """사후조건 목록을 만든다. 형태가 하나든 여럿이든 여기서 하나로 모은다.

    실행·판정·리포트가 보는 경로를 하나로 두기 위해서다. 경로가 둘이면 한쪽만
    고치는 사고가 나고, 그 사고는 대개 '검사가 조용히 약해지는' 쪽으로 난다.
    """
    if raw_expect is None:
        # 예전 형태: query.sql 하나 + expect_delta. 조건 1개짜리로 정규화한다.
        return []
    if not isinstance(raw_expect, list) or not raw_expect:
        raise ConfigError(f"{where}.expect: 사후조건 목록이 필요합니다 (최소 1개)")
    if query.sql:
        raise ConfigError(
            f"{where}: expect를 쓰면 query.sql을 두지 않습니다 "
            "(어느 쪽이 검사인지 갈립니다). query에는 db만 남기세요")
    extracted = {s.store_as for s in steps if s.store_as}
    conditions: list[PostCondition] = []
    for j, item in enumerate(raw_expect):
        spot = f"{where}.expect[{j}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{spot}: 매핑이어야 합니다 (예: {{sql: ..., delta: 1}})")
        sql = item.get("sql")
        if not isinstance(sql, str) or not sql.strip():
            raise ConfigError(f"{spot}: sql이 필요합니다")
        if var_refs(sql):
            raise ConfigError(
                f"{spot}.sql: SQL 본문에는 {{{{변수}}}}를 쓸 수 없습니다. "
                "params로 넘기고 SQL에서는 :이름 으로 받으세요 "
                "(값을 문자열로 이어붙이면 주입 위험이 생깁니다)")
        try:
            validate_read_only_sql(sql)
        except ValueError as err:
            raise ConfigError(f"{spot}.sql: {err}") from err
        if "delta" not in item:
            raise ConfigError(f"{spot}: delta가 필요합니다 (예: 1, 변하지 않아야 하면 0)")
        try:
            delta = int(item["delta"])
        except (TypeError, ValueError):
            raise ConfigError(f"{spot}.delta: 정수여야 합니다") from None
        params = {str(k): str(v) for k, v in (item.get("params") or {}).items()}
        for name, rawval in params.items():
            for ref in var_refs(rawval):
                if ref not in extracted:
                    raise ConfigError(
                        f"{spot}.params.{name}: 변수 '{{{{{ref}}}}}'를 이 시나리오의 "
                        "스텝에서 extract·fetch로 만들지 않았습니다")
        conditions.append(PostCondition(
            sql=sql, expected_delta=delta, params=params,
            label=str(item.get("label", "") or f"조건 {j + 1}"),
        ))
    return conditions


def post_conditions(spec: WriteCheckSpec) -> list[PostCondition]:
    """이 쓰기 검증이 확인할 사후조건 전부. 예전 형태도 여기서 같은 모양이 된다."""
    if spec.expect:
        return spec.expect
    return [PostCondition(
        sql=spec.query.sql,
        expected_delta=spec.expect_delta,
        params=dict(spec.query.params or {}),
        label="조건 1",
    )]


def _parse_query(q_raw, where: str, allow_api: bool,
                 require_sql: bool = True) -> QuerySpec:
    """query 파싱 — (db+sql) 또는 api 중 정확히 하나.

    require_sql=False는 사후조건을 expect에 따로 적는 쓰기 검증 전용이다.
    그때 query는 접속 정보(db)만 들고 있고 검사는 expect가 갖는다.
    """
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
    if not q_raw.get("db"):
        raise ConfigError(f"{where}: query.db가 필요합니다")
    if require_sql and not q_raw.get("sql"):
        raise ConfigError(f"{where}: query.db와 query.sql이 필요합니다")
    if not q_raw.get("sql"):
        return QuerySpec(db=_expand_env(str(q_raw["db"]), f"{where}.query.db"),
                         order_matters=bool(q_raw.get("order_matters", False)))
    sql = str(q_raw["sql"])
    if var_refs(sql):
        raise ConfigError(
            f"{where}.query.sql: SQL 본문에는 {{{{변수}}}}를 쓸 수 없습니다. "
            "query.params로 넘기고 SQL에서는 :이름 으로 받으세요 "
            "(값을 문자열로 이어붙이면 주입 위험이 생깁니다)")
    try:
        validate_read_only_sql(sql)
    except ValueError as err:
        raise ConfigError(f"{where}.query.sql: {err}") from err

    params_raw = q_raw.get("params") or {}
    if not isinstance(params_raw, dict):
        raise ConfigError(f"{where}.query.params: 매핑이어야 합니다 (예: {{order_no: '{{{{order_no}}}}'}})")
    params: dict[str, str] = {}
    for key, raw in params_raw.items():
        name = str(key)
        if not _VAR_NAME_RX.fullmatch(name):
            raise ConfigError(
                f"{where}.query.params: 파라미터 이름 '{name}'은 영문자·숫자·밑줄만 쓸 수 있습니다")
        if f":{name}" not in sql:
            raise ConfigError(
                f"{where}.query.params: '{name}'을 넘기지만 SQL에 :{name} 자리가 없습니다")
        params[name] = _expand_env(str(raw), f"{where}.query.params.{name}")

    return QuerySpec(
        db=_expand_env(str(q_raw["db"]), f"{where}.query.db"),
        sql=sql,
        order_matters=bool(q_raw.get("order_matters", False)),
        params=params,
    )


# foreach가 한 번에 만들어 낼 수 있는 시나리오 수 상한. 실수로 수천 행짜리
# 파일을 물리면 실행이 몇 시간이 되므로, 조용히 도는 대신 설정 오류로 세운다.
MAX_FOREACH_ITEMS = 200

# 검사 목록 — foreach 전개는 여기 모두에 똑같이 적용한다.
_CHECK_KEYS = ("data_checks", "spec_checks", "write_checks",
               "visual_checks", "responsive_checks", "perf_checks")


def _substitute_one_var(node, name: str, value: str):
    """설정 트리 전체에서 `{{name}}`만 값으로 바꾼다.

    다른 이름의 `{{변수}}`(extract로 뽑는 값)는 건드리지 않는다. 그래야
    foreach와 화면 값 추출을 한 시나리오에서 같이 쓸 수 있다.
    """
    if isinstance(node, str):
        return re.sub(r"\{\{\s*" + re.escape(name) + r"\s*\}\}", value, node)
    if isinstance(node, list):
        return [_substitute_one_var(item, name, value) for item in node]
    if isinstance(node, dict):
        return {key: _substitute_one_var(item, name, value) for key, item in node.items()}
    return node


def _foreach_items(spec: dict, where: str, config_dir: Path) -> list[str]:
    """반복할 값 목록 — 인라인 목록이거나 JSON 파일이다."""
    inline = spec.get("in")
    in_file = spec.get("in_file")
    if bool(inline is None) == bool(in_file is None):
        raise ConfigError(f"{where}.foreach: in 또는 in_file 중 정확히 하나를 지정하세요")

    if in_file is not None:
        raw_path = Path(str(in_file))
        if raw_path.is_absolute():
            raise ConfigError(
                f"{where}.foreach.in_file: 설정 파일 폴더 기준 상대경로여야 합니다")
        resolved = (config_dir / raw_path).resolve()
        if not resolved.is_relative_to(config_dir):
            raise ConfigError(
                f"{where}.foreach.in_file: 설정 파일 폴더를 벗어납니다 ({in_file})")
        if not resolved.is_file():
            raise ConfigError(f"{where}.foreach.in_file: 파일이 없습니다 ({resolved})")
        try:
            inline = json.loads(resolved.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as err:
            raise ConfigError(f"{where}.foreach.in_file: 읽을 수 없습니다 — {err}") from err

    if not isinstance(inline, list) or not inline:
        raise ConfigError(f"{where}.foreach: 값 목록은 비어 있지 않은 배열이어야 합니다")
    if len(inline) > MAX_FOREACH_ITEMS:
        raise ConfigError(
            f"{where}.foreach: 값이 {len(inline)}개입니다. 한 번에 만들 수 있는 "
            f"시나리오는 {MAX_FOREACH_ITEMS}개까지입니다")
    for item in inline:
        if isinstance(item, (dict, list)):
            raise ConfigError(
                f"{where}.foreach: 값은 문자열·숫자여야 합니다 (중첩 구조는 쓸 수 없습니다)")
    return [str(item) for item in inline]


def _expand_foreach(raw_list, where_prefix: str, config_dir: Path) -> list:
    """foreach가 붙은 검사를 N개의 독립 시나리오로 미리 펼친다.

    실행 중에 반복하지 않고 계획 단계에서 펼치는 이유는 결정성이다. 펼쳐 두면
    리포트에 각각 한 줄로 남고, 병렬 실행과 직전 실행 비교도 개별로 된다.
    """
    expanded: list = []
    for index, raw in enumerate(raw_list or []):
        where = f"{where_prefix}[{index}]"
        spec = raw.get("foreach") if isinstance(raw, dict) else None
        if not spec:
            expanded.append(raw)
            continue
        if not isinstance(spec, dict) or not spec.get("var"):
            raise ConfigError(f"{where}.foreach: var가 필요합니다 (예: {{var: item, in: [a, b]}})")
        name = str(spec["var"])
        if not _VAR_NAME_RX.fullmatch(name):
            raise ConfigError(
                f"{where}.foreach.var: '{name}'은 영문자·숫자·밑줄만 쓸 수 있습니다")

        body = {key: value for key, value in raw.items() if key != "foreach"}
        seen_names: set[str] = set()
        for item in _foreach_items(spec, where, config_dir):
            one = _substitute_one_var(body, name, item)
            one_name = str(one.get("name", ""))
            if one_name in seen_names:
                raise ConfigError(
                    f"{where}.foreach: 펼친 시나리오 이름이 '{one_name}'로 겹칩니다. "
                    f"name에 {{{{{name}}}}}를 넣어 서로 다르게 하세요")
            seen_names.add(one_name)
            expanded.append(one)
    return expanded


def load_config(path: str | Path,
                defer_check_env: bool = False) -> AgentConfig:
    """설정을 읽고 검증한다.

    defer_check_env=True면 **검사 섹션**(data_checks 등)의 미설정 환경변수를
    오류가 아니라 기록으로 넘긴다. `discover`처럼 그 섹션을 실행하지 않는 명령
    전용이다 — 실행하지도 않을 `${VAR}` 때문에 첫 발디딤(셀렉터 수집)이 막히면
    사용자는 아무 데도 갈 수 없다. 넘긴 이름은 `deferred_env`에 담긴다.

    auth는 이 완화의 대상이 아니다. discover도 로그인을 수행하므로, 비밀번호가
    비면 `${VAR}`가 그대로 입력돼 로그인이 조용히 실패한다 — 즉시 막는 게 맞다.
    """
    global _DEFERRED_ENV
    # 진입할 때마다 초기화한다. 앞선 호출이 파싱 도중 예외로 죽으면 완화 모드가
    # 전역에 남아, 그다음 `run`이 fail-closed를 잃은 채로 통과할 수 있다.
    _DEFERRED_ENV = None
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"설정 파일을 찾을 수 없습니다: {p}")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError("설정 파일 최상위는 매핑이어야 합니다")

    # foreach는 다른 어떤 파싱보다 먼저 펼친다. 펼친 뒤에는 평범한 검사 목록이
    # 되므로 아래 로직 전부가 반복을 몰라도 된다.
    config_dir = p.resolve().parent
    for key in _CHECK_KEYS:
        if data.get(key):
            data[key] = _expand_foreach(data[key], key, config_dir)

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
        browser=_browser_name(t.get("browser")),
        flaky_recheck=bool(t.get("flaky_recheck", False)),
        ignore_http_error_patterns=_regex_list(
            t.get("ignore_http_error_patterns"),
            "target.ignore_http_error_patterns",
        ),
        locale=str(t.get("locale", "ko-KR")),
        ignore_console_patterns=_regex_list(
            t.get("ignore_console_patterns"),
            "target.ignore_console_patterns",
        ),
    )
    if target.run_timeout_ms <= 0:
        raise ConfigError("target.run_timeout_ms는 1ms 이상이어야 합니다")
    if not target.locale.strip():
        raise ConfigError("target.locale은 비워 둘 수 없습니다 (예: ko-KR, en-US)")

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
    _DEFERRED_ENV = [] if defer_check_env else None
    for i, raw in enumerate(data.get("data_checks") or []):
        where = f"data_checks[{i}]"
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ConfigError(f"{where}: name이 필요합니다")
        name = str(raw["name"])
        steps = _parse_steps(raw.get("steps"), where)

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
            value_map={str(k): str(v)
                       for k, v in (ut_raw.get("value_map") or {}).items()},
        )

        query = _parse_query(raw.get("query"), where, allow_api=True)
        _validate_query_vars(query, steps, where)

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
        spec_steps = _parse_steps(raw.get("steps"), where)
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
        w_steps = _parse_steps(raw.get("steps"), where)
        if not w_steps:
            raise ConfigError(f"{where}: steps가 최소 1개 필요합니다")
        raw_expect = raw.get("expect")
        if raw_expect is not None and "expect_delta" in raw:
            raise ConfigError(
                f"{where}: expect와 expect_delta를 함께 쓸 수 없습니다 "
                "(사후조건이 여러 개면 expect만 씁니다)")
        if raw_expect is None and "expect_delta" not in raw:
            raise ConfigError(f"{where}: expect 또는 expect_delta가 필요합니다 (예: 1)")
        w_query = _parse_query(raw.get("query"), where, allow_api=False,
                               require_sql=raw_expect is None)
        _validate_query_vars(w_query, w_steps, where)
        w_expect = _parse_post_conditions(raw_expect, w_query, w_steps, where)
        writes.append(WriteCheckSpec(
            name=str(raw["name"]),
            page=str(raw.get("page", "/")),
            description=str(raw.get("description", "")),
            steps=w_steps,
            query=w_query,
            expect_delta=int(raw["expect_delta"]) if "expect_delta" in raw else 0,
            expect=w_expect,
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
            steps=_parse_steps(raw.get("steps"), where),
            selector=str(raw.get("selector", "")),
            full_page=bool(raw.get("full_page", False)),
            threshold=float(raw.get("threshold", 0.01)),
            severity=severity,
        ))

    responsives: list[ResponsiveCheckSpec] = []
    for i, raw in enumerate(data.get("responsive_checks") or []):
        where = f"responsive_checks[{i}]"
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ConfigError(f"{where}: name이 필요합니다")
        vp_raw = raw.get("viewports")
        if vp_raw is not None:
            if not isinstance(vp_raw, list) or not vp_raw:
                raise ConfigError(f"{where}: viewports는 비어 있지 않은 정수 목록이어야 합니다")
            try:
                viewports = [int(v) for v in vp_raw]
            except (TypeError, ValueError) as err:
                raise ConfigError(f"{where}: viewports 값은 정수여야 합니다 ({err})") from err
            if any(v <= 0 for v in viewports):
                raise ConfigError(f"{where}: viewports 폭은 1 이상이어야 합니다")
        else:
            viewports = [375, 768, 1280]
        responsives.append(ResponsiveCheckSpec(
            name=str(raw["name"]),
            page=str(raw.get("page", "/")),
            description=str(raw.get("description", "")),
            steps=_parse_steps(raw.get("steps"), where),
            viewports=viewports,
            height=int(raw.get("height", 900)),
            max_overflow_px=int(raw.get("max_overflow_px", 2)),
        ))

    perfs: list[PerfCheckSpec] = []
    for i, raw in enumerate(data.get("perf_checks") or []):
        where = f"perf_checks[{i}]"
        if not isinstance(raw, dict) or not raw.get("name"):
            raise ConfigError(f"{where}: name이 필요합니다")
        metric = str(raw.get("metric", "load"))
        if metric not in _PERF_METRICS:
            raise ConfigError(f"{where}: metric은 {sorted(_PERF_METRICS)} 중 하나여야 합니다")
        if "budget_ms" not in raw:
            raise ConfigError(f"{where}: budget_ms가 필요합니다 (예: 3000)")
        budget = int(raw["budget_ms"])
        if budget <= 0:
            raise ConfigError(f"{where}: budget_ms는 1 이상이어야 합니다")
        perfs.append(PerfCheckSpec(
            name=str(raw["name"]),
            page=str(raw.get("page", "/")),
            description=str(raw.get("description", "")),
            steps=_parse_steps(raw.get("steps"), where),
            metric=metric,
            budget_ms=budget,
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

    write_reset: WriteResetConfig | None = None
    wr_raw = data.get("write_reset")
    if wr_raw:
        if not isinstance(wr_raw, dict):
            raise ConfigError("write_reset: 매핑이어야 합니다")
        if not writes:
            raise ConfigError(
                "write_reset: write_checks가 없는데 초기화만 정의했습니다. "
                "쓰기 검증 없이 데이터를 지우는 설정은 사고를 부릅니다")
        http_raw = wr_raw.get("http")
        cmd_raw = wr_raw.get("command")
        if bool(http_raw) == bool(cmd_raw):
            raise ConfigError("write_reset: http 또는 command 중 정확히 하나를 지정하세요")

        http_url = ""
        http_method = "POST"
        http_headers: dict[str, str] = {}
        command: list[str] = []
        if http_raw:
            if not isinstance(http_raw, dict) or not http_raw.get("url"):
                raise ConfigError("write_reset.http: url이 필요합니다")
            http_method = str(http_raw.get("method", "POST")).upper()
            if http_method not in ("POST", "PUT", "DELETE"):
                raise ConfigError(
                    "write_reset.http.method는 POST, PUT, DELETE 중 하나여야 합니다 "
                    "(초기화는 상태를 바꾸는 요청입니다)")
            http_url = _expand_env(str(http_raw["url"]), "write_reset.http.url")
            http_headers = {
                str(k): _expand_env(str(v), f"write_reset.http.headers.{k}")
                for k, v in (http_raw.get("headers") or {}).items()
            }
        else:
            # 문자열을 셸에 넘기지 않는다. 리스트로만 받아 셸 확장·연쇄 실행을 원천 차단한다.
            if not isinstance(cmd_raw, list) or not cmd_raw:
                raise ConfigError(
                    "write_reset.command: 인자 목록이어야 합니다 "
                    "(예: ['python', 'seed.py']). 문자열은 셸을 거치므로 받지 않습니다")
            command = [_expand_env(str(part), "write_reset.command") for part in cmd_raw]

        timeout_ms = int(wr_raw.get("timeout_ms", 30000))
        if timeout_ms <= 0:
            raise ConfigError("write_reset.timeout_ms는 1ms 이상이어야 합니다")
        write_reset = WriteResetConfig(
            http_url=http_url, http_method=http_method, http_headers=http_headers,
            command=command, timeout_ms=timeout_ms,
        )

    a11y_raw = _sub(data, "a11y")
    a11y_severity = str(a11y_raw.get("severity", "info"))
    if a11y_severity not in ("info", "warn", "fail"):
        raise ConfigError("a11y.severity는 info, warn, fail 중 하나여야 합니다")
    a11y_engine = str(a11y_raw.get("engine", "axe")).strip().lower() or "axe"
    if a11y_engine not in ("axe", "builtin"):
        raise ConfigError("a11y.engine은 axe 또는 builtin이어야 합니다")
    a11y_min_impact = str(a11y_raw.get("min_impact", "minor")).strip().lower() or "minor"
    if a11y_min_impact not in IMPACT_ORDER:
        raise ConfigError(
            f"a11y.min_impact는 {', '.join(IMPACT_ORDER)} 중 하나여야 합니다")
    a11y = A11yConfig(enabled=bool(a11y_raw.get("enabled", False)),
                      engine=a11y_engine,
                      severity=a11y_severity,
                      min_impact=a11y_min_impact,
                      rules_exclude=[str(r) for r in (a11y_raw.get("rules_exclude") or [])],
                      tags=[str(t) for t in (a11y_raw.get("tags") or [])])

    lc_raw = _sub(data, "link_check")
    lc_severity = str(lc_raw.get("severity", "fail"))
    if lc_severity not in ("info", "warn", "fail"):
        raise ConfigError("link_check.severity는 info, warn, fail 중 하나여야 합니다")
    link_check = LinkCheckConfig(
        enabled=bool(lc_raw.get("enabled", False)),
        include_external=bool(lc_raw.get("include_external", False)),
        severity=lc_severity,
        timeout_ms=int(lc_raw.get("timeout_ms", 10000)),
        ignore_patterns=_regex_list(lc_raw.get("ignore_patterns"), "link_check.ignore_patterns"),
    )

    # 여기서 완화를 끝낸다. auth는 discover도 **실행**하므로 엄격해야 한다 —
    # 비밀번호가 비면 `${VAR}`가 그대로 입력돼 로그인이 조용히 실패한다.
    deferred_env = list(_DEFERRED_ENV or [])
    _DEFERRED_ENV = None

    auth: AuthConfig | None = None
    a_raw = data.get("auth")
    if a_raw:
        auth_steps = _parse_steps(a_raw.get("steps"), "auth")
        if not auth_steps:
            raise ConfigError("auth: steps가 최소 1개 필요합니다")
        auth = AuthConfig(steps=auth_steps,
                          per_context=bool(a_raw.get("per_context", False)),
                          indexed_db=bool(a_raw.get("indexed_db", True)))

    r = _sub(data, "report")
    report = ReportConfig(
        title=str(r.get("title", "웹 자동 테스트 리포트")),
        video=bool(r.get("video", True)),
        trace=_trace_mode(r.get("trace", "on-failure")),
        mask_selectors=[str(x) for x in r.get("mask_selectors", [])],
        mask_patterns=_mask_patterns(r.get("mask_patterns")),
    )

    # 조합 입력은 Chromium의 IME 경로를 직접 두드린다(CDP). 다른 엔진에서는
    # 재현할 수 없으므로, 돌려 놓고 중간에 실패하게 두지 않고 시작 전에 막는다.
    # 조합을 재현하지 못한 실행에 합격·불합격을 매기면 안 되기 때문이다.
    if target.browser != "chromium":
        for group, label in ((checks, "data_checks"), (specs, "spec_checks"),
                             (writes, "write_checks")):
            for spec in group:
                if any(s.action == "type_ime" for s in spec.steps):
                    raise ConfigError(
                        f"{label} '{spec.name}': type_ime는 chromium에서만 동작합니다 "
                        f"(현재 target.browser={target.browser}). 조합 입력을 재현할 수 "
                        "없는 엔진에서 이 검사를 통과로 세지 않기 위해 실행 전에 막습니다."
                    )
        if auth and any(s.action == "type_ime" for s in auth.steps):
            raise ConfigError(
                f"auth: type_ime는 chromium에서만 동작합니다 "
                f"(현재 target.browser={target.browser})")

    return AgentConfig(
        target=target,
        crawl=crawl,
        sweep=sweep,
        data_checks=checks,
        spec_checks=specs,
        write_checks=writes,
        visual_checks=visuals,
        responsive_checks=responsives,
        perf_checks=perfs,
        report=report,
        a11y=a11y,
        link_check=link_check,
        auth=auth,
        notify=notify,
        write_reset=write_reset,
        baselines_dir=str(data.get("baselines_dir", "baselines")),
        output_dir=str(data.get("output_dir", "runs")),
        deferred_env=deferred_env,
        config_path=str(p),
    )
