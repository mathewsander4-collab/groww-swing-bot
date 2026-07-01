import os
import json

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.path.join(BASE_DIR, "data")
CACHE_DIR = os.path.join(BASE_DIR, "cache")

os.makedirs(DATA_DIR,  exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

# Groww API
GROWW_API_KEY     = os.environ.get("GROWW_API_KEY", "")
GROWW_API_SECRET  = os.environ.get("GROWW_API_SECRET", "")
GROWW_TOTP_TOKEN  = os.environ.get("GROWW_TOTP_TOKEN", "")
GROWW_TOTP_SECRET = os.environ.get("GROWW_TOTP_SECRET", "")
GROWW_API_TOKEN   = os.environ.get("GROWW_API_TOKEN", "")

# Email
EMAIL_SENDER   = os.environ.get("EMAIL_SENDER", "")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER", "")

# Google Sheets
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "1ndhNsT1N8aeytFQr0MbiMoJyJpZeHOHfNDuZfP2WZ6w")
_creds_json = os.environ.get("GOOGLE_CREDS_JSON", "")
if _creds_json:
    GOOGLE_CREDS_FILE = os.path.join(DATA_DIR, "swingbot-credentials.json")
    with open(GOOGLE_CREDS_FILE, "w") as f:
        f.write(_creds_json)
else:
    GOOGLE_CREDS_FILE = os.path.join(DATA_DIR, "swingbot-credentials.json")

# Trading
PAPER_TRADE               = os.environ.get("PAPER_TRADE", "true").lower() == "true"
CAPITAL                   = float(os.environ.get("CAPITAL", "100000"))
RISK_PER_TRADE_PCT        = 1.0
MAX_CAPITAL_PER_TRADE_PCT = 20.0
MAX_CAPITAL_DEPLOYED_PCT  = 70.0   # stop opening new positions after 70% capital used
MAX_OPEN_POSITIONS        = 99     # no hard count limit — capital limit controls instead
REWARD_RISK_RATIO         = 2.0

# Universe
NIFTY500_CSV_URL    = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
UNIVERSE_CACHE_FILE = os.path.join(CACHE_DIR, "nifty500_universe.json")
UNIVERSE_CACHE_DAYS = 7
MIN_PRICE            = 30.0
MIN_AVG_DAILY_VOLUME = 100000

# Indicators
EMA_FAST              = 20
EMA_SLOW              = 50
EMA_TREND             = 200
RSI_PERIOD            = 14
ATR_PERIOD            = 14
ADX_PERIOD            = 14
BREAKOUT_LOOKBACK     = 20
VOLUME_SURGE_MULT     = 1.5
STOP_LOSS_ATR_MULT    = 2.0
RSI_OVERBOUGHT        = 75
RSI_OVERSOLD          = 35
MOMENTUM_LOOKBACK_DAYS = 63
ADX_MIN_THRESHOLD     = 25
HISTORY_DAYS          = 400

# Misc
REQUEST_TIMEOUT             = 15
REQUEST_RETRY               = 3
REQUEST_SLEEP_BETWEEN_CALLS = 0.25