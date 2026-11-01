"""USD economic calendar (ForexFactory weekly JSON feed - unofficial, fetch at most hourly)."""
from datetime import datetime

from . import config
from .http import get_json


def collect(now):
    rows = get_json(config.CALENDAR_URL)
    ev = []
    for e in rows:
        if e.get("country") != "USD" or e.get("impact") not in ("High", "Medium"):
            continue
        try:
            ts = int(datetime.fromisoformat(e["date"]).timestamp())
        except Exception:
            continue
        ev.append({"ts": ts, "name": e.get("title", "")[:60], "impact": e["impact"].upper(),
                   "forecast": e.get("forecast", ""), "previous": e.get("previous", "")})
    ev.sort(key=lambda x: x["ts"])
    upcoming = [x for x in ev if x["ts"] >= now - 3600][:12]
    nxt = next((x for x in ev if x["ts"] >= now and x["impact"] == "HIGH"), None)
    recent_high = any(x["impact"] == "HIGH" and now - 3600 <= x["ts"] < now for x in ev)
    if recent_high or (nxt and nxt["ts"] - now <= 2 * 3600):
        risk = "HIGH"
    elif nxt and nxt["ts"] - now <= 24 * 3600:
        risk = "MEDIUM"
    else:
        risk = "LOW"
    return {"event_risk": risk, "next_high": nxt, "events": upcoming}
