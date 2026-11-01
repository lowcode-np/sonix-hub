"""Offline server test: fake builder, real HTTP over loopback. python -m tests.test_server"""
import gzip
import http.client
import json
import tempfile
import threading
import time

from hub.feed import Feed
from hub.server import HubServer, Settings

TOKEN = "s3cret"
BLOB = "x" * 2000


def make_doc(now):
    return {"ver": 1, "ts": now, "ttl": 60, "symbol": "XAUUSD", "bias": "BUY", "score": 32,
            "confidence": 61, "event_risk": "LOW", "next_event": "", "next_event_ts": 0,
            "next_event_impact": "", "news_sentiment": 0.2, "summary_en": "Gold firm.",
            "summary_th": "ทองคำยืนได้", "provider": "test", "market": {"gc": {"last": 4000.0}},
            "blob": BLOB}


def call(port, path, method="GET", headers=None, body=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    hdrs = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, hdrs, data


def main():
    tmp = tempfile.mkdtemp()
    calls = []

    def builder(now):
        calls.append(now)
        return make_doc(now)

    feed = Feed(tmp, ttl=60, builder=builder, seed_history=False)
    srv = HubServer(("127.0.0.1", 0), feed, Settings(token=TOKEN, ttl=60, admin_min_interval=0, state_dir=tmp))
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)
    t.start()
    try:
        # 1. no token -> 401
        st, _, body = call(port, "/v1/xauusd.json")
        assert st == 401, st
        assert json.loads(body)["error"] == "unauthorized"

        # 2. cold start with ?k= builds synchronously and returns the feed
        st, h, body = call(port, f"/v1/xauusd.json?k={TOKEN}")
        assert st == 200, (st, body)
        doc = json.loads(body)
        assert doc["bias"] == "BUY" and doc["score"] == 32
        assert len(calls) == 1, calls
        assert "etag" in h and "cache-control" in h and "last-modified" in h
        assert h["x-feed-stale"] == "0" and h["x-feed-provider"] == "test"
        assert h["access-control-allow-origin"] == "*"
        etag = h["etag"]

        # 3. header auth (Bearer)
        st, _, _ = call(port, "/v1/xauusd.json", headers={"Authorization": "Bearer " + TOKEN})
        assert st == 200, st
        st, _, _ = call(port, "/v1/xauusd.json", headers={"X-Hub-Token": TOKEN})
        assert st == 200, st
        st, _, _ = call(port, "/v1/xauusd.json", headers={"X-Hub-Token": "wrong"})
        assert st == 401, st

        # 4. conditional GET -> 304
        st, _, body = call(port, f"/v1/xauusd.json?k={TOKEN}", headers={"If-None-Match": etag})
        assert st == 304 and body == b"", (st, body)

        # 5. gzip when the client asks for it
        st, h, body = call(port, f"/v1/xauusd.json?k={TOKEN}", headers={"Accept-Encoding": "gzip"})
        assert st == 200 and h.get("content-encoding") == "gzip"
        assert json.loads(gzip.decompress(body))["blob"] == BLOB

        # 6. HEAD: headers only
        st, h, body = call(port, f"/v1/xauusd.json?k={TOKEN}", method="HEAD")
        assert st == 200 and body == b"" and int(h["content-length"]) > 0

        # 7. health is open, history needs the token
        st, _, body = call(port, "/healthz")
        assert st == 200 and json.loads(body)["ok"] is True
        st, _, _ = call(port, "/v1/history.json")
        assert st == 401, st
        st, _, body = call(port, f"/v1/history.json?k={TOKEN}")
        hist = json.loads(body)
        assert st == 200 and len(hist) == 1 and hist[0]["bias"] == "BUY", hist

        # 8. meta + index + 404
        st, _, body = call(port, "/v1/meta")
        assert st == 200 and json.loads(body)["server"]["token_required"] is True
        st, _, _ = call(port, "/")
        assert st == 200
        st, _, _ = call(port, "/nope")
        assert st == 404

        # 9. admin refresh rebuilds in the background
        st, _, body = call(port, f"/admin/refresh?k={TOKEN}", method="POST")
        assert st == 202, (st, body)
        for _ in range(100):
            if len(calls) >= 2 and not feed.stale():
                break
            time.sleep(0.05)
        assert len(calls) == 2, calls
        assert feed.builds == 1, feed.builds          # builds counts completed rebuilds

        # 10. CORS preflight
        st, h, _ = call(port, "/v1/xauusd.json", method="OPTIONS")
        assert st == 204 and "GET" in h["access-control-allow-methods"]
    finally:
        srv.shutdown()
        srv.server_close()
        feed.stop()

    # 11. a stale snapshot is served while a rebuild happens in the background
    feed2 = Feed(tmp, ttl=60, builder=builder, seed_history=False)
    feed2.built_ts -= 3600
    assert feed2.stale()
    assert feed2.kick() is True
    for _ in range(100):
        if not feed2.stale():
            break
        time.sleep(0.05)
    assert not feed2.stale() and feed2.doc is not None

    print(json.dumps({"builds": feed2.builds, "age": feed2.age(), "etag": feed2.etag}, indent=1))
    print("SERVER TEST OK")


if __name__ == "__main__":
    main()
