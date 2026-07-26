"""시나리오 생성: 버튼 스윕(자동) + 데이터 정합성 검증(YAML)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import (AgentConfig, DataCheckSpec, Step, VisualCheckSpec,
                     WriteCheckSpec)
from .discovery import Discovery
from .models import BlockedElement
from .safety import find_hard_block

_SLUG_RX = re.compile(r"[^0-9A-Za-z가-힣_-]+")


def slugify(name: str, max_len: int = 60) -> str:
    slug = _SLUG_RX.sub("-", name).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:max_len] or "scenario"


@dataclass
class Scenario:
    name: str
    kind: str                    # sweep_button | sweep_link | data_check | spec_check | write_check
    page: str
    steps: list[Step] = field(default_factory=list)
    description: str = ""
    element_text: str = ""
    spec: DataCheckSpec | None = None
    write_spec: WriteCheckSpec | None = None
    visual_spec: VisualCheckSpec | None = None


def build_sweep(discovery: Discovery, cfg: AgentConfig) -> tuple[list[Scenario], list[BlockedElement]]:
    """인벤토리의 버튼(과 선택적으로 링크)마다 클릭 검증 시나리오를 만든다.

    avoid_patterns에 걸리는 요소는 클릭하지 않고 차단 목록에 기록한다.
    """
    scenarios: list[Scenario] = []
    blocked: list[BlockedElement] = []
    avoid = [(p, re.compile(p, re.IGNORECASE)) for p in cfg.sweep.avoid_patterns]

    for pinfo in discovery.pages:
        elements: list[tuple[str, dict]] = [("button", b) for b in pinfo.elements.get("buttons", [])]
        if cfg.sweep.include_links:
            elements += [("link", l) for l in pinfo.elements.get("links", [])]

        count = 0
        seen_selectors: set[str] = set()
        for kind, el in elements:
            if count >= cfg.sweep.max_per_page:
                break
            selector = el.get("selector") or ""
            if not selector or selector in seen_selectors:
                continue
            seen_selectors.add(selector)
            if el.get("disabled"):
                continue
            text = el.get("text") or el.get("href") or selector
            if kind == "link" and str(el.get("href", "")).startswith(("mailto:", "tel:", "javascript:")):
                continue

            # 최소 안전 패턴은 텍스트뿐 아니라 selector와 href에도 적용한다.
            # 설정 로더를 우회해 AgentConfig를 직접 만든 경우에도 제거할 수 없다.
            hard_hit = find_hard_block(text, selector, el.get("href"))
            hit = hard_hit or next(
                (pat for pat, rx in avoid
                 if rx.search(" ".join((text, selector, str(el.get("href") or ""))))),
                None,
            )
            if hit:
                blocked.append(BlockedElement(
                    page=pinfo.path,
                    text=text,
                    pattern=hit,
                    reason=(
                        "자동 탐색에서 쓰기·외부 전송·세션 변경 동작을 실행하지 않음"
                        if hard_hit else "설정의 avoid_patterns와 일치해 자동 탐색에서 제외"
                    ),
                ))
                continue

            label = "버튼" if kind == "button" else "링크"
            scenarios.append(Scenario(
                name=f"{label}점검 {text[:30]} ({pinfo.path})",
                kind=f"sweep_{kind}",
                page=pinfo.path,
                steps=[Step(action="click", selector=selector)],
                description=f"{pinfo.path} 페이지의 {label} '{text[:60]}' 클릭 시 오류·무반응 여부 점검",
                element_text=text,
            ))
            count += 1

    return scenarios, blocked


def build_data_checks(cfg: AgentConfig) -> list[Scenario]:
    return [
        Scenario(
            name=spec.name,
            kind="data_check",
            page=spec.page,
            steps=list(spec.steps),
            description=spec.description,
            spec=spec,
        )
        for spec in cfg.data_checks
    ]


def build_spec_checks(cfg: AgentConfig) -> list[Scenario]:
    """명세 기반 단언 시나리오 — 스텝 실패(단언 위반 포함)와 신호로만 판정."""
    return [
        Scenario(
            name=spec.name,
            kind="spec_check",
            page=spec.page,
            steps=list(spec.steps),
            description=spec.description,
        )
        for spec in cfg.spec_checks
    ]


def build_visual_checks(cfg: AgentConfig) -> list[Scenario]:
    """시각 회귀 시나리오 — 기준선 대비 픽셀 비교."""
    return [
        Scenario(
            name=spec.name,
            kind="visual_check",
            page=spec.page,
            steps=list(spec.steps),
            description=spec.description,
            visual_spec=spec,
        )
        for spec in cfg.visual_checks
    ]


def build_write_checks(cfg: AgentConfig) -> list[Scenario]:
    """쓰기(상태 전이) 검증 시나리오 — 스텝 전후 DB 스칼라 변화량 검증."""
    return [
        Scenario(
            name=spec.name,
            kind="write_check",
            page=spec.page,
            steps=list(spec.steps),
            description=spec.description,
            write_spec=spec,
        )
        for spec in cfg.write_checks
    ]


def block_write_checks(cfg: AgentConfig) -> list[BlockedElement]:
    """명시적 CLI 승인이 없는 write_checks를 보고서용 차단 항목으로 만든다."""
    return [
        BlockedElement(
            page=spec.page,
            text=spec.name,
            pattern="--allow-write-checks",
            reason="쓰기 검증은 명시적 실행 승인 없이는 실행하지 않음",
        )
        for spec in cfg.write_checks
    ]
