"""웹훅 알림 유닛 테스트 — 로컬 HTTP 서버로 실제 전송 검증."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from webtest_agent.models import RunMeta, ScenarioResult
from webtest_agent.notify import build_payload, send_webhook

received: list[dict] = []


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        received.append(json.loads(self.rfile.read(length).decode("utf-8")))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def _meta():
    return RunMeta(title="알림 테스트", base_url="http://x", app_version="",
                   config_path="c.yaml", started_at="2026-07-23 10:00:00")


def test_build_payload_contains_failures():
    results = [
        ScenarioResult(name="ok", kind="data_check", page="/", status="pass"),
        ScenarioResult(name="bad", kind="data_check", page="/", status="fail",
                       reasons=["UI↔DB 불일치"]),
    ]
    payload = build_payload(_meta(), {"total": 2, "pass": 1, "warn": 0, "fail": 1},
                            results, "runs/x")
    assert "실패 1" in payload["text"] and "bad" in payload["text"]
    assert payload["failures"][0]["name"] == "bad"


def test_send_webhook_roundtrip():
    received.clear()
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/hook"
        err = send_webhook(url, {"text": "hello", "summary": {"fail": 0}})
        assert err == ""
        assert received and received[0]["text"] == "hello"
    finally:
        server.shutdown()


def test_send_webhook_failure_returns_message():
    err = send_webhook("http://127.0.0.1:1/none", {"text": "x"}, timeout=1)
    assert err != ""
