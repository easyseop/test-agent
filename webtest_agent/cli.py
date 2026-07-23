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
from .models import FAIL, STATUS_LABEL, RunMeta
from .report import write_reports
from .runner import Runner
from .scenarios import build_data_checks, build_sweep


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

        discovery = Discovery()
        if cfg.crawl.enabled:
            print("① 페이지 크롤링·요소 인벤토리 수집 중...")
            discovery = crawl(session, cfg, run_dir)
            print(f"   페이지 {len(discovery.pages)}개 발견")

        scenarios = build_data_checks(cfg)
        blocked = []
        if cfg.sweep.enabled:
            sweep, blocked = build_sweep(discovery, cfg)
            scenarios += sweep
            if blocked:
                print(f"   ⛔ 차단 패턴으로 건너뛴 요소 {len(blocked)}개 (리포트에 기록)")
        print(f"② 시나리오 {len(scenarios)}개 생성 (데이터 검증 {len(cfg.data_checks)} + 스윕 {len(scenarios) - len(cfg.data_checks)})")

        runner = Runner(session, cfg, run_dir)
        results = []
        for i, sc in enumerate(scenarios, 1):
            print(f"③ [{i}/{len(scenarios)}] {sc.name} ... ", end="", flush=True)
            res = runner.run(sc, i)
            results.append(res)
            print(STATUS_LABEL[res.status])

    meta.finished_at = _now()
    meta.duration_ms = int((_time.monotonic() - t0) * 1000)

    summary = write_reports(run_dir, meta, results, blocked,
                            [{"path": p.path, "title": p.title, "url": p.url} for p in discovery.pages])

    print("─" * 60)
    print(f"실행 완료: 통과 {summary['pass']} · 경고 {summary['warn']} · 실패 {summary['fail']}"
          f" (총 {summary['total']}, {meta.duration_ms / 1000:.1f}s)")
    for r in results:
        if r.status == FAIL:
            reason = r.reasons[0] if r.reasons else ""
            print(f"  ✗ {r.name} — {reason}")
    print(f"리포트: {run_dir}/report.html (스크린샷 내장 단일 파일)")
    print(f"절차서: {run_dir}/walkthrough.md · 비디오: {run_dir}/videos/ · 트레이스: {run_dir}/traces/")
    return 1 if summary["fail"] else 0


def cmd_discover(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    run_dir = _make_run_dir(cfg, args.out)
    with BrowserSession(headless=not args.headed) as session:
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
    p_run.set_defaults(func=cmd_run)
    p_disc = sub.add_parser("discover", parents=[common], help="크롤링·인벤토리만 수행")
    p_disc.set_defaults(func=cmd_discover)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as err:
        print(f"설정 오류: {err}", file=sys.stderr)
        return 2
