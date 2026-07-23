---
description: 대상 앱의 설명서·소스·인벤토리를 읽고 테스트 시나리오 YAML과 knowledge 위키를 생성·갱신
---

대상 앱의 테스트 케이스를 작성하라. 인자: $ARGUMENTS
(인자 형식: `<앱이름> <소스 경로> [설명서 경로…]` — 인자가 없으면 무엇을 대상으로 할지 먼저 물어라)

## 절차

1. **설명서를 먼저 읽는다** (README, 기획 문서 등). 각 기능의 **의도된 동작**("~하면 ~만 표시된다")을 목록화한다. 이것이 기대값의 유일한 근거다.
2. **소스코드에서 좌표만 수집한다**: 화면 셀렉터(id/name), 라우트, DB 테이블·컬럼명. 최근 `runs/*/discovery.json`이 있으면 실제 존재하는 요소를 교차 확인한다. 없으면 앱을 띄우고 `python3 -m webtest_agent discover -c <설정>`을 먼저 실행한다.
3. **`configs/<앱이름>.yaml` 생성/갱신** — `configs/demo.yaml`을 형식 참고:
   - 의도된 동작마다 `data_checks` 항목 1개: 스텝 DSL(goto/click/fill/select/check/press/wait_for/wait_ms)로 필터 적용 → `ui_table`(비교할 UI 헤더명, 가능하면 `count_selector`) → `query.sql`.
   - **`query.sql`의 WHERE 조건은 1번의 '의도'에서 도출한다. 구현 코드의 쿼리를 복사·번역하지 마라** (동어반복 함정 — 구현 버그가 기대값에 복제된다).
   - SELECT 컬럼 순서는 `ui_table.columns`와 1:1 대응.
   - 파괴적 기능(삭제·결제 등)은 시나리오로 만들지 말고 `button_sweep.avoid_patterns`에 패턴을 보강한다.
4. **knowledge 위키 동시 갱신** (CLAUDE.md의 위키 규약 준수): `apps/<앱이름>/overview.md`(설명서 요약+근거 위치), `screens.md`(셀렉터 맵), `test-catalog.md`(각 케이스가 무엇을·왜 검증하는지 + 도출 근거 문구), `index.md`, `log.md`(`ingest` 항목 append).
5. **검증**: `python3 -c "from webtest_agent.config import load_config; load_config('configs/<앱이름>.yaml')"`로 스키마 통과 확인. 가능하면 실제 1회 실행해 스텝이 도는지 확인한다.
6. **사용자 리뷰 요청**: 생성·변경된 파일 목록과 각 시나리오의 근거(설명서 어느 문구)를 요약해 보여주고, 특히 `query.sql`을 검토받아라. 커밋은 사용자 확인 후에.

## 금지

- 설명서에 근거 없는 기대값을 지어내지 마라 — 근거가 없으면 시나리오를 만들지 말고 "설명서에 이 기능의 의도가 없다"고 보고하라.
- 판정 로직(엔진 코드) 수정으로 문제를 우회하지 마라.
