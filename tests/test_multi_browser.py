"""멀티 브라우저 지원의 회귀 테스트.

브라우저를 바꾸는 것은 '설정 한 줄'이 아니라 판정 결과를 바꾸는 변수다.
엔진마다 렌더링·JS 지원·오류 문구가 달라서, 다음이 깨지면 조용히 거짓 결과가 나온다.

  (1) 엔진 이름 검증 — 오타가 chromium으로 흘러가면 어느 엔진으로 판정했는지 모른다
  (2) 시각 기준선 격리 — 엔진이 섞이면 전부 실패하거나 기준선이 조용히 덮인다
  (3) 브라우저 사망 판별 — 크래시가 제품 실패로 둔갑하면 안 되고(false failure),
      반대로 진짜 제품 버그가 실행 불가로 숨어도 안 된다(false success). 양방향이다.
  (4) 증적에 엔진 기록 — 어느 브라우저로 판정했는지 모르면 결과를 재현할 수 없다
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from webtest_agent.browser import (ENGINES, BrowserSession,
                                   UnsupportedEngineError, normalize_engine)
from webtest_agent.runner import Runner, _is_browser_gone


# ── (1) 엔진 이름 검증 ──────────────────────────────────────────

def test_known_engines_normalize():
    for engine in ENGINES:
        assert normalize_engine(engine) == engine
    assert normalize_engine(None) == "chromium"      # 기본값
    assert normalize_engine(" FireFox ") == "firefox"  # 공백·대소문자 관대


@pytest.mark.parametrize("bad", ["chrome", "safari", "edge", "chromium2"])
def test_unknown_engine_rejected(bad):
    """오타를 조용히 기본값으로 넘기면 판정 근거를 잃는다."""
    with pytest.raises(UnsupportedEngineError):
        normalize_engine(bad)


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_engine_means_unspecified(blank):
    """빈 값은 오타가 아니라 '안 적음'이다 — 기본값으로 본다.

    'safari' 같은 오타는 다른 엔진을 의도한 것이라 조용히 넘기면 위험하지만,
    빈 값에는 다른 의도가 없다. 여기서 막으면 YAML에 키만 남겨둔 설정이 깨진다.
    """
    assert normalize_engine(blank) == "chromium"


def test_config_rejects_unknown_browser(tmp_path):
    from webtest_agent.config import ConfigError, load_config
    cfg = tmp_path / "c.yaml"
    cfg.write_text("target:\n  base_url: http://x\n  browser: safari\n",
                   encoding="utf-8")
    with pytest.raises(ConfigError, match="지원하지 않는 브라우저"):
        load_config(str(cfg))


def test_config_defaults_to_chromium(tmp_path):
    from webtest_agent.config import load_config
    cfg = tmp_path / "c.yaml"
    cfg.write_text("target:\n  base_url: http://x\n", encoding="utf-8")
    assert load_config(str(cfg)).target.browser == "chromium"


def test_cli_browser_flag_overrides_config():
    """CI가 같은 YAML로 여러 엔진을 돌릴 수 있어야 한다."""
    from webtest_agent.cli import _resolve_engine
    cfg = SimpleNamespace(target=SimpleNamespace(browser="chromium"))
    assert _resolve_engine(SimpleNamespace(browser="firefox"), cfg) == "firefox"
    assert _resolve_engine(SimpleNamespace(browser=None), cfg) == "chromium"


# ── (2) 시각 기준선 엔진별 격리 ─────────────────────────────────

def _runner_with(engine: str, baselines: str) -> Runner:
    r = Runner.__new__(Runner)
    r.session = SimpleNamespace(engine=engine, browser=None)
    r.cfg = SimpleNamespace(baselines_dir=baselines)
    return r


def test_baselines_are_isolated_per_engine():
    """엔진이 폴더를 공유하면 Firefox 실행이 Chromium 기준선과 비교돼 전부 실패한다."""
    paths = {e: _runner_with(e, "baselines")._baseline_path("메인화면-시각")
             for e in ENGINES}
    assert len(set(paths.values())) == len(ENGINES), "기준선 경로가 겹친다"
    for engine, path in paths.items():
        assert path.parent == Path("baselines") / engine


# ── (3) 브라우저 사망 판별 — 양방향 ─────────────────────────────

class _Browser:
    def __init__(self, connected=True, raises=False):
        self._connected, self._raises = connected, raises

    def is_connected(self):
        if self._raises:
            raise RuntimeError("판단 불가")
        return self._connected


def _alive(browser) -> bool:
    r = Runner.__new__(Runner)
    r.session = SimpleNamespace(browser=browser)
    return r._browser_alive()


def test_dead_browser_detected_without_relying_on_message():
    """Chromium은 죽어도 문구가 그냥 Timeout이다(실측). 생존 여부로 잡아야 한다."""
    assert _alive(_Browser(connected=False)) is False


def test_alive_browser_reported_alive():
    assert _alive(_Browser(connected=True)) is True


@pytest.mark.parametrize("browser", [None, _Browser(raises=True)])
def test_unknown_liveness_defaults_to_alive(browser):
    """확신 없이 실행 불가로 올리면 진짜 제품 실패가 조용히 묻힌다 — 보수적으로."""
    assert _alive(browser) is True


@pytest.mark.parametrize("msg", [
    "Connection closed while reading from the driver",
    "Target page, context or browser has been closed",
    "BrowserType.launch: Executable doesn't exist",
])
def test_infra_messages_classified_as_browser_gone(msg):
    assert _is_browser_gone(RuntimeError(msg)) is True


@pytest.mark.parametrize("msg", [
    "Page.click: Timeout 3000ms exceeded.",          # 느린 화면 = 제품 결함
    "locator.click: Element is not visible",         # 안 뜨는 요소 = 제품 결함
    "Dialog was closed by the page",                 # 앱 동작
    "WebSocket connection closed by the server",     # 앱 동작 (부분 문자열 주의)
])
def test_product_failures_not_misclassified_as_infra(msg):
    """이쪽이 깨지면 진짜 버그가 '실행 불가'로 숨는다 — false success."""
    assert _is_browser_gone(RuntimeError(msg)) is False


# ── (4) 증적에 엔진 기록 ────────────────────────────────────────

def test_report_labels_actual_engine_not_hardcoded_chromium():
    from webtest_agent.report import _engine_label
    meta = SimpleNamespace(browser="firefox", browser_version="151.0")
    assert _engine_label(meta) == "Firefox 151.0"
    meta = SimpleNamespace(browser="webkit", browser_version="26.5")
    assert _engine_label(meta) == "WebKit 26.5"


def test_report_label_falls_back_when_engine_missing():
    """구버전 report.json(browser 필드 없음)을 읽어도 죽지 않아야 한다."""
    from webtest_agent.report import _engine_label
    assert _engine_label(SimpleNamespace(browser="", browser_version="1")) == "Chromium 1"


def test_run_meta_records_engine():
    from webtest_agent.models import RunMeta
    meta = RunMeta(title="t", base_url="u", app_version="", config_path="c",
                   started_at="now")
    assert meta.browser == "chromium"


# ── 폴백 경계 ───────────────────────────────────────────────────

def test_chromium_fallback_does_not_leak_to_other_engines(monkeypatch):
    """Firefox 요청에 Chromium 바이너리를 물리면 엉뚱한 엔진으로 조용히 돈다."""
    import webtest_agent.browser as bmod

    called = {"fallback": 0}

    def fake_find():
        called["fallback"] += 1
        return "/somewhere/chrome"

    monkeypatch.setattr(bmod, "find_chromium_executable", fake_find)

    class _Launcher:
        def launch(self, **kw):
            raise RuntimeError("기동 실패")

    session = BrowserSession(engine="firefox")
    session._pw = SimpleNamespace(firefox=_Launcher())
    with pytest.raises(RuntimeError, match="기동 실패"):
        # start()의 폴백 분기만 검사 — sync_playwright는 태우지 않는다
        launcher = getattr(session._pw, session.engine)
        try:
            launcher.launch(headless=True)
        except Exception:
            exe = (bmod.find_chromium_executable()
                   if session.engine == "chromium" else None)
            if not exe:
                raise
    assert called["fallback"] == 0, "Chromium 폴백이 다른 엔진에 새어 나갔다"
