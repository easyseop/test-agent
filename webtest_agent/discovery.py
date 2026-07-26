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

JS_INVENTORY = """() => {
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
  const info = (el) => ({ selector: cssPath(el), text: ((el.innerText || el.value || '') + '').trim().slice(0, 80) });
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


@dataclass
class Discovery:
    pages: list[PageInfo] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"pages": [asdict(p) for p in self.pages]}


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

    discovery = Discovery()
    ctx, page, _monitor = session.new_context(base)
    try:
        queue: list[tuple[str, int]] = [("/", 0)]
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
            a11y_issues = page.evaluate(JS_A11Y) if cfg.a11y.enabled else []
            shot_rel = f"screenshots/discovery_{len(discovery.pages):02d}.jpg"
            save_screenshot(page, run_dir / shot_rel, full_page=True,
                            mask_selectors=cfg.report.mask_selectors)
            discovery.pages.append(PageInfo(
                url=page.url, path=norm,
                title=inventory.get("title", ""),
                screenshot=shot_rel, elements=inventory,
                a11y=a11y_issues,
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
