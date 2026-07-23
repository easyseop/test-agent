# webtest-agent — 웹 애플리케이션 자동 테스트 에이전트

웹 앱의 화면을 **스스로 크롤링해 버튼·링크를 전수 점검**하고, **필터 조회 결과가 실제 DB 쿼리 결과와 일치하는지 대조**하며, 그 과정을 **비디오·스크린샷·트레이스로 녹화**해 한국어 리포트로 정리해 주는 도구입니다.

실행 엔진은 100% 결정적(LLM·API 키 불필요)이며, 실패 시 종료코드 1을 반환해 CI 게이트로 쓸 수 있습니다.

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
| 버튼·링크가 동작하는가 | 전수 클릭(스윕) 후 신호 관찰 | 콘솔 에러/페이지 예외 → **실패**, HTTP≥400 → **실패**, 무반응 → **경고** |
| 필터 조회 데이터가 맞는가 | 화면 표 추출 ↔ **DB에 직접 실행한 정답 쿼리** 비교 (정규화 후) | 불일치 시 누락/초과 행 샘플과 함께 **실패** |
| 건수 표기가 맞는가 | 화면의 "N건" ↔ 실제 표 행 수 (불변식) | 불일치 → **실패** |
| 위험 버튼 안전장치 | `삭제/결제/로그아웃` 등 차단 패턴 매칭 요소는 클릭하지 않음 | 리포트에 차단 목록 기록 |

## 설치

```bash
pip install -r requirements.txt
python -m playwright install chromium   # Playwright 브라우저 (이미 있으면 생략)
```

## 빠른 시작 (동봉 데모앱)

```bash
./scripts/run_demo.sh          # 정상 모드 → 12개 시나리오 전부 통과
./scripts/run_demo.sh --bug    # 버그 주입 모드 → 심어둔 버그 3개를 정확히 검출 (종료코드 1이 정상)
```

버그 주입 모드(`DEMO_BUG=1`)의 검출 예시:

```
✗ 상태필터-shipped   — UI↔DB 불일치: 화면 75건 vs DB 36건 (화면에 초과 39건)   ← delivered가 섞여 나옴
✗ 날짜범위-6월       — UI↔DB 불일치: 화면 64건 vs DB 67건 (화면에 누락 3건)    ← 경계일(6/30) 누락
✗ 버튼점검 요약 보기 — 페이지 예외: showSummry is not defined                  ← JS 오타
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

로그인이 필요한 앱은 `steps` 앞부분에 `goto → fill(아이디/비밀번호) → click(로그인)`을 넣어 표현할 수 있습니다.

### 안전 수칙

- 반드시 **테스트/스테이징 환경 + 재생성 가능한(시드) DB**에서 실행하세요. 운영 DB는 read-only 계정으로 조회 검증만.
- 버튼 스윕은 `avoid_patterns`(삭제·결제·로그아웃 등)에 걸리는 요소를 클릭하지 않고, confirm 창은 자동으로 '취소'합니다. 대상 앱에 맞게 패턴을 보강하세요.
- 실제 앱의 스크린샷·비디오에는 개인정보가 담길 수 있습니다 — 증적 공유 범위에 주의하세요.

## LLM과 함께 쓰기 (선택)

실행 엔진은 LLM과 무관하지만, **테스트 케이스(YAML) 작성·실패 분석은 IDE의 LLM(Claude Code 등)에게 맡기는 워크플로**를 설계에 포함했습니다 — 앱 설명서·소스·`discovery.json`을 읽혀 시나리오 초안을 생성시키고, diff 리뷰 후 커밋하는 방식입니다. 지식 축적용 위키 패턴(knowledge/)을 포함한 상세 가이드는 [docs/02-design.md](docs/02-design.md) §1·§8 참조.

## v1 한계

- 데이터 대조는 단일 페이지 표 기준 (페이지네이션 미대응)
- DB는 SQLite만 (PostgreSQL/MySQL은 커넥터 추가 예정)
- 재시도 없음 — 간헐 실패는 `wait_for` 스텝 보강으로 예방

로드맵과 백로그는 [docs/02-design.md](docs/02-design.md) §13, 검토 배경은 [docs/01-review.md](docs/01-review.md) 참조.
