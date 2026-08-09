"""단위 F — 2단계 인증(TOTP)과 앱 바깥 정답원 조회(fetch).

TOTP는 표준(RFC 6238)이라 정답이 문서에 박혀 있다. 우리 구현이 그 값을 그대로
내는지 확인한다 — "돌아가는 것 같다"로는 로그인이 왜 안 되는지 알 수 없다.

fetch는 테스트용 메일함처럼 화면 밖의 정답원을 읽는 용도다. 회원가입 인증
메일이 실제로 왔는지는 화면만 봐서는 알 수 없다.
"""
from __future__ import annotations

import base64

import pytest

from webtest_agent.config import ConfigError, Step, totp_refs
from webtest_agent.runner import Runner, describe_step
from webtest_agent.totp import TotpError, generate

# RFC 6238 부록 B의 시험값. 비밀키는 ASCII "12345678901234567890"이며
# 문서가 시각별 기대 코드를 명시한다.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()


def _step(**kwargs):
    return Step.from_dict(kwargs, "t")


def _runner():
    r = Runner.__new__(Runner)
    r.deadline_monotonic = None
    return r


# ── 표준 시험값 ─────────────────────────────────────────────────

@pytest.mark.parametrize("at,expected", [
    (59, "287082"),
    (1111111109, "081804"),
    (1111111111, "050471"),
    (1234567890, "005924"),
    (2000000000, "279037"),
])
def test_matches_rfc6238_vectors(at, expected):
    assert generate(RFC_SECRET, at=at) == expected


def test_code_changes_between_periods():
    first = generate(RFC_SECRET, at=0)
    same = generate(RFC_SECRET, at=29)
    later = generate(RFC_SECRET, at=30)
    assert first == same          # 같은 30초 구간이면 같은 코드
    assert first != later         # 구간이 바뀌면 코드도 바뀐다


def test_secret_accepts_human_formatting():
    """인증기 앱은 공백을 넣어 보여주고 '=' 채움을 생략하기도 한다."""
    spaced = " ".join(RFC_SECRET[i:i + 4] for i in range(0, len(RFC_SECRET), 4))
    assert generate(spaced, at=59) == "287082"
    assert generate(RFC_SECRET.rstrip("=").lower(), at=59) == "287082"


def test_bad_secret_is_reported_clearly():
    with pytest.raises(TotpError, match="base32"):
        generate("이건 base32가 아니다!", at=0)
    with pytest.raises(TotpError, match="비어 있습니다"):
        generate("   ", at=0)


def test_unsupported_settings_rejected():
    with pytest.raises(TotpError, match="알고리즘"):
        generate(RFC_SECRET, at=0, algorithm="md5")
    with pytest.raises(TotpError, match="digits"):
        generate(RFC_SECRET, at=0, digits=4)
    with pytest.raises(TotpError, match="period"):
        generate(RFC_SECRET, at=0, period=0)


# ── 설정 연결 ────────────────────────────────────────────────────

def test_totp_placeholder_is_recognised():
    assert totp_refs("${TOTP:MY_2FA}") == ["MY_2FA"]
    assert totp_refs("고정값") == []


def test_missing_secret_fails_at_load_time(monkeypatch):
    """실행 도중이 아니라 설정을 읽을 때 세운다."""
    monkeypatch.delenv("MY_2FA", raising=False)
    with pytest.raises(ConfigError, match="2단계 인증 비밀키"):
        _step(action="fill", selector="#code", value="${TOTP:MY_2FA}")


def test_bad_secret_fails_at_load_time(monkeypatch):
    monkeypatch.setenv("MY_2FA", "이건 base32가 아니다!")
    with pytest.raises(ConfigError, match="base32"):
        _step(action="fill", selector="#code", value="${TOTP:MY_2FA}")


def test_totp_step_is_secret(monkeypatch):
    monkeypatch.setenv("MY_2FA", RFC_SECRET)
    step = _step(action="fill", selector="#code", value="${TOTP:MY_2FA}")
    assert step.secret
    assert step.log_value == "***"


def test_placeholder_survives_config_load(monkeypatch):
    """코드는 30초마다 바뀌므로 로딩 시점에 값을 박아 두면 안 된다."""
    monkeypatch.setenv("MY_2FA", RFC_SECRET)
    step = _step(action="fill", selector="#code", value="${TOTP:MY_2FA}")
    assert step.value == "${TOTP:MY_2FA}"


# ── 실행 시점 ────────────────────────────────────────────────────

def test_code_is_generated_when_the_step_runs(monkeypatch):
    monkeypatch.setenv("MY_2FA", RFC_SECRET)
    r = _runner()
    resolved = r._resolve("${TOTP:MY_2FA}")
    assert resolved.isdigit() and len(resolved) == 6


def test_generated_code_is_masked_in_messages(monkeypatch):
    """생성된 코드가 오류 메시지로 새면 안 된다.

    설정에는 자리표시자만 있고 실제 6자리는 실행 중에 생기므로, 기존
    자리표시자 마스킹만으로는 가려지지 않는다.
    """
    monkeypatch.setenv("MY_2FA", RFC_SECRET)
    r = _runner()
    code = r._resolve("${TOTP:MY_2FA}")
    assert r._mask_vars(f"입력값 {code} 이(가) 거부됨") == "입력값 *** 이(가) 거부됨"


def test_missing_secret_at_runtime_fails_the_step(monkeypatch):
    monkeypatch.delenv("MY_2FA", raising=False)
    r = _runner()
    with pytest.raises(AssertionError, match="2단계 인증 비밀키"):
        r._resolve("${TOTP:MY_2FA}")


# ── fetch ────────────────────────────────────────────────────────

def test_fetch_requires_store_as():
    with pytest.raises(ConfigError, match="store_as가 필요"):
        _step(action="fetch", value="/api/mail")


def test_fetch_requires_a_url_like_value():
    with pytest.raises(ConfigError, match="http"):
        _step(action="fetch", value="mail.json", store_as="v")


def test_fetch_accepts_absolute_and_relative(monkeypatch):
    assert _step(action="fetch", value="/api/mail", store_as="v").value == "/api/mail"
    assert _step(action="fetch", value="http://127.0.0.1:8025/x", store_as="v")


def test_fetch_cannot_target_a_frame():
    with pytest.raises(ConfigError, match="frame을 쓸 수 없습니다"):
        _step(action="fetch", value="/api/mail", store_as="v", frame="iframe#x")


class _Response:
    def __init__(self, body, status=200):
        self._body, self.status = body, status

    def text(self):
        return self._body


class _Request:
    def __init__(self, response=None, error=None):
        self._response, self._error = response, error
        self.seen_url = None

    def get(self, url, timeout=None):
        self.seen_url = url
        if self._error:
            raise self._error
        return self._response


class _Page:
    def __init__(self, request):
        self.request = request


def _fetch(r, page, **kwargs):
    step = _step(action="fetch", **kwargs)
    r._exec_fetch(page, step, step.value)
    return r


def test_fetch_extracts_with_a_pattern():
    body = '{"html":"인증하려면 <a href=\\"http://app/verify?token=abc123\\">여기</a>"}'
    r = _runner()
    r.cfg = type("C", (), {"target": type("T", (), {"base_url": "http://app"})()})()
    page = _Page(_Request(_Response(body)))
    _fetch(r, page, value="http://mail/api/latest",
           store_as="link", pattern=r'(http://app/verify\?token=[a-z0-9]+)')
    assert r._vars["link"] == "http://app/verify?token=abc123"


def test_fetch_reports_a_missing_pattern():
    r = _runner()
    r.cfg = type("C", (), {"target": type("T", (), {"base_url": "http://app"})()})()
    page = _Page(_Request(_Response("메일 없음")))
    with pytest.raises(AssertionError, match="패턴"):
        _fetch(r, page, value="http://mail/api/latest",
               store_as="link", pattern=r"(token=[a-z]+)")


def test_fetch_treats_http_errors_as_failures():
    r = _runner()
    r.cfg = type("C", (), {"target": type("T", (), {"base_url": "http://app"})()})()
    page = _Page(_Request(_Response("", status=404)))
    with pytest.raises(AssertionError, match="HTTP 404"):
        _fetch(r, page, value="/api/mail", store_as="v")


def test_fetch_refuses_a_huge_body_without_a_pattern():
    """패턴 없이 통째로 담으면 리포트가 응답 본문으로 뒤덮인다."""
    r = _runner()
    r.cfg = type("C", (), {"target": type("T", (), {"base_url": "http://app"})()})()
    page = _Page(_Request(_Response("x" * 3000)))
    with pytest.raises(AssertionError, match="너무 깁니다"):
        _fetch(r, page, value="/api/mail", store_as="v")


def test_fetch_resolves_relative_urls_against_base_url():
    r = _runner()
    r.cfg = type("C", (), {"target": type("T", (), {"base_url": "http://app"})()})()
    request = _Request(_Response("값"))
    _fetch(r, _Page(request), value="/api/mail", store_as="v")
    assert request.seen_url == "http://app/api/mail"


def test_describe_step_for_fetch():
    text = describe_step(_step(action="fetch", value="/api/mail", store_as="link"))
    assert "link" in text and "/api/mail" in text


# ── JSON 응답에서 꺼내기 ─────────────────────────────────────────

def test_json_path_only_for_fetch():
    with pytest.raises(ConfigError, match="fetch"):
        _step(action="extract", selector="#a", store_as="v", json_path="html")


def test_json_path_pulls_the_field_before_the_pattern():
    """메일함 JSON은 따옴표가 이스케이프돼 있어 본문에 바로 정규식을 걸 수 없다."""
    body = '{"html":"<a href=\\"http://app/verify?token=abc\\">확인</a>"}'
    r = _runner()
    r.cfg = type("C", (), {"target": type("T", (), {"base_url": "http://app"})()})()
    page = _Page(_Request(_Response(body)))
    _fetch(r, page, value="/api/mail", store_as="link", json_path="html",
           pattern=r'href="(http[^"]+)"')
    assert r._vars["link"] == "http://app/verify?token=abc"


def test_json_path_walks_lists():
    body = '{"messages":[{"html":"첫째"},{"html":"둘째"}]}'
    r = _runner()
    r.cfg = type("C", (), {"target": type("T", (), {"base_url": "http://app"})()})()
    page = _Page(_Request(_Response(body)))
    _fetch(r, page, value="/api/mail", store_as="v", json_path="messages.1.html")
    assert r._vars["v"] == "둘째"


def test_json_path_reports_a_missing_position():
    r = _runner()
    r.cfg = type("C", (), {"target": type("T", (), {"base_url": "http://app"})()})()
    page = _Page(_Request(_Response('{"a":1}')))
    with pytest.raises(AssertionError, match="위치가 없습니다"):
        _fetch(r, page, value="/api/mail", store_as="v", json_path="b")


def test_json_path_rejects_a_non_string_target():
    r = _runner()
    r.cfg = type("C", (), {"target": type("T", (), {"base_url": "http://app"})()})()
    page = _Page(_Request(_Response('{"a":{"b":1}}')))
    with pytest.raises(AssertionError, match="문자열이 아닙니다"):
        _fetch(r, page, value="/api/mail", store_as="v", json_path="a")


def test_non_json_body_reported_clearly():
    r = _runner()
    r.cfg = type("C", (), {"target": type("T", (), {"base_url": "http://app"})()})()
    page = _Page(_Request(_Response("<html>메일함 아님</html>")))
    with pytest.raises(AssertionError, match="JSON이 아닙니다"):
        _fetch(r, page, value="/api/mail", store_as="v", json_path="html")
