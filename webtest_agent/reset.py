"""쓰기 검증 전 데이터 초기화.

주문을 넣으면 데이터가 남는다. 다음 실행의 "13건" 검사가 거짓으로 깨지므로,
초기화 없이는 쓰기 검증을 반복할 수 없다.

여기 있는 코드는 대상 앱의 **데이터를 지운다.** 그래서 세 가지를 지킨다.

1. `--allow-write-checks` 승인 없이는 절대 실행하지 않는다. 승인 여부 판단을
   호출부에 맡기지 않고 이 모듈이 직접 확인한다.
2. 실패는 판정이 아니라 **실행 불가**다. 초기화가 안 된 상태에서 합격·불합격을
   말하면 그 판정 자체가 거짓이다.
3. command는 문자열이 아니라 인자 목록으로만 받고 셸을 거치지 않는다.
   `rm -rf / ; seed.py` 같은 연쇄 실행을 원천 차단한다.
"""
from __future__ import annotations

import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import WriteResetConfig


class ResetFailed(RuntimeError):
    """초기화가 실패했다 — 제품 결함이 아니라 실행 불가."""


@dataclass
class ResetOutcome:
    """리포트에 남길 초기화 기록."""
    performed: bool = False
    detail: str = ""
    skipped_reason: str = ""

    def to_dict(self) -> dict:
        return {
            "performed": self.performed,
            "detail": self.detail,
            "skipped_reason": self.skipped_reason,
        }


def run_write_reset(cfg: WriteResetConfig | None, *, allow_write_checks: bool,
                    base_url: str, config_dir: Path) -> ResetOutcome:
    """초기화를 수행하고 결과를 돌려준다.

    설정이 없으면 아무것도 하지 않는다. 승인이 없으면 실행하지 않고 그 사실을
    기록한다 — 설정에 초기화가 있다는 이유만으로 데이터를 지우지 않는다.
    """
    if cfg is None:
        return ResetOutcome()
    if not allow_write_checks:
        return ResetOutcome(
            skipped_reason="--allow-write-checks 승인이 없어 초기화를 실행하지 않았습니다")

    if cfg.http_url:
        _run_http(cfg, base_url)
    else:
        _run_command(cfg, config_dir)
    return ResetOutcome(performed=True, detail=cfg.describe)


def _run_http(cfg: WriteResetConfig, base_url: str) -> None:
    url = cfg.http_url if "://" in cfg.http_url else base_url.rstrip("/") + cfg.http_url
    request = urllib.request.Request(url, method=cfg.http_method,
                                     headers=cfg.http_headers, data=b"")
    try:
        with urllib.request.urlopen(request, timeout=cfg.timeout_ms / 1000) as resp:
            if resp.status >= 400:
                raise ResetFailed(f"초기화 요청이 HTTP {resp.status}로 실패했습니다: {url}")
    except urllib.error.HTTPError as err:
        raise ResetFailed(f"초기화 요청이 HTTP {err.code}로 실패했습니다: {url}") from err
    except urllib.error.URLError as err:
        raise ResetFailed(f"초기화 요청을 보내지 못했습니다: {url} ({err.reason})") from err
    except OSError as err:
        raise ResetFailed(f"초기화 요청을 보내지 못했습니다: {url} ({err})") from err


def _run_command(cfg: WriteResetConfig, config_dir: Path) -> None:
    try:
        proc = subprocess.run(
            cfg.command,
            cwd=str(config_dir),
            capture_output=True,
            text=True,
            timeout=cfg.timeout_ms / 1000,
            shell=False,          # 셸을 거치지 않는다 (연쇄 실행·확장 차단)
            check=False,
        )
    except FileNotFoundError as err:
        raise ResetFailed(f"초기화 명령을 찾을 수 없습니다: {cfg.command[0]}") from err
    except subprocess.TimeoutExpired as err:
        raise ResetFailed(
            f"초기화 명령이 {cfg.timeout_ms}ms 안에 끝나지 않았습니다: {cfg.describe}") from err
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        detail = tail[-1][:200] if tail else ""
        raise ResetFailed(
            f"초기화 명령이 종료코드 {proc.returncode}로 실패했습니다: {cfg.describe}"
            + (f" — {detail}" if detail else ""))
