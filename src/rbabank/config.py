"""Central configuration. Every threshold and source detail lives here, not in the code.

Changing a number in this file is a reviewed change (see docs/STANDARDS.md).
"""
from pathlib import Path

# ---------------------------------------------------------------- paths
ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"            # landed exactly as received; never edited
CURATED = DATA / "curated"    # cleaned, typed, aligned; rebuilt from raw
SERVE = DATA / "serve"        # the only table the dashboard reads
QUALITY = DATA / "quality"    # run log, data-quality log, quarantine

# ---------------------------------------------------------------- time
# Common time axis: the ASX trading date in Sydney local time.
# Raw timestamps are kept as received; ingestion time is recorded in UTC.
MARKET_TZ = "Australia/Sydney"

# ---------------------------------------------------------------- sources
RBA_URL = "https://www.rba.gov.au/statistics/tables/csv/f1-data.csv"   # Table F1 (daily)
RBA_SERIES = {"FIRMMCRTD": "cash_rate_target", "FIRMMCRID": "interbank_overnight_rate"}
ASX_TICKER = "^AXJO"          # S&P/ASX 200 on Yahoo Finance
ASX_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]
HISTORY_PERIOD = "2y"         # enough for a 1-year percentile window + 90-day view

# A browser-like User-Agent: the RBA site may refuse requests without one.
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (RBB analytics PoC; contact: analytics team)"}

# ---------------------------------------------------------------- failure handling
HTTP_TIMEOUT_S = 20
MAX_ATTEMPTS = 3
BACKOFF_BASE_S = 2            # waits 2s, 4s between attempts
MAX_RATE_STALENESS_DAYS = 5   # cash-rate value older than this on a trading day -> flagged

# ---------------------------------------------------------------- quality rules
RATE_MIN, RATE_MAX = 0.0, 20.0     # plausible cash-rate range (% p.a.)
RATE_MAX_DAILY_JUMP = 1.0          # percentage points in one day -> quarantined for review
MAX_DAILY_INDEX_MOVE = 0.15        # |close/prev close - 1| above 15% -> quarantined for review

# ---------------------------------------------------------------- metrics
VOL_MA_WINDOW = 20            # trading days (about one month)
VOL_MA_MIN_OBS = 15           # need 15 of 20 valid volume days, else blank
VOLATILITY_WINDOW = 14        # trading days of daily log returns
VOLATILITY_MIN_OBS = 12       # need 12 of 14 valid returns, else blank
ROC_WINDOW = 20               # trading days
TRADING_DAYS_PER_YEAR = 252

# ---------------------------------------------------------------- RAG signal
RAG_LOOKBACK = 252            # trailing trading days used to define "normal"
RAG_MIN_HISTORY = 120         # fewer than this -> GREY (not enough history to judge)
RAG_AMBER_PCTL = 0.75
RAG_RED_PCTL = 0.90
RAG_AMBER_ESCALATE_DAYS = 5   # amber for 5 trading days in a row -> red

DASHBOARD_DAYS = 90           # calendar days shown on the dashboard
