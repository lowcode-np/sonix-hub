"""SONIX Hub - one run: collect -> score -> LLM -> write public/v1/xauusd.json (+ history.json).
Usage:  python -m hub.run --out public
"""
import argparse
import json
import time

from . import config, cot, econ, flow, llm, news, prices, score
from .store import Store

VERSION = 1


def _step(name, fn, errs, *a):
    t0 = time.time()
    try:
        return fn(*a)
    except Exception as e:
        errs.append(f"{name}: {str(e)[:200]}")
        return None
    finally:
        print(f"  {name:<8} {time.time() - t0:5.1f}s")


def fallback_summary(bias, sc, prices_, econ_):
    gc = prices_.get("gc", {})
    dxy = prices_.get("dxy", {})
    ev = (econ_ or {}).get("next_high")
    en = f"Gold {gc.get('last', '-')} ({gc.get('chg_24h_pct', 0):+.2f}% 24h), DXY {dxy.get('chg_24h_pct', 0):+.2f}%. Bias {bias} ({sc})."
    th = f"ทองคำ {gc.get('last', '-')} ({gc.get('chg_24h_pct', 0):+.2f}% ใน 24 ชม.) ดอลลาร์ {dxy.get('chg_24h_pct', 0):+.2f}% มุมมอง {bias} ({sc})"
    if ev:
        en += f" Next high-impact USD event: {ev['name']}."
        th += f" ข่าวแรงถัดไป: {ev['name']}"
    return en, th


def build(now):
    errs = []
    print("SONIX Hub run", time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)), "UTC")
    px = _step("prices", prices.collect, errs)
    px, perr = px if px else ({}, [])
    errs += perr
    ec = _step("calendar", econ.collect, errs, now)
    nw = _step("news", news.collect, errs, now)
    nw, nerr = nw if nw else ([], [])
    errs += nerr
    fl = _step("flow", flow.collect, errs)
    ct = _step("cot", cot.collect, errs)

    metrics = {"gold": px.get("gc"), "dxy": px.get("dxy"), "us10y": px.get("tnx"), "vix": px.get("vix"),
               "flow_paxg": fl, "cot": ct, "event_risk": (ec or {}).get("event_risk")}
    res = _step("llm", llm.analyze, errs, nw, metrics, (ec or {}).get("events", []))
    ai, provider, lerr = res if res else (None, "none", [])
    errs += lerr

    bias, sc, conf, comp = score.compute(px, fl, ct, ai, ec)
    if ai:
        sum_en, sum_th = ai["summary_en"], ai["summary_th"]
    else:
        sum_en, sum_th = fallback_summary(bias, sc, px, ec)
    nh = (ec or {}).get("next_high") or {}
    rated = {it["i"]: it for it in (ai or {}).get("items", [])}
    top = []
    for i, n in enumerate(nw, 1):
        r = rated.get(i, {})
        top.append({"title": n["title"], "src": n["src"], "time": n["ts"], "s": r.get("s"), "impact": r.get("impact", 0)})
    top.sort(key=lambda x: (-(x["impact"] or 0), -x["time"]))

    # flat fields first: the EA reads the first occurrence of each key
    return {
        "ver": VERSION, "ts": now, "ttl": config.TTL_SEC, "symbol": "XAUUSD",
        "bias": bias, "score": sc, "confidence": conf,
        "event_risk": (ec or {}).get("event_risk", "UNKNOWN"),
        "next_event": nh.get("name", ""), "next_event_ts": nh.get("ts", 0), "next_event_impact": nh.get("impact", ""),
        "news_sentiment": (ai or {}).get("news_sentiment", 0.0),
        "summary_en": sum_en, "summary_th": sum_th,
        "provider": provider,
        "drivers": (ai or {}).get("drivers", []),
        "components": comp,
        "market": {k: v for k, v in px.items()},
        "flow": fl, "cot": ct,
        "calendar": (ec or {}).get("events", []),
        "headlines": top[:12],
        # ok = the feed is usable (gold price present, score computed); degraded = some sources failed.
        # A blocked RSS feed from a cloud IP must not make the EA think the whole feed is broken.
        "status": {"ok": bool((px or {}).get("gc")), "degraded": len(errs) > 0, "errors": errs[:20]},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="public")
    a = ap.parse_args()
    now = int(time.time())
    doc = build(now)
    Store(a.out).save(doc)
    print(f"-> {doc['bias']} score {doc['score']} conf {doc['confidence']} | {doc['provider']} | errors {len(doc['status']['errors'])}")
    for e in doc["status"]["errors"]:
        print("   !", e)


if __name__ == "__main__":
    main()
