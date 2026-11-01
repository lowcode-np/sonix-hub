"""Compare Gemini vs the 3 ThaiLLM models on the same live prompt.
python -m bench.bench_llm      (needs GEMINI_API_KEY / THAILLM_API_KEY in .env)
Scores: valid JSON, both summaries present, sentiment sign vs labels, latency."""
import json
import time

from hub import config, econ, llm, news

THAI_MODELS = ["typhoon-s-thaillm-8b-instruct", "openthaigpt-thaillm-8b-instruct-v7.2",
               "pathumma-thaillm-qwen3-8b-think-3.0.0"]

# small labelled set: (headline, expected sign for gold)
LABELS = [("Fed signals more rate cuts as inflation cools", 1), ("Dollar jumps to 3-month high after strong jobs report", -1),
          ("Middle East tensions escalate, investors seek safe havens", 1), ("US 10-year yield surges to 5%", -1),
          ("Gold ETF holdings rise for fifth week", 1), ("Hawkish Powell pushes back on rate-cut bets", -1)]


def run_one(name, fn):
    t0 = time.time()
    try:
        raw = fn()
        d = llm.clean(llm.parse_json(raw), n_news)
        hit = sum(1 for it in d["items"] if it["i"] > n_live and it["s"] * LABELS[it["i"] - n_live - 1][1] > 0)
        return {"model": name, "ok": True, "sec": round(time.time() - t0, 1), "label_hits": f"{hit}/{len(LABELS)}",
                "sent": d["news_sentiment"], "en": d["summary_en"], "th": d["summary_th"]}
    except Exception as e:
        return {"model": name, "ok": False, "sec": round(time.time() - t0, 1), "error": str(e)[:200]}


now = int(time.time())
live, _ = news.collect(now, max_items=10)
n_live = len(live)
items = live + [{"title": t, "src": "label", "ts": now} for t, _ in LABELS]
n_news = len(items)
try:
    events = econ.collect(now)["events"]
except Exception:
    events = []
prompt = llm.build_prompt(items, {"note": "benchmark"}, events)

results = []
if config.GEMINI_API_KEY:
    results.append(run_one("gemini:" + config.GEMINI_MODEL, lambda: llm.gemini(prompt)))
if config.THAILLM_API_KEY:
    for m in THAI_MODELS:
        results.append(run_one("thaillm:" + m, lambda m=m: llm.thaillm(prompt, m)))

for r in results:
    print("=" * 70)
    for k, v in r.items():
        print(f"{k:>10}: {v}")
json.dump(results, open("bench_result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\nsaved bench_result.json")
