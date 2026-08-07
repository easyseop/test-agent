"""하위 경로에 붙은 앱의 크롤링 시작점.

`/admin`처럼 하위 경로에 마운트된 앱을 base_url로 주면 그 경로에서 크롤링이
시작되어야 한다. 예전에는 시작점이 "/"로 하드코딩돼 있어 Django admin을
가리켜도 사이트 루트를 훑었다 — 대상 앱을 한 페이지도 못 보고 끝났다.
"""
from webtest_agent.config import load_config
from webtest_agent.discovery import crawl_start_path


def test_subpath_app_starts_at_its_own_path():
    assert crawl_start_path("http://127.0.0.1:8055/admin") == "/admin"
    assert crawl_start_path("https://example.test/wiki/docs") == "/wiki/docs"


def test_root_app_still_starts_at_root():
    assert crawl_start_path("http://127.0.0.1:5057") == "/"
    assert crawl_start_path("http://127.0.0.1:5057/") == "/"


def test_start_path_follows_loaded_config(tmp_path):
    config_path = tmp_path / "subpath.yaml"
    config_path.write_text(
        "target:\n  base_url: http://127.0.0.1:8055/admin/\n", encoding="utf-8")
    cfg = load_config(config_path)
    # 설정 로딩이 뒤쪽 슬래시를 떼고 나서도 경로는 남아 있어야 한다.
    assert crawl_start_path(cfg.target.base_url) == "/admin"
