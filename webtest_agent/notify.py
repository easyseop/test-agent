"""실행 결과 웹훅 알림 — Slack Incoming Webhook 호환 JSON POST."""
from __future__ import annotations

import json
import urllib.request

from .models import FAIL, RunMeta, ScenarioResult


def build_payload(meta: RunMeta, summary: dict, results: list[ScenarioResult],
                  run_dir: str) -> dict:
    failures = [r for r in results if r.status == FAIL]
    lines = [
        f"[{meta.title}] 통과 {summary['pass']} · 경고 {summary['warn']}"
        f" · 실패 {summary['fail']} (총 {summary['total']})",
        f"대상: {meta.base_url} · 산출물: {run_dir}",
    ]
    for r in failures[:10]:
        reason = r.reasons[0] if r.reasons else ""
        lines.append(f"✗ {r.name} — {reason[:160]}")
    return {
        "text": "\n".join(lines),          # Slack Incoming Webhook 호환
        "title": meta.title,
        "base_url": meta.base_url,
        "run_dir": run_dir,
        "summary": summary,
        "failures": [{"name": r.name, "reasons": r.reasons} for r in failures],
    }


def send_webhook(url: str, payload: dict, timeout: int = 10) -> str:
    """전송 성공 시 빈 문자열, 실패 시 오류 메시지 반환 (알림 실패가 실행을 깨지 않도록)."""
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status >= 400:
                return f"HTTP {resp.status}"
        return ""
    except Exception as err:  # noqa: BLE001 — 어떤 실패든 실행을 중단시키지 않는다
        return str(err).splitlines()[0][:200]
