#!/usr/bin/env bash
# 데모 원커맨드: DB 시드 → 데모앱 기동 → 에이전트 실행 → 앱 종료
#   ./scripts/run_demo.sh          # 정상 모드 (전 시나리오 통과 기대)
#   ./scripts/run_demo.sh --bug    # 버그 주입 모드 (버그 검출 기대 → 종료코드 1이 정상)
#   ./scripts/run_demo.sh --auth   # 로그인 모드 (인증 세션 재사용 + 스크린샷 마스킹 시연)
set -euo pipefail
cd "$(dirname "$0")/.."

BUG="${DEMO_BUG:-0}"
AUTH=0
CONFIG=configs/demo.yaml
case "${1:-}" in
  --bug)  BUG=1 ;;
  --auth) AUTH=1; CONFIG=configs/demo-auth.yaml; export DEMO_PASSWORD="${DEMO_PASSWORD:-demo1234}" ;;
esac

python3 demo_app/seed.py

DEMO_BUG="$BUG" DEMO_AUTH="$AUTH" python3 demo_app/app.py > /tmp/webtest-demo-app.log 2>&1 &
APP_PID=$!
trap 'kill "$APP_PID" 2>/dev/null || true' EXIT

for _ in $(seq 1 40); do
  curl -fsS http://127.0.0.1:5057/login > /dev/null 2>&1 && break
  curl -fsS http://127.0.0.1:5057/ > /dev/null 2>&1 && break
  sleep 0.5
done

status=0
python3 -m webtest_agent run -c "$CONFIG" || status=$?

if [[ "$BUG" == "1" ]]; then
  echo "(버그 주입 모드였으므로 '실패 검출 = 에이전트 정상 동작'입니다)"
fi
exit "$status"
