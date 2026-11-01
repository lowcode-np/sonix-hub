"""Free order book / order flow proxy: tokenized gold (PAXG) on Binance public market data.
Real XAUUSD spot is OTC and has no free public depth; PAXG tracks 1 oz gold and has a real order book."""
from . import config
from .http import get_json

HOSTS = ["https://data-api.binance.vision", "https://api.binance.com"]


def _get(path):
    last = None
    for h in HOSTS:
        try:
            return get_json(h + path, timeout=15, retries=1)
        except Exception as e:
            last = e
    raise RuntimeError(str(last))


def collect():
    s = config.FLOW_SYMBOL
    book = _get(f"/api/v3/depth?symbol={s}&limit=100")
    bids = [(float(p), float(q)) for p, q in book["bids"]]
    asks = [(float(p), float(q)) for p, q in book["asks"]]
    mid = (bids[0][0] + asks[0][0]) / 2
    band = mid * 0.003                                   # +-0.3 % around mid
    bq = sum(q for p, q in bids if p >= mid - band)
    aq = sum(q for p, q in asks if p <= mid + band)
    imb = (bq - aq) / (bq + aq) if bq + aq else 0.0

    k1 = _get(f"/api/v3/klines?symbol={s}&interval=1m&limit=60")       # [.., 5 vol, .., 9 taker-buy vol]
    v1 = sum(float(k[5]) for k in k1)
    tb1 = sum(float(k[9]) for k in k1)
    kh = _get(f"/api/v3/klines?symbol={s}&interval=1h&limit=24")
    vh = sum(float(k[5]) for k in kh)
    tbh = sum(float(k[9]) for k in kh)
    return {"src": f"binance:{s}", "mid": round(mid, 2), "spread": round(asks[0][0] - bids[0][0], 2),
            "book_imb": round(imb, 3), "bid_qty": round(bq, 3), "ask_qty": round(aq, 3),
            "delta_1h": round(2 * tb1 - v1, 3), "buy_ratio_1h": round(tb1 / v1, 3) if v1 else None,
            "delta_24h": round(2 * tbh - vh, 3), "buy_ratio_24h": round(tbh / vh, 3) if vh else None,
            "vol_24h": round(vh, 2)}
