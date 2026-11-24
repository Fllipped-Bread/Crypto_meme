"""Tiny local web server for the dashboard (127.0.0.1 only)."""
from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _web_dir() -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
    return base / "memebot" / "web"


def make_server(engine, port: int) -> ThreadingHTTPServer:
    web = _web_dir()

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data, code: int = 200) -> None:
            self._send(code, json.dumps(data, default=list).encode(), "application/json")

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, (web / "index.html").read_bytes(), "text/html; charset=utf-8")
            elif self.path.startswith("/api/state"):
                self._json(engine.snapshot())
            elif self.path.startswith("/api/recap"):
                self._json(engine.book.recap())
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            # Only accept requests from our own page (blocks drive-by POSTs from other sites).
            origin = self.headers.get("Origin", "")
            if origin and not origin.startswith((f"http://127.0.0.1:{port}", f"http://localhost:{port}")):
                return self._json({"error": "bad origin"}, 403)
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except json.JSONDecodeError:
                body = {}
            p = self.path
            if p == "/api/pause":
                engine.manual_pause = bool(body.get("paused", True))
                engine.j.log("INFO", "paused by you" if engine.manual_pause else "resumed by you")
            elif p == "/api/reset":
                engine.reset(body.get("balance"))
            elif p == "/api/wallet/add":
                ok = engine.wallets.add(str(body.get("address", "")), "manual")
                if ok:
                    engine.cfg["wallets"]["tracked"].append(body["address"].strip())
                    engine.j.log("WALLETS", f"tracking {body['address'][:6]}…")
                return self._json({"ok": ok})
            elif p == "/api/wallet/remove":
                engine.wallets.remove(str(body.get("address", "")))
            elif p == "/api/close":
                engine.book.force_close(str(body.get("id", "")), "closed by you")
            elif p == "/api/recap":
                engine.recap = engine.book.recap()
            else:
                return self._json({"error": "unknown"}, 404)
            engine.save()
            self._json({"ok": True})

    return ThreadingHTTPServer(("127.0.0.1", port), H)
