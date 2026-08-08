# 빠른 시작

이 문서 하나로 처음부터 끝까지 갈 수 있다. 자세한 설명은 [README](README.md),
개발 용어 없는 버전은 [사용자 가이드](docs/USER_GUIDE.md)에 있다.

## 무엇을 하는 도구인가

우리 웹사이트를 진짜 크롬으로 열어서 **버튼을 눌러보고**, 화면에 나온 숫자가
**DB와 맞는지 대조**하고, 그 과정을 **영상·캡처·절차서**로 남긴다.

판정에는 AI가 끼지 않는다. 같은 입력이면 항상 같은 결과가 나오고, 실패하면
종료코드로 알려주므로 CI 게이트로 쓸 수 있다.

브라우저에 뜨는 화면이면 언어·프레임워크는 상관없다. Go로 만든 Gitea, PHP로
만든 Kanboard에서 실측으로 확인했다.

## 1. 설치 — 한 번만

Python 3.11 이상이 필요하다.

```bash
git clone https://github.com/easyseop/test-agent
cd test-agent
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[db]" -c constraints.txt
python -m playwright install chromium
```

동봉된 데모앱으로 설치가 됐는지 확인한다. **실패 0건**으로 끝나면 정상이다.

```bash
./scripts/run_demo.sh
```

## 2. 설정 만들기 — 사이트마다 한 번

### 2-1. 화면에 뭐가 있는지 먼저 뽑는다

```bash
python -m webtest_agent discover -c configs/우리사이트.yaml
```

`runs/<시각>/discovery.json`에 페이지·버튼·링크·입력칸 목록이 생긴다.
처음에는 `target.base_url`만 적힌 최소 YAML로 시작하면 된다.

### 2-2. "이건 이래야 한다"를 적는다

`configs/demo.yaml`을 본떠서 쓴다. 자주 쓰는 건 이 세 가지다.

**로그인** — 실행 시작할 때 한 번 하고, 그 세션을 전부 재사용한다.

```yaml
auth:
  steps:
    - {action: goto, value: /login}
    - {action: fill, selector: "#username", value: 테스트계정}
    - {action: fill, selector: "#password", value: "${MY_PASSWORD}"}
    - {action: click, selector: "#login-btn"}
    - {action: assert_visible, selector: "#dashboard"}
```

비밀번호는 파일에 적지 않는다. `${환경변수}`로 두면 실행할 때만 들어가고
리포트에는 `***`로 남는다.

**기획 의도 검증** — DB 없이 화면 동작만 확인한다.

```yaml
spec_checks:
  - name: 닫힘탭-닫은것만
    description: 닫힘 탭을 누르면 닫은 이슈만 보여야 한다
    page: /issues
    steps:
      - {action: click, selector: "a[href*='state=closed']"}
      - {action: assert_visible, selector: "a[href$='/issues/1']"}
      - {action: assert_not_visible, selector: "a[href$='/issues/2']"}
```

**데이터 대조** — 화면 표와 DB 조회 결과를 맞춰본다.

```yaml
data_checks:
  - name: 상태필터-배송중
    description: 상태를 배송중으로 거르면 배송중 주문만 나와야 한다
    page: /
    steps:
      - {action: select, selector: "#status", value: "shipped"}
      - {action: click, selector: "#apply"}
      - {action: wait_for, selector: "#orders-table"}
    ui_table:
      selector: "#orders-table"
      columns: [주문번호, 고객, 상태, 금액]
      count_selector: "#result-count"
    query:
      db: sqlite:///path/to/app.db
      sql: "SELECT id, customer, status, amount FROM orders WHERE status='shipped'"
```

**화면 값을 뽑아 다시 쓰기** — 주문번호처럼 실행할 때마다 달라지는 값.

```yaml
steps:
  - {action: click, selector: "#place-order"}
  - {action: extract, selector: "#order-no", store_as: order_no,
     pattern: "([0-9]+)"}          # 선택: 이 부분만 취함
  - {action: goto, value: "/orders/{{order_no}}"}
query:
  db: sqlite:///staging.db
  sql: "SELECT status FROM orders WHERE order_no = :order_no"
  params: {order_no: "{{order_no}}"}      # SQL 본문에 직접 넣지 않는다
```

**결제창처럼 iframe·새 창으로 뜨는 화면**

```yaml
steps:
  - {action: fill, selector: "#card", value: "${TEST_CARD}", frame: "iframe#pg"}
  - {action: click, selector: "#pay"}
  - {action: wait_popup}                  # 새 창으로 옮긴다
  - {action: assert_text, selector: "#state", value: "승인"}
  - {action: close_popup}                 # 원래 창으로 돌아온다
```

> **기대값은 설명서에서 가져온다.** 구현 코드의 WHERE절을 베끼면 구현 버그가
> 기대값에 복제돼서 영원히 통과한다. 소스는 셀렉터·테이블명 확인용으로만 쓴다.

`discovery.json`과 "이건 이래야 한다" 문장 몇 줄을 IDE의 AI에게 주면 YAML
초안을 대신 써준다. 이때도 사이트 접속은 필요 없고, 뽑아둔 파일만 주면 된다.

## 3. 돌리기 — 매번

```bash
python -m webtest_agent run -c configs/우리사이트.yaml
```

| 자주 쓰는 옵션 | 뜻 |
|---|---|
| `--headed` | 브라우저 창을 띄워서 눈으로 본다 |
| `--workers 4` | 읽기 전용 시나리오를 4개씩 병렬로 (결과 순서는 고정) |
| `--allow-write-checks` | 저장·등록 검증과 데이터 초기화를 승인 (기본 차단) |

## 4. 결과 읽기

종료코드 세 가지로 갈린다. **실행이 안 된 것을 통과로 세지 않는다.**

| 코드 | 뜻 | 할 일 |
|---|---|---|
| `0` | 통과 | 없음 |
| `1` | 실패 — 사이트가 기대대로 안 됨 | 리포트에서 실패 시나리오 확인 |
| `2` | 실행 불가 — 사이트가 꺼졌거나 설정 오류 | 대상·설정 확인 후 재실행 |

산출물은 `runs/<시각>/`에 쌓인다.

- `report.html` — 캡처가 들어 있는 단일 파일. 이거 하나만 공유해도 된다
- `walkthrough.md` — 단계별 절차서. 통과면 매뉴얼, 실패면 재현 절차서
- `report.xml` — JUnit XML. CI가 테스트별 결과로 표시한다
- `videos/`, `traces/` — 화면 녹화, 되감기 기록

매 실행마다 직전 실행과 비교해서 **신규 실패 / 복구 / 계속 실패**를 표시한다.

## 5. 지켜야 할 것

- **테스트·스테이징 환경에서 돌린다.** 운영 DB는 read-only 계정으로 조회 검증만.
- 쓰기 검증을 반복하려면 `write_reset`으로 매 실행 전에 데이터를 되돌린다.
  이것도 `--allow-write-checks` 승인이 있어야 돌고, 실패하면 판정하지 않고
  실행 불가로 끝난다.
- 업로드할 파일은 설정 파일 옆에 둔다. 그 폴더 밖 경로는 거부된다.
- 저장·삭제·결제·발송·로그아웃 버튼은 자동 점검에서 **누르지 않는다.** 이 최소
  차단 목록은 YAML에서 뺄 수 없고, 막은 이유가 리포트에 남는다.
- 쓰기 검증(`write_checks`)은 기본 차단이다. 시드 DB에서 `--allow-write-checks`로
  명시 승인할 때만 돈다.
- 비밀번호·토큰은 `${환경변수}`로만 넣는다. YAML·Git·리포트에 평문으로 두지 않는다.
- 로그인 세션 파일은 실행이 끝나면 자동 삭제된다. `--preserve-auth-state`로
  남긴 경우 비밀번호처럼 다루고 직접 지운다.
- 실제 사이트의 캡처·영상에는 개인정보가 담길 수 있다. 공유 범위에 주의한다.
  가려야 할 칸은 `report.mask_selectors`로 스크린샷에서 마스킹한다.

## 막힐 때

| 증상 | 대개 이것 |
|---|---|
| 종료코드 2, 시나리오 0개 | 대상 사이트가 안 떠 있다 |
| 로그인이 안 된다 | `${환경변수}`가 안 들어갔다. 셸에서 `export`했는지 확인 |
| 셀렉터를 못 찾는다 | `discovery.json`에서 실제 값을 다시 뽑는다 |
| 화면 글자가 기대와 다르다 | 로케일 문제. 브라우저가 보는 언어로 적는다 |
| 정상인데 404가 실패로 잡힌다 | `target.ignore_http_error_patterns`에 URL 정규식 추가 |
| 가끔 실패한다 | `wait_for`를 넣는다. 재시도는 기본으로 하지 않는다 |
