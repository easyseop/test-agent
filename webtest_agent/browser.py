"""Playwright 브라우저 세션 관리와 페이지 신호 감시."""
from __future__ import annotations

import os
from glob import glob
from pathlib import Path

from playwright.sync_api import Browser, BrowserContext, Dialog, Page, sync_playwright

from .models import HttpFailure

VIEWPORT = {"width": 1280, "height": 800}
_IGNORED_URL_SUFFIXES = ("/favicon.ico",)


def find_chromium_executable() -> str | None:
    """사전 설치된 Chromium 실행 파일 탐색 (Playwright 기본 경로 실패 시 폴백)."""
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    patterns = [
        "chromium-*/chrome-linux/chrome",
        "chromium/chrome-linux/chrome",
        "chromium_headless_shell-*/chrome-linux/headless_shell",
    ]
    for pat in patterns:
        hits = sorted(glob(str(Path(root) / pat)))
        if hits:
            return hits[-1]
    return None


class PageMonitor:
    """콘솔 에러·페이지 예외·HTTP 실패·다이얼로그·다운로드·팝업·네비게이션 수집.

    다이얼로그는 안전을 위해 기본 '취소(dismiss)'로 응답한다.
    """

    def __init__(self) -> None:
        self.console_errors: list[str] = []
        self.page_errors: list[str] = []
        self.http_failures: list[HttpFailure] = []
        self.dialogs: list[str] = []
        self.downloads: list[str] = []
        self.popups: list[str] = []
        self.navigations: int = 0

    def attach(self, page: Page) -> None:
        page.on("console", self._on_console)
        page.on("pageerror", lambda err: self.page_errors.append(str(err)))
        page.on("response", self._on_response)
        page.on("requestfailed", self._on_request_failed)
        page.on("dialog", self._on_dialog)
        page.on("download", lambda d: self.downloads.append(d.suggested_filename))
        page.on("framenavigated", self._on_nav)
        page.on("popup", self._on_popup)

    @staticmethod
    def _ignored(url: str) -> bool:
        return url.split("?")[0].endswith(_IGNORED_URL_SUFFIXES)

    def _on_console(self, msg) -> None:
        if msg.type == "error":
            self.console_errors.append(msg.text)

    def _on_response(self, resp) -> None:
        try:
            if resp.status >= 400 and not self._ignored(resp.url):
                self.http_failures.append(HttpFailure(url=resp.url, status=resp.status))
        except Exception:
            pass

    def _on_request_failed(self, req) -> None:
        failure = req.failure or ""
        if "ERR_ABORTED" in failure or self._ignored(req.url):
            return  # 페이지 이탈로 인한 취소는 실패가 아님
        self.http_failures.append(HttpFailure(url=req.url, status=None, detail=failure))

    def _on_dialog(self, dialog: Dialog) -> None:
        self.dialogs.append(f"{dialog.type}: {dialog.message}")
        try:
            dialog.dismiss()
        except Exception:
            pass

    def _on_nav(self, frame) -> None:
        if frame.parent_frame is None:
            self.navigations += 1

    def _on_popup(self, popup: Page) -> None:
        self.popups.append(popup.url)
        try:
            popup.close()
        except Exception:
            pass


class BrowserSession:
    def __init__(self, headless: bool = True) -> None:
        self.headless = headless
        self._pw = None
        self.browser: Browser | None = None

    def __enter__(self) -> "BrowserSession":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    def start(self) -> None:
        self._pw = sync_playwright().start()
        try:
            self.browser = self._pw.chromium.launch(headless=self.headless)
        except Exception:
            exe = find_chromium_executable()
            if not exe:
                raise
            self.browser = self._pw.chromium.launch(headless=self.headless, executable_path=exe)

    def stop(self) -> None:
        try:
            if self.browser:
                self.browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    @property
    def version(self) -> str:
        return self.browser.version if self.browser else ""

    def new_context(
        self,
        base_url: str,
        video_dir: Path | None = None,
        trace: bool = False,
    ) -> tuple[BrowserContext, Page, PageMonitor]:
        kwargs: dict = {
            "viewport": VIEWPORT,
            "base_url": base_url,
            "accept_downloads": True,
            "locale": "ko-KR",
        }
        if video_dir is not None:
            video_dir.mkdir(parents=True, exist_ok=True)
            kwargs["record_video_dir"] = str(video_dir)
            kwargs["record_video_size"] = VIEWPORT
        ctx = self.browser.new_context(**kwargs)
        if trace:
            ctx.tracing.start(screenshots=True, snapshots=True)
        page = ctx.new_page()
        monitor = PageMonitor()
        monitor.attach(page)
        return ctx, page, monitor


def save_screenshot(page: Page, path: Path, full_page: bool = False) -> str:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), type="jpeg", quality=60, full_page=full_page)
        return str(path)
    except Exception:
        return ""
