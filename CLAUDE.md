# webtest-agent 프로젝트 메모리

## ⛳ 최우선 규칙 — 매 요청마다 인수인계서를 먼저 읽는다

**사용자의 새 질문·요청을 받을 때마다, 다른 일을 하기 전에 `HANDOFF.md`를 Read로 읽는다.**
컨텍스트가 압축되거나 세션이 새로 시작돼도 이 규칙 하나로 작업이 이어진다.

- 이 파일(`CLAUDE.md`)은 **규약**을, `HANDOFF.md`는 **현재 상태**(진행 위치·환경 사실·
  상시 지시·다음 작업)를 담는다. 둘 다 필요하다.
- 개발 단위를 끝낼 때마다 `HANDOFF.md`를 갱신한다. 갱신하지 않은 작업은 다음 세션에 없는 일이 된다.
- 단순 인사·잡담에는 생략해도 되지만, **작업·질문·판단이 섞이면 무조건 읽는다.**

---

웹 앱 자동 테스트 에이전트. 결정적 실행 엔진(`webtest_agent/`) + 검증 대상 데모앱(`demo_app/`) + LLM 유지 위키(`knowledge/`).
배경 문서: `docs/01-review.md`(검토) · `docs/02-design.md`(설계 확정본) · **`HANDOFF.md`(인수인계·현재 상태)**.

## 자주 쓰는 명령

```bash
python3 -m pytest tests/ -q                      # 유닛 테스트
./scripts/run_demo.sh                            # 데모 e2e (정상 모드, 전부 통과해야 정상)
./scripts/run_demo.sh --bug                      # 버그 주입 모드 (3건 검출 + 종료코드 1이 정상)
python3 -m webtest_agent run -c configs/demo.yaml       # 에이전트 실행 (데모앱 기동 상태에서)
python3 -m webtest_agent discover -c configs/demo.yaml  # 크롤링·인벤토리만
```

산출물은 `runs/<타임스탬프>/` (gitignore됨 — 증적은 로컬 보관): `report.html`(단일파일)·`report.json`·`walkthrough.md`·`videos/`·`traces/`·`discovery.json`.

## 역할 분담 — 절대 규칙

- **LLM(이 세션)의 일**: 테스트 케이스 YAML 작성(`/generate-tests`), 실패 분석(`/analyze-report`), `knowledge/` 위키 유지.
- **엔진의 일**: 실행·채점(합격/불합격 판정)·증적 수집. **판정 로직에 LLM을 개입시키지 말 것.**
- **기대값의 근거는 앱 '설명서'(기획 의도)에서 가져온다. 구현 코드는 셀렉터·테이블명 등 좌표 확인용으로만 쓴다.** 구현의 WHERE절을 베끼면 버그가 기대값에 복제된다(동어반복 함정).

## knowledge/ 위키 — 스키마와 규약

3층 구조: **Raw sources**(앱 설명서·소스·`runs/` 결과·`discovery.json` — 읽기만, 수정 금지) → **Wiki**(`knowledge/` — LLM이 작성·유지) → **Schema**(이 문서).

```
knowledge/
├── index.md                 # 전체 페이지 카탈로그 (모든 ingest 시 갱신)
├── log.md                   # append-only 이력 (모든 작업마다 한 줄 추가)
└── apps/<앱이름>/
    ├── overview.md          # 앱 개요·목적 (설명서 요약 + 근거 링크)
    ├── screens.md           # 화면·기능 맵 + 셀렉터 좌표
    ├── test-catalog.md      # 테스트 케이스 카탈로그 (무엇을·왜 검증하는지 + 도출 근거)
    └── issues/<슬러그>.md    # 발견된 버그 페이지 (재현 시나리오·증적·상태)
```

**규약:**
- 링크는 표준 마크다운 상대경로(`[텍스트](../screens.md)`)만 — `[[위키링크]]` 금지 (VS Code/GitHub/옵시디언 모두 호환).
- `log.md` 항목 형식: `## [YYYY-MM-DD] <작업> | <제목>` (작업: `init`/`ingest`/`run`/`query`/`lint`). `grep "^## \[" knowledge/log.md | tail -5`로 최근 이력 조회 가능.
- **위키는 정답이 아니다** — 테스트 기대값의 근거는 항상 원문(설명서)으로 소급하고, 위키 페이지에는 근거 원문 위치를 남긴다. 위키는 탐색·좌표·이력용.

**워크플로:**
- **Ingest(문서/앱 변경)**: `/generate-tests` 실행 시 YAML과 위키(overview·screens·test-catalog)를 함께 갱신.
- **Ingest(실행 결과)**: `report.json`을 읽고 — `log.md`에는 매 실행 한 줄 append. 위키 페이지는 **상태 변화가 있을 때만** 갱신(새 실패 → `issues/` 페이지 생성 + walkthrough 링크, 복구 → 해당 이슈에 해소 기록). 전 실행을 페이지에 다 쓰면 노이즈.
- **Query**: 위키에서 답을 찾고, 가치 있는 답변(비교표·분석)은 위키에 새 페이지로 재파일링.
- **Lint(요청 시)**: ① 설명서에 있는데 test-catalog에 없는 기능(커버리지 갭) ② 앱에서 사라진 셀렉터를 참조하는 시나리오(죽은 테스트) ③ 페이지 간 모순·고아 페이지 ④ index.md 누락 항목 — 발견 사항을 보고하고 수정 제안.

## 코드 컨벤션

- Python 3.11, 표준 라이브러리 + playwright/flask/pyyaml만. 타입힌트 사용.
- 사용자 대면 문자열(리포트·CLI·오류 메시지)은 한국어.
- 새 검증 로직은 반드시 `tests/`에 유닛 테스트 동반. 판정 규칙 변경 시 `docs/02-design.md` §5 갱신.
