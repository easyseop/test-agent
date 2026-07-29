"""접근성 점검 — axe-core 주입 실행과 결과 정규화.

axe-core(MPL-2.0)를 저장소에 동봉해 오프라인·폐쇄망에서도 같은 버전으로 돈다.
CDN을 쓰면 실행 시점마다 규칙이 달라져 판정이 흔들린다(결정성 위반).

**이 모듈의 가장 중요한 계약**: '위반 0건'과 '검사를 못 했다'는 다른 사건이다.
CSP가 스크립트 주입을 막거나 페이지가 죽으면 axe는 아무것도 못 돌린다. 이때
0건으로 보고하면 접근성 문제가 없다는 뜻이 되어 조용한 거짓 통과가 된다.
그래서 모든 결과는 실패 사유(`error`)를 함께 들고 다니며, 호출부는 이를
'점검 불가'로 구분해 표시해야 한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# axe가 매기는 심각도. 낮은 것부터 — 게이팅 임계값 비교에 순서를 쓴다.
IMPACT_ORDER = ("minor", "moderate", "serious", "critical")

_AXE_PATH = Path(__file__).parent / "vendor" / "axe.min.js"
# axe가 공식 제공하는 한국어 로케일. 사용자 대면 문자열은 한국어라는 프로젝트
# 규약을 지키기 위해 위반 설명을 번역해 받는다(규칙 id는 원문 유지 — 검색 가능해야 한다).
_AXE_LOCALE_PATH = Path(__file__).parent / "vendor" / "axe-ko.json"

# axe.run()에 넘길 옵션. WCAG 2.1 AA까지를 기본 범위로 잡는다 —
# 'best-practice'는 법적 기준이 아니라 권고라서 기본에서 뺀다(노이즈).
_DEFAULT_TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]

# 페이지당 axe 실행 상한. 거대한 DOM에서 axe가 수십 초를 먹으면 전체 실행
# deadline을 잡아먹는다.
AXE_TIMEOUT_MS = 30000


@dataclass
class A11yPageResult:
    """페이지 1개의 접근성 점검 결과."""
    url: str = ""
    issues: list[dict] = field(default_factory=list)
    error: str = ""          # 비어 있지 않으면 '점검 실패' — 0건과 구분해야 한다
    engine: str = "axe"
    rules_run: int = 0       # 실제로 돌린 규칙 수 (0이면 검사가 안 된 것)
    skipped: str = ""        # 점검 대상이 아님(비-HTML 등). 실패와도 0건과도 다르다

    @property
    def checked(self) -> bool:
        return not self.error


def axe_source() -> str:
    """동봉한 axe-core 소스. 없으면 FileNotFoundError."""
    return _AXE_PATH.read_text(encoding="utf-8")


def axe_available() -> bool:
    return _AXE_PATH.is_file()


def _korean_locale() -> dict | None:
    """번들된 한국어 로케일. 없거나 깨져 있으면 None(영어로 진행)."""
    import json
    try:
        return json.loads(_AXE_LOCALE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None


def _run_options(tags: list[str] | None, rules_exclude: list[str] | None) -> dict:
    opts: dict = {"runOnly": {"type": "tag", "values": list(tags or _DEFAULT_TAGS)}}
    if rules_exclude:
        opts["rules"] = {rule: {"enabled": False} for rule in rules_exclude}
    return opts


JS_RUN_AXE = """
async ({ options, timeoutMs, locale }) => {
  if (typeof window.axe === 'undefined') {
    return { error: 'axe 주입 실패 — window.axe 없음' };
  }
  if (locale) {
    // 로케일 적용이 실패해도 점검 자체는 계속한다(설명이 영어로 나올 뿐).
    try { window.axe.configure({ locale: locale }); } catch (e) {}
  }
  // axe가 멈춰도 전체 실행이 볼모가 되지 않도록 시간 상한을 건다.
  const run = window.axe.run(document, options);
  const guard = new Promise((_, reject) =>
    setTimeout(() => reject(new Error('axe 실행 시간 초과')), timeoutMs));
  try {
    const res = await Promise.race([run, guard]);
    return {
      violations: (res.violations || []).map(v => ({
        id: v.id,
        impact: v.impact || 'minor',
        help: v.help || '',
        helpUrl: v.helpUrl || '',
        nodes: (v.nodes || []).slice(0, 5).map(n => ({
          target: (n.target || []).join(' '),
          html: (n.html || '').slice(0, 160),
        })),
        nodeCount: (v.nodes || []).length,
      })),
      // 통과·미적용까지 세어야 '규칙이 실제로 돌았는지'를 알 수 있다.
      rulesRun: (res.violations || []).length + (res.passes || []).length
                + (res.incomplete || []).length + (res.inapplicable || []).length,
    };
  } catch (err) {
    return { error: String((err && err.message) || err) };
  }
}
"""


def run_axe(page, tags=None, rules_exclude=None,
            timeout_ms: int = AXE_TIMEOUT_MS) -> A11yPageResult:
    """페이지에 axe-core를 주입해 실행한다.

    주입·실행 실패는 예외로 올리지 않고 `error`에 담아 돌려준다 — 접근성 점검이
    안 됐다고 전체 실행을 무너뜨릴 이유는 없다. 다만 **0건으로 위장하지도 않는다.**
    """
    url = ""
    try:
        url = page.url
    except Exception:
        pass

    if not axe_available():
        return A11yPageResult(url=url, error=f"axe 번들 없음: {_AXE_PATH}")

    # HTML이 아닌 문서(CSV·JSON 등)에는 접근성 규칙을 적용하지 않는다.
    # 엔진마다 다운로드 처리 방식이 달라(WebKit은 /export.csv를 화면에 띄운다)
    # CSV 파일이 '<title> 없음·lang 없음'으로 잡히는 거짓 위반이 생긴다.
    try:
        content_type = page.evaluate("() => document.contentType || ''") or ""
    except Exception:
        content_type = ""
    if content_type and "html" not in content_type.lower():
        return A11yPageResult(url=url, skipped=f"HTML 문서가 아님({content_type})")

    try:
        # add_script_tag(content=...)는 CSP가 인라인 스크립트를 막으면 실패한다.
        # 실제 운영 사이트에서 흔한 일이므로 사유를 그대로 남긴다.
        page.add_script_tag(content=axe_source())
    except Exception as err:
        first = str(err).strip().splitlines()
        return A11yPageResult(
            url=url,
            error=f"axe 주입 실패(CSP 등): {(first[0] if first else '')[:200]}")

    try:
        data = page.evaluate(JS_RUN_AXE, {
            "options": _run_options(tags, rules_exclude),
            "timeoutMs": timeout_ms,
            "locale": _korean_locale(),
        })
    except Exception as err:
        first = str(err).strip().splitlines()
        return A11yPageResult(url=url,
                              error=f"axe 실행 실패: {(first[0] if first else '')[:200]}")

    if not isinstance(data, dict) or data.get("error"):
        detail = (data or {}).get("error", "알 수 없는 오류") if isinstance(data, dict) else "결과 형식 오류"
        return A11yPageResult(url=url, error=f"axe 실행 실패: {detail}")

    issues = []
    for v in data.get("violations", []):
        impact = v.get("impact") or "minor"
        if impact not in IMPACT_ORDER:
            impact = "minor"
        issues.append({
            "type": v.get("id", ""),
            "impact": impact,
            "detail": v.get("help", ""),
            "help_url": v.get("helpUrl", ""),
            "nodes": v.get("nodes", []),
            "node_count": int(v.get("nodeCount", 0) or 0),
        })

    rules_run = int(data.get("rulesRun", 0) or 0)
    if rules_run == 0:
        # 규칙이 하나도 안 돌았는데 위반 0건이면 그건 '깨끗한 페이지'가 아니라
        # '검사가 안 된 페이지'다. 여기서 구분하지 않으면 거짓 통과가 된다.
        return A11yPageResult(url=url, issues=issues, rules_run=0,
                              error="axe가 규칙을 하나도 실행하지 못했습니다")

    return A11yPageResult(url=url, issues=issues, rules_run=rules_run)


def at_or_above(impact: str, minimum: str) -> bool:
    """impact가 임계값 이상인가. 모르는 값은 가장 낮은 것으로 취급한다."""
    try:
        return IMPACT_ORDER.index(impact) >= IMPACT_ORDER.index(minimum)
    except ValueError:
        return False


def count_by_impact(issues: list[dict]) -> dict[str, int]:
    counts = {level: 0 for level in IMPACT_ORDER}
    for issue in issues:
        level = issue.get("impact", "minor")
        if level in counts:
            counts[level] += 1
    return counts


def summarize_impacts(issues: list[dict]) -> str:
    counts = count_by_impact(issues)
    parts = [f"{level} {n}건" for level, n in counts.items() if n]
    return " · ".join(parts) if parts else "없음"
