"""The local review page: a small web server for one person on one laptop, built on the Python standard library only.

Safety, since it handles customer material:
* it listens on 127.0.0.1 only, never on the network;
* every /api request must carry this launch's random token, and the Host header must be this address (blocks DNS rebinding);
  a browser Origin header, when present, must be this address too (blocks other web pages calling it);
* the page uses no inline script or style (a strict Content-Security-Policy) and inserts all text as text;
* only fixed static files and the run's own export names are ever read from disk or storage (no path is built from a request);
* request bodies are size-limited.
"""
from __future__ import annotations

import contextlib
import hmac
import json
import logging
import re
import secrets
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from ..service import AssessmentService
from .api import UiApi, UiError, clean_actor
from .routes import Caller, Router

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC = {"/static/app.js": ("app.js", "text/javascript; charset=utf-8"), "/static/style.css": ("style.css", "text/css; charset=utf-8")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
log = logging.getLogger("mra.ui")


class MraServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, port: int, api: UiApi, token: str):
        super().__init__(("127.0.0.1", port), Handler)
        self.api, self.token, self.router = api, token, Router(api)
        p = self.server_address[1]
        self.hosts = {f"127.0.0.1:{p}", f"localhost:{p}"}
        self.max_body = int(api.max_upload * 1.5)


class Handler(BaseHTTPRequestHandler):
    server: MraServer
    server_version = "mra-ui"

    def log_message(self, fmt, *args):      # silent: request lines can contain run ids and file names
        return

    def handle(self) -> None:
        with contextlib.suppress(ConnectionError, TimeoutError):      # the browser closed the connection (a cancelled or retried download)
            super().handle()

    # ---- response helpers ------------------------------------------------------
    def _send(self, status: int, data: bytes, ctype: str, html: bool = False, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if html:
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Frame-Options", "DENY")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    # ---- guards ----------------------------------------------------------------------
    def _host_ok(self) -> bool:
        if self.headers.get("Host", "") not in self.server.hosts:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{h}" for h in self.server.hosts}

    def _token_ok(self, query: dict) -> bool:
        given = self.headers.get("X-MRA-Token") or (query.get("t") or [""])[0]
        return hmac.compare_digest(given.encode(), self.server.token.encode())

    # ---- routing ------------------------------------------------------------------------
    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def _route(self, method: str) -> None:
        url = urlparse(self.path)
        path, query = url.path, parse_qs(url.query)
        if not self._host_ok():
            return self._json(403, {"error": "not allowed"})
        if method == "GET" and path == "/":
            try:
                page = (STATIC_DIR / "index.html").read_text(encoding="utf-8").replace("__MRA_TOKEN__", self.server.token)
            except OSError:
                return self._json(500, {"error": "the page files are missing from this installation"})
            return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8", html=True)
        if method == "GET" and path in STATIC:
            name, ctype = STATIC[path]
            return self._send(200, (STATIC_DIR / name).read_bytes(), ctype)
        if not path.startswith("/api/"):
            return self._json(404, {"error": "not found"})
        if not self._token_ok(query):
            return self._json(403, {"error": "missing or wrong token: reload the page"})
        body: dict = {}
        raw = b""
        if method in ("POST", "PUT"):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                return self._json(400, {"error": "bad Content-Length"})
            if n < 0:
                return self._json(400, {"error": "bad Content-Length"})
            if n > self.server.max_body:
                return self._json(413, {"error": "the upload is too large"})
            raw = self.rfile.read(n) if n else b""
            if method == "POST":
                try:
                    body = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    return self._json(400, {"error": "the request body is not valid JSON"})
                if not isinstance(body, dict):
                    return self._json(400, {"error": "the request body must be a JSON object"})
        actor = clean_actor(self.headers.get("X-MRA-Actor"))
        try:
            if method == "PUT":
                return self._put(path, raw, actor)
            resp = self.server.router.handle(method, path, body, Caller(actor))
            self._send(resp.status, resp.body, resp.ctype, extra=resp.headers)
        except (ConnectionError, TimeoutError):
            raise                      # the client went away; handle() ends the request quietly
        except UiError as e:
            self._json(e.status, e.payload)
        except Exception:
            log.exception("unhandled error for %s %s", method, path)
            self._json(500, {"error": "internal error (details are in the terminal)"})

    def _put(self, path: str, raw: bytes, actor: str) -> None:
        m = re.fullmatch(r"/api/uploads/(?P<uid>[0-9a-f]{32})/content", path)
        if not m:
            return self._json(404, {"error": "not found"})
        self.server.api.put_upload(m["uid"], raw, actor)
        self._json(200, {"ok": True})


def make_server(svc: AssessmentService, port: int = 0, token: str | None = None) -> MraServer:
    return MraServer(port, UiApi(svc), token or secrets.token_urlsafe(24))


def serve(svc: AssessmentService, port: int = 8765, open_browser: bool = True, templates: dict[str, str] | None = None) -> int:
    try:
        srv = make_server(svc, port)
    except OSError as e:
        print(f"Cannot start on port {port}: {e.strerror}. Try another one: python -m mra ui --port 8800")
        return 2
    for kind, path in (templates or {}).items():
        try:
            warnings = srv.api.load_template_file(kind, path)
        except UiError as e:
            print(e.payload.get("error", e.payload))
            srv.server_close()
            return 2
        verdict = " (check: " + " ".join(warnings) + ")" if warnings else (" (looks like the AWS deck)" if kind == "pptx" else "")
        print(f"  {kind} template: {Path(path).name}{verdict}")
    url = f"http://127.0.0.1:{srv.server_address[1]}/"
    st = svc.cfg_settings
    print(f"A04 MRA Agent review page: {url}")
    print(f"  Text engine: {svc.llm.name} ({'real model, uses AWS' if svc.llm.name == 'bedrock' else 'offline, no AWS'})")
    print(f"  Data kept in: {'s3://' + str(st.bucket) if st.storage_mode == 'aws' else Path(st.local_root).resolve()}")
    print("  Only this computer can open it. Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit("start it with: python -m mra ui")
