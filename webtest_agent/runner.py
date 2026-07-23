"""시나리오 실행(Execute)과 판정(Verify)."""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from .browser import BrowserSession, save_screenshot
from .config import AgentConfig, Step
from .datacheck import compare, extract_table, parse_count, run_query
from .models import FAIL, PASS, WARN, DataCheckResult, ScenarioResult, StepResult
from .scenarios import Scenario, slugify


class ScenarioTimeout(Exception):
    pass


def _short(err: Exception) -> str:
    text = str(err).strip().splitlines()
    return (text[0] if text else err.__class__.__name__)[:300]


def describe_step(step: Step) -> str:
    sel = f"'{step.selector}'"
    return {
        "goto": f"{step.value} 페이지로 이동한다",
        "click": f"{sel} 요소를 클릭한다",
        "fill": f"{sel}에 '{step.value}'를 입력한다",
        "select": f"{sel}에서 '{step.value}'를 선택한다",
        "check": f"{sel} 체크박스를 선택한다",
        "press": f"{sel}에서 {step.value} 키를 누른다",
        "wait_for": f"{sel} 요소가 나타날 때까지 기다린다",
        "wait_ms": f"{step.value}ms 동안 기다린다",
        "assert_visible": f"{sel} 요소가 화면에 보이는지 확인한다",
        "assert_text": f"{sel} 요소에 '{step.value}' 텍스트가 있는지 확인한다",
        "assert_url": f"주소(URL)가 '{step.value}' 패턴과 일치하는지 확인한다",
    }[step.action]


def _dom_size(page) -> int:
    try:
        return page.evaluate("() => document.body ? document.body.innerHTML.length : 0")
    except Exception:
        return -1


class Runner:
    def __init__(self, session: BrowserSession, cfg: AgentConfig, run_dir: Path) -> None:
        self.session = session
        self.cfg = cfg
        self.run_dir = run_dir
        (run_dir / "videos").mkdir(parents=True, exist_ok=True)
        (run_dir / "traces").mkdir(parents=True, exist_ok=True)

    def _rel(self, path: str | Path) -> str:
        if not path:
            return ""
        return Path(path).relative_to(self.run_dir).as_posix()

    def run(self, scenario: Scenario, index: int, suffix: str = "") -> ScenarioResult:
        res = ScenarioResult(
            name=scenario.name, kind=scenario.kind,
            page=scenario.page, description=scenario.description,
        )
        slug = f"{index:02d}_{slugify(scenario.name)}" + (f"_{suffix}" if suffix else "")
        shots_dir = self.run_dir / "screenshots" / slug
        video_tmp = (self.run_dir / "videos" / f"_tmp_{slug}") if self.cfg.report.video else None
        started = time.monotonic()
        ctx = page = video = None
        hard_fail = False

        try:
            ctx, page, monitor = self.session.new_context(
                self.cfg.target.base_url, video_dir=video_tmp, trace=self.cfg.report.trace)
            page.set_default_timeout(5000)

            page.goto(scenario.page or "/", wait_until="load",
                      timeout=self.cfg.target.nav_timeout_ms)
            page.wait_for_timeout(self.cfg.target.settle_ms)
            res.steps.append(StepResult(
                index=0, action="goto",
                description=f"{scenario.page or '/'} 페이지에 접속한다",
                screenshot=self._rel(save_screenshot(page, shots_dir / "step00.jpg")),
            ))

            # 초기 로드에서 발생한 신호는 액션 판정과 분리한다
            base_console = len(monitor.console_errors)
            base_pageerr = len(monitor.page_errors)
            base_http = len(monitor.http_failures)
            base_dialog = len(monitor.dialogs)
            base_down = len(monitor.downloads)
            base_popup = len(monitor.popups)
            pre_nav = monitor.navigations
            pre_url = page.url
            pre_dom = _dom_size(page)

            step_failed = False
            for i, step in enumerate(scenario.steps, start=1):
                if (time.monotonic() - started) * 1000 > self.cfg.target.scenario_timeout_ms:
                    raise ScenarioTimeout
                sr = StepResult(index=i, action=step.action, selector=step.selector,
                                value=step.value, description=describe_step(step))
                t0 = time.monotonic()
                try:
                    self._exec_step(page, step)
                    page.wait_for_timeout(self.cfg.target.settle_ms)
                    sr.screenshot = self._rel(save_screenshot(page, shots_dir / f"step{i:02d}.jpg"))
                except Exception as err:
                    sr.status = "fail"
                    sr.error = _short(err)
                    sr.screenshot = self._rel(save_screenshot(page, shots_dir / f"step{i:02d}_fail.jpg"))
                    res.steps.append(sr)
                    res.reasons.append(f"스텝 {i} 실패 — {sr.description}: {sr.error}")
                    step_failed = True
                    break
                sr.duration_ms = int((time.monotonic() - t0) * 1000)
                res.steps.append(sr)

            res.console_errors = monitor.console_errors[base_console:]
            res.page_errors = monitor.page_errors[base_pageerr:]
            res.http_failures = monitor.http_failures[base_http:]
            res.dialogs = monitor.dialogs[base_dialog:]
            res.downloads = monitor.downloads[base_down:]

            if scenario.kind.startswith("sweep") and not step_failed:
                effects = []
                if page.url != pre_url:
                    effects.append("페이지 이동")
                elif monitor.navigations > pre_nav:
                    effects.append("네비게이션/새로고침")
                if _dom_size(page) != pre_dom:
                    effects.append("화면 내용 변화")
                if res.dialogs:
                    effects.append("다이얼로그 표시")
                if res.downloads:
                    effects.append("파일 다운로드")
                if len(monitor.popups) > base_popup:
                    effects.append("새 창 열림")
                res.effect = ", ".join(effects) if effects else "무반응"

            if scenario.kind == "data_check" and not step_failed:
                res.data_check = self._data_check(page, scenario)
                save_screenshot(page, shots_dir / "result.jpg", full_page=True)

        except ScenarioTimeout:
            hard_fail = True
            res.reasons.append(f"시나리오 타임아웃 초과 ({self.cfg.target.scenario_timeout_ms}ms)")
        except Exception as err:
            hard_fail = True
            res.reasons.append(f"실행 오류: {_short(err)}")
        finally:
            if ctx is not None:
                if page is not None and self.cfg.report.video:
                    video = page.video
                if self.cfg.report.trace:
                    try:
                        trace_path = self.run_dir / "traces" / f"{slug}.zip"
                        ctx.tracing.stop(path=str(trace_path))
                        res.trace = self._rel(trace_path)
                    except Exception:
                        pass
                try:
                    ctx.close()
                except Exception:
                    pass
                if video is not None:
                    try:
                        final = self.run_dir / "videos" / f"{slug}.webm"
                        Path(video.path()).replace(final)
                        res.video = self._rel(final)
                    except Exception:
                        pass
                if video_tmp is not None:
                    shutil.rmtree(video_tmp, ignore_errors=True)

        res.duration_ms = int((time.monotonic() - started) * 1000)
        self._verdict(res, scenario, hard_fail)
        return res

    def _exec_step(self, page, step: Step) -> None:
        action = step.action
        locator = page.locator(step.selector).first if step.selector else None
        if action == "goto":
            page.goto(step.value, wait_until="load", timeout=self.cfg.target.nav_timeout_ms)
        elif action == "click":
            locator.click()
            try:
                page.wait_for_load_state("load", timeout=3000)
            except Exception:
                pass
        elif action == "fill":
            locator.fill(step.value)
        elif action == "select":
            locator.select_option(step.value)
        elif action == "check":
            locator.check()
        elif action == "press":
            locator.press(step.value)
        elif action == "wait_for":
            page.wait_for_selector(step.selector, state="visible", timeout=10000)
        elif action == "wait_ms":
            page.wait_for_timeout(int(step.value))
        elif action == "assert_visible":
            try:
                page.wait_for_selector(step.selector, state="visible", timeout=3000)
            except Exception:
                raise AssertionError(f"요소 '{step.selector}'가 화면에 보이지 않습니다")
        elif action == "assert_text":
            actual = page.locator(step.selector).first.inner_text(timeout=3000)
            if step.value not in actual:
                raise AssertionError(
                    f"기대 텍스트 '{step.value}'가 없습니다 (실제: '{actual[:80]}')")
        elif action == "assert_url":
            if not re.search(step.value, page.url):
                raise AssertionError(f"URL이 패턴 '{step.value}'와 일치하지 않습니다 (실제: {page.url})")

    def _data_check(self, page, scenario: Scenario) -> DataCheckResult:
        spec = scenario.spec
        try:
            headers, rows = extract_table(page, spec.ui_table.selector)
        except Exception as err:
            return DataCheckResult(note=f"UI 테이블 추출 실패: {_short(err)}")
        try:
            db_cols, db_rows = run_query(spec.query.db, spec.query.sql)
        except Exception as err:
            return DataCheckResult(note=f"DB 쿼리 실패: {_short(err)}")

        result = compare(headers, rows, spec.ui_table.columns,
                         db_cols, db_rows, spec.query.order_matters)

        if spec.ui_table.count_selector:
            el = page.query_selector(spec.ui_table.count_selector)
            if el is not None:
                result.count_display = parse_count(el.inner_text())
                if result.count_display is not None:
                    result.count_display_ok = result.count_display == result.ui_count
        return result

    def _verdict(self, res: ScenarioResult, scenario: Scenario, hard_fail: bool) -> None:
        if res.console_errors:
            res.reasons.append(f"콘솔 에러 {len(res.console_errors)}건: {res.console_errors[0][:200]}")
        if res.page_errors:
            res.reasons.append(f"페이지 예외 {len(res.page_errors)}건: {res.page_errors[0][:200]}")
        if res.http_failures:
            first = res.http_failures[0]
            status = first.status if first.status is not None else first.detail
            res.reasons.append(f"HTTP 실패 {len(res.http_failures)}건: {first.url} ({status})")

        dc = res.data_check
        dc_failed = False
        if dc is not None:
            if dc.note:
                dc_failed = True
                res.reasons.append(f"검증 불가: {dc.note}")
            elif not dc.matched:
                dc_failed = True
                res.reasons.append(
                    f"UI↔DB 불일치: 화면 {dc.ui_count}건 vs DB {dc.db_count}건 "
                    f"(화면에 누락 {dc.missing_total}건, 화면에 초과 {dc.unexpected_total}건)")
            if dc.count_display_ok is False:
                dc_failed = True
                res.reasons.append(f"건수 표기({dc.count_display}건) ≠ 실제 표 행 수({dc.ui_count}건)")

        step_failed = any(s.status == "fail" for s in res.steps)
        if (hard_fail or step_failed or res.console_errors or res.page_errors
                or res.http_failures or dc_failed):
            res.status = FAIL
        elif scenario.kind.startswith("sweep") and res.effect == "무반응":
            res.status = WARN
            res.reasons.append("클릭 후 관찰 가능한 변화가 없음 (죽은 버튼 후보)")
        else:
            res.status = PASS

        if res.dialogs:
            res.reasons.append(f"다이얼로그 {len(res.dialogs)}건 발생 — 자동 '취소' 응답 (정보)")
