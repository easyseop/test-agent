"""인증 상태 파일의 권한·정리 정책 회귀 테스트."""
import stat
from types import SimpleNamespace

from webtest_agent.browser import BrowserSession
from webtest_agent.runner import Runner


class _AuthContext:
    def __init__(self):
        self.closed = False

    def storage_state(self, path):
        # Playwright가 JSON을 쓰는 동작을 최소 형태로 대체한다.
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('{"cookies":[{"name":"session","value":"secret"}]}')

    def close(self):
        self.closed = True


class _AuthPage:
    def set_default_timeout(self, timeout):
        self.timeout = timeout


class _AuthSession:
    def __init__(self):
        self.context = _AuthContext()

    def new_context(self, base_url):
        return self.context, _AuthPage(), object()


def _config():
    return SimpleNamespace(
        target=SimpleNamespace(base_url="http://x", settle_ms=0),
        report=SimpleNamespace(mask_selectors=[]),
    )


def test_auth_state_is_owner_only(tmp_path):
    state_path = tmp_path / "auth_state.json"
    runner = Runner(_AuthSession(), _config(), tmp_path)

    runner.authenticate([], state_path)

    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert runner.session.context.closed is True


def test_browser_session_deletes_auth_state_by_default(tmp_path):
    state_path = tmp_path / "auth_state.json"
    state_path.write_text("secret", encoding="utf-8")
    session = BrowserSession()
    session.configure_storage_state(state_path)

    session.stop()

    assert not state_path.exists()
    assert session.storage_state is None
    assert session._storage_state_cleanup_path is None


def test_browser_session_preserves_auth_state_only_when_requested(tmp_path):
    state_path = tmp_path / "auth_state.json"
    state_path.write_text("secret", encoding="utf-8")
    session = BrowserSession()
    session.configure_storage_state(state_path, preserve=True)

    session.stop()

    assert state_path.exists()
    assert session.storage_state is None
    assert session._storage_state_cleanup_path is None
