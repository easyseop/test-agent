#!/usr/bin/env bash
# 사용자 프롬프트마다 인수인계서(HANDOFF.md)의 '핵심' 구간을 컨텍스트에 주입한다.
#
# 왜 훅인가: CLAUDE.md에 "매번 읽어라"라고 적어두는 것은 모델의 기억에 의존한다.
# 컨텍스트가 압축되면 그 지시 자체가 흐려질 수 있다. 훅은 모델 상태와 무관하게 매 턴
# 실행되므로 결정적이다. (이 프로젝트의 원칙과 같다 — 제안은 LLM, 보장은 코드)
#
# 왜 전문이 아니라 '핵심'만인가: 전문은 약 6,700토큰이다. 매 턴 주입하면 컨텍스트를
# 스스로 잡아먹어 압축을 앞당긴다 — 막으려던 문제를 되레 일으킨다. 그래서 CORE 마커
# 구간(약 600토큰)만 주입하고, 상세는 모델이 Read로 가져가게 한다.
# 마커가 없으면 전문으로 안전하게 되돌아간다(문서를 고쳐도 주입이 끊기지 않는다).
#
# 계약: stdout이 프롬프트 앞에 컨텍스트로 붙는다. 종료코드는 어떤 경우에도 0 —
# 인수인계 주입 실패가 사용자 작업을 막아서는 안 된다.
set -uo pipefail

HANDOFF="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}/HANDOFF.md"

# 존재하고, 읽히고, 내용이 있어야 한다.
# (root로 실행하면 권한 비트를 무시하고 -r가 통과하므로 실제 읽기로 확인한다)
body=$(cat -- "$HANDOFF" 2>/dev/null) || exit 0
[ -n "$body" ] || exit 0

core=$(printf '%s\n' "$body" | sed -n '/<!-- CORE:BEGIN/,/<!-- CORE:END/p' | sed '/<!-- CORE:/d')
[ -n "${core//[[:space:]]/}" ] || core="$body"   # 마커 없거나 비면 전문으로 폴백

# 실측 git 상태를 덧붙인다.
# 이유: 문서에 적힌 커밋·브랜치는 갱신을 잊는 순간 거짓이 된다. 매 턴 자신 있게 틀린
# 위치를 주입하는 것은 인수인계서가 없는 것보다 나쁘다. 그래서 위치만은 문서를 믿지
# 않고 지금 저장소에서 직접 읽는다. 문서와 어긋나면 아래 실측값이 정답이다.
repo="$(dirname -- "$HANDOFF")"
live=""
if git -C "$repo" rev-parse --git-dir >/dev/null 2>&1; then
    branch=$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null)
    head=$(git -C "$repo" log -1 --format='%h %s' 2>/dev/null)
    dirty=$(git -C "$repo" status --porcelain 2>/dev/null | wc -l | tr -d ' ')
    ahead=$(git -C "$repo" rev-list --count '@{u}..HEAD' 2>/dev/null || echo "?")
    live="실측 git 상태(문서보다 이쪽이 정답): 브랜치 ${branch} · HEAD ${head}"
    live="${live} · 미커밋 파일 ${dirty}개 · 원격 대비 미푸시 ${ahead}커밋"
fi

printf '<handoff-document source="HANDOFF.md">\n'
printf '이 프로젝트의 인수인계서 핵심 구간이다. 사용자 요청에 답하기 전에 현재 상태·상시\n'
printf '지시·다음 작업을 여기서 확인하라. 컨텍스트가 압축돼도 매 턴 다시 주입된다.\n\n'
printf '%s\n' "$core"
[ -n "$live" ] && printf '\n%s\n' "$live"
printf '</handoff-document>\n'
exit 0
