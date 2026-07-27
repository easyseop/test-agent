"""깨진 링크 검사 — 크롤링으로 발견한 링크의 HTTP 상태를 전수 점검."""
from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse


def collect_links(pages, base_url: str, include_external: bool,
                  ignore_patterns: list[str]) -> list[str]:
    """discovery 페이지들에서 검사할 링크 URL을 모은다(절대경로·중복 제거).

    - mailto:/tel:/javascript:/# 앵커는 제외
    - include_external=False면 같은 출처만
    - ignore_patterns(정규식)에 걸리면 제외
    """
    origin = _origin(base_url)
    ignores = [re.compile(p) for p in ignore_patterns]
    seen: dict[str, None] = {}
    for page in pages:
        page_url = page.get("url") or urljoin(base_url + "/", page.get("path", "/"))
        for link in page.get("elements", {}).get("links", []):
            href = (link.get("href") or "").strip()
            if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
                continue
            absolute = urljoin(page_url, href)
            parsed = urlparse(absolute)
            if parsed.scheme not in ("http", "https"):
                continue
            if not include_external and _origin(absolute) != origin:
                continue
            absolute = absolute.split("#", 1)[0]   # 프래그먼트 제거
            if any(rx.search(absolute) for rx in ignores):
                continue
            seen.setdefault(absolute, None)
    return list(seen)


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def probe_links(request_context, urls: list[str], timeout_ms: int) -> list[dict]:
    """각 URL의 HTTP 상태를 확인해 깨진 것(>=400/연결 실패)만 반환한다.

    Playwright의 APIRequestContext를 받아 인증 세션(쿠키)을 그대로 쓴다. HEAD를
    먼저 시도하고 405/501이면 GET으로 재시도한다(HEAD 미지원 서버 대비).
    """
    broken: list[dict] = []
    for url in urls:
        status, detail = _probe_one(request_context, url, timeout_ms)
        if status is None:
            broken.append({"url": url, "status": None, "detail": detail})
        elif status >= 400:
            broken.append({"url": url, "status": status, "detail": ""})
    return broken


def _probe_one(request_context, url: str, timeout_ms: int):
    try:
        resp = request_context.head(url, timeout=timeout_ms)
        if resp.status in (405, 501):   # HEAD 미지원 → GET 재시도
            resp = request_context.get(url, timeout=timeout_ms)
        return resp.status, ""
    except Exception as err:
        return None, str(err).splitlines()[0][:150]
