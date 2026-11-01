"""In-memory feed with stale-while-revalidate refresh.

- One build at a time (single-flight); concurrent requests never trigger duplicate work.
- Readers are lock-free: a new snapshot is swapped in atomically, so the EA always gets a
  complete, previously published document instead of waiting on the LLM.
- A stale snapshot is served immediately and a refresh is kicked off in the background.
"""
import hashlib
import json
import threading
import time

from . import run
from .store import Store

MAX_WAIT = 300


def _etag(raw):
    return '"' + hashlib.blake2b(raw, digest_size=12).hexdigest() + '"'


class Feed:
    def __init__(self, root="public", ttl=3900, builder=run.build, seed_history=True):
        self.store = Store(root, seed_history=seed_history)
        self.ttl = max(60, int(ttl))
        self._builder = builder
        self.created = int(time.time())
        self.doc = self.store.load_doc()
        self.raw = json.dumps(self.doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8") if self.doc else b""
        self.etag = _etag(self.raw) if self.raw else '""'
        self.built_ts = int((self.doc or {}).get("ts") or 0)
        self.builds = 0
        self.fails = 0
        self.last_error = None
        self.last_duration = 0.0
        self._busy = False
        self._cv = threading.Condition()
        self._stop = threading.Event()
        self._scheduler = None

    # ---------- read side (no locks: swap is atomic) ----------
    def age(self, now=None):
        if not self.built_ts:
            return None
        now = int(now if now is not None else time.time())
        return max(0, now - self.built_ts)

    def stale(self, now=None):
        if not self.doc:
            return True
        ttl = int(self.doc.get("ttl") or self.ttl)
        now = int(now if now is not None else time.time())
        return now - self.built_ts > ttl

    def status(self):
        age = self.age()
        return {"ok": self.doc is not None, "age_sec": age, "ttl": int((self.doc or {}).get("ttl") or self.ttl),
                "stale": self.stale(), "busy": self._busy, "builds": self.builds, "fails": self.fails,
                "build_sec": self.last_duration, "last_error": self.last_error, "ts": self.built_ts,
                "provider": (self.doc or {}).get("provider"), "bias": (self.doc or {}).get("bias"),
                "score": (self.doc or {}).get("score")}

    # ---------- write side ----------
    def refresh(self, force=False):
        """Build synchronously, unless a fresh snapshot exists and force is False."""
        now = int(time.time())
        end = time.time() + MAX_WAIT
        with self._cv:
            while self._busy and time.time() < end:
                if not force and not self.stale(now):
                    return self.doc
                self._cv.wait(2)
            if not force and not self.stale(now):
                return self.doc
            self._busy = True
        t0 = time.time()
        try:
            doc = self._builder(now)
            raw = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.store.save(doc)
            with self._cv:
                self.doc, self.raw, self.etag = doc, raw, _etag(raw)
                self.built_ts = int(doc.get("ts") or now)
                self.builds += 1
                self.last_error = None
                self.last_duration = round(time.time() - t0, 1)
            return doc
        except Exception as e:
            with self._cv:
                self.fails += 1
                self.last_error = f"{type(e).__name__}: {e}"[:300]
            return self.doc
        finally:
            with self._cv:
                self._busy = False
                self._cv.notify_all()

    def kick(self, force=False):
        """Force refresh in the background. Returns False when a build is already running."""
        with self._cv:
            if self._busy:
                return False
        threading.Thread(target=self.refresh, kwargs={"force": force},
                         name="sonix-refresh", daemon=True).start()
        return True

    def wait_ready(self, timeout=MAX_WAIT):
        """Block only when nothing has ever been published (cold start)."""
        end = time.time() + timeout
        with self._cv:
            while self.doc is None and self._busy and time.time() < end:
                self._cv.wait(2)
        if self.doc is None:
            self.refresh()
        return self.doc

    # ---------- scheduler ----------
    def start(self):
        if self._scheduler:
            return self._scheduler
        self._scheduler = threading.Thread(target=self._loop, name="sonix-scheduler", daemon=True)
        self._scheduler.start()
        return self._scheduler

    def _loop(self):
        self.refresh(force=self.doc is None)              # first build at boot
        every = max(15, min(300, self.ttl // 4))
        while not self._stop.wait(every):
            if self.stale():
                self.refresh()

    def stop(self):
        self._stop.set()
