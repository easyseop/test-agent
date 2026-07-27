"""CLI 진입점: run(전체 실행) / discover(인벤토리만)."""
from __future__ import annotations

import argparse
import platform
import sys
from datetime import datetime
from importlib import metadata
from pathlib import Path

from . import __version__
from .browser import BrowserSession
from .config import AgentConfig, ConfigError, load_config
from .discovery import Discovery, crawl
from .history import diff_for
from .models import FAIL, STATUS_LABEL, WARN, RunMeta
from .notify import build_payload, send_webhook
from .report import write_reports
from .runner import BrowserGoneError, Runner
from .scenarios import (build_data_checks, build_spec_checks, build_sweep,
                        block_write_checks, build_perf_checks,
                        build_responsive_checks, build_visual_checks,
                        build_write_checks)


class RunInfrastructureError(RuntimeError):
    """테스트 판정 전에 대상·환경 문제로 실행할 수 없을 때 발생."""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _short_exc(err: Exception) -> str:
    text = str(err).strip().splitlines()
    head = text[0] if text else err.__class__.__name__
    return f"{err.__class__.__name__}: {head}"[:300]


def _pw_version() -> str:
    try:
        return metadata.version("playwright")
    except metadata.PackageNotFoundError:
        return "?"


def _make_run_dir(cfg: AgentConfig, out: str | None) -> Path:
    root = Path(out) if out else Path(cfg.output_dir)
    run_dir = root / datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def _deadline_error(
    deadline_monotonic: float,
    timeout_ms: int,
    phase: str,
    completed: int = 0,
    total: int = 0,
) -> str:
    import time
    if time.monotonic() < deadline_monotonic:
        return ""
    progress = f" · 시나리오 {completed}/{total}개 완료" if total else ""
    return f"전체 실행 제한 시간 {timeout_ms}ms 초과 ({phase}{progress})"


def _check_target_available(
    session: BrowserSession,
    cfg: AgentConfig,
    timeout_ms: int | None = None,
) -> None:
    """대상 앱의 루트에 실제 브라우저로 접속 가능한지 확인한다.

    HTTP 4xx/5xx 응답은 서버에 도달한 것이므로 연결 성공으로 본다. 연결 거부,
    DNS 실패, 탐색 시간 초과처럼 페이지 자체에 도달하지 못한 경우만 실행 불가다.
    """
    ctx, page, _monitor = session.new_context(cfg.target.base_url)
    try:
        page.goto(
            "/",
            wait_until="domcontentloaded",
            timeout=timeout_ms or cfg.target.nav_timeout_ms,
        )
    except Exception as err:
        detail = str(err).splitlines()[0][:200] or err.__class__.__name__
        raise RunInfrastructureError(
            f"대상 앱에 접속할 수 없습니다 ({cfg.target.base_url}): {detail}"
        ) from err
    finally:
        ctx.close()


_A11Y_LABEL = {
    "img-alt": "대체 텍스트(alt) 없는 이미지", "input-label": "라벨 없는 입력",
    "empty-name": "접근 가능한 이름 없는 버튼/링크", "html-lang": "html lang 없음",
    "dup-id": "중복 id", "heading-skip": "제목 레벨 건너뜀",
    "positive-tabindex": "양수 tabindex", "no-main": "본문(main) 랜드마크 없음",
    "table-no-th": "헤더(th) 없는 표",
}


def _link_check_scenario(session, cfg, discovery):
    """크롤링으로 발견한 링크의 HTTP 상태를 전수 점검해 판정 시나리오로 만든다."""
    from .links import collect_links, probe_links
    from .models import FAIL, PASS, WARN, ScenarioResult

    pages = [{"url": p.url, "path": p.path, "elements": p.elements} for p in discovery.pages]
    urls = collect_links(pages, cfg.target.base_url, cfg.link_check.include_external,
                         cfg.link_check.ignore_patterns)
    res = ScenarioResult(name="깨진 링크 검사", kind="link_check", page="(크롤링 전체)",
                         description=f"발견한 링크 {len(urls)}개의 HTTP 상태 전수 점검")
    if not urls:
        res.status = PASS
        res.reasons.append("검사할 링크가 없습니다")
        return res

    ctx, _page, _mon = session.new_context(cfg.target.base_url)
    try:
        broken = probe_links(ctx.request, urls, cfg.link_check.timeout_ms)
    finally:
        ctx.close()

    if not broken:
        res.status = PASS
        res.reasons.append(f"링크 {len(urls)}개 모두 정상(<400)")
        return res

    res.status = FAIL if cfg.link_check.severity == "fail" else WARN
    res.reasons.append(f"깨진 링크 {len(broken)}/{len(urls)}개")
    for b in broken[:10]:
        status = b["status"] if b["status"] is not None else f"연결 실패: {b['detail']}"
        res.reasons.append(f"  {b['url']} → {status}")
    return res


def _a11y_scenario_result(pages, severity: str):
    """크롤링한 페이지의 접근성 이슈를 하나의 판정 시나리오로 합친다.

    severity=fail이면 이슈가 있을 때 실패, warn이면 경고. 정보성(info)일 때는
    이 함수를 호출하지 않는다(시나리오를 만들지 않음).
    """
    from .models import FAIL, PASS, WARN, ScenarioResult

    by_type: dict[str, int] = {}
    pages_with_issues = 0
    for p in pages:
        if p.a11y:
            pages_with_issues += 1
        for issue in p.a11y:
            by_type[issue["type"]] = by_type.get(issue["type"], 0) + 1

    total = sum(by_type.values())
    res = ScenarioResult(name="접근성 기본 점검", kind="a11y_check", page="(크롤링 전체)",
                         description="크롤링한 페이지의 명백한 접근성 위반 점검 (간이·내장)")
    if total == 0:
        res.status = PASS
        res.reasons.append("발견된 접근성 위반 없음 (간이 점검 기준)")
        return res

    res.status = FAIL if severity == "fail" else WARN
    summary = ", ".join(f"{_A11Y_LABEL.get(t, t)} {c}건" for t, c in sorted(by_type.items()))
    res.reasons.append(
        f"접근성 위반 {total}건 / {pages_with_issues}개 페이지 — {summary}")
    return res


def split_scenarios(scenarios):
    """(병렬 가능=읽기 전용, 직렬 전용=쓰기) 인덱스 쌍 목록으로 나눈다.

    쓰기 검증은 상태를 바꾸므로 병렬에서 제외하고 항상 직렬·최후에 실행한다.
    인덱스는 1부터이며 리포트 순서 복원에 쓰인다.
    """
    indexed = list(enumerate(scenarios, 1))
    parallel = [(i, sc) for i, sc in indexed if sc.kind != "write_check"]
    serial = [(i, sc) for i, sc in indexed if sc.kind == "write_check"]
    return parallel, serial


def order_results(collected: dict):
    """완료 순서와 무관하게 입력(인덱스) 순서로 정렬 — 결정적 리포트."""
    return [collected[i] for i in sorted(collected)]


def _run_parallel(parallel, collected, cfg, run_dir, args, deadline, workers,
                  auth_state=None) -> str:
    """읽기 전용 시나리오를 워커별 독립 브라우저로 병렬 실행.

    스레드마다 자기 BrowserSession(자기 playwright+browser)을 갖는다. 인증을 수행한
    경우 같은 storage_state 파일을 재사용해 로그인 세션을 공유한다. 결과는 인덱스로
    수집하고, 호출부가 입력 순서로 정렬해 결정성을 지킨다.
    """
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from .browser import BrowserSession
    from .runner import Runner

    local = threading.local()
    created: list[BrowserSession] = []
    lock = threading.Lock()

    def worker_runner() -> Runner:
        r = getattr(local, "runner", None)
        if r is not None:
            return r
        session = BrowserSession(headless=not args.headed)
        session.start()
        if auth_state is not None:
            session.configure_storage_state(auth_state, preserve=True)
            session.activate_storage_state()
        with lock:
            created.append(session)
        local.runner = Runner(
            session, cfg, run_dir,
            update_baselines=getattr(args, "update_baselines", False),
            allow_write_checks=getattr(args, "allow_write_checks", False),
            deadline_monotonic=deadline,
        )
        return local.runner

    def task(item):
        i, sc = item
        return i, _run_scenario_with_flaky(worker_runner(), sc, i, cfg)

    infra_error = ""
    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for i, res in ex.map(task, parallel):
                collected[i] = res
                print(f"   [{i}] {sc_name(parallel, i)} … "
                      + STATUS_LABEL[res.status]
                      + (" (flaky 의심)" if res.flaky else ""))
    except Exception as err:
        infra_error = f"병렬 실행 오류: {_short_exc(err)}"
    finally:
        for session in created:
            try:
                session.stop()
            except Exception:
                pass
    return infra_error


def sc_name(parallel, index: int) -> str:
    for i, sc in parallel:
        if i == index:
            return sc.name
    return str(index)


def _run_scenario_with_flaky(runner, sc, index: int, cfg) -> "object":
    """시나리오 1개 실행 + 실패 시 flaky 재확인. 병렬·직렬 경로 공용."""
    res = runner.run(sc, index)
    if res.status == FAIL and cfg.target.flaky_recheck:
        retry = runner.run(sc, index, suffix="retry")
        if retry.status != FAIL:
            res.status = WARN
            res.flaky = True
            evidence = retry.video or retry.trace or "재실행 증적 없음"
            res.reasons.insert(
                0,
                f"간헐(flaky) 의심: 재실행에서는 {STATUS_LABEL[retry.status]}"
                f" — 최초 실패 증적 유지, 재실행 증적: {evidence}",
            )
    return res


def _require_scenarios(scenarios: list) -> None:
    if scenarios:
        return
    raise RunInfrastructureError(
        "실행할 테스트 시나리오가 0개입니다. 설정의 data_checks/spec_checks/"
        "visual_checks/write_checks를 추가하거나 crawl과 button_sweep을 켜세요."
    )


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    run_dir = _make_run_dir(cfg, args.out)
    print(f"▶ {cfg.report.title}")
    print(f"  대상: {cfg.target.base_url} · 산출물: {run_dir}/")

    meta = RunMeta(
        title=cfg.report.title,
        base_url=cfg.target.base_url,
        app_version=cfg.target.app_version,
        config_path=cfg.config_path,
        started_at=_now(),
        playwright_version=_pw_version(),
        python_version=platform.python_version(),
        agent_version=__version__,
    )
    import time as _time
    t0 = _time.monotonic()
    deadline = t0 + cfg.target.run_timeout_ms / 1000

    discovery = Discovery()
    blocked = []
    results = []
    sweep_coverage: dict = {}
    infra_error = ""

    # 브라우저 기동 실패처럼 판정 이전 단계의 예외는 '제품 실패'가 아니라
    # 실행 불가(infra_error)다. 여기서 잡지 않으면 파이썬 traceback으로 죽어
    # 종료코드 1(계약상 '유효한 실행 + 실패')로 오해되고 리포트도 남지 않는다.
    try:
      with BrowserSession(headless=not args.headed) as session:
        meta.browser_version = session.version
        allow_write_checks = bool(getattr(args, "allow_write_checks", False))
        runner = Runner(
            session,
            cfg,
            run_dir,
            update_baselines=getattr(args, "update_baselines", False),
            allow_write_checks=allow_write_checks,
            deadline_monotonic=deadline,
        )

        infra_error = _deadline_error(
            deadline, cfg.target.run_timeout_ms, "브라우저 시작"
        )

        if not infra_error and cfg.auth:
            print("⓪ 로그인(인증) 세션 준비 중...")
            state_path = run_dir / "auth_state.json"
            preserve_state = bool(getattr(args, "preserve_auth_state", False))
            session.configure_storage_state(state_path, preserve=preserve_state)
            try:
                runner.authenticate(cfg.auth.steps, state_path)
            except Exception as err:
                infra_error = f"인증 실패: {str(err).splitlines()[0][:200]}"
            else:
                session.activate_storage_state()
                print("   인증 완료 (세션은 이번 실행의 모든 시나리오에서 재사용)")
                if preserve_state:
                    print(f"   ⚠ 인증 상태 파일 보관: {state_path} (민감정보 — 공유 금지)")
                else:
                    print("   인증 상태 파일은 실행 종료 후 자동 삭제")

        if not infra_error:
            infra_error = _deadline_error(
                deadline, cfg.target.run_timeout_ms, "대상 접속 확인"
            )

        if not infra_error:
            try:
                remaining_ms = max(1, int((deadline - _time.monotonic()) * 1000))
                _check_target_available(
                    session,
                    cfg,
                    timeout_ms=min(cfg.target.nav_timeout_ms, remaining_ms),
                )
            except RunInfrastructureError as err:
                infra_error = str(err)

        if not infra_error:
            infra_error = _deadline_error(
                deadline, cfg.target.run_timeout_ms, "대상 접속 확인"
            )

        if not infra_error and cfg.crawl.enabled:
            print("① 페이지 크롤링·요소 인벤토리 수집 중...")
            discovery = crawl(session, cfg, run_dir, deadline_monotonic=deadline)
            print(f"   페이지 {len(discovery.pages)}개 발견")
            infra_error = _deadline_error(
                deadline, cfg.target.run_timeout_ms, "페이지 크롤링"
            )
            if not infra_error and not discovery.pages:
                infra_error = (
                    "크롤링한 페이지가 0개입니다. 대상 URL과 로그인 상태, "
                    "crawl.exclude_patterns를 확인하세요."
                )

        a11y_scenario = None
        if not infra_error and cfg.a11y.enabled:
            issues = sum(len(p.a11y) for p in discovery.pages)
            gate = {"info": "정보성 — 리포트 참조", "warn": "경고로 판정",
                    "fail": "실패로 판정"}[cfg.a11y.severity]
            print(f"   접근성 기본 점검: 이슈 {issues}건 ({gate})")
            if cfg.a11y.severity != "info":
                a11y_scenario = _a11y_scenario_result(discovery.pages, cfg.a11y.severity)

        link_scenario = None
        if not infra_error and cfg.link_check.enabled and discovery.pages:
            print("   깨진 링크 검사 중...")
            try:
                link_scenario = _link_check_scenario(session, cfg, discovery)
                print(f"   {link_scenario.reasons[0]}")
            except Exception as err:
                link_scenario = None
                print(f"   링크 검사 생략(오류): {_short_exc(err)}")

        scenarios = []
        if not infra_error:
            scenarios = build_data_checks(cfg) + build_spec_checks(cfg)
            if cfg.sweep.enabled:
                sweep, blocked, sweep_coverage = build_sweep(discovery, cfg)
                scenarios += sweep
                if blocked:
                    print(f"   ⛔ 차단 패턴으로 건너뛴 요소 {len(blocked)}개 (리포트에 기록)")
                if sweep_coverage.get("capped"):
                    print(f"   ⚠ 페이지당 상한(max_per_page={cfg.sweep.max_per_page})으로 "
                          f"{sweep_coverage['capped']}개 미검사 (리포트에 기록)")
            scenarios += build_visual_checks(cfg)
            scenarios += build_responsive_checks(cfg)
            scenarios += build_perf_checks(cfg)
            write_scenarios = build_write_checks(cfg) if allow_write_checks else []
            if cfg.write_checks and not allow_write_checks:
                write_blocks = block_write_checks(cfg)
                blocked += write_blocks
                print(
                    f"   ⛔ 쓰기 검증 {len(write_blocks)}개 차단 "
                    "(실행하려면 --allow-write-checks로 명시적 승인)"
                )
            scenarios += write_scenarios  # 승인된 쓰기 검증은 데이터 상태를 바꾸므로 마지막에
            fixed = (len(cfg.data_checks) + len(cfg.spec_checks)
                     + len(cfg.visual_checks) + len(cfg.responsive_checks)
                     + len(cfg.perf_checks) + len(write_scenarios))
            print(f"② 시나리오 {len(scenarios)}개 생성 (데이터 검증 {len(cfg.data_checks)}"
                  f" + 명세 검증 {len(cfg.spec_checks)} + 시각 회귀 {len(cfg.visual_checks)}"
                  f" + 반응형 {len(cfg.responsive_checks)} + 성능 {len(cfg.perf_checks)}"
                  f" + 쓰기 검증 {len(write_scenarios)} + 스윕 {len(scenarios) - fixed})")
            try:
                # a11y·링크 게이트도 실행 대상이므로 '0개' 판정에서 함께 센다.
                gates = [g for g in (a11y_scenario, link_scenario) if g]
                _require_scenarios(scenarios or gates)
            except RunInfrastructureError as err:
                infra_error = str(err)

        # 워커는 최소 1(직렬), 시나리오 수를 넘겨봐야 낭비이므로 상한을 둔다.
        workers = max(1, int(getattr(args, "workers", 1) or 1))
        workers = min(workers, max(1, len(scenarios))) if scenarios else 1
        if not infra_error:
            # 쓰기 검증은 상태를 바꾸므로 병렬에서 제외하고 직렬·최후에 실행한다.
            # 나머지(읽기 전용)만 병렬 대상이다. 리포트 순서는 항상 입력 순서로
            # 고정해 결정성을 지킨다(완료 순서가 아니라).
            parallel, serial = split_scenarios(scenarios)
            collected: dict[int, object] = {}
            if workers > 1 and len(parallel) > 1:
                print(f"③ 읽기 전용 시나리오 {len(parallel)}개 병렬 실행 (워커 {workers})")
                infra_error = _run_parallel(
                    parallel, collected, cfg, run_dir, args, deadline, workers,
                    auth_state=session.storage_state)
            else:
                for i, sc in parallel:
                    infra_error = _deadline_error(
                        deadline, cfg.target.run_timeout_ms, "시나리오 실행",
                        completed=len(collected), total=len(scenarios))
                    if infra_error:
                        break
                    print(f"③ [{i}/{len(scenarios)}] {sc.name} ... ", end="", flush=True)
                    res = _run_scenario_with_flaky(runner, sc, i, cfg)
                    collected[i] = res
                    print(STATUS_LABEL[res.status] + (" (flaky 의심)" if res.flaky else ""))

            # 쓰기 검증은 승인된 것만, 항상 직렬로 마지막에 (동일 세션)
            for i, sc in serial:
                if infra_error:
                    break
                infra_error = _deadline_error(
                    deadline, cfg.target.run_timeout_ms, "시나리오 실행",
                    completed=len(collected), total=len(scenarios))
                if infra_error:
                    break
                print(f"③ [{i}/{len(scenarios)}] {sc.name} ... ", end="", flush=True)
                res = _run_scenario_with_flaky(runner, sc, i, cfg)
                collected[i] = res
                print(STATUS_LABEL[res.status] + (" (flaky 의심)" if res.flaky else ""))

            # 입력 순서로 정렬해 결정적 리포트 순서 보장
            results = order_results(collected)
            if link_scenario is not None:
                results.append(link_scenario)   # 깨진 링크 검사를 판정에 포함
            if a11y_scenario is not None:
                results.append(a11y_scenario)   # 접근성 게이트(warn/fail)를 판정에 포함
            if not infra_error:
                infra_error = _deadline_error(
                    deadline, cfg.target.run_timeout_ms, "시나리오 실행",
                    completed=len(results), total=len(scenarios))
    except RunInfrastructureError as err:
        infra_error = infra_error or str(err)
    except BrowserGoneError as err:
        infra_error = infra_error or (
            f"브라우저가 실행 도중 예기치 않게 종료됨: {err} — "
            "환경(메모리·샌드박스)을 확인하세요. 이 실행은 제품 판정이 아닙니다"
        )
    except Exception as err:  # 브라우저 기동·세션 준비 등 판정 이전 단계의 예외
        infra_error = infra_error or f"실행 환경 오류: {_short_exc(err)}"

    meta.finished_at = _now()
    meta.duration_ms = int((_time.monotonic() - t0) * 1000)

    if infra_error:
        meta.status = "infra_error"
        meta.error = infra_error
        summary = write_reports(
            run_dir,
            meta,
            results,
            blocked,
            [{"path": p.path, "title": p.title, "url": p.url, "a11y": p.a11y}
             for p in discovery.pages],
            coverage=sweep_coverage,
        )
        print("─" * 60)
        print(f"실행 불가: {infra_error}", file=sys.stderr)
        print("이 결과는 '테스트 통과'가 아닙니다. 설정·대상 상태를 고친 뒤 다시 실행하세요.")
        print(f"진단 리포트: {run_dir}/report.html")
        if cfg.notify:
            err = send_webhook(
                cfg.notify.webhook_url,
                build_payload(meta, summary, results, str(run_dir)),
            )
            print(f"웹훅 알림 {'전송 실패: ' + err if err else '전송 완료'}")
        return 2

    # 간헐(flaky) 강등은 '재현이 불안정한 제품 결함'이지 통과가 아니다.
    # 경고로 낮춰 증적을 구분하되, 실행 상태와 종료코드에서는 실패로 남긴다.
    unresolved = any(r.status == FAIL or r.flaky for r in results)
    meta.status = "failed" if unresolved else "passed"
    diff = diff_for(run_dir.parent, run_dir, {r.name: r.status for r in results},
                    identity=(cfg.target.base_url, cfg.config_path))
    summary = write_reports(run_dir, meta, results, blocked,
                            [{"path": p.path, "title": p.title, "url": p.url, "a11y": p.a11y}
                             for p in discovery.pages],
                            diff=diff, coverage=sweep_coverage)

    print("─" * 60)
    print(f"실행 완료: 통과 {summary['pass']} · 경고 {summary['warn']} · 실패 {summary['fail']}"
          f" (총 {summary['total']}, {meta.duration_ms / 1000:.1f}s)")
    if summary["flaky"]:
        print(f"간헐(flaky) 의심 {summary['flaky']}건 — 재실행에서 통과했으나 통과로 처리하지 않습니다")
    if sweep_coverage.get("found"):
        c = sweep_coverage
        extra = []
        if c.get("capped"):
            extra.append(f"상한초과 {c['capped']}")
        if c.get("blocked"):
            extra.append(f"안전차단 {c['blocked']}")
        note = f" ({', '.join(extra)})" if extra else ""
        print(f"스윕 커버리지: 발견 {c['found']}개 중 {c['tested']}개 클릭 검사{note}")
    if diff:
        parts = []
        for key, label in (("new_failures", "신규 실패"), ("fixed", "복구"),
                           ("still_failing", "계속 실패"), ("added", "새 시나리오")):
            if diff.get(key):
                parts.append(f"{label} {len(diff[key])}")
        print(f"전회차({diff['prev_run']}) 대비: " + (" · ".join(parts) if parts else "변화 없음"))
    for r in results:
        if r.status == FAIL:
            reason = r.reasons[0] if r.reasons else ""
            print(f"  ✗ {r.name} — {reason}")
    print(f"리포트: {run_dir}/report.html (스크린샷 내장 단일 파일)")
    print(f"절차서: {run_dir}/walkthrough.md · 비디오: {run_dir}/videos/ · 트레이스: {run_dir}/traces/")

    if cfg.notify and (cfg.notify.on == "always" or unresolved):
        err = send_webhook(cfg.notify.webhook_url,
                           build_payload(meta, summary, results, str(run_dir)))
        print(f"웹훅 알림 {'전송 실패: ' + err if err else '전송 완료'}")

    return 1 if unresolved else 0


def cmd_discover(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    run_dir = _make_run_dir(cfg, args.out)
    with BrowserSession(headless=not args.headed) as session:
        if cfg.auth:
            runner = Runner(session, cfg, run_dir)
            state_path = run_dir / "auth_state.json"
            preserve_state = bool(getattr(args, "preserve_auth_state", False))
            session.configure_storage_state(state_path, preserve=preserve_state)
            try:
                runner.authenticate(cfg.auth.steps, state_path)
            except Exception as err:
                print(f"인증 실패 — 실행을 중단합니다: {str(err).splitlines()[0][:200]}", file=sys.stderr)
                return 2
            session.activate_storage_state()
            if preserve_state:
                print(f"⚠ 인증 상태 파일 보관: {state_path} (민감정보 — 공유 금지)")
        try:
            _check_target_available(session, cfg)
        except RunInfrastructureError as err:
            print(f"실행 불가: {err}", file=sys.stderr)
            return 2
        discovery = crawl(session, cfg, run_dir)
    if not discovery.pages:
        print(
            "실행 불가: 크롤링한 페이지가 0개입니다. 대상 URL과 로그인 상태를 확인하세요.",
            file=sys.stderr,
        )
        return 2
    print(f"페이지 {len(discovery.pages)}개 → {run_dir}/discovery.json")
    for p in discovery.pages:
        e = p.elements
        print(f"  {p.path} — 버튼 {len(e.get('buttons', []))} · 링크 {len(e.get('links', []))}"
              f" · 입력 {len(e.get('inputs', []))} · 셀렉트 {len(e.get('selects', []))}  ({p.title})")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="webtest_agent",
        description="웹 애플리케이션 자동 테스트 에이전트 (결정적 실행 엔진)")
    sub = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-c", "--config", required=True, help="설정 YAML 경로")
    common.add_argument("--headed", action="store_true", help="브라우저 창을 띄워 실행")
    common.add_argument("--out", help="산출물 루트 디렉터리 (기본: 설정의 output_dir)")
    common.add_argument(
        "--preserve-auth-state",
        action="store_true",
        help="인증 상태 파일을 삭제하지 않고 보관(민감정보, 기본은 실행 종료 후 삭제)",
    )

    p_run = sub.add_parser("run", parents=[common], help="크롤링→시나리오→실행→리포트 전체 수행")
    p_run.add_argument("--update-baselines", action="store_true",
                       help="시각 회귀 기준선을 이번 실행 화면으로 갱신(의도된 UI 변경 승인)")
    p_run.add_argument(
        "--allow-write-checks",
        action="store_true",
        help="YAML의 write_checks 실행을 명시적으로 승인(테스트/스테이징 전용)",
    )
    p_run.add_argument(
        "--workers",
        type=int,
        default=1,
        help="읽기 전용 시나리오 병렬 워커 수(기본 1=직렬). 쓰기 검증은 항상 직렬·최후",
    )
    p_run.set_defaults(func=cmd_run)
    p_disc = sub.add_parser("discover", parents=[common], help="크롤링·인벤토리만 수행")
    p_disc.set_defaults(func=cmd_discover)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as err:
        print(f"설정 오류: {err}", file=sys.stderr)
        return 2
