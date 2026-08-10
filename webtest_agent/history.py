"""전회차 실행과의 비교(diff) — 회귀 추적."""
from __future__ import annotations

import json
from pathlib import Path

from .models import FAIL


def _identity_of(run_dir: Path) -> tuple[str, str, str] | None:
    """실행의 비교 가능 여부를 정하는 키 — (대상 URL, 설정 파일)."""
    try:
        payload = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        return None
    # 브라우저를 빼면 같은 설정을 Chromium·Firefox·WebKit으로 돌린 결과가
    # 한 추이에 섞인다. 엔진이 다르면 실패하는 검사도 다르므로, "어제는 통과했는데
    # 오늘 깨졌다"가 실은 "어제는 Chromium, 오늘은 Firefox"인 경우가 생긴다.
    return (str(meta.get("base_url", "")), str(meta.get("config_path", "")),
            str(meta.get("browser", "")))


def find_previous_run(output_root: Path, current_run_dir: Path,
                      identity: tuple[str, str, str] | None = None) -> Path | None:
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
             identity: tuple[str, str, str] | None = None) -> dict | None:
    prev_dir = find_previous_run(output_root, current_run_dir, identity=identity)
    if prev_dir is None:
        return None
    prev_statuses = load_statuses(prev_dir)
    if not prev_statuses:
        return None
    return compute_diff(prev_dir.name, prev_statuses, cur_statuses)


def collect_runs(output_root: Path, identity: tuple[str, str, str] | None = None,
                 limit: int = 20) -> list[Path]:
    """최근 실행 디렉터리들을 오래된 것부터 돌려준다.

    직전 1회 비교(diff_for)와 같은 identity 규칙을 쓴다. 대상·설정이 다른 실행이
    섞이면 "이 시나리오가 언제부터 깨졌나"가 무의미해지기 때문이다.
    """
    if not output_root.exists():
        return []
    runs = sorted(
        d for d in output_root.iterdir()
        if d.is_dir() and (d / "report.json").exists()
    )
    if identity is not None:
        runs = [d for d in runs if _identity_of(d) == identity]
    return runs[-limit:] if limit > 0 else runs


def _run_summary(run_dir: Path) -> dict | None:
    try:
        payload = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    meta = payload.get("meta") or {}
    return {
        "run": run_dir.name,
        "started_at": str(meta.get("started_at", "")),
        "status": str(meta.get("status", "")),
        "statuses": {s["name"]: s["status"] for s in payload.get("scenarios", [])
                     if isinstance(s, dict) and "name" in s and "status" in s},
    }


def build_trend(output_root: Path, identity: tuple[str, str, str] | None = None,
                limit: int = 20) -> dict:
    """시나리오 × 최근 실행 표를 만든다.

    각 시나리오가 언제부터 깨졌는지, 간헐적으로 흔들리는지를 한눈에 보이게 한다.
    직전 1회 비교로는 "어제도 그제도 깨져 있었다"와 "오늘 처음 깨졌다"를
    구분할 수 없다.

    **실행 불가(infra_error)는 판정이 아니므로 통계에서 제외한다.** 대상이
    꺼져 있어 아무것도 못 한 실행을 '실패'로 세면 제품 품질이 나빠 보이고,
    '통과'로 세면 더 나쁘다.
    """
    summaries = [s for s in (_run_summary(d) for d in collect_runs(output_root, identity, limit))
                 if s is not None]
    judged = [s for s in summaries if s["status"] != "infra_error"]

    names: list[str] = []
    for summary in judged:
        for name in summary["statuses"]:
            if name not in names:
                names.append(name)

    rows = []
    for name in sorted(names):
        history = [s["statuses"].get(name, "") for s in judged]
        seen = [status for status in history if status]
        fails = sum(1 for status in seen if status == FAIL)
        # 판정이 뒤집힌 횟수. 잦으면 앱이 아니라 테스트가 흔들리는 것일 수 있다.
        flips = sum(1 for a, b in zip(seen, seen[1:]) if a != b)
        rows.append({
            "name": name,
            "history": history,
            "runs": len(seen),
            "failures": fails,
            "flips": flips,
            "current": seen[-1] if seen else "",
        })

    return {
        "runs": [{"run": s["run"], "started_at": s["started_at"], "status": s["status"]}
                 for s in judged],
        "skipped_infra_runs": [s["run"] for s in summaries if s["status"] == "infra_error"],
        "scenarios": rows,
    }
