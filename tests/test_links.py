"""깨진 링크 검사 회귀 테스트 — 수집 규칙과 probe 판정(브라우저 없이)."""
from __future__ import annotations

import pytest

from webtest_agent.config import ConfigError, load_config
from webtest_agent.links import collect_links, probe_links


def _pages(hrefs):
    return [{"url": "http://app/", "path": "/",
             "elements": {"links": [{"href": h} for h in hrefs]}}]


def test_collect_dedupes_and_absolutizes():
    urls = collect_links(_pages(["/a", "/a", "http://app/b"]), "http://app", False, [])
    assert sorted(urls) == ["http://app/a", "http://app/b"]


def test_collect_excludes_non_http_schemes():
    urls = collect_links(_pages(["mailto:x@y.z", "tel:123", "javascript:void(0)", "#top", "/ok"]),
                         "http://app", False, [])
    assert urls == ["http://app/ok"]


def test_collect_same_origin_only_by_default():
    urls = collect_links(_pages(["/in", "https://other.com/out"]), "http://app", False, [])
    assert urls == ["http://app/in"]


def test_collect_include_external():
    urls = collect_links(_pages(["/in", "https://other.com/out"]), "http://app", True, [])
    assert sorted(urls) == ["http://app/in", "https://other.com/out"]


def test_collect_strips_fragment_and_respects_ignore():
    urls = collect_links(_pages(["/a#frag", "/skip", "/keep"]), "http://app", False, [r"/skip$"])
    assert sorted(urls) == ["http://app/a", "http://app/keep"]


# ── probe_links 판정 (가짜 request context) ──────────────────────

class _Resp:
    def __init__(self, status):
        self.status = status


class _FakeRequest:
    """url→status 매핑. head가 405면 get으로 재시도하는 경로도 검증."""

    def __init__(self, statuses, head_405_for=None, errors=None):
        self._statuses = statuses
        self._head_405 = head_405_for or set()
        self._errors = errors or {}
        self.get_calls = []

    def head(self, url, timeout=None):
        if url in self._errors:
            raise RuntimeError(self._errors[url])
        if url in self._head_405:
            return _Resp(405)
        return _Resp(self._statuses[url])

    def get(self, url, timeout=None):
        self.get_calls.append(url)
        return _Resp(self._statuses[url])


def test_probe_reports_only_broken():
    req = _FakeRequest({"http://app/ok": 200, "http://app/gone": 404, "http://app/err": 500})
    broken = probe_links(req, ["http://app/ok", "http://app/gone", "http://app/err"], 5000)
    urls = {b["url"] for b in broken}
    assert urls == {"http://app/gone", "http://app/err"}


def test_probe_head_405_falls_back_to_get():
    req = _FakeRequest({"http://app/x": 200}, head_405_for={"http://app/x"})
    broken = probe_links(req, ["http://app/x"], 5000)
    assert broken == []
    assert req.get_calls == ["http://app/x"]   # GET로 재시도했다


def test_probe_connection_failure_is_broken():
    req = _FakeRequest({}, errors={"http://app/dead": "ECONNREFUSED"})
    broken = probe_links(req, ["http://app/dead"], 5000)
    assert len(broken) == 1
    assert broken[0]["status"] is None
    assert "ECONNREFUSED" in broken[0]["detail"]


# ── 설정 검증 ────────────────────────────────────────────────────

def _cfg(tmp_path, body):
    p = tmp_path / "c.yaml"
    p.write_text("target: {base_url: 'http://127.0.0.1:5057'}\n"
                 "report: {video: false, trace: false}\n" + body, encoding="utf-8")
    return p


def test_config_link_check_defaults(tmp_path):
    cfg = load_config(_cfg(tmp_path, "link_check: {enabled: true}\n"))
    assert cfg.link_check.enabled is True
    assert cfg.link_check.include_external is False
    assert cfg.link_check.severity == "fail"


def test_config_link_check_invalid_severity(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_cfg(tmp_path, "link_check: {enabled: true, severity: nope}\n"))


def test_config_link_check_invalid_ignore_regex(tmp_path):
    with pytest.raises(ConfigError):
        load_config(_cfg(tmp_path, "link_check: {enabled: true, ignore_patterns: ['[unclosed']}\n"))
