"""SONIX Hub HTTP API server - stdlib only, no pip install, no framework.

Endpoints
  GET  /v1/xauusd.json              main feed for the EA (ETag / 304 / gzip / cache headers)
  GET  /v1/history.json             14-day history (bias / score / price) for backtests
  GET  /healthz                     liveness + feed age, never requires a token
  GET  /v1/meta                     build / feed diagnostics
  GET or POST /admin/refresh?k=...  force a rebuild (202, rate limited)
  GET  /                            tiny JSON index of the above

Auth: set HUB_TOKEN, then pass ?k=<token>, X-Hub-Token: <token> or Authorization: Bearer <token>.
MQL5 WebRequest cannot set custom headers, so the query string form (?k=) is supported.

Run:  python -m hub.server --port 8080 --out public
"""
import argparse
import gzip
import hmac
import json
import logging
import os
import platform
import signal
import sys
import threading
import time
from dataclasses import dataclass
from email.utils import formatdate, mktime_tz, parsedate_tz
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import config
from .feed import Feed

log = logging.getLogger("sonix.hub")

VERSION = "1.0"
MIN_GZIP = 900

ENDPOINTS = [
    {"path": "/v1/xauusd.json", "auth": True, "desc": "main feed (EA)"},
    {"path": "/v1/history.json", "auth": True, "desc": "14-day history"},
    {"path": "/healthz", "auth": False, "desc": "liveness + feed age"},
    {"path": "/v1/meta", "auth": False, "desc": "diagnostics"},
    {"path": "/admin/refresh", "auth": True, "desc": "force rebuild (POST or GET)"},
]


@dataclass
class Settings:
    token: str = ""
    ttl: int = 3900
    ready_timeout: int = 240
    admin_min_interval: int = 60
    state_dir: str = "public"
    allow_build: bool = True
    version: str = VERSION


def _json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "SonixHub/" + VERSION
    sys_version = ""
    timeout = 65

    # ---------- plumbing ----------
    @property
    def feed(self):
        return self.server.feed

    @property
    def settings(self):
        return self.server.settings

    def log_message(self, fmt, *args):
        # never log query strings: they may carry the token
        log.info("%s %s %s", self.address_string(), self.path.split("?")[0], fmt % args)

    def do_OPTIONS(self):
        self._respond(204, b"", ctype=None, headers={
            "Access-Control-Allow-Methods": "GET, POST, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "X-Hub-Token, Authorization, If-None-Match, Content-Type",
            "Access-Control-Max-Age": "600",
        })

    def do_HEAD(self):
        self._route()

    def do_GET(self):
        self._route()

    def do_POST(self):
        self._route()

    def _respond(self, code, body=b"", ctype="application/json; charset=utf-8", headers=None, gzip_ok=True):
        h = {"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"}
        h.update(headers or {})
        if body and gzip_ok and len(body) > MIN_GZIP and "gzip" in (self.headers.get("Accept-Encoding") or "").lower():
            body = gzip.compress(body, 6)
            h["Content-Encoding"] = "gzip"
            h["Vary"] = "Accept-Encoding"
        self.send_response(code)
        if ctype:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        for k, v in h.items():
            self.send_header(k, v)
        self.end_headers()
        if body and self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, code, obj, headers=None):
        self._respond(code, _json_bytes(obj), headers=headers)

    def _unauthorized(self):
        self._json(401, {"error": "unauthorized", "hint": "pass ?k=<token> or X-Hub-Token header"},
                   headers={"WWW-Authenticate": 'Bearer realm="sonix-hub"'})

    # ---------- auth ----------
    def _authorized(self, q):
        token = self.settings.token
        if not token:
            return True
        cands = list(q.get("k", [])) + list(q.get("token", []))
        hdr = self.headers.get("X-Hub-Token") or self.headers.get("X-Api-Key") or ""
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            hdr = hdr or auth[7:].strip()
        cands.append(hdr)
        return any(c and hmac.compare_digest(str(c), token) for c in cands)

    # ---------- routing ----------
    def _route(self):
        u = urlparse(self.path)
        path = u.path.rstrip("/") or "/"
        q = parse_qs(u.query)

        if path in ("/", "/index.json"):
            return self._json(200, {"name": "SONIX Hub", "version": VERSION,
                                    "ts": int(time.time()), "endpoints": ENDPOINTS})
        if path == "/healthz":
            st = self.feed.status()
            return self._json(200 if st["ok"] else 503, st)
        if path == "/v1/meta":
            st = self.feed.status()
            st.update({"server": {"version": VERSION, "uptime_sec": int(time.time()) - self.feed.created,
                                  "python": platform.python_version(), "token_required": bool(self.settings.token),
                                  "state_dir": self.settings.state_dir},
                       "endpoints": ENDPOINTS})
            return self._json(200, st)
        if path in ("/admin/refresh", "/refresh"):
            return self._admin_refresh(q)

        if path in ("/v1/xauusd.json", "/xauusd.json"):
            if not self._authorized(q):
                return self._unauthorized()
            return self._feed_doc()

        if path in ("/v1/history.json", "/history.json"):
            if not self._authorized(q):
                return self._unauthorized()
            hist = self.feed.store.history()
            return self._respond(200, _json_bytes(hist),
                                 headers={"Cache-Control": "public, max-age=300"})

        return self._json(404, {"error": "not_found", "path": path, "endpoints": [e["path"] for e in ENDPOINTS]})

    def _feed_doc(self):
        doc = self.feed.doc
        if doc is None:                                   # very first request: build now
            if not self.settings.allow_build:
                return self._json(503, {"error": "no_feed", "detail": "no cached feed and rebuilds are disabled",
                                        "retry_after": 60}, headers={"Retry-After": "60"})
            doc = self.feed.wait_ready(self.settings.ready_timeout)
        if doc is None:
            return self._json(503, {"error": "warming_up", "detail": self.feed.last_error,
                                    "retry_after": 30}, headers={"Retry-After": "30"})
        if self.settings.allow_build and self.feed.stale():
            self.feed.kick()                              # serve stale now, rebuild in background

        ctype = "application/json; charset=utf-8"
        headers = self._feed_headers()
        etag = headers.get("ETag")
        inm = self.headers.get("If-None-Match")
        if etag and inm and etag in [x.strip() for x in inm.split(",")]:
            return self._respond(304, b"", ctype=None, headers=headers, gzip_ok=False)
        ims = self.headers.get("If-Modified-Since")
        if ims and self.feed.built_ts:
            t = parsedate_tz(ims)
            if t and int(mktime_tz(t)) >= self.feed.built_ts:
                return self._respond(304, b"", ctype=None, headers=headers, gzip_ok=False)

        log.info("feed ts=%s age=%ss stale=%s provider=%s", self.feed.built_ts,
                 headers["X-Feed-Age"], headers["X-Feed-Stale"], headers["X-Feed-Provider"])
        return self._respond(200, self.feed.raw, ctype=ctype, headers=headers)

    def _feed_headers(self):
        age = self.feed.age() or 0
        ttl = int((self.feed.doc or {}).get("ttl") or self.settings.ttl)
        stale = self.feed.stale()
        max_age = 30 if stale else max(0, ttl - age)
        return {
            "Cache-Control": f"public, max-age={max_age}",
            "ETag": self.feed.etag,
            "Last-Modified": formatdate(self.feed.built_ts or time.time(), usegmt=True),
            "X-Feed-Age": str(age),
            "X-Feed-Ttl": str(ttl),
            "X-Feed-Stale": "1" if stale else "0",
            "X-Feed-Provider": str((self.feed.doc or {}).get("provider") or "none"),
        }

    def _admin_refresh(self, q):
        if not self._authorized(q):
            return self._unauthorized()
        if not self.settings.allow_build:
            return self._json(409, {"error": "refresh_disabled", "detail": "server started with --no-refresh"})
        if self.command not in ("GET", "POST"):
            return self._json(405, {"error": "method_not_allowed"}, headers={"Allow": "GET, POST"})
        force = str(q.get("force", ["0"])[0]).lower() not in ("0", "false", "")
        now = int(time.time())
        wait = self.settings.admin_min_interval - (now - self.server.last_admin)
        if wait > 0 and not force:
            return self._json(429, {"error": "too_many_requests", "retry_after": wait},
                              headers={"Retry-After": str(wait)})
        self.server.last_admin = now
        queued = self.feed.kick(force=True)
        log.info("admin refresh queued=%s force=%s", queued, force)
        return self._json(202, {"queued": queued, "age_sec": self.feed.age(),
                                "builds": self.feed.builds, "status": self.feed.status()})


class HubServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128
    timeout = 60

    def __init__(self, addr, feed, settings):
        self.feed = feed
        self.settings = settings
        self.last_admin = 0
        super().__init__(addr, Handler)


def make_server(host, port, feed, settings):
    return HubServer((host, int(port)), feed, settings)


def main(argv=None):
    ap = argparse.ArgumentParser(description="SONIX Hub API server")
    ap.add_argument("--host", default=config.HOST)
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--out", default=config.HUB_STATE_DIR, help="state dir for v1/*.json")
    ap.add_argument("--token", default=config.HUB_TOKEN)
    ap.add_argument("--ttl", type=int, default=config.TTL_SEC)
    ap.add_argument("--no-refresh", action="store_true", help="serve the cached file only, never rebuild")
    ap.add_argument("--no-seed", action="store_true", help="do not inherit history from FEED_URL")
    ap.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    a = ap.parse_args(argv)

    logging.basicConfig(level=getattr(logging, a.log_level.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stdout)

    feed = Feed(a.out, ttl=a.ttl, seed_history=not a.no_seed)
    settings = Settings(token=a.token, ttl=a.ttl, state_dir=a.out, allow_build=not a.no_refresh)
    srv = make_server(a.host, a.port, feed, settings)

    if not a.no_refresh:
        feed.start()
    if not settings.token and a.host not in ("127.0.0.1", "localhost"):
        log.warning("HUB_TOKEN is empty - anyone reaching this port can read the feed. "
                    "Bind 127.0.0.1 or restrict the port in the firewall if that is not intended.")

    def _shutdown(signum, frame):
        log.info("signal %s - shutting down", signum)
        threading.Thread(target=srv.shutdown, daemon=True).start()

    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                signal.signal(sig, _shutdown)
            except (ValueError, OSError):
                pass

    log.info("SONIX Hub %s listening on http://%s:%s  state=%s  auth=%s  refresh=%s",
             VERSION, a.host, srv.server_address[1], os.path.abspath(a.out),
             "on" if settings.token else "off", "off" if a.no_refresh else "on")
    log.info("feed: GET /v1/xauusd.json | history: GET /v1/history.json | health: GET /healthz")
    try:
        srv.serve_forever(poll_interval=1)
    except KeyboardInterrupt:
        pass
    finally:
        feed.stop()
        srv.server_close()
        log.info("stopped")


if __name__ == "__main__":
    main()
