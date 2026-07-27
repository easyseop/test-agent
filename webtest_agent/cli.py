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
                        block_write_checks, build_visual_checks,
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

        if not infra_error and cfg.a11y.enabled:
            issues = sum(len(p.a11y) for p in discovery.pages)
            print(f"   접근성 기본 점검: 이슈 {issues}건 (정보성 — 리포트 참조)")

        scenarios = []
        if not infra_error:
            scenarios = build_data_checks(cfg) + build_spec_checks(cfg)
            if cfg.sweep.enabled:
                sweep, blocked = build_sweep(discovery, cfg)
                scenarios += sweep
                if blocked:
                    print(f"   ⛔ 차단 패턴으로 건너뛴 요소 {len(blocked)}개 (리포트에 기록)")
            scenarios += build_visual_checks(cfg)
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
                     + len(cfg.visual_checks) + len(write_scenarios))
            print(f"② 시나리오 {len(scenarios)}개 생성 (데이터 검증 {len(cfg.data_checks)}"
                  f" + 명세 검증 {len(cfg.spec_checks)} + 시각 회귀 {len(cfg.visual_checks)}"
                  f" + 쓰기 검증 {len(write_scenarios)} + 스윕 {len(scenarios) - fixed})")
            try:
                _require_scenarios(scenarios)
            except RunInfrastructureError as err:
                infra_error = str(err)

        if not infra_error:
            for i, sc in enumerate(scenarios, 1):
                infra_error = _deadline_error(
                    deadline,
                    cfg.target.run_timeout_ms,
                    "시나리오 실행",
                    completed=len(results),
                    total=len(scenarios),
                )
                if infra_error:
                    break
                print(f"③ [{i}/{len(scenarios)}] {sc.name} ... ", end="", flush=True)
                res = runner.run(sc, i)
                if res.status == FAIL and cfg.target.flaky_recheck:
                    print("실패 → 재실행(간헐 확인) ... ", end="", flush=True)
                    retry = runner.run(sc, i, suffix="retry")
                    if retry.status != FAIL:
                        res.status = WARN
                        res.flaky = True
                        evidence = retry.video or retry.trace or "재실행 증적 없음"
                        res.reasons.insert(
                            0,
                            f"간헐(flaky) 의심: 재실행에서는 {STATUS_LABEL[retry.status]}"
                            f" — 최초 실패 증적 유지, 재실행 증적: {evidence}",
                        )
                results.append(res)
                print(STATUS_LABEL[res.status] + (" (flaky 의심)" if res.flaky else ""))
            if not infra_error:
                infra_error = _deadline_error(
                    deadline,
                    cfg.target.run_timeout_ms,
                    "시나리오 실행",
                    completed=len(results),
                    total=len(scenarios),
                )
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
                            diff=diff)

    print("─" * 60)
    print(f"실행 완료: 통과 {summary['pass']} · 경고 {summary['warn']} · 실패 {summary['fail']}"
          f" (총 {summary['total']}, {meta.duration_ms / 1000:.1f}s)")
    if summary["flaky"]:
        print(f"간헐(flaky) 의심 {summary['flaky']}건 — 재실행에서 통과했으나 통과로 처리하지 않습니다")
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
    p_run.set_defaults(func=cmd_run)
    p_disc = sub.add_parser("discover", parents=[common], help="크롤링·인벤토리만 수행")
    p_disc.set_defaults(func=cmd_discover)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as err:
        print(f"설정 오류: {err}", file=sys.stderr)
        return 2
