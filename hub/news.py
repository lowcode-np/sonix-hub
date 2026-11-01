"""Free RSS / Atom headlines, filtered for gold + USD macro relevance."""
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from . import config
from .http import get_text

KEYWORDS = ["gold", "xau", "bullion", "precious", "fed", "fomc", "powell", "inflation", "cpi", "pce", "payroll",
            "nfp", "jobs", "unemployment", "treasury", "yield", "dollar", "dxy", "rate cut", "rate hike", "tariff",
            "geopolit", "war", "sanction", "central bank", "ecb", "boj", "pboc", "recession", "gdp", "safe haven"]


def _ts(s):
    if not s:
        return None
    s = s.strip()
    try:
        return int(parsedate_to_datetime(s).timestamp())
    except Exception:
        pass
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
    except Exception:
        return None


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse(xml_text, src):
    root = ET.fromstring(xml_text)
    items = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        d = {_local(c.tag): (c.text or "").strip() for c in el}
        link = d.get("link", "")
        if not link:
            for c in el:
                if _local(c.tag) == "link" and c.get("href"):
                    link = c.get("href")
        title = re.sub(r"\s+", " ", d.get("title", ""))
        ts = _ts(d.get("pubDate") or d.get("published") or d.get("updated") or d.get("date"))
        if title and ts:
            items.append({"title": title[:180], "src": src, "ts": ts, "link": link[:300]})
    return items


def _relevant(title, src):
    t = title.lower()
    return "federalreserve" in src or any(k in t for k in KEYWORDS)


def _norm(t):
    t = re.sub(r" - [^-]{2,40}$", "", t.lower())       # Google News adds " - Publisher"
    return re.sub(r"[^a-z0-9]+", " ", t).strip()[:80]


def collect(now, max_items=30, max_age_h=24):
    seen, out, errs = set(), [], []
    for url in config.RSS_FEEDS:
        src = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
        try:
            for it in parse(get_text(url, timeout=15, retries=1), src):
                if now - it["ts"] > max_age_h * 3600 or it["ts"] > now + 600:
                    continue
                if not _relevant(it["title"], src):
                    continue
                k = _norm(it["title"])
                if k in seen:
                    continue
                seen.add(k)
                out.append(it)
        except Exception as e:
            errs.append(f"rss.{src}: {e}")
    out.sort(key=lambda x: x["ts"], reverse=True)
    return out[:max_items], errs


def utc_str(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%m-%d %H:%M")
