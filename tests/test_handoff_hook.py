"""인수인계서 자동 주입 훅의 회귀 테스트.

이 훅은 컨텍스트가 압축돼도 세션이 이어지게 하는 장치다. 조용히 깨지면 다음 세션이
절대 규칙을 모른 채 작업하게 되므로, 깨졌을 때 반드시 드러나야 한다.

검증 대상:
  (1) HANDOFF.md에 CORE 마커가 살아 있는가
  (2) 훅이 CORE만 뽑고 전문을 쏟지 않는가 (매 턴 주입이라 크기가 곧 비용)
  (3) 절대 규칙이 주입물에 실제로 들어 있는가
  (4) 어떤 실패에도 종료코드 0 — 주입 실패가 사용자 작업을 막지 않아야 한다
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOK = ROOT / ".claude" / "hooks" / "inject-handoff.sh"
HANDOFF = ROOT / "HANDOFF.md"

# 주입물 상한. 매 턴 붙으므로 커지면 스스로 컨텍스트를 잡아먹어 압축을 앞당긴다
# — 막으려던 문제를 되레 일으킨다.
MAX_INJECT_BYTES = 6000

pytestmark = pytest.mark.skipif(
    not HOOK.exists() or shutil.which("bash") is None,
    reason="훅 또는 bash 없음",
)


def _run(project_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(HOOK)],
        capture_output=True, text=True, timeout=30,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin",
             "CLAUDE_PROJECT_DIR": str(project_dir)},
    )


def test_handoff_has_core_markers():
    """CORE 마커가 사라지면 훅이 전문을 주입해 매 턴 비용이 폭증한다."""
    text = HANDOFF.read_text(encoding="utf-8")
    assert "<!-- CORE:BEGIN" in text and "<!-- CORE:END" in text


def test_hook_injects_absolute_rules():
    """세션이 바뀌어도 잃으면 안 되는 규칙이 주입물에 있어야 한다."""
    out = _run(ROOT).stdout
    assert "동어반복" in out          # 기대값을 구현에서 베끼지 않는다
    assert "적대적 검증" in out        # 테스트 통과로 끝내지 않는다
    assert "infra_error" in out       # 판정 계약
    assert "knowledge/" in out        # 위키는 사용자 영역


def test_hook_emits_core_only_not_whole_document():
    out = _run(ROOT).stdout
    assert "JUnit XML" not in out, "전문이 주입됐다 — CORE 추출이 깨졌다"
    assert "CORE:BEGIN" not in out, "마커 자체가 새어 나갔다"
    assert len(out.encode()) < MAX_INJECT_BYTES


def test_hook_reports_live_git_state():
    """문서에 적힌 커밋은 갱신을 잊으면 거짓이 된다. 위치는 실측값이 정답."""
    out = _run(ROOT).stdout
    assert "실측 git 상태" in out
    branch = subprocess.run(["git", "-C", str(ROOT), "rev-parse",
                             "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True).stdout.strip()
    assert branch in out


@pytest.mark.parametrize("setup", ["missing", "empty", "no_markers"])
def test_hook_never_blocks_user(tmp_path, setup):
    """주입이 실패해도 종료코드는 0이어야 한다."""
    if setup == "empty":
        (tmp_path / "HANDOFF.md").write_text("", encoding="utf-8")
    elif setup == "no_markers":
        (tmp_path / "HANDOFF.md").write_text("# 문서\n본문\n", encoding="utf-8")

    res = _run(tmp_path)
    assert res.returncode == 0
    if setup == "no_markers":
        assert "본문" in res.stdout        # 마커 없으면 전문 폴백
    else:
        assert res.stdout.strip() == ""    # 읽을 게 없으면 조용히 아무것도 안 한다
