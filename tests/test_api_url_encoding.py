"""정답원 API 주소에 한글이 들어가도 조회가 된다.

실제로 겪은 결함이다. `/plan-tests` → `/generate-tests` 흐름을 데모앱에 돌렸더니
`?category=전자기기`와 `?q=김` 두 검사가 이렇게 끝났다.

    정답원(DB/API) 조회 실패: 'ascii' codec can't encode character '\\uae40'

한글 카테고리·검색어는 실무에서 흔하다. 이게 막히면 그 화면은 **아예 검증할 수
없다.** 다만 이때 도구가 '실패'가 아니라 **'판정 불가'(종료코드 2)** 로 보고한
것은 맞는 동작이었다 — 화면이 틀렸는지 아닌지 알 수 없는 상태였기 때문이다.

여기서 고정하는 계약:

    한글이 들어가도 조회된다     안 되면 그 화면은 검증 자체가 불가능하다
    두 번 인코딩하지 않는다      이미 %XX인 주소를 또 감싸면 서버가 못 알아본다
    구조 문자는 건드리지 않는다   ?와 &가 인코딩되면 질의가 통째로 깨진다
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from webtest_agent.config import ApiSpec
from webtest_agent.datacheck import run_api_query


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _capture(monkeypatch, payload=None):
    """실제 요청 대신 어떤 주소로 나갔는지 잡아둔다."""
    sent = {}

    def fake_urlopen(req, timeout=None):
        sent["url"] = req.full_url
        return _FakeResponse(payload if payload is not None else {"rows": []})

    monkeypatch.setattr("webtest_agent.datacheck.urllib.request.urlopen", fake_urlopen)
    return sent


def _spec(url):
    return ApiSpec(url=url, columns=["id"], rows_path="rows")


BASE = "http://127.0.0.1:5057"


# ── 한글이 들어가도 나간다 ──────────────────────────────────────

def test_korean_query_value_is_encoded(monkeypatch):
    sent = _capture(monkeypatch)
    run_api_query(_spec("/api/orders?category=전자기기"), BASE)
    assert "전자기기" not in sent["url"], "인코딩되지 않은 한글은 보낼 수 없다"
    assert "%EC%A0%84%EC%9E%90%EA%B8%B0%EA%B8%B0" in sent["url"]


def test_korean_search_term_is_encoded(monkeypatch):
    sent = _capture(monkeypatch)
    run_api_query(_spec("/api/orders?q=김"), BASE)
    assert sent["url"].endswith("q=%EA%B9%80")


def test_space_is_encoded(monkeypatch):
    sent = _capture(monkeypatch)
    run_api_query(_spec("/api/orders?q=hong gil"), BASE)
    assert " " not in sent["url"]


# ── 두 번 인코딩하지 않는다 ─────────────────────────────────────

def test_already_encoded_url_is_left_alone(monkeypatch):
    """`%EA%B9%80`을 또 감싸면 `%25EA...`가 되어 서버가 다른 값으로 읽는다."""
    sent = _capture(monkeypatch)
    run_api_query(_spec("/api/orders?q=%EA%B9%80"), BASE)
    assert sent["url"].endswith("q=%EA%B9%80")
    assert "%25" not in sent["url"]


# ── 구조 문자는 건드리지 않는다 ─────────────────────────────────

def test_query_structure_survives(monkeypatch):
    """`?`나 `&`가 인코딩되면 질의가 통째로 깨진다."""
    sent = _capture(monkeypatch)
    run_api_query(_spec("/api/orders?status=shipped&category=의류&limit=50"), BASE)
    assert sent["url"].count("?") == 1
    assert sent["url"].count("&") == 2
    assert "status=shipped" in sent["url"]
    assert "limit=50" in sent["url"]


def test_ascii_url_is_unchanged(monkeypatch):
    """영문만 있는 주소는 예전과 한 글자도 달라지면 안 된다."""
    sent = _capture(monkeypatch)
    run_api_query(_spec("/api/orders?status=shipped"), BASE)
    assert sent["url"] == f"{BASE}/api/orders?status=shipped"


def test_absolute_url_still_works(monkeypatch):
    sent = _capture(monkeypatch)
    run_api_query(_spec("http://other.test:9000/api/x?q=김"), BASE)
    assert sent["url"].startswith("http://other.test:9000/api/x?q=")
    assert "%EA%B9%80" in sent["url"]


# ── 응답은 그대로 읽힌다 ────────────────────────────────────────

def test_rows_are_still_extracted(monkeypatch):
    _capture(monkeypatch, payload={"rows": [{"id": 1}, {"id": 2}]})
    cols, rows = run_api_query(_spec("/api/orders?q=김"), BASE)
    assert cols == ["id"]
    assert rows == [(1,), (2,)]
