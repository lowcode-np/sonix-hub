"""Prices from Yahoo Finance chart API (same source as yfinance, without the package)."""
import math
import urllib.parse

from .http import get_json

BASE = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval={iv}"
SYMS = {"gc": "GC=F", "dxy": "DX-Y.NYB", "tnx": "^TNX", "vix": "^VIX", "si": "SI=F", "gld": "GLD"}


def series(sym, rng, iv):
    j = get_json(BASE.format(sym=urllib.parse.quote(sym), rng=rng, iv=iv))
    r = j["chart"]["result"][0]
    q = r["indicators"]["quote"][0]
    out = []
    for i, t in enumerate(r.get("timestamp") or []):
        c = q["close"][i]
        if c is None:
            continue
        out.append((t, q["open"][i] or c, q["high"][i] or c, q["low"][i] or c, c, q["volume"][i] or 0))
    if len(out) < 3:
        raise RuntimeError(f"{sym}: no data")
    return out


def pct(a, b):
    return round((b - a) / a * 100, 3) if a else 0.0


def ema(vals, n):
    k, e = 2 / (n + 1), vals[0]
    for v in vals[1:]:
        e = v * k + e * (1 - k)
    return e


def corr(a, b):
    n = min(len(a), len(b))
    a, b = a[-n:], b[-n:]
    if n < 5:
        return None
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    return round(sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb), 3) if sa and sb else None


def _rets(closes):
    return [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]


def collect():
    out, errs, daily = {}, [], {}
    for key, sym in SYMS.items():
        try:
            h1 = series(sym, "5d", "60m")
            d1 = series(sym, "6mo", "1d")
            last_t, last = h1[-1][0], h1[-1][4]
            ago = next((b[4] for b in reversed(h1) if b[0] <= last_t - 86400), h1[0][4])
            row = {"last": round(last, 4), "chg_1h_pct": pct(h1[-2][4], last), "chg_24h_pct": pct(ago, last),
                   "chg_5d_pct": pct(d1[-6][4] if len(d1) > 6 else d1[0][4], last), "t": last_t}
            if key == "gc":
                c1 = [b[4] for b in h1]
                row["ema20_1h"] = round(ema(c1[-60:], 20), 2)
                row["ema50_1h"] = round(ema(c1, 50), 2)
                trs = [max(b[2], d1[i - 1][4]) - min(b[3], d1[i - 1][4]) for i, b in enumerate(d1) if i > 0]
                row["atr14_d"] = round(sum(trs[-14:]) / min(14, len(trs)), 2)
                row["hi_20d"] = round(max(b[2] for b in d1[-20:]), 2)
                row["lo_20d"] = round(min(b[3] for b in d1[-20:]), 2)
                row["vol_1d"] = d1[-1][5]
            out[key] = row
            daily[key] = [b[4] for b in d1[-21:]]
        except Exception as e:
            errs.append(f"price.{key}: {e}")
    if "gc" in daily and "dxy" in daily:
        out.setdefault("gc", {})["corr_dxy_20d"] = corr(_rets(daily["gc"]), _rets(daily["dxy"]))
    if "gc" in out and "si" in out and out["si"].get("last"):
        out["gc"]["gold_silver_ratio"] = round(out["gc"]["last"] / out["si"]["last"], 2)
    return out, errs
