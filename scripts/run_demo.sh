#!/usr/bin/env bash
# 데모 원커맨드: DB 시드 → 데모앱 기동 → 에이전트 실행 → 앱 종료
#   ./scripts/run_demo.sh          # 읽기 전용 기본 모드 (쓰기 검증은 차단 목록에 기록)
#   ./scripts/run_demo.sh --write  # 시드 DB의 주문 등록 검증을 명시적으로 승인
#   ./scripts/run_demo.sh --bug    # 버그 주입 모드 (버그 검출 기대 → 종료코드 1이 정상)
#   ./scripts/run_demo.sh --auth   # 로그인 모드 (인증 세션 재사용 + 스크린샷 마스킹 시연)
#   ./scripts/run_demo.sh --update-baselines  # 의도된 데모 화면 기준선 갱신
#   ./scripts/run_demo.sh --load-error  # 첫 화면 로드 오류 검출(종료코드 1이 정상)
#   ./scripts/run_demo.sh --deadline  # 전체 실행 제한 초과(종료코드 2가 정상)
set -euo pipefail
cd "$(dirname "$0")/.."

BUG="${DEMO_BUG:-0}"
AUTH=0
ALLOW_WRITE=0
UPDATE_BASELINES=0
LOAD_ERROR=0
DEADLINE=0
CONFIG=configs/demo.yaml
for arg in "$@"; do
  case "$arg" in
    --bug) BUG=1 ;;
    --auth)
      AUTH=1
      CONFIG=configs/demo-auth.yaml
      export DEMO_PASSWORD="${DEMO_PASSWORD:-demo1234}"
      ;;
    --write) ALLOW_WRITE=1 ;;
    --update-baselines) UPDATE_BASELINES=1 ;;
    --load-error)
      LOAD_ERROR=1
      CONFIG=configs/demo-load-error.yaml
      ;;
    --deadline)
      DEADLINE=1
      CONFIG=configs/demo-deadline.yaml
      ;;
    *)
      echo "지원하지 않는 옵션: $arg (--bug, --auth, --write, --update-baselines, --load-error, --deadline 중 선택)" >&2
      exit 2
      ;;
  esac
done

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
RUN_ARGS=(run -c "$CONFIG")
if [[ "$ALLOW_WRITE" == "1" ]]; then
  RUN_ARGS+=(--allow-write-checks)
fi
if [[ "$UPDATE_BASELINES" == "1" ]]; then
  RUN_ARGS+=(--update-baselines)
fi
python3 -m webtest_agent "${RUN_ARGS[@]}" || status=$?

if [[ "$BUG" == "1" ]]; then
  echo "(버그 주입 모드였으므로 '실패 검출 = 에이전트 정상 동작'입니다)"
fi
if [[ "$LOAD_ERROR" == "1" ]]; then
  echo "(초기 로드 오류 모드였으므로 '실패 검출 = 에이전트 정상 동작'입니다)"
fi
if [[ "$DEADLINE" == "1" ]]; then
  echo "(deadline 모드였으므로 '실행 불가·종료코드 2 = 에이전트 정상 동작'입니다)"
fi
exit "$status"
