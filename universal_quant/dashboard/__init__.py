"""Stdlib HTTP dashboard for the UPV paper book."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from universal_quant import config as cfg
from universal_quant.live.runner import refresh_paper, snapshot
from universal_quant.live.session import load_account

STATIC_DIR = Path(__file__).resolve().parent / "static"
LOCK = threading.Lock()


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, default=str).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        print("[dashboard]", self.address_string(), fmt % args)

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            html = (STATIC_DIR / "index.html").read_bytes()
            self._send(200, html, "text/html; charset=utf-8")
            return
        if path == "/api/state":
            with LOCK:
                snap = snapshot(load_account())
            self._send(200, _json_bytes(snap), "application/json; charset=utf-8")
            return
        self._send(404, b'{"error":"not found"}', "application/json")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._read_json()
        try:
            with LOCK:
                if path == "/api/refresh":
                    snap = refresh_paper(force=bool(body.get("force", True)))
                elif path == "/api/reset":
                    snap = refresh_paper(force=True, reset=True)
                elif path == "/api/kill":
                    snap = refresh_paper(force=False, kill=True)
                elif path == "/api/resume":
                    snap = refresh_paper(force=False, kill=False)
                elif path == "/api/step":
                    snap = refresh_paper(force=False, step=True)
                else:
                    self._send(404, b'{"error":"not found"}', "application/json")
                    return
            self._send(200, _json_bytes(snap), "application/json; charset=utf-8")
        except Exception as exc:  # noqa: BLE001
            self._send(500, _json_bytes({"error": str(exc)}), "application/json; charset=utf-8")


def serve(host: str = cfg.DASHBOARD_HOST, port: int = cfg.DASHBOARD_PORT) -> None:
    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"UPV dashboard http://127.0.0.1:{port}  (model {cfg.PAPER_MODEL} paper)")
    httpd.serve_forever()


def main(argv=None) -> int:
    serve()
    return 0
