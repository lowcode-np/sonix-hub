"""All settings come from environment variables (GitHub Secrets / Variables or a local .env)."""
import os


def _load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_dotenv()


def env(name, default=""):
    # an unset GitHub Variable/Secret reaches us as "" - treat that as "not set" so the
    # code keeps its built-in default instead of an empty model name / empty LLM order
    return (os.environ.get(name) or "").strip() or default


GEMINI_API_KEY = env("GEMINI_API_KEY")
GEMINI_MODEL = env("GEMINI_MODEL", "gemini-3.6-flash")   # gemini-2.5-flash 404s for some API keys
THAILLM_API_KEY = env("THAILLM_API_KEY")
THAILLM_BASE = env("THAILLM_BASE", "http://thaillm.or.th/api/v1")
THAILLM_MODEL = env("THAILLM_MODEL", "typhoon-s-thaillm-8b-instruct")
LLM_ORDER = [x.strip() for x in env("LLM_ORDER", "gemini,thaillm").split(",") if x.strip()]

FEED_URL = env("FEED_URL")            # public URL of the previous xauusd.json (keeps history between runs)
TTL_SEC = int(env("TTL_SEC", "3900"))  # hourly run + 5 min margin

RSS_FEEDS = [x for x in env("RSS_FEEDS", "").split("|") if x] or [
    # Google News, macro-filtered: drops retail "gold rate today" spam
    "https://news.google.com/rss/search?q=(gold+OR+XAUUSD+OR+bullion)+AND+(Fed+OR+FOMC+OR+dollar+OR+yields+OR+CPI+OR+inflation)+when:2d&hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/search?q=(Federal+Reserve+OR+Treasury+yields+OR+CPI+OR+inflation)+when:2d&hl=en-US&gl=US&ceid=US:en",
    "https://www.federalreserve.gov/feeds/press_all.xml",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "https://www.mining.com/feed/",
]
CALENDAR_URL = env("CALENDAR_URL", "https://nfs.faireconomy.media/ff_calendar_thisweek.json")
FLOW_SYMBOL = env("FLOW_SYMBOL", "PAXGUSDT")
COT_URL = env("COT_URL", "https://publicreporting.cftc.gov/resource/72hh-3qpy.json")
COT_CODE = env("COT_CODE", "088691")   # COMEX gold

# --- server mode (python -m hub.server) ---
HOST = env("HOST", "0.0.0.0")
PORT = int(env("PORT", "8080"))
HUB_TOKEN = env("HUB_TOKEN")                    # empty = no auth; set it before exposing the port
HUB_STATE_DIR = env("HUB_STATE_DIR", "public")  # where v1/xauusd.json + history.json live
READY_TIMEOUT = int(env("READY_TIMEOUT", "240"))  # seconds a cold-start request may wait for the first build
