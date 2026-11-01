"""LLM news reading + bilingual summary. Gemini first, ThaiLLM as fallback (order via LLM_ORDER)."""
import json
import re

from . import config
from .http import post_json
from .news import utc_str

SCHEMA = """{
 "news_sentiment": <float -1..1, impact on GOLD price: +1 very bullish, -1 very bearish>,
 "items": [{"i": <headline number>, "s": <float -1..1>, "impact": <0..3>}],
 "drivers": [<up to 3 short English phrases>],
 "summary_en": "<max 2 sentences, English>",
 "summary_th": "<max 2 sentences, Thai language>"
}"""


def build_prompt(news, metrics, events):
    lines = [f"{i}. [{utc_str(n['ts'])} UTC | {n['src']}] {n['title']}" for i, n in enumerate(news, 1)]
    ev = [f"{utc_str(e['ts'])} UTC {e['impact']} {e['name']} (f:{e['forecast']} p:{e['previous']})" for e in events[:6]]
    return (
        "You are a professional gold (XAUUSD) macro analyst.\n"
        "Task: rate how each headline affects the GOLD price over the next 1-24 hours, then give an overall view.\n"
        "Rules: use ONLY the data below, do not invent numbers or events; weak/irrelevant headlines get impact 0; "
        "a stronger USD / higher US yields / hawkish Fed are bearish for gold; risk-off, geopolitical stress, "
        "dovish Fed, weak US data are bullish for gold. The Thai summary must be natural Thai with the same meaning "
        "as the English one.\n\n"
        f"MARKET METRICS (computed, reliable):\n{json.dumps(metrics, ensure_ascii=False)}\n\n"
        f"UPCOMING USD EVENTS:\n" + ("\n".join(ev) or "none") + "\n\n"
        f"HEADLINES:\n" + ("\n".join(lines) or "none") + "\n\n"
        f"Reply with ONLY one JSON object, no markdown:\n{SCHEMA}"
    )


def parse_json(txt):
    txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S)           # reasoning models
    txt = re.sub(r"```(?:json)?", "", txt)
    a, b = txt.find("{"), txt.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("no JSON in reply")
    return json.loads(txt[a:b + 1])


def gemini(prompt):
    if not config.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
    body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 4096, "responseMimeType": "application/json"}}
    j = post_json(url, body, headers={"x-goog-api-key": config.GEMINI_API_KEY})
    parts = j["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


def thaillm(prompt, model=None):
    if not config.THAILLM_API_KEY:
        raise RuntimeError("THAILLM_API_KEY not set")
    body = {"model": model or config.THAILLM_MODEL, "max_tokens": 2048, "temperature": 0.2,
            "messages": [{"role": "system", "content": "You are a precise financial analyst. Answer with JSON only."},
                         {"role": "user", "content": prompt}]}
    j = post_json(config.THAILLM_BASE.rstrip("/") + "/chat/completions", body,
                  headers={"Authorization": "Bearer " + config.THAILLM_API_KEY}, timeout=150)
    return j["choices"][0]["message"]["content"]


PROVIDERS = {"gemini": lambda p: (gemini(p), f"gemini:{config.GEMINI_MODEL}"),
             "thaillm": lambda p: (thaillm(p), f"thaillm:{config.THAILLM_MODEL}")}


def clean(d, n_news):
    def clip(v, lo, hi):
        try:
            return max(lo, min(hi, float(v)))
        except Exception:
            return 0.0
    items = []
    for it in d.get("items") or []:
        try:
            i = int(it.get("i"))
        except Exception:
            continue
        if 1 <= i <= n_news:
            items.append({"i": i, "s": round(clip(it.get("s"), -1, 1), 2), "impact": int(clip(it.get("impact"), 0, 3))})
    return {"news_sentiment": round(clip(d.get("news_sentiment"), -1, 1), 3), "items": items,
            "drivers": [str(x)[:60] for x in (d.get("drivers") or [])][:3],
            "summary_en": str(d.get("summary_en", "")).strip()[:400],
            "summary_th": str(d.get("summary_th", "")).strip()[:400]}


def analyze(news, metrics, events):
    prompt = build_prompt(news, metrics, events)
    errs = []
    for name in config.LLM_ORDER:
        fn = PROVIDERS.get(name)
        if not fn:
            continue
        try:
            txt, prov = fn(prompt)
            out = clean(parse_json(txt), len(news))
            if not out["summary_en"] or not out["summary_th"]:
                raise ValueError("empty summary")
            return out, prov, errs
        except Exception as e:
            errs.append(f"llm.{name}: {str(e)[:160]}")
    return None, "none", errs
