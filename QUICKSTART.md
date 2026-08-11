# 빠른 시작

이 문서 하나로 처음부터 끝까지 갈 수 있다. 자세한 설명은 [README](README.md),
개발 용어 없는 버전은 [사용자 가이드](docs/USER_GUIDE.md)에 있다.

> **이 문서는 무엇을 쓸 수 있는지를 늘어놓은 사전이다.**
> 처음이라 *어떤 순서로* 해야 할지 모르겠으면
> [설정 한 바퀴](docs/04-config-walkthrough.md)를 먼저 본다 — 아무것도 없는
> 상태에서 실행까지 한 번 돌린다.

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

`runs/<시각>/discovery.json`에 페이지·버튼·링크·입력칸·표 목록이 생긴다.
처음에는 `target.base_url`만 적힌 최소 YAML로 시작하면 된다.

표는 화면에 바로 찍히므로 `discovery.json`을 열지 않아도 된다.

```
  / — 버튼 4 · 링크 4 · 입력 3 · 셀렉트 2 · 표 1  (주문 관리 대시보드)
      #orders-table  123행  [주문번호 | 고객 | 상태 | 카테고리 | 금액 | 주문일]
```

`ui_table`의 `selector`와 `columns`가 그대로 여기 있다. `id`가 없으면
`data-testid` 같은 테스트용 속성을 셀렉터로 쓴다 — 자리 기반 경로보다 오래 간다.

**행 수를 같이 보는 이유.** 1행짜리 표를 정답원과 대조해봐야 증명되는 게 거의
없다. 그 사실은 설정을 **쓰기 전에** 알아야지, 다 돌리고 리포트를 열어야 알면
늦다. 안 보이는 표(`※화면에 안 보임`)와 0행 표도 지우지 않고 그대로 보여준다.

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

DB에 못 붙으면 그 화면이 쓰는 REST 응답을 정답원으로 둔다. 화면이 백엔드 응답을
제대로 그리는지까지는 이걸로 확인된다.

```yaml
    query:
      api:
        url: "/api/orders?status=shipped"
        rows_path: orders                     # 응답에서 행 배열 위치 (점 표기)
        columns: [id, customer, status, amount]
        headers: {Authorization: "Bearer ${API_TOKEN}"}   # 필요할 때만
```

`rows_path`와 `columns`를 손으로 찾지 말고 응답에서 뽑는다.

```bash
python -m webtest_agent inspect-api /api/orders?status=shipped -c configs/우리사이트.yaml
```

응답을 걸어다니며 **행 배열 후보를 전부** 보여주고, 각 후보의 필드를 점 표기
경로·타입·예시값으로 나열한 뒤 붙여넣을 초안까지 만든다. 어느 배열이 맞는지,
어느 필드를 대조할지는 고르지 않는다 — 그건 사람이 정한다.

토큰이 필요하면 `-H '이름:값'`으로 준다. 설정에 이미 `data_checks`의 API
헤더가 있으면 그걸 그대로 쓴다. 401·403이 오면 그렇게 알려준다.

`columns`는 화면 표의 열과 **같은 순서로** 맞춘다. 응답에 있는 필드를 다 적는
게 아니라, 그 화면이 보여주기로 한 값만 적는다.

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

**저장·등록 검증** — 눌렀을 때 데이터가 실제로 어떻게 바뀌었는지 본다.
사후조건은 **여러 개** 걸 수 있다.

```yaml
write_checks:
  - name: 주문등록
    page: /new
    steps:
      - {action: fill, selector: "#customer", value: 테스트고객}
      - {action: click, selector: "#save"}
    query:
      db: sqlite:///staging.db        # 접속 정보만. 검사는 아래 expect가 갖는다
    expect:
      - {label: 주문,    sql: "SELECT COUNT(*) FROM orders", delta: 1}
      - {label: 품목,    sql: "SELECT COUNT(*) FROM order_items", delta: 2}
      - {label: 감사로그, sql: "SELECT COUNT(*) FROM audit_log", delta: 1}
      - {label: 다른고객, sql: "SELECT COUNT(*) FROM orders WHERE customer <> '테스트고객'",
         delta: 0}                    # 엉뚱한 데이터가 같이 늘지 않았는지
```

**건수 하나만 보는 검사는 통과해도 증명하는 게 거의 없다.** "주문이 1건
늘었다"는 확인되지만 품목이 같이 저장됐는지, 감사 로그가 남았는지, 엉뚱한
행이 같이 늘지 않았는지는 확인되지 않는다.

하나라도 어긋나면 실패다. 어긋난 조건은 이름과 함께 **전부** 보고된다.
조건을 지우면 `checks_sha256`이 달라지므로, 검사를 조용히 약하게 만들 수 없다.

`expect_delta` 하나만 쓰던 예전 형태도 그대로 동작한다(둘을 같이 쓰면 오류).

**결제창처럼 iframe·새 창으로 뜨는 화면**

```yaml
steps:
  - {action: fill, selector: "#card", value: "${TEST_CARD}", frame: "iframe#pg"}
  - {action: click, selector: "#pay"}
  - {action: wait_popup}                  # 새 창으로 옮긴다
  - {action: assert_text, selector: "#state", value: "승인"}
  - {action: close_popup}                 # 원래 창으로 돌아온다
```

**같은 검사를 여러 값에 반복** — 실행 전에 N개로 펼쳐지므로 리포트에 각각
한 줄로 남고, 어느 항목이 깨졌는지 바로 보인다.

```yaml
spec_checks:
  - name: "상태필터-{{status}}"                    # 이름에 값을 넣어 서로 구분
    foreach: {var: status, in: [pending, shipped, delivered]}
    # 또는  foreach: {var: status, in_file: statuses.json}
    page: /
    steps:
      - {action: select, selector: "#status", value: "{{status}}"}
      - {action: click, selector: "#apply"}
```

**한글 입력** — `fill`은 값을 통째로 꽂아 조합 자체가 일어나지 않는다. 조합 중에
글자가 유실되는 결함은 `type_ime`로만 재현된다. (Chromium 전용 — 다른 엔진에서는
실행 전에 막는다. 재현 못 한 검사를 통과로 세지 않기 위해서다.)

```yaml
- {action: type_ime, selector: "#editor", value: "한글날"}
- {action: assert_text_exact, selector: "#saved", value: "한글날"}
```

`assert_text`는 부분 일치라 `한글날`이 `한한글한글날` 안에 있어도 통과한다.
값이 정확히 무엇인지 봐야 하면 `assert_text_exact`를 쓴다.

**2단계 인증(6자리 코드)** — 코드는 30초마다 바뀌므로 입력하기 직전에 만들어진다.
비밀키는 환경변수로만 받고 리포트에는 `***`로 남는다.

```yaml
- {action: fill, selector: "#otp", value: "${TOTP:MY_2FA_SECRET}"}
```

**인증 메일 확인** — 메일이 실제로 왔는지는 화면만 봐서는 알 수 없다.
테스트용 메일함(Mailpit·MailHog 등)의 조회 API에서 링크를 뽑아 온다.

```yaml
- {action: fetch, value: "http://127.0.0.1:8025/api/v1/message/latest",
   store_as: verify_link, json_path: html,
   pattern: 'href="(http[^"]+/verify[^"]+)"'}
- {action: goto, value: "{{verify_link}}"}
```

**실전 SPA에서 자주 걸리는 것들**

```yaml
target:
  locale: en-US                    # 화면 표기 언어. 정답원이 영어 enum을 주면 맞춰둔다
  ignore_console_patterns:         # 무해한 프레임워크 경고 (콘솔 에러 1건이면 실패이므로)
    - "i18next::translator"

auth:
  per_context: true                # 시나리오마다 다시 로그인
  steps: [...]
```

`per_context`는 로그인 세션이 `storage_state`로 안 옮겨지는 앱에 쓴다. 토큰을
쿠키나 localStorage가 아니라 sessionStorage·메모리에 두는 SPA가 그렇다. 로그인은
성공하는데 정작 검사할 화면마다 로그인 페이지로 튕기면 이걸 켠다. 매번 로그인해서
느려지지만 정확하다.

화면 표기와 정답원 표기가 다를 때는 값을 이어붙일 수 있다. 다만 **`locale`로 언어를
맞출 수 있으면 그게 낫다** — 번역표를 우리가 떠안지 않게 된다.

```yaml
ui_table:
  selector: "#tests"
  columns: [대상, 상태]
  value_map: {실패: Failed, 성공: Success}     # 화면 표기 → 정답원 표기
```

**권한 검증** — 오류 응답이 곧 기대값인 경우. 이게 없으면 400 이상이 전부
실패로 처리돼 "401이 나와야 정상"을 표현할 수 없다.

```yaml
- {action: fetch, value: "/api/v1/admin/users", expect_status: 403}
```

> **기대값은 설명서에서 가져온다.** 구현 코드의 WHERE절을 베끼면 구현 버그가
> 기대값에 복제돼서 영원히 통과한다. 소스는 셀렉터·테이블명 확인용으로만 쓴다.

`discovery.json`과 "이건 이래야 한다" 문장 몇 줄을 IDE의 AI에게 주면 YAML
초안을 대신 써준다. 이때도 사이트 접속은 필요 없고, 뽑아둔 파일만 주면 된다.

### 2-3. 돌리기 전에 셀렉터를 한 번에 확인한다

```bash
python -m webtest_agent check-config -c configs/우리사이트.yaml
```

`run`은 첫 실패에서 그 시나리오를 접으므로 못 찾은 셀렉터가 한 번에 하나씩만
드러난다. 고치고 다시 돌리고를 반복하게 된다. 이 명령은 끝까지 걸어가며 전부
모아 준다.

```
✗ [data_check] 상태필터-shipped  — 못 찾음 1개
     못 찾음    1. select  #status-오타
     확인 못 함  2. click  #apply   1개
     확인 못 함  표  #orders-table   1개

✗ [data_check] 전체목록  — 못 찾음 1개
     참고            헤더 6개 · 123행   주문번호 | 고객 | 상태 | 카테고리 | 금액 | 주문일
     못 찾음          ui_table.columns   화면 헤더에 없는 열: ['Status']
```

**'못 찾음'과 '확인 못 함'은 다르다.** 앞 스텝이 실패하면 그 뒤 화면은 원래
나와야 할 상태가 아니다. 그 상태에서 본 결과를 '설정이 틀렸다'로 보고하면
멀쩡한 줄을 고치게 되고, '찾았다'로 보고하면 앞을 고친 뒤에도 맞다는 보장이
없는데 확인된 것처럼 읽힌다. 그래서 둘 다 하지 않고 따로 센다. **앞의 '못
찾음'부터 고치고 다시 돌린다.**

열 이름도 같이 본다. 로케일이 바뀌면 헤더 글자가 통째로 달라지는데, 이걸
실행해서 '대조 0행'이 나와야 아는 것과 돌리기 전에 아는 것은 다르다.

종료코드는 `run`과 같은 계약이다 — 0 전부 찾음 · 1 못 찾은 셀렉터 있음 ·
2 점검 자체를 못 함(사이트가 안 뜨거나 로그인 실패).

이 명령이 보는 것은 **셀렉터가 있는지**까지다. 기대값이 맞는지는 실행해야 안다.
`write_checks`는 데이터를 바꾸므로 실행하지 않고, 건너뛴 사실을 출력에 남긴다.
파괴적으로 보이는 클릭도 `run`과 같은 기준으로 누르지 않는다.

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
| `2` | 실행 불가 — 사이트가 꺼졌거나, **판정을 못 한 검사가 있음** | 대상·정답원 확인 후 재실행 |

> **정답원(DB·API)이 죽으면 `2`로 끝난다.** 화면이 맞는지 틀린지 확인하지 못한
> 것이지 제품이 잘못된 게 아니기 때문이다. 이걸 `1`로 내보내면 CI를 보는 사람이
> 멀쩡한 코드를 뒤지게 된다. 단, **진짜 결함이 함께 있으면 `1`이 우선**이다 —
> 결함을 가리면 안 되므로.

산출물은 `runs/<시각>/`에 쌓인다.

- `report.html` — 캡처가 들어 있는 단일 파일. 이거 하나만 공유해도 된다
- `walkthrough.md` — 단계별 절차서. 통과면 매뉴얼, 실패면 재현 절차서
- `report.xml` — JUnit XML. CI가 테스트별 결과로 표시한다
- `videos/` — 화면 녹화
- `traces/` — 되감기 기록. **기본은 실패한 시나리오만 남는다**

> ⚠️ **트레이스는 함부로 공유하지 않는다.** 네트워크 요청이 통째로 담겨
> **인증 토큰과 쿠키가 평문으로** 들어간다. 실제 실행 산출물에서 확인했다.
> `report.html`과 스크린샷에는 없지만 실행 폴더를 통째로 넘기면 함께 나간다.
> 필요 없으면 `report.trace: false`, 전부 남기려면 `true`.

매 실행마다 직전 실행과 비교해서 **신규 실패 / 복구 / 계속 실패**를 표시한다.

**검사 정의가 바뀌었으면 같이 알려준다.**

```
전회차(20260810-090632) 대비: 변화 없음
  ⚠ 검사 정의가 직전 실행과 다릅니다 — 같은 조건의 비교가 아닙니다
```

대조할 열을 넷에서 하나로 줄여도 통과 건수와 행 수는 그대로라, 결과만 봐서는
테스트가 약해진 것을 알 수 없다. 그래서 리포트에 **검사 지문**을 남긴다.
주석이나 제목을 고쳐도 지문은 그대로고, 대조 열·정답 SQL·화면 언어가 바뀌면
달라진다.

여러 번 쌓인 뒤에는 추이를 볼 수 있다. "오늘 처음 깨졌다"와 "지난주부터 계속
깨져 있다"를 구분할 수 있고, 자주 뒤집히는 검사는 따로 표시된다.

```bash
python -m webtest_agent history -c configs/우리사이트.yaml
```

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
- **캡처만 가리는 것으로는 부족하다.** 실제 데이터를 대조하면 불일치 표본·
  추출값·단언 메시지에 이름·전화번호가 리포트 본문에 그대로 남는다.
  `report.mask_patterns`로 본문까지 가린다.

  ```yaml
  report:
    mask_selectors: ["#customer-name"]        # 스크린샷
    mask_patterns:                            # report.json·md·html·xml 본문
      - "010-\\d{4}-\\d{4}"
      - "[가-힣]{2,4}@[a-z.]+"
  ```

  판정이 끝난 뒤 리포트를 쓸 때만 적용되므로 **가려도 합격·불합격은 바뀌지
  않는다.** 다만 패턴을 넓게 쓰면 일반 단어까지 가려져 리포트가 읽기 어려워진다
  (`이[가-힣]{1,2}`는 '이미지'도 문다). 좁게 적는다.

## 막힐 때

| 증상 | 대개 이것 |
|---|---|
| 종료코드 2, 시나리오 0개 | 대상 사이트가 안 떠 있다 |
| 실패인데 제품 문제가 아니다 | `summary.not_proven`을 본다 — 판정 불가 건수다 |
| 로그인이 안 된다 | `${환경변수}`가 안 들어갔다. 셸에서 `export`했는지 확인 |
| 셀렉터를 못 찾는다 | `discovery.json`에서 실제 값을 다시 뽑는다 |
| `rows_path`를 못 찾는다 | `inspect-api <경로>`로 응답 구조를 뽑는다 |
| 화면 글자가 기대와 다르다 | 로케일 문제. 브라우저가 보는 언어로 적는다 |
| 정상인데 404가 실패로 잡힌다 | `target.ignore_http_error_patterns`에 URL 정규식 추가 |
| 가끔 실패한다 | `wait_for`를 넣는다. 재시도는 기본으로 하지 않는다 |
