"""Tiny HTTP server: GET /healthz (JSON) and GET /metrics (Prometheus text). Runs in a thread."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from trading_agent.observability.metrics import Metrics


def start_health_server(
    port: int, metrics: Metrics, health_fn: Callable[[], dict[str, Any]]
) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            if self.path.startswith("/metrics"):
                body = generate_latest(metrics.registry)
                self._reply(200, CONTENT_TYPE_LATEST, body)
            elif self.path.startswith("/healthz"):
                status = health_fn()
                code = 200 if status.get("ok") else 503
                self._reply(code, "application/json", json.dumps(status, default=str).encode())
            else:
                self._reply(404, "text/plain", b"not found")

        def _reply(self, code: int, ctype: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_: Any) -> None:  # silence default access log
            return None

    server = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, name="health-server", daemon=True).start()
    return server
