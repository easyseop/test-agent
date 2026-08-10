"""페이지 크롤링과 요소 인벤토리 수집."""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

from .browser import BrowserSession, save_screenshot
from .config import AgentConfig

# raw 문자열이어야 한다. 안의 JS 정규식 `/\s+/`를 Python 이스케이프로 해석하면
# 지금은 DeprecationWarning이고 이후 Python에서는 SyntaxError가 된다.
JS_INVENTORY = r"""() => {
  function cssPath(el) {
    if (el.id) return '#' + CSS.escape(el.id);
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 8) {
      if (node.id) { parts.unshift('#' + CSS.escape(node.id)); break; }
      let part = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter(c => c.tagName === node.tagName);
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      if (!parent || part === 'body') break;
      node = parent;
    }
    return parts.join(' > ');
  }
  const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  // 표 셀렉터는 화면 구조가 조금만 바뀌어도 깨지는 nth-of-type 경로보다
  // 테스트용 속성이 훨씬 오래 간다. 있으면 그걸 쓴다. 값에 따옴표·공백이
  // 섞이면 인용 규칙이 갈리므로, 안전한 문자로만 된 값에서만 쓴다.
  const TESTID_ATTRS = ['data-testid', 'data-test-id', 'data-test', 'data-qa'];
  const stableSelector = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    for (const attr of TESTID_ATTRS) {
      const v = el.getAttribute(attr);
      if (v && /^[\w.:-]+$/.test(v)) return '[' + attr + '="' + v + '"]';
    }
    return cssPath(el);
  };
  // 아이콘 전용 버튼은 innerText가 비어 있다. aria-label/title/alt까지 텍스트로 모아야
  // 위험 동작(삭제 등) 차단 판정이 그런 버튼에도 적용된다.
  const label = (el) => [
    el.innerText, el.value, el.getAttribute('aria-label'),
    el.getAttribute('title'), (el.querySelector('img') || {}).alt,
  ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim().slice(0, 80);
  const info = (el) => ({ selector: cssPath(el), text: label(el) });
  return {
    title: document.title,
    buttons: Array.from(document.querySelectorAll('button, input[type=button], input[type=submit], [role=button]'))
      .filter(visible).map(el => ({ ...info(el), disabled: !!el.disabled })),
    links: Array.from(document.querySelectorAll('a[href]')).filter(visible)
      .map(el => ({ ...info(el), href: el.getAttribute('href') || '' })),
    inputs: Array.from(document.querySelectorAll('input:not([type=button]):not([type=submit]):not([type=hidden]), textarea'))
      .filter(visible).map(el => ({ ...info(el), name: el.name || '', type: el.type || 'text' })),
    selects: Array.from(document.querySelectorAll('select')).filter(visible)
      .map(el => ({ ...info(el), name: el.name || '',
                    options: Array.from(el.options).map(o => o.value).slice(0, 20) })),
    tables: Array.from(document.querySelectorAll('table, [role=table], [role=grid]'))
      .slice(0, 20).map(el => {
        // 표 안에 표가 들어 있으면 안쪽 th·tr까지 딸려 온다. 자기 표의
        // 것만 센다 — 안 그러면 헤더가 뒤섞이고 행 수가 부풀려진다.
        const mine = (nodes) => Array.from(nodes).filter(
          n => n.closest('table, [role=table], [role=grid]') === el);
        const heads = mine(el.querySelectorAll('th, [role=columnheader]'));
        let headers = heads.map(h => label(h)).slice(0, 30);
        if (!headers.length) {
          const first = mine(el.querySelectorAll('tr, [role=row]'))[0];
          headers = first ? Array.from(first.children).map(c => label(c)).slice(0, 30) : [];
        }
        const rows = mine(el.querySelectorAll('tr, [role=row]')).filter(r => {
          if (r.closest('thead')) return false;
          const cells = Array.from(r.children);
          // 셀이 전부 헤더인 줄은 데이터 행이 아니다. 행 머리글(th)이 섞인
          // 줄은 데이터 행이 맞으므로 every로 본다.
          return cells.length > 0 && !cells.every(
            c => c.tagName === 'TH' || c.getAttribute('role') === 'columnheader');
        });
        // 숨은 표도 버리지 않고 표시만 한다. 탭을 안 열어 안 보이는 표를
        // 목록에서 지우면 "그런 표는 없다"로 읽힌다.
        return { selector: stableSelector(el), headers: headers,
                 row_count: rows.length, visible: visible(el) };
      }),
  };
}"""


JS_A11Y = """() => {
  const issues = [];
  const snippet = (el) => (el.outerHTML || '').slice(0, 80);
  document.querySelectorAll('img:not([alt])').forEach(el =>
    issues.push({ type: 'img-alt', detail: snippet(el) }));
  document.querySelectorAll(
    'input:not([type=hidden]):not([type=submit]):not([type=button]), select, textarea'
  ).forEach(el => {
    const labelled = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby')
      || (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]'))
      || el.closest('label');
    if (!labelled) issues.push({ type: 'input-label', detail: el.id || el.name || snippet(el) });
  });
  document.querySelectorAll('button, a[href]').forEach(el => {
    if (!(el.innerText || '').trim() && !el.getAttribute('aria-label'))
      issues.push({ type: 'empty-name', detail: snippet(el) });
  });
  if (!document.documentElement.getAttribute('lang'))
    issues.push({ type: 'html-lang', detail: '' });
  const ids = {};
  document.querySelectorAll('[id]').forEach(el => { ids[el.id] = (ids[el.id] || 0) + 1; });
  Object.entries(ids).filter(([, c]) => c > 1)
    .forEach(([id, c]) => issues.push({ type: 'dup-id', detail: '#' + id + ' x' + c }));
  // 제목(heading) 레벨을 건너뛰면 스크린리더 목차가 깨진다 (h1 → h3)
  let prev = 0;
  document.querySelectorAll('h1,h2,h3,h4,h5,h6').forEach(el => {
    const level = Number(el.tagName[1]);
    if (prev && level > prev + 1)
      issues.push({ type: 'heading-skip', detail: 'h' + prev + ' → h' + level + ': ' + snippet(el) });
    prev = level;
  });
  // 양수 tabindex는 포커스 순서를 왜곡하는 안티패턴
  document.querySelectorAll('[tabindex]').forEach(el => {
    if (Number(el.getAttribute('tabindex')) > 0)
      issues.push({ type: 'positive-tabindex', detail: 'tabindex=' + el.getAttribute('tabindex') + ' ' + snippet(el) });
  });
  // 본문 랜드마크(main)가 없으면 '본문 바로가기'가 동작하지 않는다
  if (!document.querySelector('main, [role=main]'))
    issues.push({ type: 'no-main', detail: '' });
  // 데이터 표(행 5개 이상)에 th 헤더가 없으면 셀 의미를 읽을 수 없다
  document.querySelectorAll('table').forEach(el => {
    const rows = el.querySelectorAll('tr').length;
    if (rows >= 5 && el.querySelectorAll('th').length === 0)
      issues.push({ type: 'table-no-th', detail: rows + '행 표에 th 없음: ' + snippet(el) });
  });
  return issues;
}"""


@dataclass
class PageInfo:
    url: str
    path: str
    title: str
    screenshot: str
    elements: dict = field(default_factory=dict)
    a11y: list = field(default_factory=list)
    # 비어 있지 않으면 '점검 실패'다. 위반 0건과 반드시 구분해야 한다 —
    # 검사를 못 한 페이지를 깨끗한 페이지로 읽으면 조용한 거짓 통과가 된다.
    a11y_error: str = ""


@dataclass
class Discovery:
    pages: list[PageInfo] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"pages": [asdict(p) for p in self.pages]}


def _collect_a11y(page, cfg) -> tuple[list, str]:
    """페이지 접근성 점검. (이슈 목록, 실패 사유) 반환.

    axe 주입이 CSP에 막히거나 실패해도 크롤링 전체를 무너뜨리지 않는다. 대신
    사유를 그대로 올려보내 '위반 0건'으로 위장되지 않게 한다.
    """
    if not cfg.a11y.enabled:
        return [], ""
    if cfg.a11y.engine == "builtin":
        try:
            return page.evaluate(JS_A11Y), ""
        except Exception as err:
            return [], f"간이 점검 실패: {str(err).splitlines()[0][:200]}"

    from .a11y import run_axe
    result = run_axe(page, tags=cfg.a11y.tags or None,
                     rules_exclude=cfg.a11y.rules_exclude or None)
    if result.skipped:
        return [], ""      # 점검 대상이 아님 — 실패도 위반도 아니다
    return result.issues, result.error


def _same_origin_path(href: str, current_url: str, origin: str) -> str | None:
    if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
        return None
    absolute = urljoin(current_url, href)
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https"):
        return None
    if f"{parsed.scheme}://{parsed.netloc}" != origin:
        return None
    path = parsed.path or "/"
    if parsed.query:
        path += f"?{parsed.query}"
    return path


def crawl_start_path(base_url: str) -> str:
    """크롤링을 시작할 경로. base_url이 하위 경로를 가리키면 거기서 시작한다.

    `/admin`, `/wiki`처럼 하위 경로에 붙은 앱을 base_url로 지정했을 때 "/"에서
    시작하면 앱 바깥을 크롤링하게 된다. Playwright의 context base_url은 "/"를
    origin 기준으로 풀기 때문에, 여기서 명시하지 않으면 경로가 통째로 버려진다.
    """
    return urlparse(base_url).path or "/"


def crawl(
    session: BrowserSession,
    cfg: AgentConfig,
    run_dir: Path,
    deadline_monotonic: float | None = None,
) -> Discovery:
    """base_url에서 시작해 동일 출처 링크를 BFS로 순회하며 인벤토리를 만든다."""
    base = cfg.target.base_url
    parsed_base = urlparse(base)
    origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
    excludes = [re.compile(p) for p in cfg.crawl.exclude_patterns]
    start_path = crawl_start_path(base)

    discovery = Discovery()
    ctx, page, _monitor = session.new_context(base)
    try:
        queue: list[tuple[str, int]] = [(start_path, 0)]
        seen: set[str] = set()
        while queue and len(discovery.pages) < cfg.crawl.max_pages:
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                break
            path, depth = queue.pop(0)
            norm = path.split("#")[0] or "/"
            if norm in seen or any(rx.search(norm) for rx in excludes):
                continue
            seen.add(norm)
            try:
                nav_timeout = cfg.target.nav_timeout_ms
                if deadline_monotonic is not None:
                    remaining = int((deadline_monotonic - time.monotonic()) * 1000)
                    if remaining <= 0:
                        break
                    nav_timeout = min(nav_timeout, remaining)
                page.goto(norm, wait_until="load", timeout=max(1, nav_timeout))
            except Exception:
                continue
            settle_ms = cfg.target.settle_ms
            if deadline_monotonic is not None:
                remaining = int((deadline_monotonic - time.monotonic()) * 1000)
                if remaining <= 0:
                    break
                settle_ms = min(settle_ms, remaining)
            page.wait_for_timeout(max(0, settle_ms))
            inventory = page.evaluate(JS_INVENTORY)
            a11y_issues, a11y_error = _collect_a11y(page, cfg)
            shot_rel = f"screenshots/discovery_{len(discovery.pages):02d}.jpg"
            save_screenshot(page, run_dir / shot_rel, full_page=True,
                            mask_selectors=cfg.report.mask_selectors)
            discovery.pages.append(PageInfo(
                url=page.url, path=norm,
                title=inventory.get("title", ""),
                screenshot=shot_rel, elements=inventory,
                a11y=a11y_issues, a11y_error=a11y_error,
            ))
            if depth < cfg.crawl.max_depth:
                for link in inventory.get("links", []):
                    nxt = _same_origin_path(link.get("href", ""), page.url, origin)
                    if nxt and nxt not in seen:
                        queue.append((nxt, depth + 1))
    finally:
        ctx.close()

    (run_dir / "discovery.json").write_text(
        json.dumps(discovery.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return discovery
