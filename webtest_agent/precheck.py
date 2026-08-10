"""설정을 돌리기 전에 셀렉터가 실제로 맞는지 확인한다.

설정을 쓰고 나면 대개 이렇게 된다.

    run → 3번 스텝에서 셀렉터 못 찾음 → 고침 → run → 5번에서 또 못 찾음 → …

한 번에 하나씩만 드러나서 왕복이 길다. 여기서는 시나리오를 끝까지 걸어가며
**못 찾은 것을 전부 모아** 한 번에 보여준다.

다만 앞 스텝이 실패하면 그 뒤 화면은 원래 나와야 할 상태가 아니다. 그 상태에서
못 찾은 셀렉터를 '설정이 틀렸다'로 보고하면 멀쩡한 줄을 고치게 된다. 그래서
세 가지를 구분한다.

    찾음        n개 맞음
    못 찾음     앞이 다 성공한 상태에서 0개 — 고쳐야 할 것
    확인 못 함  앞이 막혀서 이 줄은 판단할 수 없음 — 참고만

'확인 못 함'을 '못 찾음'에 섞지 않는 것이 이 명령의 핵심이다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

OK, MISS, UNCHECKED, INFO = "찾음", "못 찾음", "확인 못 함", "참고"

# 실행 시점에 앞 스텝이 뽑아 넣는 값. 지금은 실제 값을 모르므로 셀렉터를
# 확정할 수 없다. 추측해서 0개로 세면 없는 결함을 만들어낸다.
_VAR_RX = re.compile(r"\{\{[^}]+\}\}")

# 화면 상태를 바꾸는 액션. 뒤 스텝이 이걸 딛고 서므로 확인만 하고 넘어가면
# 이후 셀렉터가 전부 '못 찾음'으로 나온다.
ADVANCING = {"goto", "click", "fill", "select", "check", "press", "wait_for",
             "wait_ms", "hover", "scroll_to", "drag", "upload", "type_ime",
             "wait_popup", "close_popup", "extract", "fetch"}


@dataclass
class Probe:
    step: str          # "3. click #login-btn"
    status: str
    detail: str = ""


@dataclass
class ScenarioProbe:
    name: str
    kind: str
    probes: list[Probe] = field(default_factory=list)
    error: str = ""    # 시나리오를 아예 시작 못 함 (페이지 이동 실패 등)

    @property
    def misses(self) -> list[Probe]:
        return [p for p in self.probes if p.status == MISS]

    @property
    def unchecked(self) -> list[Probe]:
        return [p for p in self.probes if p.status == UNCHECKED]


@dataclass
class PrecheckResult:
    scenarios: list[ScenarioProbe] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)   # 일부러 안 본 것
    fatal: str = ""    # 점검 자체가 불가 (사이트 다운·로그인 실패)

    @property
    def miss_total(self) -> int:
        return sum(len(s.misses) for s in self.scenarios)

    @property
    def unchecked_total(self) -> int:
        return sum(len(s.unchecked) for s in self.scenarios)

    @property
    def failed_scenarios(self) -> list[ScenarioProbe]:
        return [s for s in self.scenarios if s.error]


def step_label(index: int, action: str, selector: str | None) -> str:
    return f"{index}. {action}" + (f"  {selector}" if selector else "")


def has_unresolved_var(selector: str | None) -> bool:
    """셀렉터에 실행 시점에야 정해지는 값이 들어 있는가."""
    return bool(selector and _VAR_RX.search(selector))


def classify(count: int, blocked: bool, needs_selector: bool) -> str:
    """이 스텝의 셀렉터 조회 결과를 무엇으로 볼 것인가.

    blocked면 앞이 이미 막힌 것이다. 화면이 원래 상태가 아니므로 찾았든 못
    찾았든 **판단이 안 된다**. 찾은 쪽을 '찾음'으로 세면 앞을 고친 뒤에도
    맞다는 보장이 없는데 확인된 것처럼 읽힌다. 개수는 detail에 남긴다.
    """
    if blocked:
        return UNCHECKED
    if count > 0:
        return OK
    return MISS if needs_selector else UNCHECKED


def destructive_hit(step) -> str | None:
    """이 스텝을 이 명령이 눌러도 되는가. 걸리는 패턴이 있으면 그것을 반환한다.

    check-config도 스텝을 실제로 실행한다. `run`이 승인 없이 누르지 않는 동작을
    여기서 누르면 안전 경계가 이 명령 하나로 뚫린다. 같은 기준을 그대로 쓴다.
    """
    from .runner import _GUARDED_ACTIONS
    from .safety import find_destructive

    if step.action not in _GUARDED_ACTIONS:
        return None
    return find_destructive(step.selector,
                            step.value if step.action == "drag" else None,
                            step.frame)


def missing_columns(configured: list[str], headers: list[str]) -> list[str]:
    """설정의 열 이름 중 화면 헤더에 없는 것.

    로케일이 바뀌면 헤더 글자가 통째로 달라진다. 실행해서 대조가 0행 나와야
    아는 것과, 돌리기 전에 아는 것은 다르다.
    """
    return [c for c in configured if c not in headers]


def exit_code(result: PrecheckResult) -> int:
    """`run`과 같은 계약을 쓴다. 확인 못 한 것을 통과로 세지 않는다.

        0  전부 찾았다
        1  못 찾은 셀렉터가 있다 — 설정을 고쳐야 한다
        2  점검 자체를 못 했다 — 사이트가 안 뜨거나 로그인이 안 됐다
    """
    if result.fatal:
        return 2
    if result.miss_total or result.failed_scenarios:
        return 1
    return 0


def render(result: PrecheckResult) -> str:
    lines: list[str] = []
    if result.fatal:
        return f"점검 불가: {result.fatal}"

    for sc in result.scenarios:
        head = f"[{sc.kind}] {sc.name}"
        if sc.error:
            lines += [head, f"  시작하지 못함: {sc.error}", ""]
            continue
        bad = len(sc.misses)
        # 확인 못 한 줄이 있는데 ✓를 달면 '이 시나리오는 다 봤다'로 읽힌다.
        mark = "✗" if bad else ("·" if sc.unchecked else "✓")
        lines.append(f"{mark} {head}" + (f"  — 못 찾음 {bad}개" if bad else ""))
        for p in sc.probes:
            if p.status == OK:
                continue          # 맞은 것까지 다 찍으면 틀린 게 안 보인다
            lines.append(f"     {p.status:<7} {p.step}"
                         + (f"   {p.detail}" if p.detail else ""))
        lines.append("")

    for note in result.skipped:
        lines.append(f"건너뜀 — {note}")
    if result.skipped:
        lines.append("")

    lines.append("─" * 60)
    lines.append(f"시나리오 {len(result.scenarios)}개 · 못 찾음 {result.miss_total}개"
                 f" · 확인 못 함 {result.unchecked_total}개")
    if result.unchecked_total:
        lines.append("'확인 못 함'은 앞 단계가 막혀 판단할 수 없었던 줄입니다. 셀렉터가")
        lines.append("틀렸다는 뜻도, 맞다는 뜻도 아닙니다 — 앞의 '못 찾음'부터 고치고 "
                     "다시 돌려 보세요.")
    if not result.miss_total and not result.failed_scenarios:
        lines.append("셀렉터는 전부 찾았습니다. 이 명령은 셀렉터가 있는지만 봅니다 — "
                     "기대값이 맞는지는")
        lines.append("보지 않습니다.")
    return "\n".join(lines)
