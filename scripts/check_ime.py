#!/usr/bin/env python3
"""한글 조합 입력(type_ime)이 실제로 결함을 잡는지 확인한다.

조합 입력을 지원한다고 말하려면, **fill로는 못 잡는 결함을 type_ime로는 잡는다**를
보여야 한다. 그러지 못하면 조합하는 시늉만 하는 것이고, 통과 리포트는 거짓이 된다.

조합을 깨뜨리는 에디터를 띄우고 같은 검사를 두 방식으로 돌린다.

    fill     → 통과해야 한다 (조합이 일어나지 않으므로 결함을 건드리지 못한다)
    type_ime → 실패해야 한다 (조합 중간값이 전달돼 결함이 드러난다)

둘 다 통과하면 type_ime가 조합을 재현하지 못하는 것이므로, 이 스크립트는
실패로 끝난다.

    python scripts/check_ime.py
"""
import subprocess
import sys
import tempfile
import threading
import http.server
import socketserver
from pathlib import Path

PORT = 5099

# 제어형 에디터의 흔한 결함 — 입력이 들어올 때마다 값을 정규화해 되꽂는다.
# 조합 중간값에는 낱자(ㅎ, ㄱ)가 섞이므로 그때마다 값이 잘리고 다시 붙어
# 글자가 중복된다. 완성된 문자열을 한 번에 넣으면 걸러낼 것이 없어 멀쩡해 보인다.
PAGE = """<!doctype html><html lang=ko><head><meta charset=utf-8><title>에디터</title></head><body>
<input id="editor">
<p>저장된 값: <b id="saved"></b></p>
<script>
const ed = document.getElementById('editor'), saved = document.getElementById('saved');
ed.addEventListener('input', () => {
  const cleaned = [...ed.value].filter(c => !(c >= '\\u3131' && c <= '\\u318E')).join('');
  if (cleaned !== ed.value) ed.value = cleaned;
  saved.textContent = ed.value;
});
</script></body></html>"""

CONFIG = """
target: {{base_url: "http://127.0.0.1:{port}", settle_ms: 300}}
crawl: {{enabled: false}}
button_sweep: {{enabled: false}}
spec_checks:
  - name: 한글입력-{label}
    description: 에디터에 '한글날'을 입력하면 그대로 남아야 한다
    page: /
    steps:
      - {{action: {action}, selector: "#editor", value: "한글날"}}
      - {{action: assert_text_exact, selector: "#saved", value: "한글날"}}
report: {{video: false, trace: false}}
output_dir: {out}
"""


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main() -> int:
    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer(("127.0.0.1", PORT), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    root = Path(__file__).resolve().parent.parent
    failures = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for label, action, expected_code in (
                ("fill방식", "fill", 0),
                ("조합방식", "type_ime", 1),
            ):
                cfg = Path(tmp) / f"{action}.yaml"
                cfg.write_text(CONFIG.format(port=PORT, label=label, action=action,
                                             out=str(Path(tmp) / f"runs-{action}")),
                               encoding="utf-8")
                proc = subprocess.run(
                    [sys.executable, "-m", "webtest_agent", "run", "-c", str(cfg)],
                    cwd=root, capture_output=True, text=True)
                got = "통과" if proc.returncode == 0 else "실패"
                want = "통과" if expected_code == 0 else "실패"
                ok = proc.returncode == expected_code
                print(f"  {'OK ' if ok else '!! '}{action:8s} → {got} (기대: {want})")
                if not ok:
                    failures.append(f"{action}: 종료코드 {proc.returncode}, 기대 {expected_code}")
    finally:
        server.shutdown()
        server.server_close()

    if failures:
        print("\n실패 — type_ime가 조합을 재현하지 못하고 있습니다:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\n통과 — fill로는 놓치는 조합 결함을 type_ime가 잡아냅니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
