"""Offline smoke test: fakes every HTTP call, runs the full pipeline. python -m tests.test_offline"""
import io
import json
import os
import tempfile
import time
import urllib.request

NOW = int(time.time())


def chart(base, n, step):
    ts = [NOW - (n - i) * step for i in range(n)]
    c = [base * (1 + 0.001 * ((i % 7) - 3) + 0.0004 * i) for i in range(n)]
    return {"chart": {"result": [{"timestamp": ts, "indicators": {"quote": [
        {"open": c, "high": [x * 1.002 for x in c], "low": [x * 0.998 for x in c], "close": c, "volume": [1000] * n}]}}]}}


RSS = f"""<?xml version="1.0"?><rss><channel>
<item><title>Gold climbs as dollar slips ahead of CPI</title><link>https://x/1</link><pubDate>{time.strftime('%a, %d %b %Y %H:%M:%S +0000', time.gmtime(NOW - 1800))}</pubDate></item>
<item><title>Fed's Waller: no rush to cut rates</title><link>https://x/2</link><pubDate>{time.strftime('%a, %d %b %Y %H:%M:%S +0000', time.gmtime(NOW - 3600))}</pubDate></item>
<item><title>Celebrity news unrelated</title><link>https://x/3</link><pubDate>{time.strftime('%a, %d %b %Y %H:%M:%S +0000', time.gmtime(NOW - 600))}</pubDate></item>
</channel></rss>"""

ATOM = f"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Treasury yields rise after jobs data</title>
<link href="https://y/1"/><updated>{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(NOW - 7200))}</updated></entry></feed>"""

CAL = [{"title": "CPI m/m", "country": "USD", "date": time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime(NOW + 5400)),
        "impact": "High", "forecast": "0.3%", "previous": "0.2%"},
       {"title": "ECB Speech", "country": "EUR", "date": time.strftime('%Y-%m-%dT%H:%M:%S+00:00', time.gmtime(NOW + 100)),
        "impact": "High", "forecast": "", "previous": ""}]

LLM_OUT = {"news_sentiment": 0.3, "items": [{"i": 1, "s": 0.6, "impact": 2}, {"i": 2, "s": -0.4, "impact": 2}],
           "drivers": ["weaker USD", "CPI risk"], "summary_en": "Gold firm on softer dollar; CPI ahead adds risk.",
           "summary_th": "ทองคำยืนได้จากดอลลาร์ที่อ่อนค่า แต่ตัวเลข CPI ที่จะประกาศเพิ่มความเสี่ยง"}


class Resp(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def fake(req, timeout=0):
    u = req.full_url
    if "finance.yahoo" in u:
        base = 4000 if "GC" in u else (100 if "DX" in u else (4.3 if "TNX" in u else (17 if "VIX" in u else (50 if "SI" in u else 370))))
        body = chart(base, 120, 3600 if "60m" in u else 86400)
    elif "faireconomy" in u:
        body = CAL
    elif "depth" in u:
        body = {"bids": [["3999.5", "2.0"], ["3998", "3"]], "asks": [["4000.5", "1.0"], ["4002", "1"]]}
    elif "klines" in u:
        body = [[0, 0, 0, 0, 0, "10", 0, 0, 0, "6"] for _ in range(60)]
    elif "cftc" in u:
        body = [{"report_date_as_yyyy_mm_dd": "2026-10-27T00:00:00", "m_money_positions_long_all": "200000",
                 "m_money_positions_short_all": "40000", "open_interest_all": "500000"},
                {"report_date_as_yyyy_mm_dd": "2026-10-20T00:00:00", "m_money_positions_long_all": "190000",
                 "m_money_positions_short_all": "45000", "open_interest_all": "490000"}]
    elif "generativelanguage" in u:
        body = {"candidates": [{"content": {"parts": [{"text": json.dumps(LLM_OUT, ensure_ascii=False)}]}}]}
    elif "fxstreet" in u:
        return Resp(ATOM.encode())
    elif "news.google" in u or "rss" in u or "feeds" in u:
        return Resp(RSS.encode())
    else:
        raise OSError("blocked " + u)
    return Resp(json.dumps(body).encode())


def main():
    os.environ["GEMINI_API_KEY"] = "test"
    urllib.request.urlopen = fake
    from hub import config, run
    config.GEMINI_API_KEY = "test"
    import sys
    out = tempfile.mkdtemp()
    sys.argv = ["run", "--out", out]
    run.main()
    doc = json.load(open(os.path.join(out, "v1", "xauusd.json"), encoding="utf-8"))
    assert doc["bias"] in ("BUY", "SELL", "NEUTRAL")
    assert doc["summary_th"].startswith("ทองคำ")
    assert doc["event_risk"] == "HIGH" and doc["next_event"] == "CPI m/m"
    assert doc["flow"]["book_imb"] > 0 and doc["cot"]["mm_net_chg"] == 15000
    assert list(doc)[:3] == ["ver", "ts", "ttl"]
    print(json.dumps({k: doc[k] for k in list(doc)[:15]}, ensure_ascii=False, indent=1))
    print("components", doc["components"], "errors", doc["status"]["errors"])
    print("OFFLINE TEST OK")


if __name__ == "__main__":
    main()
