"""Makes evals/run.py and evals/feedback_to_cases.py importable (the latter does `from run import …`),
and provides a fake Open WebUI that streams /api/chat/completions like the real one."""

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

EVALS = Path(__file__).resolve().parents[2] / "evals"
sys.path.insert(0, str(EVALS))


class FakeOpenWebUI:
    """Answers each chat request with the next reply in `replies`:
    {"answer": str, "sources": list, "error": str} streamed as SSE, or {"status": 401} etc."""

    def __init__(self):
        self.replies, self.requests = [], []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):  # /health
                self.send_response(200)
                self.end_headers()

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.requests.append({"body": body, "auth": self.headers.get("Authorization")})
                reply = server.replies.pop(0) if server.replies else {"answer": "ok"}
                if "status" in reply:
                    self.send_response(reply["status"])
                    self.end_headers()
                    self.wfile.write(b'{"detail": "nope"}')
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                events = [{"sources": reply.get("sources", [])}, "not json", [1, 2]]
                if reply.get("error"):
                    events.append({"error": reply["error"]})
                words = reply.get("answer", "").split(" ")
                events += [{"choices": [{"delta": {"content": w + (" " if i < len(words) - 1 else "")}}]}
                           for i, w in enumerate(words) if w]
                for event in events:
                    data = event if isinstance(event, str) else json.dumps(event)
                    self.wfile.write(f"data: {data}\n\n".encode())
                self.wfile.write(b": keep-alive comment\n\ndata: [DONE]\n\n")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def owui():
    server = FakeOpenWebUI()
    yield server
    server.close()
