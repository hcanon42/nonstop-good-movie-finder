"""Serve the generated page so the viewer can load another Letterboxd profile."""

from __future__ import annotations

import json
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def serve(state: Any, port: int) -> int:
    html_path = state.html_path

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            print(f"[serve] {self.address_string()} {fmt % args}", file=sys.stderr)

        def _send(self, code: int, body: bytes | str, content_type: str) -> None:
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            if path in {"/", "/index.html"}:
                try:
                    body = html_path.read_text(encoding="utf-8")
                except OSError:
                    self._send(500, "Missing page", "text/plain; charset=utf-8")
                    return
                self._send(200, body, "text/html; charset=utf-8")
                return
            if path == "/api/viewer":
                self._send(200, json.dumps(state.viewer_payload()), "application/json")
                return
            self._send(404, "Not found", "text/plain; charset=utf-8")

        def do_POST(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            if path != "/api/viewer":
                self._send(404, json.dumps({"error": "Not found"}), "application/json")
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length > 100_000:
                self._send(413, json.dumps({"error": "Request too large"}), "application/json")
                return
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send(400, json.dumps({"error": "Invalid JSON"}), "application/json")
                return
            if not isinstance(payload, dict):
                self._send(400, json.dumps({"error": "Invalid JSON"}), "application/json")
                return
            try:
                reload = state.apply_viewer(payload)
            except ValueError as exc:
                self._send(400, json.dumps({"error": str(exc)}), "application/json")
                return
            except RuntimeError as exc:
                self._send(502, json.dumps({"error": str(exc)}), "application/json")
                return
            self._send(200, json.dumps({"ok": True, "reload": reload}), "application/json")

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        print(f"Could not listen on 127.0.0.1:{port}: {exc}", file=sys.stderr)
        return 1
    print(f"Serving http://127.0.0.1:{port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
    return 0
