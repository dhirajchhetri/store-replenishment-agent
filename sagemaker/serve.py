"""Listen on port 8080 for SageMaker's ping and invocations routes."""

from __future__ import annotations

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from inference import handle_invocation, model_fn

MODEL = model_fn(os.environ.get("SM_MODEL_DIR", "/opt/ml/model"))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler name
        if self.path != "/ping":
            self.send_error(404)
            return
        self.send_response(200)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler name
        if self.path != "/invocations":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        try:
            payload = handle_invocation(MODEL, body)
        except Exception as exc:  # surface a client error rather than a stack in the response
            message = str(exc).encode()
            self.send_response(400)
            self.send_header("Content-Length", str(len(message)))
            self.end_headers()
            self.wfile.write(message)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, fmt: str, *args) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8080), Handler)
    server.serve_forever()


if __name__ == "__main__":
    main()
