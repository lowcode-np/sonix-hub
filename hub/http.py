"""Tiny HTTP helpers (stdlib only - no pip install needed)."""
import gzip
import json
import time
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (X11; Linux x86_64) SonixHub/1.0"


def fetch(url, data=None, headers=None, timeout=20, retries=2):
    h = {"User-Agent": UA, "Accept": "*/*", "Accept-Encoding": "gzip"}
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8") if isinstance(data, (dict, list)) else data
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    last = None
    for i in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=body, headers=h, method="POST" if body else "GET")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (400, 401, 403, 404, 451):      # no point retrying
                break
        except Exception as e:                           # timeout, DNS, ...
            last = type(e).__name__ + ": " + str(e)[:120]
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"{url.split('?')[0]} -> {last}")   # never print query strings (keys)


def get_json(url, **kw):
    return json.loads(fetch(url, **kw).decode("utf-8"))


def get_text(url, **kw):
    return fetch(url, **kw).decode("utf-8", "replace")


def post_json(url, data, headers=None, timeout=90):
    return json.loads(fetch(url, data=data, headers=headers, timeout=timeout, retries=1).decode("utf-8"))
