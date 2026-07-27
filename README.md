# webtest-agent — 웹 애플리케이션 자동 테스트 에이전트

웹 앱의 화면을 **스스로 크롤링해 버튼·링크를 전수 점검**하고, **필터 조회 결과가 실제 DB 쿼리 결과와 일치하는지 대조**하며, 그 과정을 **비디오·스크린샷·트레이스로 녹화**해 한국어 리포트로 정리해 주는 도구입니다.

실행 엔진은 100% 결정적(LLM·API 키 불필요)이며, 실패 시 종료코드 1을 반환해 CI 게이트로 쓸 수 있습니다.

> **처음 사용하는 분:** 개발 용어 없이 준비물부터 결과 읽는 법까지 설명한
> [비개발자용 사용자 가이드](docs/USER_GUIDE.md)를 먼저 보세요.
> 테스트 목적과 합격 기준은 [테스트 계획](docs/TEST_PLAN.md)에 정리합니다.
> 대상 앱이 꺼져 있거나 테스트가 0개면 이제 “통과”가 아니라 **실행 불가**로 표시됩니다.
> 전체 실행은 기본 30분 안에 끝나야 하며, 초과하면 부분 결과를 통과로 보지 않습니다.

```
① Discover  페이지 크롤링 · 버튼/링크/폼 인벤토리 수집
② Plan      버튼 스윕 시나리오 자동 생성 + YAML의 데이터 검증 시나리오
③ Execute   Playwright 실행 — 시나리오별 비디오 녹화, 스텝별 스크린샷, 신호 감시
④ Verify    콘솔 에러·HTTP 실패·무반응 판정 + UI 테이블 ↔ DB 쿼리 대조
⑤ Report    report.html(단일파일) · walkthrough.md(절차서) · md/json · webm · trace
```

## 무엇을 검증하나

| 검증 | 방법 | 판정 |
|------|------|------|
| 버튼·링크가 동작하는가 | 첫 화면 로드부터 전수 클릭(스윕)까지 신호 관찰 | 콘솔 에러/페이지 예외 → **실패**, HTTP≥400 → **실패**, 무반응 → **경고** |
| 필터 조회 데이터가 맞는가 | 화면 표 추출 ↔ **정답원 대조** — SQL 쿼리(`query.db+sql`) 또는 REST API(`query.api`). 페이지네이션 순회 지원 | 불일치 시 누락/초과 행 샘플과 함께 **실패** |
| 긴 ID가 정확한가 | UI·API·DB 숫자를 float로 바꾸지 않고 문자열 기준 정규화 | 2^53보다 큰 ID도 한 자리 차이를 **실패**로 검출 |
| 건수 표기가 맞는가 | 화면의 "N건" ↔ 실제 표 행 수 (불변식) | 불일치 → **실패** |
| 기획 의도대로 동작하는가 | `spec_checks` — 단언 스텝(`assert_visible`/`assert_text`/`assert_url`)으로 기대 동작 명시 | 단언 위반 → **실패** |
| 쓰기가 DB에 반영되는가 | `write_checks` — `--allow-write-checks`로 명시적으로 승인한 시드/스테이징 실행만 허용 | 기대 변화량과 다르면 **실패** |
| 화면이 예전과 같은가 | `visual_checks` — 기준선(스냅샷) 대비 픽셀 비교, diff 이미지 생성. 의도된 변경은 `run --update-baselines`로 승인 | 불일치 → **경고** (severity: fail 선택 가능) |
| 작은 화면에서 안 깨지는가 | `responsive_checks` — 여러 뷰포트 폭에서 가로 오버플로(`scrollWidth > clientWidth`) 검출, 원인 요소 지목 | 오버플로 → **실패** (결정적 불변식) |
| 접근성 기본 상태 | `a11y.enabled` — alt 없는 이미지·라벨 없는 입력 등 간이 점검 | 정보성 (판정 미반영) |
| 위험 버튼 안전장치 | 자동 스윕은 저장·삭제·결제·발송·로그아웃 등을 설정과 실행 양쪽에서 차단 | 이유와 함께 리포트에 기록 |

또한 매 실행마다 **직전 실행과 비교(diff)** 해 "신규 실패 / 복구 / 계속 실패"를 리포트와 CLI에 표시하고, `target.flaky_recheck: true`면 실패 시나리오를 1회 재실행해 간헐(flaky) 의심을 경고로 구분하며, `notify.webhook_url`을 설정하면 실행 결과를 **웹훅(Slack Incoming Webhook 호환)** 으로 전송합니다.

**병렬 실행**: `run --workers N`으로 읽기 전용 시나리오를 N개 워커(각자 독립 브라우저)로 병렬 실행합니다. 쓰기 검증(`write_checks`)은 상태를 바꾸므로 항상 직렬·최후에 실행하고, **리포트 순서는 완료 순서가 아니라 입력 순서로 고정**해 판정 결정성을 지킵니다(같은 결과를 더 빠르게). 기본값은 1(직렬).

## 설치

```bash
pip install -r requirements.txt
python -m playwright install chromium   # Playwright 브라우저 (이미 있으면 생략)
```

## 빠른 시작 (동봉 데모앱)

```bash
./scripts/run_demo.sh          # 읽기 전용 기본 모드 → 19개 통과, 위험 동작 차단 기록
./scripts/run_demo.sh --write  # 시드 DB 주문 등록 검증을 명시적으로 승인
./scripts/run_demo.sh --bug    # 버그 주입 모드 → 심어둔 버그 5건 검출 (종료코드 1이 정상)
./scripts/run_demo.sh --auth   # 로그인 모드 → 10개 통과 + 인증 상태 자동 삭제
./scripts/run_demo.sh --load-error  # 첫 화면 로드 오류 1건 검출 (종료코드 1이 정상)
./scripts/run_demo.sh --deadline  # 전체 제한 초과 → 실행 불가·종료코드 2
```

버그 주입 모드(`DEMO_BUG=1`)의 검출 예시:

```
✗ 상태필터-shipped   — UI↔DB 불일치: 화면 76건 vs DB 37건 (화면에 초과 39건)   ← delivered가 섞여 나옴
✗ 날짜범위-6월       — UI↔DB 불일치: 화면 64건 vs DB 67건 (화면에 누락 3건)    ← 경계일(6/30) 누락
✗ 버튼점검 요약 보기 — 페이지 예외: showSummry is not defined                  ← JS 오타
✗ 메인화면-반응형    — 가로 오버플로: 375px(+875)에서 화면이 옆으로 넘칩니다     ← 고정폭 배너
```

수동 실행:

```bash
python3 demo_app/seed.py                 # 데모 DB 시드 (고정 시드, 재실행 시 초기화)
python3 demo_app/app.py &                # 데모앱 기동 (DEMO_BUG=1 로 버그 주입)
python3 -m webtest_agent run -c configs/demo.yaml
python3 -m webtest_agent discover -c configs/demo.yaml   # 크롤링·인벤토리만
```

## 산출물 (`runs/<타임스탬프>/`)

| 파일 | 설명 |
|------|------|
| `report.html` | **스크린샷 내장 단일 파일 리포트** — 이 파일 하나만 공유해도 됨 |
| `walkthrough.md` | 단계별 캡처+설명 **절차서** — 통과=기능 매뉴얼/인수인계서, 실패=버그 재현 절차서. 이미지가 상대 경로라 폴더째 옵시디언 볼트에 넣으면 렌더링됨 |
| `report.md` / `report.json` | 텍스트 요약 / 기계 판독용 전체 결과(실행 환경 메타데이터 포함) |
| `videos/*.webm` | 시나리오별 화면 녹화 |
| `traces/*.zip` | Playwright Trace — [trace.playwright.dev](https://trace.playwright.dev)에 드래그하면 액션별 DOM·네트워크·콘솔 타임라인을 시간여행하며 분석 가능 |
| `discovery.json` | 페이지·요소 인벤토리 |

## 실제 앱에 적용하기

`configs/demo.yaml`을 본떠 새 YAML을 만들면 됩니다. 핵심은 `data_checks` — **필터 시나리오와 정답 쿼리**를 정의하는 부분입니다.

```yaml
data_checks:
  - name: 상태필터-shipped
    description: 상태를 shipped로 필터하면 shipped 주문만 표시되어야 한다   # ← 근거는 앱 '설명서'의 의도
    page: /
    steps:                                   # 스텝 DSL: goto/click/fill/select/check/press/wait_for/wait_ms
      - {action: select, selector: "#status", value: "shipped"}
      - {action: click,  selector: "#apply"}
      - {action: wait_for, selector: "#orders-table"}
    ui_table:
      selector: "#orders-table"
      columns: [주문번호, 고객, 상태, 금액]    # 비교할 UI 헤더명
      count_selector: "#result-count"         # (선택) 건수 표기 불변식
    query:
      db: sqlite:///path/to/app.db            # v1은 SQLite 지원
      sql: "SELECT id, customer, status, amount FROM orders WHERE status = 'shipped'"
      # SELECT 컬럼 순서는 columns와 1:1 대응
```

**정답 쿼리 작성 원칙** — 기대값의 근거는 앱 **설명서(기획 의도)** 에서 가져오고, 소스코드는 셀렉터·테이블명 확인용으로만 씁니다. 구현 코드의 WHERE절을 베끼면 구현 버그가 기대값에 복제되어 영원히 통과합니다(동어반복 함정).

주문번호처럼 긴 정수는 UI·API·DB 모두 원래 자릿수를 유지해 비교합니다.
데모에는 JavaScript 안전 정수보다 큰 `9007199254740993` 주문번호가 포함되어 있습니다.

**로그인이 필요한 앱**은 `auth` 섹션에 로그인 스텝을 정의합니다 — 실행 시작 시 1회 수행되고, 저장된 세션(storage_state)을 모든 시나리오와 크롤링이 재사용합니다:

```yaml
auth:
  steps:
    - {action: goto, value: /login}
    - {action: fill, selector: "#username", value: demo}
    - {action: fill, selector: "#password", value: "${DEMO_PASSWORD}"}   # 비밀번호는 환경변수로
    - {action: click, selector: "#login-btn"}
    - {action: assert_visible, selector: "#orders-table"}                # 로그인 성공 확인

crawl:
  exclude_patterns: ["/logout"]   # 크롤러가 로그아웃을 밟아 세션을 끊지 않도록
```

인증 상태 파일은 소유자 전용 권한으로 만들고 실행 종료 후 자동 삭제합니다.
문제 분석을 위해 꼭 필요할 때만 `--preserve-auth-state`로 보관하며, 이 파일은
로그인된 세션과 같으므로 공유하거나 Git에 추가하면 안 됩니다.

개인정보가 표시되는 요소는 `report.mask_selectors: ["td:nth-of-type(2)"]`로 **스크린샷에서 마스킹**할 수 있습니다(비디오·트레이스는 미적용). DB는 `sqlite:///` 외에 SQLAlchemy URL(PostgreSQL·MySQL 등)도 지원합니다.

DB 대조가 필요 없는 **기획 의도 검증**은 `spec_checks`에 단언으로 적습니다:

```yaml
spec_checks:
  - name: 요약보기-합계표시
    description: 요약 보기를 누르면 금액 합계가 화면에 나타나야 한다
    page: /
    steps:
      - {action: click, selector: "#summary-btn"}
      - {action: assert_visible, selector: "#summary"}
      - {action: assert_text, selector: "#summary", value: "합계"}
```

### 안전 수칙

- 반드시 **테스트/스테이징 환경 + 재생성 가능한(시드) DB**에서 실행하세요. 운영 DB는 read-only 계정으로 조회 검증만.
- 인증 상태 파일은 기본 자동 삭제됩니다. 보관한 경우 비밀번호처럼 취급하고 사용 후 직접 삭제하세요.
- 자동 버튼 스윕은 저장·삭제·결제·발송·초대·배포·로그아웃을 클릭하지 않습니다. 최소 차단 목록은 YAML에서 제거할 수 없고 리포트에 이유가 남습니다.
- `write_checks`는 기본 차단됩니다. 시드/스테이징 DB와 허용 범위를 확인한 실행에서만 `--allow-write-checks`를 붙입니다.
- `query.sql`은 단일 `SELECT` 또는 `WITH` 조회만 허용합니다. 설정과 실제 실행에서 쓰기·관리 키워드와 다중 문장을 모두 차단하며, DB 계정 자체도 read-only 권한을 사용해야 합니다.
- 첫 화면 로드 중 콘솔·페이지·HTTP 오류도 실패입니다. 정상으로 합의된 리소스 실패만 `target.ignore_http_error_patterns: ["/optional-widget\\.png$"]`처럼 URL 정규식으로 제외합니다.
- 전체 실행 제한은 `target.run_timeout_ms`로 설정하며 기본값은 30분입니다. 초과하면 남은 시나리오를 시작하지 않고 `infra_error`·종료코드 2로 끝냅니다.
- 실제 앱의 스크린샷·비디오에는 개인정보가 담길 수 있습니다 — 증적 공유 범위에 주의하세요.

## LLM과 함께 쓰기 (선택)

실행 엔진은 LLM과 무관하지만, **테스트 케이스(YAML) 작성·실패 분석은 IDE의 LLM(Claude Code)에게 맡기는 워크플로**가 준비되어 있습니다:

- **`/generate-tests <앱> <소스경로> [설명서…]`** — 설명서(기대값의 근거)·소스(셀렉터 좌표)·`discovery.json`을 읽고 시나리오 YAML 초안을 생성. diff 리뷰 후 커밋이 승인 게이트.
- **`/analyze-report [report.json]`** — 실행 결과를 읽고 실패 원인(앱 버그/테스트 결함/환경)을 분류·분석해 수정 제안.
- **`knowledge/` 위키** — LLM이 유지하는 앱 지식 베이스. 구조·규약·운영 워크플로(ingest/query/lint)는 [CLAUDE.md](CLAUDE.md) 스키마 참조 (위키 콘텐츠는 직접 구축).

상세 배경은 [docs/02-design.md](docs/02-design.md) §1·§8 참조.

## 현재 한계

- API 오라클(`query.api`)은 표시 계층 검증 — UI와 API가 같은 백엔드 로직을 공유하면 백엔드 버그는 못 잡습니다 (백엔드까지 보려면 SQL 오라클)
- 기본 재시도 없음(결정성 우선) — 간헐 실패는 `wait_for` 보강으로 예방하고, 필요 시 `target.flaky_recheck: true`로 재실행 기반 flaky 표시 사용. **flaky로 표시돼도 통과가 아닙니다** (종료코드 1 유지)
- 값 마스킹은 **스텝 기록·스크린샷**에 적용 — `${환경변수}`로 넣은 값과 비밀번호·토큰 입력은 리포트에 `***`로 남지만, **비디오·트레이스에는 입력 과정이 그대로 녹화**됩니다 (공유 범위 주의)
- 위험 동작 차단 목록은 단어 매칭 최후 안전망입니다 — 목록에 없는 어휘(사내 용어 등)는 막히지 않으므로 `avoid_patterns`로 보강하세요
- 정답원 SQL 가드는 보수적 문자열 검사입니다 — 함수 호출까지 완전히 막지 못하므로 **DB 계정 자체를 read-only로 두는 것이 본 방어선**입니다
- 접근성 점검은 간이 내장 검사 (axe 수준 아님 — 정보성)
- 시각 회귀 기준선(`baselines/`)은 실행 환경(폰트·렌더링)에 종속 — 같은 환경에서 생성·비교하고, 팀 공유 시 CI 등 단일 환경에서 생성할 것

로드맵과 백로그는 [docs/02-design.md](docs/02-design.md) §13, 검토 배경은 [docs/01-review.md](docs/01-review.md) 참조.
개발 재개 상태와 다음 작업은 [HANDOFF.md](HANDOFF.md)를 기준으로 확인합니다.
