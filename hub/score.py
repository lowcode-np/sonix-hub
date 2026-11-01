"""Rule-based gold bias score -100..+100 from computed data (LLM only contributes the news sentiment)."""


def _clip(v, lo, hi):
    return max(lo, min(hi, v))


def compute(prices, flow, cot, llm, econ):
    comp = {}
    gc = prices.get("gc", {})
    if gc.get("ema20_1h") and gc.get("ema50_1h"):
        last, e20, e50 = gc["last"], gc["ema20_1h"], gc["ema50_1h"]
        comp["trend"] = 25 if last > e20 > e50 else (-25 if last < e20 < e50 else (10 if last > e50 else -10))
    if "dxy" in prices:
        comp["dxy"] = round(_clip(-prices["dxy"]["chg_24h_pct"] * 40, -20, 20), 1)      # 0.5 % DXY = 20 pts
    if "tnx" in prices:
        comp["yields"] = round(_clip(-prices["tnx"]["chg_24h_pct"] * 4, -15, 15), 1)    # 3.75 % yield move = 15
    if "vix" in prices:
        comp["risk_off"] = round(_clip(prices["vix"]["chg_24h_pct"] * 0.5, -5, 5), 1)
    if llm:
        comp["news"] = round(llm["news_sentiment"] * 20, 1)
    if flow:
        f = flow.get("book_imb", 0) * 10
        if flow.get("buy_ratio_1h") is not None:
            f += _clip((flow["buy_ratio_1h"] - 0.5) * 40, -10, 10)
        comp["flow"] = round(_clip(f, -15, 15), 1)
    if cot and cot.get("mm_net_chg") is not None and cot.get("mm_net"):
        comp["cot"] = round(_clip(cot["mm_net_chg"] / max(1, abs(cot["mm_net"])) * 100, -10, 10), 1)

    score = int(round(_clip(sum(comp.values()), -100, 100)))
    signs = [1 if v > 0 else -1 for v in comp.values() if abs(v) >= 2]
    agree = (abs(sum(signs)) / len(signs)) if signs else 0
    coverage = len(comp) / 7
    conf = int(round(_clip(35 + abs(score) * 0.4 + agree * 15 + coverage * 10, 0, 95)))
    if econ and econ.get("event_risk") == "HIGH":
        conf = int(conf * 0.7)                         # less trust right before / after big data
    bias = "BUY" if score >= 20 else ("SELL" if score <= -20 else "NEUTRAL")
    return bias, score, conf, comp
