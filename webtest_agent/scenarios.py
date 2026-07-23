"""시나리오 생성: 버튼 스윕(자동) + 데이터 정합성 검증(YAML)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import AgentConfig, DataCheckSpec, Step
from .discovery import Discovery
from .models import BlockedElement

_SLUG_RX = re.compile(r"[^0-9A-Za-z가-힣_-]+")


def slugify(name: str, max_len: int = 60) -> str:
    slug = _SLUG_RX.sub("-", name).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug[:max_len] or "scenario"


@dataclass
class Scenario:
    name: str
    kind: str                    # sweep_button | sweep_link | data_check
    page: str
    steps: list[Step] = field(default_factory=list)
    description: str = ""
    element_text: str = ""
    spec: DataCheckSpec | None = None


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

            hit = next((pat for pat, rx in avoid if rx.search(text)), None)
            if hit:
                blocked.append(BlockedElement(page=pinfo.path, text=text, pattern=hit))
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
