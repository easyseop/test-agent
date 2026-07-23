# 웹 애플리케이션 자동 테스트 에이전트 — 설계 (Phase 2)

> 작성일: 2026-07-23 · 상태: 설계 확정 → 구현(Phase 3) 진행
> 선행 문서: [01-review.md](./01-review.md)

## 0. 확정된 설계 결정

검토(§7)에서 제기한 미결 사항을 아래 기본값으로 확정한다. (사용자 피드백 시 변경 가능하도록 격리 설계)

| # | 결정 사항 | 확정 값 | 근거 / 변경 방법 |
|---|----------|--------|-----------------|
| D1 | 첫 검증 대상 | **동봉 데모앱** (Flask+SQLite, 버그 주입 모드 포함) | 안전·재현 가능. 에이전트가 실제 버그를 잡는지 엔드투엔드 자가 검증 가능. 실제 앱 적용 시 → 설정 YAML만 새로 작성 |
| D2 | 정답 쿼리 정의 | **사람이 YAML에 SQL 작성** | 독립적 정답 → 동어반복 함정 회피(검토 §3.2). LLM 생성으로 바꾸려면 → Plan 단계 플러그인 추가(§7) |
| D3 | LLM 사용 | **v1은 룰 기반으로 완결. LLM은 v2 확장 포인트로만 설계** | 결정적·비용 0·CI 적합(검토 §3.5). v2에서 시나리오 제안/실패 요약을 Plan·Report 단계에 플러그인 |
| D4 | 인증 | v1 범위 외 (데모앱은 로그인 없음) | 스텝 DSL에 `goto/fill/click`이 있어 로그인 시나리오를 `setup_steps`로 추가 가능한 구조만 확보 |
| D5 | 리포트 전달 | **파일 산출** (HTML 단일 파일 + MD + JSON + webm 비디오) | HTML은 스크린샷 내장 단일 파일 → 공유 용이. 메일/슬랙 전송은 v3 |

## 1. 전체 아키텍처

```
                       ┌──────────────────────────────────────────────┐
 configs/demo.yaml ──▶ │  cli.py  (run | discover)                    │
                       └───────────────┬──────────────────────────────┘
                                       ▼
        ┌──────────── ① Discover  discovery.py ─────────────┐
        │  BFS 크롤링(동일 출처, max_pages/depth)             │
        │  페이지별 인벤토리: 버튼/링크/입력/셀렉트 + 셀렉터   │
        │  페이지 전체 스크린샷                               │
        └───────────────────────┬───────────────────────────┘
                                ▼
        ┌──────────── ② Plan  scenarios.py ─────────────────┐
        │  버튼 스윕 시나리오 생성 (avoid_patterns 필터링)     │
        │  data_checks(YAML) → 데이터 정합성 시나리오          │
        │  [v2 확장점: LLM 시나리오 제안 플러그인]             │
        └───────────────────────┬───────────────────────────┘
                                ▼
        ┌──────────── ③ Execute  runner.py + browser.py ────┐
        │  시나리오별 새 브라우저 컨텍스트(=비디오 1개)        │
        │  스텝 DSL 실행, 스텝 전후 스크린샷                  │
        │  콘솔 에러/페이지 예외/HTTP≥400/다이얼로그/         │
        │  다운로드/네비게이션 감시 (browser.PageMonitor)     │
        └───────────────────────┬───────────────────────────┘
                                ▼
        ┌──────────── ④ Verify  runner.py + datacheck.py ───┐
        │  자동 신호 판정(§5 판정 규칙표)                     │
        │  UI 테이블 추출 → 정규화 → DB 쿼리 결과와 대조      │
        └───────────────────────┬───────────────────────────┘
                                ▼
        ┌──────────── ⑤ Report  report.py ──────────────────┐
        │  report.html(스크린샷 base64 내장, 한국어)          │
        │  report.md / report.json / videos/*.webm           │
        └────────────────────────────────────────────────────┘
```

- **드라이버(③)와 검증(④)·리포트(⑤)를 분리**: 추후 API·데이터파이프라인 대상 확장 시 ③만 교체.
- 실행 1회 = `runs/<타임스탬프>/` 디렉터리 하나에 모든 산출물 격리.

## 2. 저장소 구조

```
test-agent/
├── docs/                      # 01-review, 02-design (본 문서)
├── webtest_agent/             # 에이전트 패키지 (python -m webtest_agent)
│   ├── __main__.py, cli.py    # CLI 진입점 (run | discover)
│   ├── config.py              # YAML 로딩·검증 (dataclass, ConfigError)
│   ├── models.py              # 결과 모델 (ScenarioResult, StepResult…)
│   ├── browser.py             # Playwright 세션, PageMonitor, 스크린샷/비디오
│   ├── discovery.py           # 크롤러 + 요소 인벤토리
│   ├── scenarios.py           # 시나리오 생성 (버튼 스윕 + 데이터 검증)
│   ├── runner.py              # 스텝 실행 + 판정
│   ├── datacheck.py           # DB 쿼리 실행, UI 테이블 추출, 정규화·비교
│   └── report.py              # HTML/MD/JSON 리포트
├── demo_app/                  # 검증 대상 데모앱 (Flask + SQLite)
│   ├── app.py, seed.py, templates/
├── configs/demo.yaml          # 데모앱용 설정 (실제 앱 적용 시 이 파일을 본떠 작성)
├── scripts/run_demo.sh        # 시드→앱 기동→에이전트 실행→종료 원커맨드
├── tests/                     # 유닛 테스트 (브라우저 불필요, 순수 로직)
└── runs/                      # 실행 산출물 (gitignore)
```

## 3. 설정 스키마 (YAML)

```yaml
target:
  base_url: http://127.0.0.1:5057   # 필수
  settle_ms: 400                    # 각 액션 후 안정화 대기(ms)
  nav_timeout_ms: 10000             # 페이지 이동 타임아웃

crawl:
  enabled: true
  max_pages: 5                      # 크롤 상한
  max_depth: 2
  exclude_patterns: ["/logout"]     # URL 정규식 제외

button_sweep:
  enabled: true
  include_links: true               # 동일 출처 링크도 클릭 검증
  max_per_page: 30
  avoid_patterns:                   # 텍스트 정규식 — 매칭 시 클릭 금지(리포트에 '차단됨' 표기)
    ["삭제", "delete", "remove", "탈퇴", "결제", "pay", "logout", "로그아웃"]

data_checks:                        # UI↔DB 정합성 시나리오 (D2: 사람이 작성)
  - name: 상태필터-shipped
    description: 상태 필터 shipped 적용 시 화면 = DB
    page: /                        # base_url 기준 상대 경로
    steps:                         # §4 스텝 DSL
      - {action: select, selector: "#status", value: "shipped"}
      - {action: click,  selector: "#apply"}
      - {action: wait_for, selector: "#orders-table"}
    ui_table:
      selector: "#orders-table"
      columns: [주문번호, 고객, 상태, 금액]   # 비교할 UI 헤더명(부분 선택 가능)
    query:
      db: sqlite:///demo_app/demo.db          # 상대 경로는 CWD 기준
      sql: "SELECT id, customer, status, amount FROM orders WHERE status='shipped'"
      order_matters: false          # 기본: 순서 무시(멀티셋 비교)

report:
  title: 주문 대시보드 자동 테스트
  video: true                       # 시나리오별 webm 녹화
  language: ko

output_dir: runs
```

- `query.sql`의 SELECT 컬럼은 `ui_table.columns`와 **순서대로 1:1 대응** (개수 불일치 시 설정 오류로 실패 처리).
- DB는 v1에서 SQLite 지원(`sqlite:///path`). 확장점: URL 스킴별 커넥터 등록(§7).

## 4. 시나리오 스텝 DSL

| action | selector | value | 의미 |
|--------|:--:|:--:|------|
| `goto` | – | 경로/URL | 페이지 이동 |
| `click` | ✔ | – | 요소 클릭 |
| `fill` | ✔ | 문자열 | 입력값 채우기 |
| `select` | ✔ | 옵션 value | 셀렉트 박스 선택 |
| `check` | ✔ | – | 체크박스 체크 |
| `press` | ✔ | 키 이름 | 키 입력 (예: Enter) |
| `wait_for` | ✔ | – | 요소 출현 대기 |
| `wait_ms` | – | 숫자 | 고정 대기 |

- 각 스텝은 Playwright 자동 대기(가시성·활성화) 위에서 실행, 스텝 실패 = 시나리오 실패 + 실패 시점 스크린샷.
- 로그인이 필요한 실제 앱: `data_checks[].steps` 앞부분(또는 추후 `setup_steps`)에 `goto/fill/click`으로 표현 가능 (D4).

## 5. 판정 규칙 (오라클, 검토 §3.1 반영)

| 신호 | 판정 | 비고 |
|------|------|------|
| 스텝 실행 실패 (요소 없음/타임아웃) | **실패** | 실패 스크린샷 첨부 |
| JS 콘솔 에러 / 페이지 예외 발생 | **실패** | 메시지 수집 |
| 액션이 유발한 HTTP 응답 ≥ 400 | **실패** | URL·상태코드 수집 |
| UI↔DB 대조 불일치 | **실패** | 누락/초과 행 샘플 + 건수 diff |
| 버튼 클릭 후 무반응 (URL·DOM·다이얼로그·다운로드·네비게이션 전무) | **경고** | 죽은 버튼 후보 |
| 다이얼로그 출현 | 정보 (기본 '취소' 응답) | 파괴적 확인창 안전장치 |
| 그 외 | **통과** | |

- 시나리오 판정 = 소속 신호 중 최악값 (실패 > 경고 > 통과).
- 실행 종료 코드: 실패 ≥ 1 → `1` (CI 게이트 사용 가능), 아니면 `0`.

## 6. 데이터 정합성 비교 규칙 (datacheck.py)

1. **UI 추출**: `ui_table.selector`에서 `thead th` → 헤더, `tbody tr > td` → 행 (thead 없으면 첫 행을 헤더로).
2. **컬럼 매핑**: `columns`에 적힌 UI 헤더명 순서대로 열 추출 ↔ SQL SELECT 컬럼 순서 1:1.
3. **정규화**: 공백 압축·trim → 통화기호/콤마 제거 후 숫자로 해석되면 수치 표준형(`12,300원`→`12300`), 아니면 문자열 유지(날짜 `2026-06-01` 등).
4. **비교**: 기본 멀티셋(Counter) 비교(순서 무시), `order_matters: true` 시 리스트 비교.
5. **결과**: `일치 여부, UI 건수, DB 건수, UI에 없는 DB 행(누락) 샘플≤10, DB에 없는 UI 행(초과) 샘플≤10`.
6. **v1 한계 (문서화)**: 페이지네이션 미대응 — 단일 페이지 전체 표시 표 기준. (v3에서 페이지 순회)

## 7. 확장 포인트 (v2/v3 대비)

| 확장 | 위치 | 방법 |
|------|------|------|
| LLM 시나리오 제안 (v2) | Plan 단계 | `scenarios.py`의 시나리오 리스트에 제안분 병합. 인벤토리(JSON)+앱 설명을 입력으로 Claude API 구조화 출력 → 스텝 DSL 검증 후 채택. **판정 로직은 불변** |
| LLM 실패 요약 (v2) | Report 단계 | `report.py`에 요약 섹션 추가 (결과 JSON 입력) |
| 타 DB (PostgreSQL/MySQL) | datacheck | DB URL 스킴별 커넥터 함수 등록 |
| 페이지네이션 | datacheck | '다음' 셀렉터 순회 옵션 |
| API/파이프라인 대상 | Execute 교체 | 드라이버 인터페이스 분리 유지 |

## 8. 데모앱 명세 (검증 대상)

**주문 관리 대시보드** — `demo_app/` (Flask + SQLite, 포트 5057)

- 데이터: `orders(id, customer, status, category, amount, created_at)` — `seed.py`가 고정 시드로 120건 생성 (상태 4종, 카테고리 3종, 2026-05-01~06-30).
- UI: 필터(상태·카테고리·고객 검색·날짜 범위) + `조회`/`초기화`/`새로고침`/`요약 보기`/`CSV 내려받기` 버튼 + 결과 표(`#orders-table`) + 건수 표시 + About 링크 + **`전체 삭제` 버튼(차단 패턴 시연용 — 스윕이 건너뛰어야 정상)**.
- **버그 주입 모드** `DEMO_BUG=1` (에이전트 탐지 능력 시연):
  - BUG-1 (데이터): 상태 `shipped` 필터가 `delivered`까지 포함해 조회 → 데이터 정합성 검증이 잡아야 함
  - BUG-2 (JS): `요약 보기` 버튼이 미정의 함수 호출 → 콘솔 에러를 버튼 스윕이 잡아야 함
  - BUG-3 (데이터): 날짜 범위 종료일이 미포함(`<`) 처리 → 경계일 주문 누락을 정합성 검증이 잡아야 함
- 수용 기준: `DEMO_BUG=0`이면 전 시나리오 통과, `DEMO_BUG=1`이면 위 3건이 정확히 실패로 보고될 것.

## 9. 산출물 구조

```
runs/20260723-153000/
├── report.html        # 단일 파일(스크린샷 base64 내장) — 공유용
├── report.md          # 텍스트 요약
├── report.json        # 기계 판독용 전체 결과
├── discovery.json     # 페이지/요소 인벤토리
├── screenshots/       # 스텝·페이지 스크린샷 (jpeg)
└── videos/            # 시나리오별 webm 녹화
```

## 10. 테스트 전략

- **유닛** (`tests/`, pytest, 브라우저 불필요): 정규화·비교 로직, 설정 파싱·검증 오류, 리포트 생성.
- **엔드투엔드** (`scripts/run_demo.sh`): 정상 모드 전체 통과 + 버그 모드 3건 탐지 — §8 수용 기준으로 확인.
- 안전성: 데모앱의 `전체 삭제` 버튼이 스윕에서 차단되는지 리포트로 확인.
