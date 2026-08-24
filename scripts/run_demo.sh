#!/usr/bin/env bash
# 데모 원커맨드: DB 시드 → 데모앱 기동 → 에이전트 실행 → 앱 종료
#   ./scripts/run_demo.sh          # 읽기 전용 기본 모드 (쓰기 검증은 차단 목록에 기록)
#   ./scripts/run_demo.sh --write  # 시드 DB의 주문 등록 검증을 명시적으로 승인
#   ./scripts/run_demo.sh --bug    # 버그 주입 모드 (버그 검출 기대 → 종료코드 1이 정상)
#   ./scripts/run_demo.sh --auth   # 로그인 모드 (인증 세션 재사용 + 스크린샷 마스킹 시연)
#   ./scripts/run_demo.sh --update-baselines  # 의도된 데모 화면 기준선 갱신
#   ./scripts/run_demo.sh --load-error  # 첫 화면 로드 오류 검출(종료코드 1이 정상)
#   ./scripts/run_demo.sh --deadline  # 전체 실행 제한 초과(종료코드 2가 정상)
#   ./scripts/run_demo.sh --frames  # iframe·새 창·파일 첨부·마우스 동작 시연
#   ./scripts/run_demo.sh --2fa    # 2단계 인증 로그인·인증 메일 확인 시연
set -euo pipefail
cd "$(dirname "$0")/.."

# Python 버전을 여기서 먼저 막는다.
#
# 이 스크립트가 '설치가 됐는지' 확인하는 진입점이라, 구버전으로 들어오면 여기서
# 잡는 게 효과가 가장 크다. 그냥 두면 pip가 대신 실패하는데 그 메시지가 원인을
# 알려주지 않는다 — macOS 기본 Python에 딸려오는 pip 21.x는 PEP 660 editable을
# 몰라서 "setup.py 없음"이라고만 말하고, Python이 낮다는 얘기는 어디에도 없다.
PY_BIN="${PYTHON:-python3}"
if ! command -v "$PY_BIN" > /dev/null 2>&1; then
  echo "Python을 찾을 수 없습니다 ($PY_BIN). Python 3.11 이상을 설치하세요 — QUICKSTART.md §1" >&2
  exit 2
fi
if ! "$PY_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
  CURRENT="$("$PY_BIN" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])' 2>/dev/null || echo "?")"
  cat >&2 <<MSG
Python 3.11 이상이 필요합니다 (현재 $CURRENT).

  python3.11 -m venv .venv && . .venv/bin/activate
  pip install -e ".[db,dev]" -c constraints.txt

자세한 순서는 QUICKSTART.md §1에 있습니다.
다른 파이썬을 쓰려면 PYTHON=/path/to/python3.11 $0 처럼 지정하세요.
MSG
  exit 2
fi

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
    --frames)
      CONFIG=configs/demo-frames.yaml
      ;;
    --2fa)
      CONFIG=configs/demo-2fa.yaml
      # 데모용 고정값. 실제 사용에서는 각자 환경변수로 준다.
      export DEMO_PASSWORD="${DEMO_PASSWORD:-demo1234}"
      export DEMO_2FA_SECRET="${DEMO_2FA_SECRET:-JBSWY3DPEHPK3PXP}"
      ;;
    *)
      echo "지원하지 않는 옵션: $arg (--bug, --auth, --write, --update-baselines, --load-error, --deadline, --frames, --2fa 중 선택)" >&2
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

# 시각 기준선이 없으면 먼저 승인 생성한다. 미승인 기준선은 경고로 처리되므로
# (첫 실행 화면을 무비판 채택하지 않도록) 데모의 정상 all-pass를 보이려면
# 사람이 승인하는 단계를 흉내 내 한 번 --update-baselines로 만들어 둔다.
# 정상 모드에서만 하며, 버그 모드는 기준선 오염을 막기 위해 부트스트랩하지 않는다.
# 기준선은 엔진별 하위 폴더(baselines/<엔진>/)에 있다 — 글롭이 한 단계 깊다.
if [[ "$BUG" == "0" && "$UPDATE_BASELINES" == "0" ]] && ! ls baselines/*/*.png >/dev/null 2>&1; then
  echo "· 시각 기준선이 없어 승인 생성합니다 (--update-baselines 1회)"
  python3 -m webtest_agent run -c "$CONFIG" --update-baselines >/dev/null 2>&1 || true
fi

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
