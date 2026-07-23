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
| D5 | 인증 | ✅ v3 슬라이스로 구현 — `auth.steps`(스텝 DSL)로 로그인 1회 수행 → storage_state 저장 → 전 시나리오·크롤링이 세션 재사용. 비밀번호는 `${환경변수}` 치환(평문 금지). 실패 시 실행 중단(종료코드 2) |
| D6 | 리포트 전달 | 파일 산출 (HTML 단일파일 + MD + JSON + walkthrough + webm + trace) |
| D7 | flaky 정책 | 기본 **재시도 없음**. 명시적 대기(`wait_for`)와 안정화 대기로 예방. v2: `target.flaky_recheck: true` 설정 시 실패 시나리오를 1회 재실행해 통과하면 '간헐(flaky) 의심' **경고**로 표시(최초 실패 증적 유지) — 기본 꺼짐 |

## 1. 검증 방법 분류 (오라클 기준)와 배치

| # | 검증 방법 | 정답의 출처 | v1 구현 |
|---|----------|-----------|:--:|
| ① | 오류 신호 기반 — 콘솔 에러·페이지 예외·HTTP≥400·무반응·크래시 | 보편 상식 | ✅ 버튼 스윕 |
| ② | 독립 정답원 대조 — UI 표시 데이터 ↔ DB 직접 쿼리 | DB | ✅ **핵심 기능** |
| ③ | 명세 기반 단언 (기대 동작 명시) | 사람/LLM 명세 | ✅ v2 구현 — `spec_checks` + `assert_visible/assert_text/assert_url` (명세 작성은 사람 또는 `/generate-tests`) |
| ④ | 불변식 기반 — 예: 표시 건수 = 실제 행 수 | 항상 참인 성질 | ✅ 일부 (건수 불변식) |
| ⑤ | 상태 전이 (쓰기 액션의 DB 반영) | 전후 DB 상태 | v3 |
| ⑥ | 기준선/스냅샷 대조 (시각 회귀) | 과거 실행 | v3 |
| ⑦ | 사람/LLM 판단 | 사람 감각 | 참고 코멘트만 (v2) |

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
    query:
      db: sqlite:///demo_app/demo.db         # 상대 경로는 CWD 기준
      sql: "SELECT id, customer, status, amount FROM orders WHERE status='shipped'"
      order_matters: false

spec_checks:                        # (v2) 명세 기반 단언 시나리오 — 검증 방법 ③
  - name: 요약보기-합계표시
    page: /
    steps:
      - {action: click, selector: "#summary-btn"}
      - {action: assert_visible, selector: "#summary"}
      - {action: assert_text, selector: "#summary", value: "합계"}

report:
  title: 주문 대시보드 자동 테스트
  video: true
  trace: true
  mask_selectors: ["#orders-table td:nth-of-type(2)"]   # (v3) 스크린샷에서 가릴 요소(개인정보)

output_dir: runs
```

`query.sql`의 SELECT 컬럼 ↔ `ui_table.columns` **순서 1:1 대응** (개수 불일치 = 설정 오류로 실패).
`query.db`는 `sqlite:///`(내장, read-only) 외에 SQLAlchemy URL(`postgresql://…`, `mysql+pymysql://…`)도 지원 — sqlalchemy+드라이버 설치 필요. 접속 문자열의 비밀번호는 `${환경변수}`로.
`mask_selectors`는 **스크린샷에만** 적용된다(비디오·트레이스는 미적용 — 공유 범위 주의).

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
| 클릭 후 무반응(URL·DOM·다이얼로그·다운로드·팝업·네비게이션 전무) | 경고 |
| 다이얼로그 출현(기본 '취소' 응답) | 정보 |

시나리오 판정 = 최악값(실패>경고>통과). 종료 코드: 실패≥1 → `1` (CI 게이트).

## 6. 데이터 정합성 비교 규칙

UI 추출(thead th→헤더, tbody tr td→행) → `columns` 헤더명으로 열 선택 → 정규화(공백 압축, 콤마·통화기호 제거 후 숫자 표준화 `12,300원→12300`, 날짜는 문자열 유지) → 기본 멀티셋(Counter) 비교, `order_matters` 옵션 → 결과: 일치 여부·UI/DB 건수·누락/초과 행 샘플≤10. `count_selector` 지정 시 표시 건수=실제 행 수 불변식 추가 검증. **v1 한계: 페이지네이션 미대응**(단일 페이지 표 기준).

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
- **flaky**: 재시도 없음(D7). 간헐 실패는 대기 스텝 보강으로 해결.

## 12. 테스트 전략

유닛(pytest, 브라우저 불필요: 정규화·비교·설정·리포트) + e2e(`scripts/run_demo.sh [--bug]`) — §10 수용 기준 확인.

## 13. 백로그

**v2 완료(2026-07-23)**: 명세 단언 스텝+spec_checks(③), 전회차 diff, flaky 재확인 옵션, IDE 커맨드(/generate-tests·/analyze-report).
**v3 1차 완료(2026-07-23)**: 로그인 인증(auth.steps + storage_state 재사용), 스크린샷 마스킹(mask_selectors), SQLAlchemy 경유 DB 확장(PostgreSQL/MySQL), `${환경변수}` 치환.
**잔여 백로그**: a11y 검사(axe — 의존성 결정 필요), 쓰기 검증(⑤), 시각 회귀(⑥), 페이지네이션, 병렬 실행, 크로스 브라우저, 슬랙/메일 전송, **REST API 오라클**(`query.api` — 오픈메타데이터처럼 엔티티를 DB에 JSON으로 저장하고 목록·검색이 검색엔진(ES)을 경유하는 앱은 SQL 대신 자체 REST API 응답을 정답원으로 쓰는 것이 적합).
