"""
SBO Bot Configuration - Production
====================================
Complete configuration for the SBO system.
Copy .env.example to .env and set your API keys.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# 🔄 SUPABASE CONFIG OVERRIDE
# If Supabase is configured, read config from DB (dashboard-editable)
# Falls back to env variables if Supabase unavailable
# ──────────────────────────────────────────────
def _db_cfg() -> dict:
    """Load config from Neon Postgres (or legacy Supabase REST). Returns empty dict if neither reachable."""
    # 1) Neon Postgres via psycopg2 (preferred)
    db_url = os.getenv("DATABASE_URL", "")
    if db_url.startswith(("postgres://", "postgresql://")):
        try:
            import psycopg2
            conn = psycopg2.connect(db_url, connect_timeout=5)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT key, value FROM sbo_config")
                    return {k: v for k, v in cur.fetchall()}
            finally:
                conn.close()
        except Exception:
            pass
    # 2) Legacy Supabase REST fallback
    try:
        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_SERVICE_KEY", "")
        if not url or not key:
            return {}
        import requests
        r = requests.get(f"{url}/rest/v1/sbo_config?select=key,value",
                         headers={"apikey": key, "Authorization": f"Bearer {key}"},
                         timeout=5)
        if r.status_code == 200:
            return {row["key"]: row["value"] for row in r.json()}
    except Exception:
        pass
    return {}

_DB = _db_cfg()
if _DB:
    print(f"\u2705 Config from DB ({len(_DB)} keys): {_DB.get('business_name', '?')[:40]}")
def _cfg(key: str, env_key: str = "", default: str = "") -> str:
    """Get config: Supabase first, then env, then default.

    Falls through only when a higher-priority source is ABSENT (DB key
    missing / env var unset), NOT when it holds a falsy-but-valid value
    such as "0" or "". This preserves intentional DB values like "0"/"".
    """
    if key in _DB and _DB[key] is not None:
        return _DB[key]
    return os.getenv(env_key or key.upper(), default)

# ──────────────────────────────────────────────
# 🎯 TARGET BUSINESS
# ──────────────────────────────────────────────
BUSINESS_NAME = _cfg("business_name", "BUSINESS_NAME", "")
SEARCH_PREFIX = _cfg("search_prefix", "SEARCH_PREFIX", "")

# Multiple prefixes to monitor
_prefixes_raw = _cfg("search_prefixes", "SEARCH_PREFIXES", "")
if _prefixes_raw:
    SEARCH_PREFIXES = [p.strip() for p in _prefixes_raw.split(",") if p.strip()]
else:
    # Auto-generate from business name: progressive typing
    _words = BUSINESS_NAME.split()
    SEARCH_PREFIXES = [SEARCH_PREFIX]
    for i in range(1, len(_words) + 1):
        p = " ".join(_words[:i]).lower()
        if p != SEARCH_PREFIX and p not in SEARCH_PREFIXES:
            SEARCH_PREFIXES.append(p)

# Match keywords — words that identify YOUR business in results
# (used to click the right listing when multiple similar names exist)
_match_raw = _cfg("match_keywords", "MATCH_KEYWORDS", "")
if _match_raw:
    MATCH_KEYWORDS = [k.strip().lower() for k in _match_raw.split(",") if k.strip()]
else:
    MATCH_KEYWORDS = [w.lower() for w in BUSINESS_NAME.split() if len(w) > 2]

# Exclude keywords — skip listings that contain these (competitors/wrong results)
_exclude_raw = _cfg("exclude_keywords", "EXCLUDE_KEYWORDS", "")
if _exclude_raw:
    EXCLUDE_KEYWORDS = [k.strip().lower() for k in _exclude_raw.split(",") if k.strip()]
else:
    EXCLUDE_KEYWORDS = []

TARGET_CITY = _cfg("target_city", "TARGET_CITY", "Dubai")
TARGET_COUNTRY = _cfg("target_country", "TARGET_COUNTRY", "AE")
MAPS_LANGUAGE = _cfg("maps_language", "MAPS_LANGUAGE", "ar")

# ──────────────────────────────────────────────
# 🌍 GEO TARGETING (from .env / business row)
# ──────────────────────────────────────────────
GEO_CENTER_LAT = float(_cfg("geo_lat", "GEO_LAT", "0") or "0")
GEO_CENTER_LNG = float(_cfg("geo_lng", "GEO_LNG", "0") or "0")
GEO_RADIUS_KM = float(_cfg("geo_radius_km", "GEO_RADIUS_KM", "5") or "5")

# Open Maps CENTERED on the target coordinates so the first view is the right
# city regardless of the proxy's IP geolocation. Without coords the map centers
# on whatever country the exit IP resolves to (a common "wrong location" bug).
# This is the base map only — the bot still types in the search box (no direct
# /maps/search/ URL, which would kill the SBO typing signal).
if GEO_CENTER_LAT and GEO_CENTER_LNG:
    MAPS_URL = (f"https://www.google.com/maps/@{GEO_CENTER_LAT},{GEO_CENTER_LNG},13z"
                f"?hl={MAPS_LANGUAGE}&gl={TARGET_COUNTRY}")
else:
    MAPS_URL = f"https://www.google.com/maps?gl={TARGET_COUNTRY}&hl={MAPS_LANGUAGE}"

# Default Place ID (optional — used as fallback by sbo launcher)
DEFAULT_PLACE_ID = _cfg("place_id", "PLACE_ID", "")
GEO_ACCURACY = 100

# ──────────────────────────────────────────────
# 🤖 SESSION BEHAVIOR
# ──────────────────────────────────────────────
SESSIONS_PER_RUN = int(_cfg("sessions_per_run", "SESSIONS_PER_RUN", "20"))
MIN_DELAY_BETWEEN_SESSIONS = int(_cfg("min_delay", "MIN_DELAY", "120"))
MAX_DELAY_BETWEEN_SESSIONS = int(_cfg("max_delay", "MAX_DELAY", "300"))
# Add random variation ±20% for unpredictability

# Typing
MIN_TYPING_DELAY = 180   # Realistic human typing speed
MAX_TYPING_DELAY = 450   # With natural variation
PAUSE_AFTER_PREFIX = 3000 # Increased from 2000ms for natural behavior
PAUSE_BEFORE_CLICK = 1000 # Increased from 800ms

# Engagement
MIN_DWELL_TIME = int(_cfg("min_dwell", "MIN_DWELL", "30"))
MAX_DWELL_TIME = int(_cfg("max_dwell", "MAX_DWELL", "90"))
DIRECTIONS_WAIT = 10      # Increased from 5s for natural behavior

# Engagement weights (probability each action happens)
ACTION_WEIGHTS = {
    "directions": 0.85,    # 85% click directions
    "photos": 0.70,        # 70% browse photos
    "reviews": 0.40,       # 40% scroll reviews
    "website": 0.20,       # 20% click website
    "call": 0.10,          # 10% click call
    "share": 0.05,         # 5% click share
}

# ──────────────────────────────────────────────
# 📱 DEVICE DISTRIBUTION
# ──────────────────────────────────────────────
# Percentage of sessions per device type
# When USE_PROXY=false, force desktop (mobile without UAE proxy is useless)
_use_proxy = _cfg("use_proxy", "USE_PROXY", "false").lower() in ("true", "1", "yes")
_mobile_pct = float(os.getenv("MOBILE_PCT", "0.35"))
_tablet_pct = float(os.getenv("TABLET_PCT", "0.15"))
if not _use_proxy:
    DEVICE_DISTRIBUTION = {"mobile": 0.0, "desktop": 1.0, "tablet": 0.0}
else:
    DEVICE_DISTRIBUTION = {
        "mobile": _mobile_pct,
        "desktop": 1.0 - _mobile_pct - _tablet_pct,
        "tablet": _tablet_pct,
    }

# ──────────────────────────────────────────────
# 🔒 STEALTH
# ──────────────────────────────────────────────
HEADLESS = _cfg("headless", "HEADLESS", "true").lower() in ("true", "1", "yes")
USE_PATCHRIGHT = True       # Use Patchright instead of Playwright
USE_BROWSERFORGE = True     # Use browserforge for fingerprints
USE_GHOST_CURSOR = True     # Use ghost-cursor for mouse movements
BROWSER_CHANNEL = os.getenv("BROWSER_CHANNEL", "")  # empty = Chromium (Docker), "chrome" = local Chrome
# Additional anti-detection flags
EXTRA_CHROME_ARGS = [
    '--disable-blink-features=AutomationControlled',
    '--ignore-certificate-errors',          # accept self-signed certs from proxy
    '--no-sandbox',
    '--disable-dev-shm-usage',
    '--disable-extensions',
    '--disable-default-apps',
    '--no-first-run',
    # REMOVED — these caused issues:
    # --ignore-certificate-errors-spki-list  (needs a value, broke DNS)
    # --disable-web-security                 (broke loaders)
    # --disable-site-is-tracing              (not a real flag)
    # --disable-gpu                          (hides window on macOS)
    # --disable-background-networking        (kills DNS prefetch)
]

# ──────────────────────────────────────────────
# 🌐 PROXY CONFIGURATION
# ──────────────────────────────────────────────
USE_PROXY = _use_proxy  # Already parsed above for DEVICE_DISTRIBUTION
PROXY_PROVIDER = _cfg("proxy_provider", "PROXY_PROVIDER", "brightdata")
# Recommended: Use residential proxies for better success rate

# Bright Data
BRIGHTDATA_CUSTOMER_ID = os.getenv("BRIGHTDATA_CUSTOMER_ID", "")
BRIGHTDATA_ZONE = os.getenv("BRIGHTDATA_ZONE", "mobile")
BRIGHTDATA_PASSWORD = os.getenv("BRIGHTDATA_PASSWORD", "")
BRIGHTDATA_HOST = "brd.superproxy.io"
BRIGHTDATA_PORT = 33335

# SOAX
SOAX_API_KEY = os.getenv("SOAX_API_KEY", "")
SOAX_HOST = os.getenv("SOAX_HOST", "")
SOAX_PORT = int(os.getenv("SOAX_PORT", "0") or "0")

# Custom proxies list (format: protocol://user:pass@host:port)
CUSTOM_PROXIES = [
    # "http://user:pass@proxy1.example.com:8080",
]

# Universal proxy URL — works with any provider (DataImpulse, IPRoyal, Webshare,
# SmartProxy, Oxylabs, NetNut, etc.). Auto-detects format:
#   host:port:user:pass                              (DataImpulse style)
#   user:pass@host:port
#   http://user:pass@host:port
#   socks5://user:pass@host:port
PROXY_URL = os.getenv("PROXY_URL", "")

# Proxy settings
PROXY_STICKY_SESSION = True   # Same IP for entire session
PROXY_MAX_USES = 50           # Max uses before cooldown
PROXY_COOLDOWN_MINUTES = 30   # Cooldown after max uses
PROXY_HEALTH_CHECK_INTERVAL = 300  # seconds

# ──────────────────────────────────────────────
# 📊 DATABASE
# ──────────────────────────────────────────────
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///sbo_data.db")

# ──────────────────────────────────────────────
# 🔍 POSITION MONITORING
# ──────────────────────────────────────────────
# Google Places API (BEST - same API Maps uses, ~$2.83/1000 requests)
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "")

# SerpApi (alternative - $75/mo)
SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
USE_SERPAPI = bool(SERPAPI_KEY)

POSITION_CHECK_INTERVAL = 3600 * 4  # Every 4 hours

# Google autocomplete direct endpoint (free fallback, rate-limited)
AUTOCOMPLETE_ENDPOINT = "https://www.google.com/complete/search"

# ──────────────────────────────────────────────
# 📅 SCHEDULER
# ──────────────────────────────────────────────
DAILY_SESSION_TARGET = int(_cfg("daily_target", "DAILY_SESSION_TARGET", "50"))
SCHEDULE_START_HOUR = 9    # 9 AM Dubai time (UTC+4)
SCHEDULE_END_HOUR = 23     # 11 PM Dubai time (UTC+4)
SCHEDULE_PEAK_HOUR = 20    # 8 PM peak — highest traffic on Google Maps
WEEKEND_MULTIPLIER = 0.8   # 80% volume on weekends (Dubai Fri/Sat)

# ──────────────────────────────────────────────
# 🖥️ DASHBOARD
# ──────────────────────────────────────────────
DASHBOARD_PORT = 8501
DASHBOARD_REFRESH_SECONDS = 10

# ──────────────────────────────────────────────
# 📁 LOGGING
# ──────────────────────────────────────────────
LOG_DIR = "logs"
LOG_LEVEL = "INFO"
SCREENSHOT_ON_ERROR = True
SCREENSHOT_ON_SUCCESS = False
SAVE_SESSION_DATA = True
MAX_LOG_FILES = 100  # Auto-cleanup old logs

# ──────────────────────────────────────────────
# 💀 AGGRESSIVE TEST MODE (set by bot at runtime)
# ──────────────────────────────────────────────
AGGRESSIVE_MODE = False  # Will be set True by bot when --aggressive flag is used
