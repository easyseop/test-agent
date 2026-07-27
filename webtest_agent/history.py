"""전회차 실행과의 비교(diff) — 회귀 추적."""
from __future__ import annotations

import json
from pathlib import Path

from .models import FAIL


def _identity_of(run_dir: Path) -> tuple[str, str] | None:
    """실행의 비교 가능 여부를 정하는 키 — (대상 URL, 설정 파일)."""
    try:
        payload = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return None
    return (str(meta.get("base_url", "")), str(meta.get("config_path", "")))


def find_previous_run(output_root: Path, current_run_dir: Path,
                      identity: tuple[str, str] | None = None) -> Path | None:
    """같은 산출물 루트에서 직전 실행 디렉터리를 찾는다 (report.json 존재 기준).

    여러 대상·설정이 같은 output_dir을 공유하면 서로 다른 앱·다른 시나리오 묶음의
    실행끼리 비교돼 '신규 실패/복구'가 무의미해진다. identity가 주어지면 같은
    (대상 URL, 설정 파일) 조합의 실행만 후보로 본다.

    주의: 앱 쪽 토글(환경변수 등)로 동작이 바뀐 경우는 도구가 알 수 없다 —
    그런 구분이 필요하면 `target.app_version`을 설정에 명시할 것.
    """
    if not output_root.exists():
        return None
    current = current_run_dir.resolve()
    candidates = sorted(
        d for d in output_root.iterdir()
        if d.is_dir() and (d / "report.json").exists() and d.resolve() != current
    )
    if identity is not None:
        candidates = [d for d in candidates if _identity_of(d) == identity]
    return candidates[-1] if candidates else None


def load_statuses(run_dir: Path) -> dict[str, str]:
    """실행 디렉터리의 report.json에서 시나리오명 → 판정 매핑을 읽는다."""
    try:
        payload = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
        return {s["name"]: s["status"] for s in payload.get("scenarios", [])}
    except (OSError, ValueError, KeyError):
        return {}


def compute_diff(prev_run_id: str, prev: dict[str, str], cur: dict[str, str]) -> dict:
    """전회차 대비 변화. new_failures에는 '신규 시나리오인데 바로 실패'도 포함된다."""
    return {
        "prev_run": prev_run_id,
        "new_failures": sorted(n for n, s in cur.items() if s == FAIL and prev.get(n) != FAIL),
        "fixed": sorted(n for n, s in prev.items() if s == FAIL and n in cur and cur[n] != FAIL),
        "still_failing": sorted(n for n, s in cur.items() if s == FAIL and prev.get(n) == FAIL),
        "added": sorted(n for n in cur if n not in prev),
        "removed": sorted(n for n in prev if n not in cur),
    }


def diff_for(output_root: Path, current_run_dir: Path, cur_statuses: dict[str, str],
             identity: tuple[str, str] | None = None) -> dict | None:
    prev_dir = find_previous_run(output_root, current_run_dir, identity=identity)
    if prev_dir is None:
        return None
    prev_statuses = load_statuses(prev_dir)
    if not prev_statuses:
        return None
    return compute_diff(prev_dir.name, prev_statuses, cur_statuses)
