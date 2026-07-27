"""시나리오 실행(Execute)과 판정(Verify)."""
from __future__ import annotations

import os
import re
import shutil
import time
from pathlib import Path

from .browser import BrowserSession, save_screenshot
from .config import MASK, AgentConfig, Step
from .datacheck import (compare, extract_table, parse_count, run_api_query,
                        run_query, run_scalar_query)
from .models import (FAIL, PASS, WARN, DataCheckResult, ScenarioResult,
                     StepResult, VisualResult, WriteCheckResult)
from .scenarios import Scenario, slugify
from .safety import find_destructive, find_hard_block
from .visual import compare_images


class ScenarioTimeout(Exception):
    pass


class RunDeadlineExceeded(ScenarioTimeout):
    pass


def _short(err: Exception) -> str:
    text = str(err).strip().splitlines()
    return (text[0] if text else err.__class__.__name__)[:300]


def _scrub(text: str, step: Step | None) -> str:
    """오류 메시지에 비밀값이 섞여 나오는 경우를 대비한 최종 방어."""
    if step is None or not step.secret or not step.value:
        return text
    return text.replace(step.value, MASK)


def describe_step(step: Step) -> str:
    """스텝 설명 — 리포트·절차서에 그대로 실리므로 비밀값은 마스킹된 값을 쓴다."""
    sel = f"'{step.selector}'"
    val = step.log_value
    return {
        "goto": f"{val} 페이지로 이동한다",
        "click": f"{sel} 요소를 클릭한다",
        "fill": f"{sel}에 '{val}'를 입력한다",
        "select": f"{sel}에서 '{val}'를 선택한다",
        "check": f"{sel} 체크박스를 선택한다",
        "press": f"{sel}에서 {val} 키를 누른다",
        "wait_for": f"{sel} 요소가 나타날 때까지 기다린다",
        "wait_ms": f"{val}ms 동안 기다린다",
        "assert_visible": f"{sel} 요소가 화면에 보이는지 확인한다",
        "assert_text": f"{sel} 요소에 '{val}' 텍스트가 있는지 확인한다",
        "assert_url": f"주소(URL)가 '{val}' 패턴과 일치하는지 확인한다",
    }[step.action]


def _dom_size(page) -> int:
    try:
        return page.evaluate("() => document.body ? document.body.innerHTML.length : 0")
    except Exception:
        return -1


class Runner:
    def __init__(self, session: BrowserSession, cfg: AgentConfig, run_dir: Path,
                 update_baselines: bool = False, allow_write_checks: bool = False,
                 deadline_monotonic: float | None = None) -> None:
        self.session = session
        self.cfg = cfg
        self.run_dir = run_dir
        self.update_baselines = update_baselines
        self.allow_write_checks = allow_write_checks
        self.deadline_monotonic = deadline_monotonic
        (run_dir / "videos").mkdir(parents=True, exist_ok=True)
        (run_dir / "traces").mkdir(parents=True, exist_ok=True)

    def _rel(self, path: str | Path) -> str:
        if not path:
            return ""
        return Path(path).relative_to(self.run_dir).as_posix()

    def _shot(self, page, path: Path, full_page: bool = False) -> str:
        return save_screenshot(page, path, full_page=full_page,
                               mask_selectors=self.cfg.report.mask_selectors)

    def _http_failures(self, failures):
        """명시적으로 허용한 URL 패턴만 제외하고 HTTP 실패를 복사한다."""
        patterns = [
            re.compile(pattern)
            for pattern in self.cfg.target.ignore_http_error_patterns
        ]
        return [
            failure for failure in failures
            if not any(pattern.search(failure.url) for pattern in patterns)
        ]

    def _bounded_timeout(self, requested_ms: int) -> int:
        if self.deadline_monotonic is None:
            return requested_ms
        remaining = int((self.deadline_monotonic - time.monotonic()) * 1000)
        if remaining <= 0:
            raise RunDeadlineExceeded
        return max(1, min(requested_ms, remaining))

    def _wait(self, page, requested_ms: int) -> None:
        bounded = self._bounded_timeout(requested_ms)
        page.wait_for_timeout(bounded)
        if bounded < requested_ms:
            raise RunDeadlineExceeded

    def authenticate(self, steps, state_path: Path) -> None:
        """로그인 스텝을 1회 수행하고 세션(storage_state)을 저장한다."""
        state_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(state_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
        ctx, page, _monitor = self.session.new_context(self.cfg.target.base_url)
        try:
            page.set_default_timeout(5000)
            for step in steps:
                page.set_default_timeout(self._bounded_timeout(5000))
                self._exec_step(page, step)
                self._wait(page, self.cfg.target.settle_ms)
            ctx.storage_state(path=str(state_path))
            os.chmod(state_path, 0o600)
        finally:
            ctx.close()

    def run(self, scenario: Scenario, index: int, suffix: str = "") -> ScenarioResult:
        res = ScenarioResult(
            name=scenario.name, kind=scenario.kind,
            page=scenario.page, description=scenario.description,
        )
        started = time.monotonic()

        # CLI 계획 단계를 우회해 Runner를 직접 호출해도 쓰기 경계를 넘지 못한다.
        if scenario.kind == "write_check" and not self.allow_write_checks:
            res.reasons.append(
                "안전 차단: write_checks는 --allow-write-checks 승인 없이 실행할 수 없습니다"
            )
            res.duration_ms = int((time.monotonic() - started) * 1000)
            self._verdict(res, scenario, hard_fail=True)
            return res

        if scenario.kind.startswith("sweep"):
            selector = scenario.steps[0].selector if scenario.steps else ""
            hit = find_hard_block(scenario.element_text, selector)
            if hit:
                res.reasons.append(
                    f"안전 차단: 자동 탐색은 쓰기·외부 전송·세션 변경 동작을 "
                    f"클릭하지 않습니다 ({hit})"
                )
                res.duration_ms = int((time.monotonic() - started) * 1000)
                self._verdict(res, scenario, hard_fail=True)
                return res
        elif scenario.kind != "write_check":
            # 사람이 적은 스텝이라도 되돌릴 수 없는 동작은 승인 없이 실행하지 않는다.
            # (승인받은 write_check만 예외 — 위 kind 분기에서 제외됨)
            hit = next(
                (h for step in scenario.steps if step.action == "click"
                 and (h := find_destructive(step.selector))),
                None,
            )
            if hit:
                res.reasons.append(
                    f"안전 차단: 파괴적 동작으로 보이는 클릭이 있습니다 ({hit}) — "
                    "의도한 상태 변경이면 write_checks로 옮기고 --allow-write-checks로 승인하세요"
                )
                res.duration_ms = int((time.monotonic() - started) * 1000)
                self._verdict(res, scenario, hard_fail=True)
                return res

        slug = f"{index:02d}_{slugify(scenario.name)}" + (f"_{suffix}" if suffix else "")
        shots_dir = self.run_dir / "screenshots" / slug
        video_tmp = (self.run_dir / "videos" / f"_tmp_{slug}") if self.cfg.report.video else None
        ctx = page = video = None
        hard_fail = False

        try:
            ctx, page, monitor = self.session.new_context(
                self.cfg.target.base_url, video_dir=video_tmp, trace=self.cfg.report.trace)
            page.set_default_timeout(self._bounded_timeout(5000))

            page.goto(scenario.page or "/", wait_until="load",
                      timeout=self._bounded_timeout(self.cfg.target.nav_timeout_ms))
            self._wait(page, self.cfg.target.settle_ms)
            res.steps.append(StepResult(
                index=0, action="goto",
                description=f"{scenario.page or '/'} 페이지에 접속한다",
                screenshot=self._rel(self._shot(page, shots_dir / "step00.jpg")),
            ))

            # 초기 page.goto 중 발생한 오류도 시나리오 판정에 포함한다.
            base_dialog = len(monitor.dialogs)
            base_down = len(monitor.downloads)
            base_popup = len(monitor.popups)
            pre_nav = monitor.navigations
            pre_url = page.url
            pre_dom = _dom_size(page)

            write_pre: float | None = None
            if scenario.kind == "write_check":
                spec = scenario.write_spec
                write_pre = run_scalar_query(spec.query.db, spec.query.sql)

            step_failed = False
            for i, step in enumerate(scenario.steps, start=1):
                if (time.monotonic() - started) * 1000 > self.cfg.target.scenario_timeout_ms:
                    raise ScenarioTimeout
                page.set_default_timeout(self._bounded_timeout(5000))
                sr = StepResult(index=i, action=step.action, selector=step.selector,
                                value=step.log_value, description=describe_step(step))
                t0 = time.monotonic()
                try:
                    self._exec_step(page, step)
                    self._wait(page, self.cfg.target.settle_ms)
                    sr.screenshot = self._rel(self._shot(page, shots_dir / f"step{i:02d}.jpg"))
                except RunDeadlineExceeded:
                    raise
                except Exception as err:
                    sr.status = "fail"
                    sr.error = _scrub(_short(err), step)
                    sr.screenshot = self._rel(self._shot(page, shots_dir / f"step{i:02d}_fail.jpg"))
                    res.steps.append(sr)
                    res.reasons.append(f"스텝 {i} 실패 — {sr.description}: {sr.error}")
                    step_failed = True
                    break
                sr.duration_ms = int((time.monotonic() - t0) * 1000)
                res.steps.append(sr)

            res.console_errors = list(monitor.console_errors)
            res.page_errors = list(monitor.page_errors)
            res.http_failures = self._http_failures(monitor.http_failures)
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
                self._shot(page, shots_dir / "result.jpg", full_page=True)

            if scenario.kind == "visual_check" and not step_failed:
                res.visual = self._visual_check(page, scenario, shots_dir)

            if scenario.kind == "write_check" and not step_failed and write_pre is not None:
                spec = scenario.write_spec
                try:
                    post = run_scalar_query(spec.query.db, spec.query.sql)
                    delta = post - write_pre
                    res.write_check = WriteCheckResult(
                        pre=write_pre, post=post, delta=delta,
                        expected_delta=spec.expect_delta,
                        matched=delta == spec.expect_delta,
                    )
                except Exception as err:
                    res.write_check = WriteCheckResult(
                        pre=write_pre, expected_delta=spec.expect_delta,
                        note=f"사후 측정 실패: {_short(err)}")

        except RunDeadlineExceeded:
            hard_fail = True
            res.reasons.append(
                f"전체 실행 제한 시간 초과 ({self.cfg.target.run_timeout_ms}ms)"
            )
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
            page.goto(
                step.value,
                wait_until="load",
                timeout=self._bounded_timeout(self.cfg.target.nav_timeout_ms),
            )
        elif action == "click":
            locator.click()
            load_timeout = self._bounded_timeout(3000)
            try:
                page.wait_for_load_state("load", timeout=load_timeout)
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
            page.wait_for_selector(
                step.selector,
                state="visible",
                timeout=self._bounded_timeout(10000),
            )
        elif action == "wait_ms":
            self._wait(page, int(step.value))
        elif action == "assert_visible":
            timeout = self._bounded_timeout(3000)
            try:
                page.wait_for_selector(step.selector, state="visible", timeout=timeout)
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

    def _extract_all_pages(self, page, spec) -> tuple[list[str], list[list[str]]]:
        """표 추출 — pagination 설정 시 '다음' 버튼을 순회하며 전체 행을 수집."""
        headers, rows = extract_table(page, spec.ui_table.selector)
        pag = spec.ui_table.pagination
        if not pag:
            return headers, rows
        for _ in range(pag.max_pages - 1):
            # 순회도 전체 실행 제한 시간에 묶는다 (넘기면 RunDeadlineExceeded)
            page.set_default_timeout(self._bounded_timeout(5000))
            nxt = page.query_selector(pag.next_selector)
            if nxt is None or not nxt.is_visible() or nxt.is_disabled():
                break
            nxt.click()
            self._wait(page, self.cfg.target.settle_ms)
            _, more = extract_table(page, spec.ui_table.selector)
            if not more:
                break
            rows += more
        return headers, rows

    def _visual_check(self, page, scenario: Scenario, shots_dir: Path) -> VisualResult:
        spec = scenario.visual_spec
        baseline = Path(self.cfg.baselines_dir) / f"{slugify(spec.name)}.png"
        current = shots_dir / "visual_current.png"
        current.parent.mkdir(parents=True, exist_ok=True)

        masks = [page.locator(s) for s in self.cfg.report.mask_selectors] or None
        try:
            if spec.selector:
                page.locator(spec.selector).first.screenshot(path=str(current), mask=masks)
            else:
                page.screenshot(path=str(current), full_page=spec.full_page, mask=masks)
        except Exception as err:
            return VisualResult(note=f"화면 캡처 실패: {_short(err)}", threshold=spec.threshold)

        result = VisualResult(threshold=spec.threshold,
                              baseline=str(baseline), current=self._rel(current))
        if self.update_baselines or not baseline.exists():
            baseline.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(current, baseline)
            result.matched = True
            result.baseline_updated = self.update_baselines and not result.baseline_created
            result.baseline_created = not self.update_baselines
            return result

        try:
            diff_path = shots_dir / "visual_diff.png"
            ratio, note = compare_images(baseline, current, diff_path)
            result.ratio = ratio
            result.note = note
            result.matched = ratio <= spec.threshold and not note
            if diff_path.exists():
                result.diff = self._rel(diff_path)
        except Exception as err:
            result.note = f"이미지 비교 실패: {_short(err)}"
        return result

    def _data_check(self, page, scenario: Scenario) -> DataCheckResult:
        spec = scenario.spec
        try:
            headers, rows = self._extract_all_pages(page, spec)
        except RunDeadlineExceeded:
            raise   # 제한 시간 초과는 검증 실패가 아니라 실행 불가로 올린다
        except Exception as err:
            return DataCheckResult(note=f"UI 테이블 추출 실패: {_short(err)}")
        try:
            if spec.query.api:
                db_cols, db_rows = run_api_query(spec.query.api, self.cfg.target.base_url)
            else:
                db_cols, db_rows = run_query(spec.query.db, spec.query.sql)
        except Exception as err:
            return DataCheckResult(note=f"정답원(DB/API) 조회 실패: {_short(err)}")

        result = compare(headers, rows, spec.ui_table.columns,
                         db_cols, db_rows, spec.query.order_matters)

        if spec.ui_table.count_selector:
            el = page.query_selector(spec.ui_table.count_selector)
            if el is not None:
                result.count_display = parse_count(el.inner_text())
                if result.count_display is not None:
                    # 페이지네이션 시 건수 표기는 전체 건수 기준 → 누적 행 수와 비교
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

        vis = res.visual
        vis_failed = vis_warn = False
        if vis is not None:
            if vis.baseline_created:
                res.reasons.append(f"시각 기준선 생성됨: {vis.baseline} (다음 실행부터 비교)")
            elif vis.baseline_updated:
                res.reasons.append(f"시각 기준선 갱신됨(--update-baselines): {vis.baseline}")
            elif vis.note and "실패" in vis.note:
                vis_failed = True
                res.reasons.append(f"시각 비교 불가: {vis.note}")
            elif not vis.matched:
                msg = (f"화면이 기준선과 {vis.ratio * 100:.2f}% 다릅니다"
                       f" (허용 {vis.threshold * 100:.2f}%)"
                       + (f" — {vis.note}" if vis.note else "")
                       + " · 의도된 변경이면 `run --update-baselines`로 기준선 갱신")
                if scenario.visual_spec and scenario.visual_spec.severity == "fail":
                    vis_failed = True
                else:
                    vis_warn = True
                res.reasons.append(msg)

        wc = res.write_check
        wc_failed = False
        if wc is not None:
            if wc.note:
                wc_failed = True
                res.reasons.append(f"쓰기 검증 불가: {wc.note}")
            elif not wc.matched:
                wc_failed = True
                res.reasons.append(
                    f"상태 전이 불일치: 사전 {wc.pre:g} → 사후 {wc.post:g}"
                    f" (변화 {wc.delta:+g}, 기대 {wc.expected_delta:+d})")

        step_failed = any(s.status == "fail" for s in res.steps)
        if (hard_fail or step_failed or res.console_errors or res.page_errors
                or res.http_failures or dc_failed or wc_failed or vis_failed):
            res.status = FAIL
        elif vis_warn:
            res.status = WARN
        elif scenario.kind.startswith("sweep") and res.effect == "무반응":
            res.status = WARN
            res.reasons.append("클릭 후 관찰 가능한 변화가 없음 (죽은 버튼 후보)")
        else:
            res.status = PASS

        if res.dialogs:
            res.reasons.append(f"다이얼로그 {len(res.dialogs)}건 발생 — 자동 '취소' 응답 (정보)")
