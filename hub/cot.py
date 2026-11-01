"""CFTC Commitments of Traders (weekly, free Socrata API) - COMEX gold, disaggregated futures only."""
import urllib.parse

from . import config
from .http import get_json


def _f(row, *names):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return float(row[n])
    raise KeyError(names[0])


def collect():
    q = urllib.parse.urlencode({"cftc_contract_market_code": config.COT_CODE,
                                "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 2})
    rows = get_json(f"{config.COT_URL}?{q}")
    if not rows:
        raise RuntimeError("cot: empty")

    def one(r):
        lng = _f(r, "m_money_positions_long_all", "m_money_positions_long")
        sht = _f(r, "m_money_positions_short_all", "m_money_positions_short")
        return {"date": r.get("report_date_as_yyyy_mm_dd", "")[:10], "mm_net": int(lng - sht),
                "oi": int(_f(r, "open_interest_all", "open_interest"))}

    cur = one(rows[0])
    if len(rows) > 1:
        prev = one(rows[1])
        cur["mm_net_chg"] = cur["mm_net"] - prev["mm_net"]
        cur["oi_chg_pct"] = round((cur["oi"] - prev["oi"]) / prev["oi"] * 100, 2) if prev["oi"] else None
    return cur
