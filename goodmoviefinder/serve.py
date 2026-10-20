"""Serve the generated page so the viewer can load another Letterboxd profile."""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from goodmoviefinder.config import GENERATED_HTML
from goodmoviefinder.progress import format_progress_html


class ServeHub:
    """Holds build progress until the program page is ready, then that page."""

    def __init__(self) -> None:
        from goodmoviefinder.progress import BuildProgress

        self.progress = BuildProgress()
        self._page: Any = None
        self._lock = threading.Lock()

    def attach(self, page: Any) -> None:
        with self._lock:
            self._page = page

    @property
    def page(self) -> Any:
        with self._lock:
            return self._page

    @property
    def html_path(self):
        page = self.page
        return page.html_path if page is not None else GENERATED_HTML


def start_server(state: Any, port: int, *, open_browser: bool = True) -> ThreadingHTTPServer | None:
    html_path = GENERATED_HTML
    hub = state if isinstance(state, ServeHub) else None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: object) -> None:
            print(f"[serve] {self.address_string()} {fmt % args}", file=sys.stderr)

        def handle(self) -> None:
            try:
                super().handle()
            except (ConnectionResetError, BrokenPipeError, TimeoutError):
                return

        def _send(self, code: int, body: bytes | str, content_type: str) -> None:
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def _page(self) -> Any:
            if hub is not None:
                return hub.page
            return state

        def _building(self) -> bool:
            page = self._page()
            if page is None:
                return True
            if hub is None:
                return False
            return hub.progress.phase != "ready"

        def _show_progress(self, path: str) -> bool:
            if not self._building():
                return False
            program_paths = {"/", "/index.html", "/program-ranked.html"}
            account_paths = {"/account", "/account.html", "/for-you"}
            if path not in program_paths and path not in account_paths:
                return False
            if path in program_paths and hub is not None and hub.progress.program_ready:
                return False
            return True

        def do_OPTIONS(self) -> None:
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            if path == "/api/progress":
                if hub is None:
                    self._send(
                        200,
                        json.dumps(
                            {
                                "phase": "ready",
                                "programReady": True,
                                "error": None,
                                "overall": 1,
                                "steps": [],
                            }
                        ),
                        "application/json",
                    )
                    return
                self._send(200, json.dumps(hub.progress.snapshot()), "application/json")
                return
            if self._show_progress(path):
                self._send(200, format_progress_html(), "text/html; charset=utf-8")
                return
            page = self._page()
            if path == "/for-you":
                self.send_response(302)
                self.send_header("Location", "/account.html#recommendations")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            pages = {
                "/": html_path,
                "/index.html": html_path,
                "/program-ranked.html": html_path,
                "/account": html_path.parent / "account.html",
                "/account.html": html_path.parent / "account.html",
            }
            page_path = pages.get(path)
            if page_path is not None:
                try:
                    body = page_path.read_text(encoding="utf-8")
                except OSError:
                    self._send(404, "This page is not ready yet", "text/plain; charset=utf-8")
                    return
                self._send(200, body, "text/html; charset=utf-8")
                return
            if page is None:
                self._send(
                    503,
                    json.dumps({"error": "Still building the pages."}),
                    "application/json",
                )
                return
            if path == "/api/viewer":
                self._send(200, json.dumps(page.viewer_payload()), "application/json")
                return
            if path == "/api/friends":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                slug = (query.get("slug") or [""])[0]
                try:
                    payload = page.friend_ratings(slug)
                except ValueError as exc:
                    self._send(400, json.dumps({"error": str(exc)}), "application/json")
                    return
                except Exception as exc:
                    print(f"[serve] friend ratings: {exc}", file=sys.stderr)
                    self._send(
                        502,
                        json.dumps({"error": "Letterboxd could not be reached."}),
                        "application/json",
                    )
                    return
                self._send(200, json.dumps(payload), "application/json")
                return
            if path == "/api/following":
                try:
                    payload = page.following_list()
                except ValueError as exc:
                    self._send(400, json.dumps({"error": str(exc)}), "application/json")
                    return
                except Exception as exc:
                    print(f"[serve] following list: {exc}", file=sys.stderr)
                    self._send(
                        502,
                        json.dumps({"error": "Letterboxd could not be reached."}),
                        "application/json",
                    )
                    return
                self._send(200, json.dumps(payload), "application/json")
                return
            if path == "/api/calendar":
                try:
                    payload = page.refresh_calendar()
                except ValueError as exc:
                    self._send(400, json.dumps({"error": str(exc)}), "application/json")
                    return
                except Exception as exc:
                    self._send(502, json.dumps({"error": str(exc)}), "application/json")
                    return
                self._send(200, json.dumps({"ok": True, **payload}), "application/json")
                return
            if path == "/api/recommendations":
                self._send(200, json.dumps(page.recommendations_status()), "application/json")
                return
            if path == "/api/letterboxd-backdrop":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                slug = (query.get("slug") or [""])[0]
                try:
                    payload = page.letterboxd_backdrop(slug)
                except ValueError as exc:
                    self._send(400, json.dumps({"error": str(exc)}), "application/json")
                    return
                except Exception as exc:
                    print(f"[serve] letterboxd backdrop: {exc}", file=sys.stderr)
                    self._send(
                        502,
                        json.dumps({"error": "Letterboxd could not be reached."}),
                        "application/json",
                    )
                    return
                self._send(200, json.dumps(payload), "application/json")
                return
            if path == "/api/letterboxd-search":
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                q = (query.get("q") or [""])[0]
                try:
                    payload = page.search_letterboxd(q)
                except ValueError as exc:
                    self._send(400, json.dumps({"error": str(exc)}), "application/json")
                    return
                except Exception as exc:
                    print(f"[serve] letterboxd search: {exc}", file=sys.stderr)
                    self._send(
                        502,
                        json.dumps({"error": "Letterboxd could not be reached."}),
                        "application/json",
                    )
                    return
                self._send(200, json.dumps(payload), "application/json")
                return
            self._send(404, "Not found", "text/plain; charset=utf-8")

        def do_POST(self) -> None:
            path = urllib.parse.urlparse(self.path).path
            payload = self._read_json()
            if payload is None:
                return
            if path == "/api/progress/dismiss":
                if hub is not None:
                    hub.progress.resume()
                self._send(200, json.dumps({"ok": True}), "application/json")
                return
            page = self._page()
            if page is None:
                self._send(
                    503,
                    json.dumps({"error": "Still building the pages."}),
                    "application/json",
                )
                return
            if path == "/api/viewer":
                try:
                    reload = page.apply_viewer(payload)
                except ValueError as exc:
                    self._send(400, json.dumps({"error": str(exc)}), "application/json")
                    return
                except RuntimeError as exc:
                    self._send(502, json.dumps({"error": str(exc)}), "application/json")
                    return
                self._send(200, json.dumps({"ok": True, "reload": reload}), "application/json")
                return
            if path == "/api/plan":
                try:
                    saved = page.save_plan(payload)
                except ValueError as exc:
                    self._send(400, json.dumps({"error": str(exc)}), "application/json")
                    return
                except Exception as exc:
                    self._send(502, json.dumps({"error": str(exc)}), "application/json")
                    return
                self._send(200, json.dumps({"ok": True, **saved}), "application/json")
                return
            if path == "/api/recommendations":
                try:
                    payload = page.start_recommendations_refresh()
                except ValueError as exc:
                    self._send(400, json.dumps({"error": str(exc)}), "application/json")
                    return
                except Exception as exc:
                    print(f"[serve] recommendations refresh: {exc}", file=sys.stderr)
                    self._send(
                        502,
                        json.dumps({"error": "Recommendations could not be refreshed."}),
                        "application/json",
                    )
                    return
                self._send(200, json.dumps({"ok": True, **payload}), "application/json")
                return
            if path == "/api/match":
                try:
                    saved = page.apply_match(payload)
                except ValueError as exc:
                    self._send(400, json.dumps({"error": str(exc)}), "application/json")
                    return
                except Exception as exc:
                    print(f"[serve] match repair: {exc}", file=sys.stderr)
                    self._send(
                        502,
                        json.dumps({"error": str(exc) or "Letterboxd could not be reached."}),
                        "application/json",
                    )
                    return
                self._send(200, json.dumps({"ok": True, **saved}), "application/json")
                return
            self._send(404, json.dumps({"error": "Not found"}), "application/json")

        def _read_json(self) -> dict | None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > 100_000:
                self._send(413, json.dumps({"error": "Request too large"}), "application/json")
                return None
            if length == 0:
                return {}
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send(400, json.dumps({"error": "Invalid JSON"}), "application/json")
                return None
            if not isinstance(payload, dict):
                self._send(400, json.dumps({"error": "Invalid JSON"}), "application/json")
                return None
            return payload

    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        print(f"Could not listen on 127.0.0.1:{port}: {exc}", file=sys.stderr)
        return None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{port}"
    print(f"Serving {url}", file=sys.stderr)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception as exc:
            print(f"Could not open a browser: {exc}", file=sys.stderr)
    return server


def wait_server(server: ThreadingHTTPServer) -> int:
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        server.shutdown()
    return 0


def serve(state: Any, port: int) -> int:
    server = start_server(state, port)
    if server is None:
        return 1
    return wait_server(server)
