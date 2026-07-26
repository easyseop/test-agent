# test-agent 개발 인수인계서

> 마지막 갱신: 2026-07-27  
> 기준 저장소: `https://github.com/easyseop/test-agent`  
> 기준 브랜치: `claude/web-app-test-agent-yyc2eq`  
> 작업 시작 기준 커밋: `75a985cce8639430f82abbf1aa3fb7dcfb812684`

## 1. 프로젝트 역할

`test-agent`는 웹사이트의 테스트를 실제로 실행하는 Python·Playwright Runner다.
사이트 탐색, UI 조작, UI↔API·DB 대조, 증거 수집, 리포트 생성을 담당한다.

테스트용 사이트와 비공개 정답표는 `webtest-agent-lab`, 결과 운영 화면은
`webtest-agent-site`의 책임이다.

## 2. 이번 완료 개발 단위

원본 Runner의 첫 번째 false-success P0를 수정했다.

- 대상 앱에 브라우저로 접속하지 못하면 `실행 불가`로 종료
- 실행할 시나리오가 0개면 `실행 불가`로 종료
- crawl이 켜져 있는데 발견 페이지가 0개면 `실행 불가`로 종료
- `run` 명령은 실행 불가일 때 종료코드 2 반환
- `discover` 명령도 접속 실패·0페이지를 성공으로 종료하지 않음
- `report.json`의 `meta.status`에 `passed`, `failed`, `infra_error` 기록
- `report.json`의 `meta.error`에 실행 불가 사유 기록
- HTML·Markdown 리포트에서 실행 불가를 일반 통과와 명확히 구분
- 웹훅 알림에도 실행 상태와 실행 불가 사유 포함
- 인증 실패도 진단 리포트를 남기는 실행 불가로 처리

비개발자용 가이드도 함께 추가했다.

- `docs/USER_GUIDE.md`
- `README.md` 첫 부분에 가이드 링크와 실행 불가 설명
- 담당자 역할, 테스트 정의 보관 위치, 반복 실행 시점, 위키 선택 기준 정리

두 번째 안전 P0인 인증 상태 파일 보호도 완료했다.

- `auth_state.json`을 소유자 전용 권한(0600)으로 생성
- 정상·실패 종료 모두 기본 자동 삭제
- `--preserve-auth-state`일 때만 명시적으로 보관
- 보관 파일은 `.gitignore`로 Git 제외
- 삭제 실패 시 경고 표시
- 간결한 `docs/TEST_PLAN.md` 추가

세 번째 안전 P0인 쓰기 동작 경계도 완료했다.

- 자동 버튼 스윕에서 저장·수정·삭제·결제·발송·초대·배포·로그아웃 차단
- YAML에서 최소 차단 목록을 제거해도 설정 로더가 다시 병합
- 설정 로더를 우회해도 Runner 실행 직전 동일 위험 패턴 재검사
- `write_checks`는 기본 차단하고 `--allow-write-checks`일 때만 실행
- 차단된 요소·쓰기 시나리오와 이유를 JSON·Markdown·HTML 리포트에 기록
- 데모 스크립트도 기본 읽기 전용, `--write`로만 시드 DB 쓰기 승인

큰 정수 ID 정규화 단위도 완료했다.

- 셀 값을 float로 바꾸던 정규화를 자릿수 제한 없는 문자열 정규화로 교체
- JSON 소수는 `Decimal`로 읽어 API 응답의 유효 자릿수 보존
- JavaScript 안전 정수보다 큰 `9007199254740993` 주문을 데모 시드에 추가
- 큰 ID의 UI·API·DB 일치와 한 자리 차이 검출 회귀 테스트 추가
- 의도된 고정 데이터 변경에 맞춰 데모 시각 기준선 갱신

최초 페이지 로드 오류 판정 단위도 완료했다.

- 시나리오 첫 `page.goto` 중 콘솔·페이지·HTTP 오류를 판정에 포함
- 정상으로 합의된 HTTP 실패는 `target.ignore_http_error_patterns`로만 제외
- 허용 URL 패턴의 잘못된 정규식은 설정 단계에서 차단
- `configs/demo-load-error.yaml`과 `--load-error` 실브라우저 검출 모드 추가

전체 실행 deadline 단위도 완료했다.

- `target.run_timeout_ms` 추가, 기본값 30분
- 브라우저 시작·인증·대상 확인·크롤링·시나리오 전체에 같은 deadline 적용
- 시나리오 동작 중 남은 시간을 Playwright timeout과 대기 시간에 반영
- 제한 초과 부분 결과는 `passed`가 아닌 `infra_error`, 종료코드 2
- 컨텍스트 종료로 브라우저와 기본 인증 상태 파일 정리
- `configs/demo-deadline.yaml`과 `--deadline` 실브라우저 모드 추가

## 3. 검증 결과

```bash
python3 -m pytest tests/ -q
./scripts/run_demo.sh
./scripts/run_demo.sh --bug
./scripts/run_demo.sh --auth
```

- 전체 유닛 테스트: 65개 통과
- 새 회귀 테스트:
  - 대상 연결 실패 → `infra_error`
  - 시나리오 0개 → `infra_error`
  - 실행 불가 상태·사유가 JSON/Markdown/HTML에 표시
  - 실행 불가 상태·사유가 웹훅에도 표시
  - 인증 상태 파일 0600 권한
  - 기본 삭제와 명시적 보관
  - YAML에서 최소 쓰기 차단 패턴 제거 불가
  - 자동 스윕 계획과 Runner 실행 단계의 이중 차단
  - 승인 없는 `write_checks` 미실행과 차단 리포트
  - 2^53 초과 정수의 정확한 정규화와 한 자리 차이 검출
  - 첫 page.goto 콘솔·페이지·HTTP 오류 판정
  - 허용 HTTP URL 필터와 잘못된 정규식 차단
  - 전체 deadline의 설정 검증·Runner 메시지·partial-success 차단
- 실제 Chromium E2E:
  - 기본 읽기 전용 데모: 19개 통과, 위험 동작 4개 차단, DB 123건 유지
  - `--write` 승인 데모: 20개 통과, 주문 등록 1건 증가 검증
  - 버그 주입 데모: 알려진 버그를 4개 실패 시나리오로 검출, 종료코드 1
  - 로그인 데모: 11개 통과, 위험 동작 4개 차단, 경고 0, 실패 0
    - 실행 종료 후 `auth_state.json` 미잔존 확인
  - 꺼진 대상: `infra_error` 리포트 생성, 종료코드 2
  - 큰 주문번호 `9007199254740993`: UI·API·DB 대조 통과, 시각 경고 0
  - 초기 로드 오류 데모: 콘솔 오류 1건을 실패로 검출, 종료코드 1
  - deadline 데모: 시나리오 중단, `infra_error`, 종료코드 2

버그 주입 데모의 구현 버그는 3종이지만, 요약 보기 버그는 명세 시나리오와
버튼 스윕에서 각각 검출되므로 실패 시나리오는 4개다.

## 4. 주요 변경 경로

- `webtest_agent/cli.py`: 대상 연결 사전 확인, 0시나리오 가드, 실행 불가 종료
- `webtest_agent/models.py`: 실행 상태와 실행 불가 사유
- `webtest_agent/report.py`: 리포트 상태 표시
- `webtest_agent/notify.py`: 웹훅 실행 상태·사유 표시
- `webtest_agent/browser.py`: 인증 상태 생명주기와 자동 삭제
- `webtest_agent/runner.py`: 인증 상태 파일 0600 생성
- `webtest_agent/safety.py`: 제거 불가능한 자동 탐색 최소 차단 목록
- `webtest_agent/datacheck.py`: 큰 정수·JSON 소수의 무손실 정규화
- `webtest_agent/config.py`: 허용 HTTP 오류 URL 정규식 설정·검증
- `webtest_agent/runner.py`: 첫 page.goto 오류 포함과 HTTP 허용 패턴 필터
- `demo_app/seed.py`: 2^53 초과 주문번호 고정 데이터
- `demo_app/app.py`: 초기 콘솔 오류 검증 페이지
- `configs/demo-load-error.yaml`: 초기 로드 오류 전용 E2E 설정
- `configs/demo-deadline.yaml`: 전체 제한 초과 전용 E2E 설정
- `webtest_agent/discovery.py`: 크롤링에 전체 deadline 적용
- `tests/test_initial_load.py`: 최초 로드 신호와 허용 URL 회귀 테스트
- `baselines/메인화면-시각.png`: 변경된 고정 데모 데이터 기준선
- `webtest_agent/scenarios.py`: 위험 요소와 승인 없는 쓰기 검증 차단 계획
- `webtest_agent/runner.py`: 실행 직전 쓰기 경계 재검사
- `scripts/run_demo.sh`: 읽기 전용 기본값과 `--write` 승인
- `tests/test_auth_state.py`: 권한·삭제·보관 회귀 테스트
- `tests/test_write_safety.py`: 계획·실행 이중 차단 회귀 테스트
- `tests/test_cli_guards.py`: false-success 회귀 테스트
- `tests/test_report.py`: 실행 불가 리포트 회귀 테스트
- `tests/test_notify.py`: 실행 불가 웹훅 회귀 테스트
- `docs/USER_GUIDE.md`: 비개발자용 사용 가이드
- `docs/TEST_PLAN.md`: 테스트 목적·합격 기준 목록
- `README.md`: 사용자 가이드 진입점
- `docs/02-design.md`: 실행 상태 계약

## 5. 상태 해석 계약

| `meta.status` | CLI 종료코드 | 의미 |
|---|---:|---|
| `passed` | 0 | 테스트를 실제 실행했고 실패 없음 |
| `failed` | 1 | 테스트를 실행했고 하나 이상 실패 |
| `infra_error` | 2 | 접속·인증·설정 문제로 유효한 판정 불가 |

Lab adapter와 웹 콘솔은 시나리오 수만 보지 말고 이 상태를 우선 사용해야 한다.

## 6. 다음 개발 단위

Runner의 현재 안전 P0 목록은 완료됐다. 다음 시스템 단위는 두 가지 중 하나다.

1. 실제 대상 URL·테스트 계정 범위를 받아 사이트별 YAML과 테스트 계획 작성
2. Lab adapter가 Runner의 `meta.status`(`passed/failed/infra_error`)를 우선 해석하도록 연결 검증

2번은 2026-07-27 Lab 로컬 작업본에서 완료했다.

- Lab Node 테스트 60개 통과
- 최신 Runner normal profile 7/7 통과
- `infra_error` 우선 처리와 상태·종료코드 충돌 차단
- Lab `docs/USER_GUIDE.md`, `HANDOFF.md` 갱신
- GitHub 반영 전

같은 날 최신 Runner 로컬 작업본의 Auth Lab 9-profile 탐색 측정도 완료했다.

- normal 공개 scenario 7/7 통과
- seeded bug 8개 중 7개 탐지(87.5%)
- 초기 화면 console error 프로필 탐지
- `AUTH-B08` 1개 미탐
- 예상 밖 finding 0, 실행 불가 0

원격에 고정된 Runner 커밋이 아니므로 Lab의 공식 private baseline은 갱신하지
않았다. 이제 실제 대상 정보를 받거나, Runner·Lab 로컬 변경을 GitHub에
반영한 뒤 커밋 SHA 기준으로 같은 측정을 반복한다.

LLM Wiki 구축은 사용자가 다시 요청하기 전까지 범위에서 제외한다.

## 7. 재개 명령

```bash
git clone https://github.com/easyseop/test-agent.git
cd test-agent
pip install -r requirements.txt
python -m playwright install chromium
python3 -m pytest tests/ -q
./scripts/run_demo.sh
./scripts/run_demo.sh --auth
```

## 8. 다음 Codex·Claude 작업 요청 예시

```text
test-agent의 README.md, CLAUDE.md, HANDOFF.md,
docs/USER_GUIDE.md를 먼저 읽어줘.
Runner HANDOFF.md와 docs/USER_GUIDE.md를 먼저 읽어줘.
완료된 안전 P0와 Lab meta.status 연결을 유지해줘.
실제 대상 URL이 있으면 사이트별 테스트 계획·YAML을 작성하고,
없으면 GitHub 반영 승인 여부를 확인한 뒤 최신 커밋의 9-profile 기준선을 측정해줘.
비개발자용 사용자 가이드를 함께 갱신하고,
유닛 테스트와 demo/auth E2E를 검증한 뒤 HANDOFF.md에 결과를 기록해줘.
토큰·비밀번호·쿠키는 문서나 커밋에 넣지 마.
```

## 9. 문서 갱신 규칙

완료된 개발 단위마다 다음 순서를 지킨다.

```text
구현 → 테스트 → 비개발자 가이드 갱신 → HANDOFF.md 갱신 → 변경 검토
```

인수인계서에는 변경 경로, 검증 결과, 알려진 문제, 다음 작업을 기록한다.
실제 토큰, 비밀번호, 쿠키, DB 자격증명은 기록하지 않는다.
