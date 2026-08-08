"""시나리오 실행(Execute)과 판정(Verify)."""
from __future__ import annotations

import os
import re
import shutil
import time
from dataclasses import replace
from pathlib import Path

from .browser import BrowserSession, save_screenshot
from .config import MASK, AgentConfig, Step, substitute_vars
from .datacheck import (compare, extract_table, parse_count, run_api_query,
                        run_query, run_scalar_query)
from .models import (FAIL, PASS, WARN, DataCheckResult, PerfResult,
                     ResponsiveResult, ScenarioResult, StepResult,
                     ViewportResult, VisualResult, WriteCheckResult)
from .scenarios import Scenario, slugify
from .safety import find_destructive, find_hard_block
from .visual import compare_images


class ScenarioTimeout(Exception):
    pass


class RunDeadlineExceeded(ScenarioTimeout):
    pass


class BrowserGoneError(RuntimeError):
    """브라우저/컨텍스트가 예기치 않게 닫힘 — 제품 실패가 아니라 실행 불가."""


# Playwright가 브라우저/드라이버 자체의 소실에만 쓰는 구체적 문구로 한정한다.
# "has been closed"·"crashed" 같은 넓은 조각은 앱의 다이얼로그·웹소켓·모달
# 오류(정상 제품 실패)까지 삼켜 infra로 오분류하므로 쓰지 않는다.
_BROWSER_GONE_MARKERS = (
    "Target page, context or browser has been closed",
    "Target closed",
    "TargetClosedError",
    "Browser has been closed",
    "Browser closed",
    "browserContext.newPage",          # 컨텍스트 생성 실패 = 브라우저 소실
    "BrowserType.launch",
    "Browser.newContext",
    "has crashed",                     # "Page has crashed" 등 Playwright 문구
    "Page crashed",
    # 드라이버(Playwright node 프로세스)가 죽은 경우. 제품 코드로는 절대 만들 수
    # 없는 문구이므로 오탐 위험 없이 실행 불가로 볼 수 있다.
    "Connection closed",
    "while reading from the driver",
)


def _is_browser_gone(err: Exception) -> bool:
    text = str(err)
    return any(marker in text for marker in _BROWSER_GONE_MARKERS)


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
        "assert_not_visible": f"{sel} 요소가 화면에 보이지 않는지 확인한다",
        "assert_not_text": f"{sel} 요소에 '{val}' 텍스트가 없는지 확인한다",
        "extract": f"{sel}의 값을 읽어 '{step.store_as}'에 담는다",
    }[step.action]


JS_OVERFLOW = """(maxOverflow) => {
  const doc = document.scrollingElement || document.documentElement;
  const overflow = doc.scrollWidth - doc.clientWidth;
  const offenders = [];
  if (overflow > maxOverflow) {
    const limit = doc.clientWidth + maxOverflow;
    const seen = new Set();
    for (const el of document.querySelectorAll('body *')) {
      const r = el.getBoundingClientRect();
      // 화면 밖으로 나간 요소 중, 자식이 아닌 실제 원인에 가까운 것만 추린다
      if (r.right > limit && el.children.length < 12) {
        const tag = el.tagName.toLowerCase();
        const cls = (typeof el.className === 'string' && el.className)
          ? '.' + el.className.trim().split(/\\s+/)[0] : '';
        const id = el.id ? '#' + el.id : '';
        const key = tag + id + cls;
        if (!seen.has(key)) {
          seen.add(key);
          offenders.push(key + ' (right=' + Math.round(r.right) + ')');
          if (offenders.length >= 8) break;
        }
      }
    }
  }
  return { scrollWidth: doc.scrollWidth, clientWidth: doc.clientWidth, overflow, offenders };
}"""


JS_PERF = """(metric) => {
  const nav = performance.getEntriesByType('navigation')[0];
  if (!nav) return { value: -1, note: 'navigation timing 없음' };
  const t = {
    load: nav.loadEventEnd,
    dcl: nav.domContentLoadedEventEnd,
    response: nav.responseEnd,
  };
  if (metric === 'fcp') {
    const fcp = performance.getEntriesByType('paint')
      .find(e => e.name === 'first-contentful-paint');
    return { value: fcp ? fcp.startTime : -1, note: fcp ? '' : 'FCP 미측정' };
  }
  const v = t[metric];
  return { value: (v === undefined || v === null) ? -1 : v, note: '' };
}"""


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
        # 화면에서 뽑은 값. 시나리오가 시작될 때마다 비운다 — 시나리오 사이로 값이
        # 새면 실행 순서에 의존이 생겨 병렬 실행과 결과 독립성이 깨진다.
        self._vars: dict[str, str] = {}
        self._secret_vars: set[str] = set()
        (run_dir / "videos").mkdir(parents=True, exist_ok=True)
        (run_dir / "traces").mkdir(parents=True, exist_ok=True)

    def _reset_vars(self) -> None:
        self._vars = {}
        self._secret_vars = set()

    def _ensure_vars(self) -> None:
        """변수 저장소가 반드시 있게 한다.

        `Runner.__new__`로 __init__을 건너뛰고 만든 인스턴스(스텝 단위 테스트가
        쓰는 방식)에서도 스텝 실행이 되어야 한다. 클래스 변수로 기본값을 두면
        인스턴스끼리 같은 dict를 공유해 값이 새므로 그렇게 하지 않는다.
        """
        if "_vars" not in self.__dict__:
            self._reset_vars()

    def _resolve(self, text: str | None) -> str | None:
        """`{{변수}}`를 이번 시나리오에서 뽑은 값으로 바꾼다."""
        if text is None:
            return None
        self._ensure_vars()
        try:
            return substitute_vars(text, self._vars)
        except KeyError as err:
            raise AssertionError(
                f"변수 '{{{{{err.args[0]}}}}}'가 아직 추출되지 않았습니다") from err

    def _mask_vars(self, text: str) -> str:
        """비밀로 판정된 추출값이 메시지·증거에 섞여 나오지 않게 가린다."""
        self._ensure_vars()
        for name in self._secret_vars:
            value = self._vars.get(name)
            if value:
                text = text.replace(value, MASK)
        return text

    def _query_params(self, query) -> dict[str, str]:
        """정답 쿼리에 넘길 바인딩 값 — 변수 치환은 여기서만 일어난다."""
        return {name: self._resolve(raw) for name, raw in (query.params or {}).items()}

    def _rel(self, path: str | Path) -> str:
        if not path:
            return ""
        return Path(path).relative_to(self.run_dir).as_posix()

    def _shot(self, page, path: Path, full_page: bool = False) -> str:
        return save_screenshot(page, path, full_page=full_page,
                               mask_selectors=self.cfg.report.mask_selectors)

    def _ignore_patterns(self):
        return [re.compile(p) for p in self.cfg.target.ignore_http_error_patterns]

    def _http_failures(self, failures):
        """명시적으로 허용한 URL 패턴만 제외하고 HTTP 실패를 복사한다."""
        patterns = self._ignore_patterns()
        return [
            failure for failure in failures
            if not any(pattern.search(failure.url) for pattern in patterns)
        ]

    def _console_errors(self, monitor):
        """콘솔 에러도 HTTP 실패와 같은 허용 목록으로 거른다.

        리소스 로드 실패는 HTTP 실패와 콘솔 에러로 동시에 관측되므로, 한쪽만
        제외하면 정상으로 합의한 응답이 다른 쪽에서 실패로 되살아난다.
        """
        patterns = self._ignore_patterns()
        urls = getattr(monitor, "console_error_urls", [])
        return [
            text for index, text in enumerate(monitor.console_errors)
            if not (index < len(urls) and urls[index]
                    and any(p.search(urls[index]) for p in patterns))
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
        self._reset_vars()
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
        self._reset_vars()
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
                write_pre = run_scalar_query(spec.query.db, spec.query.sql,
                                             self._query_params(spec.query))

            step_failed = False
            for i, step in enumerate(scenario.steps, start=1):
                if (time.monotonic() - started) * 1000 > self.cfg.target.scenario_timeout_ms:
                    raise ScenarioTimeout
                page.set_default_timeout(self._bounded_timeout(5000))
                sr = StepResult(index=i, action=step.action, selector=step.selector,
                                value=step.log_value, description=describe_step(step))
                t0 = time.monotonic()
                try:
                    self._exec_step(page, step, record=sr)
                    self._wait(page, self.cfg.target.settle_ms)
                    sr.screenshot = self._rel(self._shot(page, shots_dir / f"step{i:02d}.jpg"))
                except RunDeadlineExceeded:
                    raise
                except Exception as err:
                    sr.status = "fail"
                    sr.error = self._mask_vars(_scrub(_short(err), step))
                    sr.screenshot = self._rel(self._shot(page, shots_dir / f"step{i:02d}_fail.jpg"))
                    res.steps.append(sr)
                    res.reasons.append(f"스텝 {i} 실패 — {sr.description}: {sr.error}")
                    step_failed = True
                    break
                sr.duration_ms = int((time.monotonic() - t0) * 1000)
                res.steps.append(sr)

            res.console_errors = self._console_errors(monitor)
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

            if scenario.kind == "responsive_check" and not step_failed:
                res.responsive = self._responsive_check(page, scenario, shots_dir)

            if scenario.kind == "perf_check" and not step_failed:
                res.perf = self._perf_check(page, scenario)

            if scenario.kind == "write_check" and not step_failed and write_pre is not None:
                spec = scenario.write_spec
                try:
                    post = run_scalar_query(spec.query.db, spec.query.sql,
                                            self._query_params(spec.query))
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
            # 브라우저/컨텍스트가 죽은 것은 제품 실패가 아니라 실행 불가다.
            # 여기서 실패로 처리하면 이후 시나리오도 줄줄이 거짓 실패가 된다.
            #
            # 문구 매칭만으로는 부족하다: Chromium이 죽으면 'Target closed'가 아니라
            # 그냥 Timeout으로 나타난다(실측). 타임아웃 문구를 실행 불가로 넣으면
            # 이번엔 진짜 제품 버그(느린 화면·안 뜨는 요소)가 실행 불가로 숨는다.
            # 그래서 문구 대신 브라우저 생존 여부를 직접 묻는다 — 엔진 문구에
            # 의존하지 않는 결정적 판별이다.
            if _is_browser_gone(err) or not self._browser_alive():
                raise BrowserGoneError(_short(err)) from err
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

    def _exec_step(self, page, step: Step, record=None) -> None:
        action = step.action
        # 셀렉터·값의 `{{변수}}`는 실행 시점에 푼다. 설정 로딩 시점에 푸는
        # `${환경변수}`와 달리 이 값은 방금 화면에서 읽은 것이다.
        selector = self._resolve(step.selector)
        value = self._resolve(step.value)
        # 절차서·리포트는 재현 문서다. 변수를 쓴 스텝은 원문이 아니라 그때 실제로
        # 쓰인 값을 남겨야 나중에 무슨 값이었는지 알 수 있다.
        if record is not None:
            if selector != step.selector:
                record.selector = selector
            if value != step.value:
                record.value = MASK if step.secret else value
                record.description = describe_step(
                    replace(step, selector=selector, value=value))
        locator = page.locator(selector).first if selector else None
        if action == "goto":
            page.goto(
                value,
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
            locator.fill(value)
        elif action == "select":
            locator.select_option(value)
        elif action == "check":
            locator.check()
        elif action == "press":
            locator.press(value)
        elif action == "wait_for":
            page.wait_for_selector(
                selector,
                state="visible",
                timeout=self._bounded_timeout(10000),
            )
        elif action == "wait_ms":
            self._wait(page, int(value))
        elif action == "extract":
            self._exec_extract(page, step, selector, record)
        elif action == "assert_visible":
            timeout = self._bounded_timeout(3000)
            try:
                page.wait_for_selector(selector, state="visible", timeout=timeout)
            except Exception:
                raise AssertionError(f"요소 '{selector}'가 화면에 보이지 않습니다")
        elif action == "assert_text":
            actual = page.locator(selector).first.inner_text(timeout=3000)
            if value not in actual:
                raise AssertionError(
                    f"기대 텍스트 '{value}'가 없습니다 (실제: '{actual[:80]}')")
        elif action == "assert_url":
            if not re.search(value, page.url):
                raise AssertionError(f"URL이 패턴 '{value}'와 일치하지 않습니다 (실제: {page.url})")
        elif action == "assert_not_visible":
            # 요소가 아예 없거나(=hidden 대기 성공) 숨겨져야 통과. 짧은 대기 후 판정.
            try:
                page.wait_for_selector(selector, state="hidden",
                                       timeout=self._bounded_timeout(3000))
            except Exception:
                raise AssertionError(
                    f"요소 '{selector}'가 화면에서 사라지지 않았습니다 (보이면 안 됨)")
        elif action == "assert_not_text":
            loc = page.locator(selector).first
            try:
                actual = loc.inner_text(timeout=3000)
            except Exception:
                actual = ""   # 요소 자체가 없으면 텍스트도 없는 것 → 통과
            if value in actual:
                raise AssertionError(
                    f"금지 텍스트 '{value}'가 존재합니다 (실제: '{actual[:80]}')")

    def _exec_extract(self, page, step: Step, selector: str, record=None) -> None:
        """화면의 값을 뽑아 변수에 담는다.

        입력칸이면 표시 텍스트가 아니라 입력값을 읽는다 — 사람이 화면에서 보는
        값이 그쪽이기 때문이다.
        """
        self._ensure_vars()
        locator = page.locator(selector).first
        try:
            locator.wait_for(state="attached", timeout=self._bounded_timeout(5000))
            tag = (locator.evaluate("el => el.tagName") or "").lower()
            if tag in ("input", "textarea", "select"):
                raw = locator.input_value(timeout=3000)
            else:
                raw = locator.inner_text(timeout=3000)
        except RunDeadlineExceeded:
            raise
        except Exception as err:
            raise AssertionError(
                f"요소 '{selector}'에서 값을 읽지 못했습니다: {_short(err)}") from err

        text = (raw or "").strip()
        if step.pattern:
            match = re.search(step.pattern, text)
            if not match:
                raise AssertionError(
                    f"패턴 '{step.pattern}'에 맞는 값이 없습니다 (실제: '{text[:80]}')")
            text = match.group(1).strip()
        if not text:
            raise AssertionError(f"요소 '{selector}'에서 읽은 값이 비어 있습니다")

        self._vars[step.store_as] = text
        if step.secret:
            self._secret_vars.add(step.store_as)
        if record is not None:
            record.value = MASK if step.secret else text

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

    def _browser_alive(self) -> bool:
        """브라우저가 아직 붙어 있는가.

        엔진마다 사망 시 문구가 다르다(실측: Firefox·WebKit은 'Target closed',
        Chromium은 Timeout, 드라이버 사망은 'Connection closed'). 문구를 넓게
        잡으면 앱 오류가 실행 불가로 둔갑하므로, 애매할 때는 이 값을 근거로 쓴다.

        판단이 불가능하면 True(=살아 있음)를 돌려준다. 확신 없이 실행 불가로
        올리면 진짜 제품 실패가 조용히 묻힌다 — 판정은 보수적으로.
        """
        browser = getattr(self.session, "browser", None)
        if browser is None:
            return True
        try:
            return bool(browser.is_connected())
        except Exception:
            return True

    def _baseline_path(self, name: str) -> Path:
        """시각 기준선은 엔진별로 분리한다.

        같은 화면이라도 Chromium·Firefox·WebKit은 글꼴 힌팅·안티에일리어싱이 달라
        픽셀이 어긋난다. 한 폴더를 공유하면 Firefox 실행이 Chromium 기준선과
        비교돼 전 시나리오가 실패하거나, 반대로 --update-baselines 한 번에
        다른 엔진의 기준선이 덮여 조용히 사라진다.
        """
        return Path(self.cfg.baselines_dir) / self.session.engine / f"{slugify(name)}.png"

    def _visual_check(self, page, scenario: Scenario, shots_dir: Path) -> VisualResult:
        spec = scenario.visual_spec
        baseline = self._baseline_path(spec.name)
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
            # 승인 기준은 '사람이 --update-baselines를 줬는가' 하나다.
            # 줬으면 승인된 갱신(PASS), 안 줬는데 기준선이 없어 자동 생성한
            # 경우는 미승인 기준선 → 판정에서 경고로 다룬다(_verdict 참조).
            result.baseline_updated = self.update_baselines
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

    def _responsive_check(self, page, scenario: Scenario, shots_dir: Path) -> ResponsiveResult:
        spec = scenario.responsive_spec
        result = ResponsiveResult(max_overflow_px=spec.max_overflow_px)
        original = self.session.viewport
        try:
            for width in spec.viewports:
                page.set_viewport_size({"width": width, "height": spec.height})
                self._wait(page, self.cfg.target.settle_ms)
                data = page.evaluate(JS_OVERFLOW, spec.max_overflow_px)
                vr = ViewportResult(
                    width=width, height=spec.height,
                    scroll_width=int(data["scrollWidth"]),
                    client_width=int(data["clientWidth"]),
                    overflow_px=int(data["overflow"]),
                    ok=int(data["overflow"]) <= spec.max_overflow_px,
                    offenders=list(data["offenders"]),
                )
                if not vr.ok:
                    shot = shots_dir / f"responsive_{width}px.jpg"
                    vr.screenshot = self._rel(self._shot(page, shot, full_page=False))
                result.viewports.append(vr)
        except RunDeadlineExceeded:
            raise
        except Exception as err:
            result.note = f"반응형 측정 실패: {_short(err)}"
        finally:
            # 다음 시나리오에 영향이 없도록 기본 뷰포트로 되돌린다
            try:
                page.set_viewport_size(original)
            except Exception:
                pass
        result.matched = not result.note and all(v.ok for v in result.viewports)
        return result

    def _perf_check(self, page, scenario: Scenario) -> PerfResult:
        spec = scenario.perf_spec
        result = PerfResult(metric=spec.metric, budget_ms=spec.budget_ms)
        try:
            data = page.evaluate(JS_PERF, spec.metric)
        except Exception as err:
            result.note = f"성능 측정 실패: {_short(err)}"
            result.matched = False
            return result
        value = float(data.get("value", -1))
        if value < 0:
            result.note = data.get("note") or "지표를 측정할 수 없습니다"
            result.matched = False
            return result
        result.measured_ms = round(value, 1)
        result.matched = value <= spec.budget_ms
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
                db_cols, db_rows = run_query(spec.query.db, spec.query.sql,
                                         self._query_params(spec.query))
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
                # 사람이 승인하지 않은 기준선이다. 첫 실행 화면이 깨져 있어도
                # 그것을 '정상'으로 못박지 않도록 통과가 아니라 경고로 둔다.
                vis_warn = True
                res.reasons.append(
                    f"미승인 시각 기준선 생성됨: {vis.baseline} — 이 화면이 올바른지"
                    " 사람이 확인하고 `run --update-baselines`로 승인하세요"
                    " (다음 실행부터 이 기준선과 비교)")
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

        rc = res.responsive
        rc_failed = False
        if rc is not None:
            if rc.note:
                rc_failed = True
                res.reasons.append(f"반응형 검증 불가: {rc.note}")
            elif not rc.matched:
                rc_failed = True
                bad = [v for v in rc.viewports if not v.ok]
                head = bad[0]
                detail = f" — 예: {', '.join(head.offenders[:3])}" if head.offenders else ""
                widths = ", ".join(f"{v.width}px(+{v.overflow_px})" for v in bad)
                res.reasons.append(
                    f"가로 오버플로: {widths}에서 화면이 옆으로 넘칩니다"
                    f" (허용 {rc.max_overflow_px}px){detail}")

        pc = res.perf
        pc_failed = False
        if pc is not None:
            _METRIC = {"load": "load", "dcl": "DOMContentLoaded", "fcp": "FCP",
                       "response": "responseEnd"}
            if pc.note:
                pc_failed = True
                res.reasons.append(f"성능 측정 불가: {pc.note}")
            elif not pc.matched:
                pc_failed = True
                res.reasons.append(
                    f"성능 예산 초과: {_METRIC.get(pc.metric, pc.metric)} "
                    f"{pc.measured_ms:.0f}ms > 예산 {pc.budget_ms}ms")

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
                or res.http_failures or dc_failed or wc_failed or vis_failed
                or rc_failed or pc_failed):
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
