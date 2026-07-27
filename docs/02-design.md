# 웹 애플리케이션 자동 테스트 에이전트 — 설계 (Phase 2, 확정본)

> 작성일: 2026-07-23 · 상태: **확정** → 구현(Phase 3) 진행
> 선행 문서: [01-review.md](./01-review.md)

## 0. 확정된 설계 결정

| # | 결정 사항 | 확정 값 |
|---|----------|--------|
| D1 | 첫 검증 대상 | 동봉 데모앱 (Flask+SQLite, 버그 주입 모드). 실제 앱 적용은 설정 YAML 작성만으로 |
| D2 | 정답 쿼리 정의 | YAML에 직접 작성 (사람 또는 IDE의 LLM이 작성 → diff 리뷰·커밋이 승인 게이트) |
| D3 | LLM 관여 방식 | **런타임 API 호출 없음.** LLM은 작성 시점에 IDE(Claude Code)에서 관여 — 테스트 케이스(YAML) 작성·실패 분석·시나리오 유지보수. 실행 엔진은 100% 결정적 |
| D4 | v1 구현 범위 | **엔진 전체**(크롤러·스윕·UI↔DB 대조·증적·리포트) + 데모앱 + LLM 워크플로 스캐폴드(`CLAUDE.md` 스키마, `/generate-tests`·`/analyze-report` 커맨드). **`knowledge/` 위키 콘텐츠는 사용자가 직접 구축**(§8은 그 가이드) |
| D5 | 인증 | ✅ 구현 — `auth.steps`로 로그인 1회 수행 → storage_state를 전 시나리오·크롤링이 재사용. 상태 파일은 소유자 전용(0600)으로 생성하고 기본 자동 삭제, `--preserve-auth-state`일 때만 명시적 보관. 비밀번호는 `${환경변수}` 치환 |
| D6 | 리포트 전달 | 파일 산출 (HTML 단일파일 + MD + JSON + walkthrough + webm + trace) |
| D7 | flaky 정책 | 기본 **재시도 없음**. 명시적 대기(`wait_for`)와 안정화 대기로 예방. v2: `target.flaky_recheck: true` 설정 시 실패 시나리오를 1회 재실행해 통과하면 '간헐(flaky) 의심' **경고**로 표시(최초 실패 증적 유지) — 기본 꺼짐 |

## 1. 검증 방법 분류 (오라클 기준)와 배치

| # | 검증 방법 | 정답의 출처 | v1 구현 |
|---|----------|-----------|:--:|
| ① | 오류 신호 기반 — 콘솔 에러·페이지 예외·HTTP≥400·무반응·크래시 | 보편 상식 | ✅ 버튼 스윕 |
| ② | 독립 정답원 대조 — UI 표시 데이터 ↔ 정답원 | DB(SQL) 또는 **REST API**(`query.api`, 표시 계층 검증) | ✅ **핵심 기능** (페이지네이션 순회 지원) |
| ③ | 명세 기반 단언 (기대 동작 명시) | 사람/LLM 명세 | ✅ v2 구현 — `spec_checks` + `assert_visible/assert_text/assert_url` (명세 작성은 사람 또는 `/generate-tests`) |
| ④ | 불변식 기반 — 예: 표시 건수 = 실제 행 수 | 항상 참인 성질 | ✅ 일부 (건수 불변식) |
| ⑤ | 상태 전이 (쓰기 액션의 DB 반영) | 전후 DB 상태 | ✅ 구현 — `write_checks` (스텝 전후 스칼라 변화량, 시드/스테이징 전용) |
| ⑥ | 기준선/스냅샷 대조 (시각 회귀) | 과거 실행(기준선) | ✅ 구현 — `visual_checks`: 첫 실행 시 기준선 자동 생성 → 픽셀 비교, 불일치 기본 **경고**(severity로 fail 가능), 의도된 변경은 `run --update-baselines`로 승인 |
| ⑦ | 사람/LLM 판단 | 사람 감각 | 참고 코멘트만 (v2) — 접근성 기본 점검(간이·정보성)은 `a11y.enabled`로 구현 |

**LLM 역할 규칙** (D3): 입력 3종 = 설명서(기대값의 근거) + 소스코드(셀렉터·스키마 좌표) + 크롤링 인벤토리(실존 확인). **기대값의 근거는 설명서에서, 코드는 좌표 참조용** — 구현 버그가 기대값에 복제되는 동어반복 함정 방지. 판정(채점)은 LLM이 하지 않는다.

## 2. 전체 아키텍처

```
 configs/<앱>.yaml ──▶ cli.py (run | discover)
        │
        ▼
 ① Discover  discovery.py — BFS 크롤링(동일 출처), 요소 인벤토리, 페이지 스크린샷
        ▼
 ② Plan      scenarios.py — 버튼 스윕 생성(avoid_patterns 차단 목록 기록)
        │                  + data_checks(YAML) 시나리오
        ▼
 ③ Execute   runner.py + browser.py — 시나리오별 새 컨텍스트(=비디오 1개)
        │      스텝 DSL 실행, 스텝별 스크린샷, Playwright Trace,
        │      콘솔/예외/HTTP≥400/다이얼로그/다운로드/팝업/네비게이션 감시
        ▼
 ④ Verify    runner.py + datacheck.py — 신호 판정 + UI 테이블 추출→정규화→DB 대조
        ▼
 ⑤ Report    report.py — report.html(단일파일)·md·json + walkthrough.md + 증적
```

드라이버(③)와 검증(④)·리포트(⑤) 분리 — API/파이프라인 대상 확장 시 ③만 교체.

## 3. 설정 스키마 (YAML)

```yaml
target:
  base_url: http://127.0.0.1:5057   # 필수
  settle_ms: 400                    # 액션 후 안정화 대기
  nav_timeout_ms: 10000
  scenario_timeout_ms: 60000        # 시나리오 상한 — 무한 대기 방지(초과=실패)
  app_version: ""                   # (선택) 대상 앱 버전/커밋 — 리포트 메타에 기록
  flaky_recheck: false              # (v2) 실패 시 1회 재실행해 간헐 의심 표시

auth:                               # (v3) 로그인 — 1회 수행 후 세션 재사용
  steps:
    - {action: goto, value: /login}
    - {action: fill, selector: "#username", value: demo}
    - {action: fill, selector: "#password", value: "${DEMO_PASSWORD}"}   # 환경변수 치환
    - {action: click, selector: "#login-btn"}
    - {action: assert_visible, selector: "#orders-table"}                # 성공 확인

crawl:
  enabled: true
  max_pages: 8
  max_depth: 2
  exclude_patterns: []              # URL 정규식 제외

button_sweep:
  enabled: true
  include_links: true
  max_per_page: 30
  avoid_patterns: ["삭제","delete","remove","탈퇴","결제","pay","logout","로그아웃"]

data_checks:
  - name: 상태필터-shipped
    description: 상태 shipped 필터 적용 시 화면 = DB
    page: /
    steps:
      - {action: select, selector: "#status", value: "shipped"}
      - {action: click,  selector: "#apply"}
      - {action: wait_for, selector: "#orders-table"}
    ui_table:
      selector: "#orders-table"
      columns: [주문번호, 고객, 상태, 금액]   # 비교할 UI 헤더명
      count_selector: "#result-count"        # (선택) 건수 표기 불변식 검증
      pagination:                            # (선택) '다음' 버튼 순회하며 전체 행 수집
        {next_selector: "#next-page", max_pages: 10}
    query:
      db: sqlite:///demo_app/demo.db         # 상대 경로는 CWD 기준
      sql: "SELECT id, customer, status, amount FROM orders WHERE status='shipped'"
      order_matters: false
      # 또는 REST API를 정답원으로 (db+sql 대신):
      # api:
      #   url: /api/orders?status=shipped    # 상대 경로는 base_url 기준
      #   rows_path: orders                  # 응답 JSON에서 행 배열 위치 (점 표기)
      #   columns: [id, customer, status, amount]   # 행 객체 필드 (점 표기 지원)
      #   headers: {Authorization: "Bearer ${API_TOKEN}"}

spec_checks:                        # (v2) 명세 기반 단언 시나리오 — 검증 방법 ③
  - name: 요약보기-합계표시
    page: /
    steps:
      - {action: click, selector: "#summary-btn"}
      - {action: assert_visible, selector: "#summary"}
      - {action: assert_text, selector: "#summary", value: "합계"}

write_checks:                       # 쓰기(상태 전이) 검증 — 검증 방법 ⑤. 시드/스테이징 전용
  - name: 주문등록
    page: /new
    steps: [...폼 입력..., {action: click, selector: "#save"}]
    query: {db: "sqlite:///demo_app/demo.db", sql: "SELECT COUNT(*) FROM orders"}  # 단일 수치
    expect_delta: 1                 # (사후 - 사전) 기대 변화량

a11y:
  enabled: false                    # 접근성 기본 점검(간이·내장) — 정보성, 판정에 미반영

visual_checks:                      # 시각 회귀 (검증 방법 ⑥)
  - name: 메인화면-시각
    page: /
    threshold: 0.01                 # 허용 픽셀 변화 비율 (1%)
    severity: warn                  # warn(기본) | fail
    # selector: "#orders-table"     # (선택) 요소만 비교, full_page: true 도 가능
baselines_dir: baselines            # 기준선 저장 위치 — 같은 실행 환경에서 생성·비교할 것

notify:                             # (선택) 실행 후 웹훅 알림 — Slack Incoming Webhook 호환
  webhook_url: "${WEBHOOK_URL}"
  on: fail                          # fail(기본) | always

report:
  title: 주문 대시보드 자동 테스트
  video: true
  trace: true
  mask_selectors: ["#orders-table td:nth-of-type(2)"]   # (v3) 스크린샷에서 가릴 요소(개인정보)

output_dir: runs
```

`query.sql`의 SELECT 컬럼 ↔ `ui_table.columns` **순서 1:1 대응** (개수 불일치 = 설정 오류로 실패).
`query.db`는 `sqlite:///`(내장, read-only) 외에 SQLAlchemy URL(`postgresql://…`, `mysql+pymysql://…`)도 지원 — sqlalchemy+드라이버 설치 필요. 접속 문자열의 비밀번호는 `${환경변수}`로.
`query.sql`은 문자열·인용 식별자·주석을 제외한 코드 기준으로 단일
`SELECT`/`WITH` 문장만 허용한다. 설정 로딩과 실제 DB 실행 양쪽에서
쓰기·DDL·관리 키워드와 다중 문장을 거부한다. 이 방어와 별개로 PostgreSQL·MySQL
계정은 데이터베이스 권한 자체를 read-only로 제한한다.
`mask_selectors`는 **스크린샷에만** 적용된다(비디오·트레이스는 미적용 — 공유 범위 주의).
인증 상태 파일은 실행 종료 시 기본 삭제된다. 디버깅을 위한 명시적 보관은
`--preserve-auth-state`를 사용하며, 보관 파일은 Git·리포트·공유 폴더에서 제외한다.

## 4. 스텝 DSL

`goto`(value=경로) · `click` · `fill`(value) · `select`(value) · `check` · `press`(value=키) · `wait_for` · `wait_ms`(value=ms) · **단언**: `assert_visible`(요소 표시) · `assert_text`(selector+value: 텍스트 포함) · `assert_url`(value: URL 정규식). Playwright 자동 대기 위에서 실행, 스텝 실패(단언 위반 포함) = 시나리오 실패 + 실패 시점 스크린샷.

단언만으로 구성된 시나리오는 `spec_checks:` 섹션에 정의한다(§3) — 검증 방법 ③의 실행 형태.

## 5. 판정 규칙

| 신호 | 판정 |
|------|------|
| 스텝 실패(요소 없음/타임아웃) · 시나리오 타임아웃 초과 | 실패 |
| JS 콘솔 에러 / 페이지 예외 | 실패 |
| 액션이 유발한 HTTP ≥ 400 / 요청 실패 | 실패 |
| UI↔DB 불일치 / 건수 표기 ≠ 실제 행 수 | 실패 |
| 파괴적 동작(삭제·결제·발송·초기화 등) 클릭 시도 — 승인된 `write_checks` 외 | 실패(안전 차단) |
| 클릭 후 무반응(URL·DOM·다이얼로그·다운로드·팝업·네비게이션 전무) | 경고 |
| 재실행에서 통과한 실패(간헐 의심) | 경고 + `flaky` 집계 — **통과로 처리하지 않음** |
| 다이얼로그 출현(기본 '취소' 응답) | 정보 |

시나리오 판정 = 최악값(실패>경고>통과). 종료 코드: **실패≥1 또는 flaky≥1 → `1`** (CI 게이트).

간헐(flaky) 강등은 증적을 구분하기 위한 표시이지 면제가 아니다. 재실행 한 번에
통과했다는 사실이 제품 결함을 없애지는 않으므로, `meta.status`는 `failed`로 남고
종료 코드도 1이다. 이 규칙이 없으면 경합 조건 같은 간헐적 제품 결함이 CI를
초록으로 통과한다.

### 실행 자체의 상태 계약

시나리오 결과와 별도로 실행 전체에 다음 상태를 기록한다.

| `report.json`의 `meta.status` | 종료 코드 | 의미 |
|---|---:|---|
| `passed` | 0 | 하나 이상의 유효한 시나리오를 실행했고 실패 없음 |
| `failed` | 1 | 하나 이상의 유효한 시나리오를 실행했고 실패 있음 |
| `infra_error` | 2 | 대상 접속, 인증, 크롤링 또는 테스트 정의 문제로 유효한 판정 불가 |

대상에 접속할 수 없거나, 크롤링 페이지가 0개이거나, 최종 시나리오가 0개이면
`infra_error`다. 이때 통과 0·실패 0이라는 숫자만으로 통과로 해석하지 않으며,
HTML·Markdown·JSON 리포트에 실행 불가 사유를 남긴다.

## 6. 데이터 정합성 비교 규칙

UI 추출(thead th→헤더, tbody tr td→행) → `columns` 헤더명으로 열 선택 → 정규화(공백 압축, 콤마·통화기호 제거 후 숫자 표준화 `12,300원→12300`, 날짜는 문자열 유지) → 기본 멀티셋(Counter) 비교, `order_matters` 옵션 → 결과: 일치 여부·UI/DB 건수·누락/초과 행 샘플≤10. `count_selector` 지정 시 표시 건수=실제 행 수 불변식 추가 검증. `pagination` 지정 시 '다음' 버튼을 순회하며 전체 행을 누적한 뒤 대조.

**API 오라클의 검증 범위** — `query.api`는 "화면이 백엔드 응답을 올바르게 표시하는가"(표시 계층)를 검증한다. UI와 API가 같은 백엔드 로직을 쓰면 백엔드 버그는 양쪽에 동일하게 나타나 잡히지 않는다 — 백엔드 로직까지 검증하려면 SQL 오라클을 쓴다. 엔티티를 JSON으로 저장하거나 목록이 검색엔진을 경유하는 앱(오픈메타데이터 등)은 API 오라클이 실용적 선택.

## 7. 증적 체계 (3+1단)

| 증적 | 형태 | 용도 |
|------|------|------|
| 스크린샷 | 스텝별 jpeg, report.html에 base64 내장 | 최소 캡처본 — 리포트만 열어도 확인 |
| 비디오 | 시나리오별 webm | 흐름 재생 |
| Trace | 시나리오별 trace.zip (DOM 스냅샷·네트워크·콘솔 타임라인) | 원인 분석 — trace.playwright.dev에서 열람 |
| **walkthrough.md** | 단계별 캡처+설명 절차서 (이미지 상대경로 → 옵시디언 호환) | 통과=기능 매뉴얼/인수인계서, 실패=버그 재현 절차서 |

## 8. 지식 관리 — LLM 위키 (v1 구현 제외, 사용자 직접 구축 가이드)

"LLM이 유지하는 영속 위키" 패턴 적용: **Raw sources**(앱 설명서·소스·`discovery.json`·`runs/` 결과 — 불변) / **Wiki**(`knowledge/` — LLM이 작성·유지: 앱 개요, 화면·기능 페이지, 테스트 카탈로그, 버그 페이지, `index.md`, `log.md`) / **Schema**(`CLAUDE.md` — 구조·규약·워크플로).

- **Ingest**: 설명서/앱 변경 시 YAML과 위키 동시 갱신. 실행 후에는 **상태 변화 시에만** 페이지 갱신(새 실패→버그 페이지+walkthrough 링크), `log.md`엔 매 실행 한 줄 append
- **Query**: 위키 기반 질의, 좋은 답변은 위키에 재파일링
- **Lint**: 테스트 갭 분석 — "설명서에 있는데 테스트 없는 기능", 모순, 죽은 시나리오
- **안전 규칙**: **위키는 정답이 아니다.** 기대값의 근거는 항상 원문(설명서)으로 소급, 위키에는 근거 원문 링크를 남긴다 (위키발 동어반복 함정 방지)

## 9. 산출물 구조

```
runs/<타임스탬프>/
├── report.html      # 단일 파일(스크린샷 내장) — 공유용
├── report.md        # 텍스트 요약
├── report.json      # 기계 판독용 (실행 메타데이터 포함)
├── walkthrough.md   # 절차서 (상대경로 이미지)
├── discovery.json   # 페이지·요소 인벤토리
├── screenshots/  ├── videos/  └── traces/
```

리포트 메타데이터(재현성): 대상 URL, 앱 버전(설정 제공 시), 브라우저·Playwright·Python 버전, 실행 시각·소요 시간.

**전회차 대비 diff** (v2): 같은 `output_dir`의 직전 실행(report.json)과 비교해 신규 실패/복구/계속 실패/새·사라진 시나리오를 리포트(md·html·json)와 CLI에 표시 — 회귀 추적용. 앱별로 `output_dir`을 분리하면 diff도 앱별로 격리된다.

## 10. 데모앱 명세

**주문 관리 대시보드** (`demo_app/`, Flask+SQLite, 포트 5057). `orders(id, customer, status, category, amount, created_at)` — `seed.py` 고정 시드 120건(2026-05-01~06-30, 경계일 데이터 보장). UI: 필터(상태·카테고리·고객 검색·날짜 범위) + 조회/초기화/새로고침/요약 보기/CSV 버튼 + `#orders-table` + `#result-count` + About + **`전체 삭제` 버튼(차단 패턴 시연용)**.

**버그 주입 `DEMO_BUG=1`**: BUG-1 상태 `shipped` 필터가 `delivered` 포함(→②가 탐지) · BUG-2 `요약 보기`가 미정의 함수 호출(→①이 탐지) · BUG-3 날짜 종료일 미포함 `<` 처리(→②가 탐지).

**수용 기준**: `DEMO_BUG=0` 전 시나리오 통과 · `DEMO_BUG=1` 위 3건이 정확히 실패로 검출 · `전체 삭제`는 차단 목록에 표기.

## 11. 운영 원칙 (문서화 사항)

- **사전 준비**: 데이터 검증은 DB 상태를 아는 것이 전제 — 실행 전 시드 스크립트 재실행 또는 read-only 검증만. 운영 DB는 read-only 계정.
- **민감정보**: 실제 앱의 증적에 개인정보가 담길 수 있음 — 스크린샷은 `report.mask_selectors`로 마스킹(v3 구현), 비디오·트레이스는 마스킹 미적용이므로 공유 범위 주의.
- **인증 상태**: `auth_state.json`은 0600 권한으로 생성하고 기본 자동 삭제. 명시적으로 보관한 파일은 로그인 세션과 동일한 민감정보로 취급.
- **쓰기 경계**: 자동 스윕의 저장·삭제·결제·발송·초대·배포·로그아웃 최소 차단 목록은 설정에서 제거 불가. `write_checks`는 `--allow-write-checks` 승인 후에만 실행하며 Runner도 동일 정책을 재검사.
- **DB 정답원**: `query.sql`은 설정+실행 이중 경계에서 단일 `SELECT/WITH`만 허용. DB 계정의 read-only 권한은 별도 필수.
- **큰 정수**: UI·API·DB 셀은 binary float로 바꾸지 않고 문자열로 정규화. JSON 소수도 `Decimal`로 읽어 유효 자릿수를 보존.
- **초기 로드 신호**: 시나리오 첫 `page.goto` 중 콘솔·페이지·HTTP 오류도 판정에 포함. 정상으로 합의된 리소스 실패만 `target.ignore_http_error_patterns` 정규식으로 제외.
- **전체 deadline**: `target.run_timeout_ms` 기본 30분. 브라우저 시작·인증·접속·크롤링·시나리오 전체에 적용하고, 초과한 부분 실행은 `infra_error`와 종료코드 2로 처리.
- **flaky**: 재시도 없음(D7). 간헐 실패는 대기 스텝 보강으로 해결.

## 12. 테스트 전략

유닛(pytest, 브라우저 불필요: 정규화·비교·설정·리포트) + e2e(`scripts/run_demo.sh [--bug]`) — §10 수용 기준 확인.

## 13. 백로그

**v2 완료(2026-07-23)**: 명세 단언 스텝+spec_checks(③), 전회차 diff, flaky 재확인 옵션, IDE 커맨드(/generate-tests·/analyze-report).
**v3 1차 완료(2026-07-23)**: 로그인 인증(auth.steps + storage_state 재사용), 스크린샷 마스킹(mask_selectors), SQLAlchemy 경유 DB 확장(PostgreSQL/MySQL), `${환경변수}` 치환.
**v3 2차 완료(2026-07-23)**: REST API 오라클(`query.api` — 오픈메타데이터류 대비), 페이지네이션 순회(`ui_table.pagination`), 쓰기 검증(⑤, `write_checks`), 접근성 기본 점검(간이·정보성, `a11y.enabled`).
**v3 3차 완료(2026-07-23)**: 시각 회귀(⑥, `visual_checks` + `--update-baselines` 승인 플로 — pillow 픽셀 diff·마젠타 diff 이미지), 웹훅 알림(`notify` — Slack Incoming Webhook 호환).
**v3 4차 완료(2026-07-27)**: 인증 상태 파일 0600 권한, 정상·실패 종료 기본 자동 삭제, `--preserve-auth-state` 명시적 보관, Git 제외와 회귀 테스트.
**v3 5차 완료(2026-07-27)**: 자동 스윕 최소 쓰기 차단 목록(설정+Runner 이중 경계), 차단 이유 리포트, `write_checks` 기본 차단과 `--allow-write-checks` 명시적 승인.
**v3 6차 완료(2026-07-27)**: float 없는 숫자 문자열 정규화, JSON 소수 정밀도 보존, 2^53 초과 주문번호 UI·API·DB E2E.
**v3 7차 완료(2026-07-27)**: 첫 page.goto 오류 판정, HTTP 오류 URL 허용 목록과 정규식 검증, 초기 콘솔 오류 Chromium 검출 데모.
**v3 8차 완료(2026-07-27)**: 기본 30분 전체 deadline, 크롤링·Runner 제한 연동, 부분 실행의 infra_error·종료코드 2와 전용 E2E.
**v3 9차 완료(2026-07-27)**: SQL 정답원 단일 `SELECT/WITH` 제한, 쓰기·DDL·관리 키워드와 다중 문장의 설정+실행 이중 차단.
**v3 10차 완료(2026-07-27, 코드 검토 반영)**: 3개 저장소 적대적 검토에서 실측한 반례 수정 —
`${환경변수}` 값과 비밀번호형 입력의 증거 마스킹(P0), DB NULL↔빈 셀 정규화,
간헐(flaky) 강등이 종료코드·`meta.status`를 덮지 않도록 수정,
SQL 가드에 `INTO`/`OUTFILE`/`DUMPFILE`·밑줄 함수명(`LOAD_FILE`류) 추가와 외부 DB 세션 read-only,
파괴적 동작 2단 차단 목록(명시 스텝까지 적용)과 한국어 어휘 보강,
`aria-label` 수집으로 아이콘 버튼 차단, 페이지네이션 deadline 결속,
UI 헤더 중복 감지, 실행 이력 비교의 (대상·설정) 일치 조건, 쓰기 변화량 `Decimal` 전환.

**v3 11차 완료(2026-07-27)**: 반응형 점검(`responsive_checks`) — 여러 뷰포트 폭에서
가로 오버플로(`scrollWidth > clientWidth`)를 결정적으로 검출하고 원인 요소를 지목.
데모 BUG-5(고정폭 배너)로 375·768·1280px 실패 검출, 정상 모드는 표를 스크롤 컨테이너에
넣어 전 폭 통과. Codex frontend-testing류 스킬 대비 결손이던 반응형 검증을 결정적 판정으로 편입.

**잔여 백로그**: axe 기반 정식 a11y(의존성 결정 필요), 병렬 실행, 크로스 브라우저(firefox/webkit 설치 필요),
시각 기준선 최초 생성 승인 정책(현재는 첫 실행 화면을 무승인 채택). — 이로써 검증 방법 ①~⑥이 모두 엔진에 구현됨 (⑦은 설계상 참고용).
