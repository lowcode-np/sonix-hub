"""Snapshot storage: atomic JSON writes + 14-day history.

Used by both the CLI (`python -m hub.run`) and the API server (`python -m hub.server`).
Every file is written to a temp file first and then os.replace()'d, so a reader never
sees a half-written feed.
"""
import json
import os
import tempfile
import threading
import time

from . import config

V1 = "v1"
DOC = "xauusd.json"
HIST = "history.json"
HIST_DAYS = 14
HIST_MAX = 336            # 14 days hourly
HIST_MERGE_SEC = 300      # rebuilds closer than this replace the last point


def _atomic_write(path, text):
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Store:
    def __init__(self, root="public", seed_history=True):
        self.root = root
        self.dir = os.path.join(root, V1)
        self.doc_path = os.path.join(self.dir, DOC)
        self.hist_path = os.path.join(self.dir, HIST)
        self._lock = threading.Lock()
        self._seed = seed_history
        self._seed_done = False

    @staticmethod
    def _read(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def load_doc(self):
        doc = self._read(self.doc_path)
        return doc if isinstance(doc, dict) else None

    def history(self):
        h = self._read(self.hist_path)
        if isinstance(h, list) and h:
            return h
        if self._seed and not self._seed_done:      # first boot on a new host: inherit old feed
            self._seed_done = True
            r = self._remote_history()
            if r:
                return r
        return []

    def _remote_history(self):
        if not config.FEED_URL:
            return []
        url = config.FEED_URL.rsplit("/", 1)[0] + "/" + HIST
        try:
            from .http import get_json
            j = get_json(url, timeout=10, retries=0)
            return j if isinstance(j, list) else []
        except Exception as e:
            print(f"  history: {url} -> {str(e)[:120]} (starting a new one)")
            return []

    def save(self, doc):
        """Write xauusd.json + append history. Returns the compact JSON bytes."""
        raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            _atomic_write(self.doc_path, raw)
            self._write_history(doc)
            try:
                open(os.path.join(self.root, ".nojekyll"), "a").close()
            except OSError:
                pass
        return raw.encode("utf-8")

    def _write_history(self, doc):
        now = int(doc.get("ts") or time.time())
        hist = self.history()
        hist = [h for h in hist if isinstance(h, dict) and h.get("ts", 0) > now - HIST_DAYS * 86400]
        row = {"ts": now, "bias": doc.get("bias"), "score": doc.get("score"),
               "confidence": doc.get("confidence"),
               "gold": ((doc.get("market") or {}).get("gc") or {}).get("last"),
               "news": doc.get("news_sentiment"), "event_risk": doc.get("event_risk")}
        if hist and now - int(hist[-1].get("ts") or 0) < HIST_MERGE_SEC:
            hist[-1] = row
        else:
            hist.append(row)
        _atomic_write(self.hist_path, json.dumps(hist[-HIST_MAX:], separators=(",", ":")))
