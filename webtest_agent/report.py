"""리포트 생성: JSON · Markdown · walkthrough(절차서) · 단일파일 HTML."""
from __future__ import annotations

import base64
import html as html_mod
import json
from dataclasses import asdict
from pathlib import Path

from .models import FAIL, PASS, WARN, BlockedElement, RunMeta, ScenarioResult

BADGE = {PASS: ("통과", "#16a34a"), WARN: ("경고", "#d97706"), FAIL: ("실패", "#dc2626")}
KIND_LABEL = {"sweep_button": "버튼 스윕", "sweep_link": "링크 스윕",
              "data_check": "데이터 검증", "spec_check": "명세 검증",
              "write_check": "쓰기 검증", "visual_check": "시각 회귀"}

_A11Y_LABEL = {"img-alt": "대체 텍스트(alt) 없는 이미지", "input-label": "라벨 없는 입력 요소",
               "empty-name": "접근 가능한 이름 없는 버튼/링크", "html-lang": "html lang 속성 없음",
               "dup-id": "중복 id"}

_DIFF_LABEL = {"new_failures": "🔴 신규 실패", "fixed": "🟢 복구됨", "still_failing": "⚠️ 계속 실패",
               "added": "새 시나리오", "removed": "사라진 시나리오"}

_MAX_EMBED_BYTES = 900_000


def summarize(results: list[ScenarioResult]) -> dict:
    return {
        "total": len(results),
        "pass": sum(1 for r in results if r.status == PASS),
        "warn": sum(1 for r in results if r.status == WARN),
        "fail": sum(1 for r in results if r.status == FAIL),
    }


def write_reports(
    run_dir: Path,
    meta: RunMeta,
    results: list[ScenarioResult],
    blocked: list[BlockedElement],
    discovery_pages: list[dict],
    diff: dict | None = None,
) -> dict:
    summary = summarize(results)
    _write_json(run_dir / "report.json", meta, summary, results, blocked, discovery_pages, diff)
    _write_markdown(run_dir / "report.md", meta, summary, results, blocked, diff)
    _write_walkthrough(run_dir / "walkthrough.md", meta, results)
    _write_html(run_dir / "report.html", run_dir, meta, summary, results, blocked, diff, discovery_pages)
    return summary


# ── JSON ──────────────────────────────────────────────────────────

def _write_json(path, meta, summary, results, blocked, discovery_pages, diff) -> None:
    payload = {
        "meta": asdict(meta),
        "summary": summary,
        "diff": diff,
        "blocked_elements": [asdict(b) for b in blocked],
        "scenarios": [r.to_dict() for r in results],
        "discovery_pages": discovery_pages,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _diff_lines(diff: dict | None) -> list[str]:
    if not diff:
        return []
    lines = ["", f"## 전회차 대비 (직전 실행: {diff['prev_run']})"]
    changed = False
    for key in ("new_failures", "fixed", "still_failing", "added", "removed"):
        names = diff.get(key) or []
        if names:
            changed = True
            lines.append(f"- {_DIFF_LABEL[key]} {len(names)}건: {', '.join(names)}")
    if not changed:
        lines.append("- 변화 없음")
    return lines


# ── Markdown ──────────────────────────────────────────────────────

def _meta_lines(meta: RunMeta, summary: dict) -> list[str]:
    version = f" ({meta.app_version})" if meta.app_version else ""
    return [
        f"- 대상: {meta.base_url}{version}",
        f"- 실행: {meta.started_at} ~ {meta.finished_at} ({meta.duration_ms / 1000:.1f}s)",
        f"- 환경: Chromium {meta.browser_version} · Playwright {meta.playwright_version}"
        f" · Python {meta.python_version} · agent {meta.agent_version}",
        f"- 결과: **통과 {summary['pass']} · 경고 {summary['warn']} · 실패 {summary['fail']}** (총 {summary['total']})",
    ]


def _write_markdown(path, meta, summary, results, blocked, diff=None) -> None:
    lines = [f"# {meta.title}", ""]
    lines += _meta_lines(meta, summary)
    lines += _diff_lines(diff)
    lines += ["", "| # | 시나리오 | 종류 | 판정 | 소요 | 비고 |", "|---|---|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        note = r.reasons[0] if r.reasons else (r.effect or "")
        lines.append(
            f"| {i} | {r.name} | {KIND_LABEL.get(r.kind, r.kind)} | {BADGE[r.status][0]} "
            f"| {r.duration_ms / 1000:.1f}s | {note[:80]} |")

    failures = [r for r in results if r.status == FAIL]
    if failures:
        lines += ["", "## 실패 상세"]
        for r in failures:
            lines += ["", f"### {r.name}"]
            lines += [f"- {reason}" for reason in r.reasons]
            dc = r.data_check
            if dc and (dc.missing_in_ui or dc.unexpected_in_ui):
                header = " | ".join(dc.columns)
                if dc.missing_in_ui:
                    lines += ["", f"화면에 누락된 DB 행 (샘플 {len(dc.missing_in_ui)}/{dc.missing_total}건):",
                              f"| {header} |", "|" + "---|" * len(dc.columns)]
                    lines += ["| " + " | ".join(row) + " |" for row in dc.missing_in_ui]
                if dc.unexpected_in_ui:
                    lines += ["", f"DB에 없는데 화면에 있는 행 (샘플 {len(dc.unexpected_in_ui)}/{dc.unexpected_total}건):",
                              f"| {header} |", "|" + "---|" * len(dc.columns)]
                    lines += ["| " + " | ".join(row) + " |" for row in dc.unexpected_in_ui]

    if blocked:
        lines += ["", "## 버튼 스윕 차단 목록 (안전장치)"]
        lines += [f"- `{b.page}` — '{b.text}' (차단 패턴: `{b.pattern}`)" for b in blocked]

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Walkthrough (절차서) ───────────────────────────────────────────

def _write_walkthrough(path, meta, results) -> None:
    lines = [
        "# 작업 절차서 (walkthrough)",
        "",
        f"> {meta.title} · {meta.started_at} 자동 생성",
        "> 이미지는 상대 경로입니다 — 이 실행 폴더째 옮기거나 옵시디언 볼트에 넣으면 그대로 렌더링됩니다.",
        "> 통과 시나리오는 기능 사용 절차서로, 실패 시나리오는 버그 재현 절차서로 활용할 수 있습니다.",
    ]
    for i, r in enumerate(results, 1):
        label = BADGE[r.status][0]
        lines += ["", f"## {i}. {r.name} — {label}"]
        if r.description:
            lines.append(f"{r.description}")
        lines.append("")
        for s in r.steps:
            suffix = f" ⚠️ 실패: {s.error}" if s.status == "fail" else ""
            lines.append(f"{s.index + 1}. {s.description}{suffix}")
            if s.screenshot:
                lines.append(f"   ![]({s.screenshot})")
        outcome = []
        if r.effect:
            outcome.append(f"관찰된 효과: {r.effect}")
        dc = r.data_check
        if dc and not dc.note:
            mark = "일치 ✅" if dc.matched else "불일치 ❌"
            outcome.append(f"화면 {dc.ui_count}건 vs DB {dc.db_count}건 → {mark}")
        wc = r.write_check
        if wc and not wc.note:
            mark = "일치 ✅" if wc.matched else "불일치 ❌"
            outcome.append(f"상태 전이 {wc.pre:g}→{wc.post:g} (기대 {wc.expected_delta:+d}) → {mark}")
        vis = r.visual
        if vis and not vis.baseline_created and not vis.baseline_updated and not vis.note:
            mark = "일치 ✅" if vis.matched else "불일치 ⚠️"
            outcome.append(f"기준선 대비 변화 {vis.ratio * 100:.2f}% → {mark}")
        outcome += r.reasons
        lines.append("")
        lines.append(f"→ **결과: {label}**" + (" — " + " / ".join(outcome) if outcome else ""))
        evidence = []
        if r.video:
            evidence.append(f"[비디오]({r.video})")
        if r.trace:
            evidence.append(f"[트레이스]({r.trace})")
        if evidence:
            lines.append(f"   증적: {' · '.join(evidence)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── HTML (단일 파일) ───────────────────────────────────────────────

_CSS = """
body{font-family:'Apple SD Gothic Neo','Malgun Gothic',system-ui,sans-serif;margin:0;background:#f6f7f9;color:#111827}
.wrap{max-width:1100px;margin:0 auto;padding:24px}
h1{font-size:22px} h2{font-size:17px;margin:24px 0 8px}
.card{background:#fff;border:1px solid #e5e7eb;border-radius:10px;padding:14px 18px;margin:12px 0}
.badge{display:inline-block;padding:2px 10px;border-radius:999px;color:#fff;font-size:12.5px;font-weight:600;vertical-align:middle}
.kind{color:#6b7280;font-size:12.5px;margin-left:8px}
.meta td{border:none;padding:2px 10px 2px 0;font-size:13.5px;color:#374151}
.summary{font-size:15px;margin:10px 0}
.summary b{font-size:20px}
table.data{border-collapse:collapse;width:100%;margin:6px 0}
table.data td,table.data th{border:1px solid #e5e7eb;padding:5px 8px;font-size:13px;text-align:left}
table.data th{background:#f9fafb}
.reasons li{color:#b91c1c;font-size:13.5px}
.info li{color:#6b7280}
.steps li{font-size:13.5px;margin:4px 0}
.shots{display:flex;flex-wrap:wrap;gap:10px;margin:8px 0}
.shots figure{margin:0;max-width:310px}
.shots img{max-width:100%;border:1px solid #d1d5db;border-radius:6px}
.shots figcaption{font-size:12px;color:#6b7280;margin-top:2px}
details summary{cursor:pointer;font-weight:600;font-size:15px;padding:2px 0}
.evidence a{margin-right:12px;font-size:13.5px}
.duration{color:#6b7280;font-size:12.5px;float:right}
"""


def _b64_img(run_dir: Path, rel: str) -> str:
    if not rel:
        return ""
    p = run_dir / rel
    try:
        data = p.read_bytes()
    except OSError:
        return ""
    if len(data) > _MAX_EMBED_BYTES:
        return ""
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64," + base64.b64encode(data).decode()


def _esc(s) -> str:
    return html_mod.escape(str(s), quote=True)


def _diff_table(columns: list[str], rows: list[list[str]], caption: str, total: int) -> str:
    if not rows:
        return ""
    head = "".join(f"<th>{_esc(c)}</th>" for c in columns)
    body = "".join("<tr>" + "".join(f"<td>{_esc(v)}</td>" for v in row) + "</tr>" for row in rows)
    return (f"<p style='margin:8px 0 2px;font-size:13.5px'><b>{_esc(caption)}</b>"
            f" (샘플 {len(rows)}/{total}건)</p>"
            f"<table class='data'><tr>{head}</tr>{body}</table>")


def _scenario_card(run_dir: Path, index: int, r: ScenarioResult) -> str:
    label, color = BADGE[r.status]
    open_attr = " open" if r.status in (FAIL, WARN) else ""
    parts = [f"<div class='card'><details{open_attr}><summary>"
             f"<span class='badge' style='background:{color}'>{label}</span> "
             f"{index}. {_esc(r.name)}<span class='kind'>{KIND_LABEL.get(r.kind, r.kind)}</span>"
             f"<span class='duration'>{r.duration_ms / 1000:.1f}s</span></summary>"]
    if r.description:
        parts.append(f"<p style='font-size:13.5px;color:#374151'>{_esc(r.description)}</p>")
    if r.reasons:
        items = "".join(f"<li>{_esc(x)}</li>" for x in r.reasons)
        cls = "reasons" if r.status == FAIL else "reasons info"
        parts.append(f"<ul class='{cls}'>{items}</ul>")
    if r.effect and r.status != FAIL:
        parts.append(f"<p style='font-size:13.5px'>관찰된 효과: {_esc(r.effect)}</p>")

    dc = r.data_check
    if dc and not dc.note:
        mark = "일치 ✅" if dc.matched else "불일치 ❌"
        count_note = ""
        if dc.count_display is not None:
            ok = "일치" if dc.count_display_ok else "<b style='color:#b91c1c'>불일치</b>"
            count_note = f" · 건수 표기 {dc.count_display}건 ({ok})"
        parts.append(f"<p style='font-size:13.5px'>UI {dc.ui_count}건 vs DB {dc.db_count}건 → {mark}{count_note}</p>")
        parts.append(_diff_table(dc.columns, dc.missing_in_ui, "화면에 누락된 DB 행", dc.missing_total))
        parts.append(_diff_table(dc.columns, dc.unexpected_in_ui, "DB에 없는데 화면에 있는 행", dc.unexpected_total))

    vis = r.visual
    if vis is not None and not vis.baseline_created and not vis.baseline_updated and not vis.note:
        mark = "일치 ✅" if vis.matched else "<b style='color:#d97706'>불일치</b>"
        parts.append(f"<p style='font-size:13.5px'>기준선 대비 변화 {vis.ratio * 100:.2f}%"
                     f" (허용 {vis.threshold * 100:.2f}%) → {mark}</p>")
    if vis is not None and (vis.current or vis.diff):
        figures = []
        for label, rel in (("이번 실행", vis.current), ("diff (변경=마젠타)", vis.diff)):
            src = _b64_img(run_dir, rel)
            if src:
                figures.append(f"<figure><img src='{src}' alt=''><figcaption>{label}</figcaption></figure>")
        if figures:
            parts.append(f"<div class='shots'>{''.join(figures)}</div>")

    wc = r.write_check
    if wc is not None and not wc.note:
        mark = "일치 ✅" if wc.matched else "<b style='color:#b91c1c'>불일치 ❌</b>"
        parts.append(f"<p style='font-size:13.5px'>상태 전이: 사전 {wc.pre:g} → 사후 {wc.post:g}"
                     f" (변화 {wc.delta:+g}, 기대 {wc.expected_delta:+d}) → {mark}</p>")

    if r.steps:
        items = []
        for s in r.steps:
            mark = " <b style='color:#b91c1c'>← 실패</b>" if s.status == "fail" else ""
            items.append(f"<li>{_esc(s.description)}{mark}</li>")
        parts.append(f"<ol class='steps' start='1'>{''.join(items)}</ol>")
        figures = []
        for s in r.steps:
            src = _b64_img(run_dir, s.screenshot)
            if src:
                figures.append(f"<figure><img src='{src}' alt=''>"
                               f"<figcaption>{s.index + 1}. {_esc(s.description[:60])}</figcaption></figure>")
        if figures:
            parts.append(f"<div class='shots'>{''.join(figures)}</div>")

    if r.console_errors or r.page_errors:
        errs = "\n".join((r.console_errors + r.page_errors)[:5])
        parts.append(f"<pre style='background:#111;color:#f87171;padding:10px;border-radius:6px;"
                     f"font-size:12px;overflow-x:auto'>{_esc(errs)}</pre>")

    evidence = []
    if r.video:
        evidence.append(f"<a href='{_esc(r.video)}'>🎬 비디오(webm)</a>")
    if r.trace:
        evidence.append(f"<a href='{_esc(r.trace)}'>🔍 트레이스(zip)</a>")
    if evidence:
        parts.append(f"<p class='evidence'>{''.join(evidence)}"
                     f"<span style='color:#9ca3af;font-size:12px'> — 파일은 리포트와 같은 폴더 기준 상대 경로</span></p>")

    parts.append("</details></div>")
    return "".join(parts)


def _diff_html(diff: dict | None) -> str:
    if not diff:
        return ""
    items = []
    for key in ("new_failures", "fixed", "still_failing", "added", "removed"):
        names = diff.get(key) or []
        if names:
            items.append(f"<li>{_DIFF_LABEL[key]} <b>{len(names)}</b>건: "
                         f"{_esc(', '.join(names))}</li>")
    body = f"<ul style='font-size:13.5px'>{''.join(items)}</ul>" if items \
        else "<p style='font-size:13.5px;color:#6b7280'>변화 없음</p>"
    return (f"<h2>전회차 대비 <span style='color:#9ca3af;font-size:13px'>(직전 실행: "
            f"{_esc(diff['prev_run'])})</span></h2><div class='card'>{body}</div>")


def _a11y_html(discovery_pages: list[dict]) -> str:
    pages = [p for p in (discovery_pages or []) if p.get("a11y")]
    total = sum(len(p["a11y"]) for p in pages)
    if not pages:
        return ""
    items = []
    for p in pages:
        counts: dict[str, int] = {}
        for issue in p["a11y"]:
            counts[issue.get("type", "?")] = counts.get(issue.get("type", "?"), 0) + 1
        detail = " · ".join(f"{_A11Y_LABEL.get(t, t)} {c}건" for t, c in counts.items())
        items.append(f"<li><code>{_esc(p.get('path', ''))}</code> — {detail}</li>")
    return (f"<h2>접근성 기본 점검 <span style='color:#9ca3af;font-size:13px'>"
            f"(간이 내장 검사 · 정보성, 판정에 미반영 · 총 {total}건)</span></h2>"
            f"<div class='card'><ul class='info' style='font-size:13.5px'>{''.join(items)}</ul></div>")


def _write_html(path, run_dir, meta, summary, results, blocked, diff=None, discovery_pages=None) -> None:
    version = f" ({_esc(meta.app_version)})" if meta.app_version else ""
    rows = "".join([
        f"<tr><td>대상</td><td>{_esc(meta.base_url)}{version}</td></tr>",
        f"<tr><td>실행</td><td>{_esc(meta.started_at)} ~ {_esc(meta.finished_at)}"
        f" ({meta.duration_ms / 1000:.1f}s)</td></tr>",
        f"<tr><td>환경</td><td>Chromium {_esc(meta.browser_version)} · Playwright "
        f"{_esc(meta.playwright_version)} · Python {_esc(meta.python_version)}"
        f" · agent {_esc(meta.agent_version)}</td></tr>",
    ])
    blocked_html = ""
    if blocked:
        items = "".join(f"<li><code>{_esc(b.page)}</code> — '{_esc(b.text)}'"
                        f" (차단 패턴: <code>{_esc(b.pattern)}</code>)</li>" for b in blocked)
        blocked_html = (f"<h2>버튼 스윕 차단 목록 (안전장치)</h2><div class='card'>"
                        f"<ul class='info' style='font-size:13.5px'>{items}</ul></div>")

    cards = "".join(_scenario_card(run_dir, i, r) for i, r in enumerate(results, 1))
    doc = f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(meta.title)}</title><style>{_CSS}</style></head>
<body><div class="wrap">
<h1>{_esc(meta.title)}</h1>
<table class="meta">{rows}</table>
<p class="summary">
  <span class="badge" style="background:{BADGE[PASS][1]}">통과 <b>{summary['pass']}</b></span>&nbsp;
  <span class="badge" style="background:{BADGE[WARN][1]}">경고 <b>{summary['warn']}</b></span>&nbsp;
  <span class="badge" style="background:{BADGE[FAIL][1]}">실패 <b>{summary['fail']}</b></span>
  &nbsp;<span style="color:#6b7280">/ 총 {summary['total']}개 시나리오</span>
</p>
{_diff_html(diff)}
{blocked_html}
{_a11y_html(discovery_pages)}
<h2>시나리오 결과</h2>
{cards}
<p style="color:#9ca3af;font-size:12px;margin-top:24px">webtest-agent 자동 생성 리포트 ·
스크린샷은 문서에 내장되어 이 파일 하나로 공유 가능 · 비디오/트레이스는 실행 폴더의 videos/, traces/ 참조</p>
</div></body></html>"""
    path.write_text(doc, encoding="utf-8")
