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
from .runner import Runner
from .scenarios import (build_data_checks, build_spec_checks, build_sweep,
                        build_visual_checks, build_write_checks)


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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

    with BrowserSession(headless=not args.headed) as session:
        meta.browser_version = session.version
        runner = Runner(session, cfg, run_dir,
                        update_baselines=getattr(args, "update_baselines", False))

        if cfg.auth:
            print("⓪ 로그인(인증) 세션 준비 중...")
            state_path = run_dir / "auth_state.json"
            try:
                runner.authenticate(cfg.auth.steps, state_path)
            except Exception as err:
                print(f"인증 실패 — 실행을 중단합니다: {str(err).splitlines()[0][:200]}", file=sys.stderr)
                return 2
            session.storage_state = state_path  # 이후 모든 컨텍스트가 로그인 세션 재사용
            print("   인증 완료 (세션은 이번 실행의 모든 시나리오에서 재사용)")

        discovery = Discovery()
        if cfg.crawl.enabled:
            print("① 페이지 크롤링·요소 인벤토리 수집 중...")
            discovery = crawl(session, cfg, run_dir)
            print(f"   페이지 {len(discovery.pages)}개 발견")

        if cfg.a11y.enabled:
            issues = sum(len(p.a11y) for p in discovery.pages)
            print(f"   접근성 기본 점검: 이슈 {issues}건 (정보성 — 리포트 참조)")

        scenarios = build_data_checks(cfg) + build_spec_checks(cfg)
        blocked = []
        if cfg.sweep.enabled:
            sweep, blocked = build_sweep(discovery, cfg)
            scenarios += sweep
            if blocked:
                print(f"   ⛔ 차단 패턴으로 건너뛴 요소 {len(blocked)}개 (리포트에 기록)")
        scenarios += build_visual_checks(cfg)
        scenarios += build_write_checks(cfg)  # 쓰기 검증은 데이터 상태를 바꾸므로 마지막에
        fixed = len(cfg.data_checks) + len(cfg.spec_checks) + len(cfg.visual_checks) + len(cfg.write_checks)
        print(f"② 시나리오 {len(scenarios)}개 생성 (데이터 검증 {len(cfg.data_checks)}"
              f" + 명세 검증 {len(cfg.spec_checks)} + 시각 회귀 {len(cfg.visual_checks)}"
              f" + 쓰기 검증 {len(cfg.write_checks)} + 스윕 {len(scenarios) - fixed})")

        results = []
        for i, sc in enumerate(scenarios, 1):
            print(f"③ [{i}/{len(scenarios)}] {sc.name} ... ", end="", flush=True)
            res = runner.run(sc, i)
            if res.status == FAIL and cfg.target.flaky_recheck:
                print("실패 → 재실행(간헐 확인) ... ", end="", flush=True)
                retry = runner.run(sc, i, suffix="retry")
                if retry.status != FAIL:
                    res.status = WARN
                    res.flaky = True
                    evidence = retry.video or retry.trace or "재실행 증적 없음"
                    res.reasons.insert(0, f"간헐(flaky) 의심: 재실행에서는 {STATUS_LABEL[retry.status]}"
                                          f" — 최초 실패 증적 유지, 재실행 증적: {evidence}")
            results.append(res)
            print(STATUS_LABEL[res.status] + (" (flaky 의심)" if res.flaky else ""))

    meta.finished_at = _now()
    meta.duration_ms = int((_time.monotonic() - t0) * 1000)

    diff = diff_for(run_dir.parent, run_dir, {r.name: r.status for r in results})
    summary = write_reports(run_dir, meta, results, blocked,
                            [{"path": p.path, "title": p.title, "url": p.url, "a11y": p.a11y}
                             for p in discovery.pages],
                            diff=diff)

    print("─" * 60)
    print(f"실행 완료: 통과 {summary['pass']} · 경고 {summary['warn']} · 실패 {summary['fail']}"
          f" (총 {summary['total']}, {meta.duration_ms / 1000:.1f}s)")
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

    if cfg.notify and (cfg.notify.on == "always" or summary["fail"]):
        err = send_webhook(cfg.notify.webhook_url,
                           build_payload(meta, summary, results, str(run_dir)))
        print(f"웹훅 알림 {'전송 실패: ' + err if err else '전송 완료'}")

    return 1 if summary["fail"] else 0


def cmd_discover(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    run_dir = _make_run_dir(cfg, args.out)
    with BrowserSession(headless=not args.headed) as session:
        if cfg.auth:
            runner = Runner(session, cfg, run_dir)
            state_path = run_dir / "auth_state.json"
            try:
                runner.authenticate(cfg.auth.steps, state_path)
            except Exception as err:
                print(f"인증 실패 — 실행을 중단합니다: {str(err).splitlines()[0][:200]}", file=sys.stderr)
                return 2
            session.storage_state = state_path
        discovery = crawl(session, cfg, run_dir)
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

    p_run = sub.add_parser("run", parents=[common], help="크롤링→시나리오→실행→리포트 전체 수행")
    p_run.add_argument("--update-baselines", action="store_true",
                       help="시각 회귀 기준선을 이번 실행 화면으로 갱신(의도된 UI 변경 승인)")
    p_run.set_defaults(func=cmd_run)
    p_disc = sub.add_parser("discover", parents=[common], help="크롤링·인벤토리만 수행")
    p_disc.set_defaults(func=cmd_discover)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as err:
        print(f"설정 오류: {err}", file=sys.stderr)
        return 2
