"""
Central configuration for the scanner.
Tune weights, thresholds, and sheet names here — nothing else should need editing
for routine adjustments.
"""

import os

# ----------------------------------------------------------------------------
# UNIVERSE SOURCES
# ----------------------------------------------------------------------------
SP500_SOURCE_URL = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"

# Official NSE archive (requires browser-like headers; NSE blocks bare requests)
NSE500_SOURCE_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
NSE_HOME_URL = "https://www.nseindia.com"  # visited first to obtain cookies

# Emergency fallback ONLY (used if both live NSE fetch attempts fail). This is a
# tiny seed list of large, stable names so the pipeline doesn't crash — it is
# NOT a substitute for the real NSE500 list. Fix the live fetch instead of
# relying on this.
NSE_FALLBACK_TICKERS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
    "ITC", "SBIN", "BHARTIARTL", "KOTAKBANK", "LT", "AXISBANK",
    "BAJFINANCE", "MARUTI", "ASIANPAINT", "TITAN", "SUNPHARMA",
    "ULTRACEMCO", "NESTLEIND", "WIPRO",
]

INDEX_TICKER_NSE = "^NSEI"   # Nifty 50, used as RS benchmark for NSE names
INDEX_TICKER_US = "^GSPC"    # S&P 500 index (use "SPY" if you prefer the ETF)

# ----------------------------------------------------------------------------
# DATA FETCH
# ----------------------------------------------------------------------------
PRICE_HISTORY_PERIOD = "3y"     # daily history pulled per ticker (needs buffer for weekly MACD warm-up)
BATCH_SIZE = 75                 # tickers per yfinance batch call
BATCH_SLEEP_SECONDS = 2         # pause between batches to avoid rate-limiting
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 5

FUNDAMENTALS_MAX_WORKERS = 6    # parallel threads for per-ticker fundamentals calls
FUNDAMENTALS_REFRESH_WEEKDAY = 0  # 0=Monday. Fundamentals are refetched only on this weekday.
FUNDAMENTALS_CACHE_PATH = "cache/fundamentals_cache.json"

# ----------------------------------------------------------------------------
# INDICATOR PARAMETERS
# ----------------------------------------------------------------------------
OBV_SLOPE_SHORT_WINDOW = 20
OBV_SLOPE_LONG_WINDOW = 50

MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9

SUPERTREND_SLOW = dict(period=10, multiplier=3)
SUPERTREND_FAST = dict(period=2, multiplier=1)

EMA_PERIOD = 20

RS_LOOKBACKS = [21, 63, 126]   # ~1m, 3m, 6m trading days, blended for RS score

# ----------------------------------------------------------------------------
# FUNDAMENTAL FILTERS (qualifying screen, NOT part of the composite score)
# ----------------------------------------------------------------------------
MIN_SALES_CAGR = 15.0     # percent, over available years (typically 3y)
MIN_PROFIT_CAGR = 15.0    # percent
MIN_ROCE = 18.0           # percent
MAX_DEBT_EQUITY = 0.5
FUNDAMENTAL_CAGR_YEARS = 3   # years of statements used for CAGR calc (yfinance typically gives ~4y annual)

# ----------------------------------------------------------------------------
# COMPOSITE SCORE WEIGHTS (must sum to 100)
# ----------------------------------------------------------------------------
WEIGHT_OBV = 30
WEIGHT_MACD_WEEKLY = 20
WEIGHT_MACD_DAILY = 15
WEIGHT_TREND = 20
WEIGHT_RELATIVE_STRENGTH = 15

assert WEIGHT_OBV + WEIGHT_MACD_WEEKLY + WEIGHT_MACD_DAILY + WEIGHT_TREND + WEIGHT_RELATIVE_STRENGTH == 100

# Sub-weights within the "Trend" bucket (Supertrend slow + fast + EMA20)
TREND_SUBWEIGHT_SUPERTREND_SLOW = 0.5
TREND_SUBWEIGHT_SUPERTREND_FAST = 0.25
TREND_SUBWEIGHT_EMA20 = 0.25

# ----------------------------------------------------------------------------
# OUTPUT CATEGORIES
# ----------------------------------------------------------------------------
ELITE_THRESHOLD = 85
EMERGING_THRESHOLD_LOW = 75
EMERGING_THRESHOLD_HIGH = 85
EXIT_THRESHOLD = 50
TOP_N = 20

# ----------------------------------------------------------------------------
# GOOGLE SHEETS
# ----------------------------------------------------------------------------
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON_PATH = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_JSON_PATH", "service_account.json"
)

SHEET_TABS = {
    "nse_full": "NSE500_Full_Scan",
    "us_full": "SP500_Full_Scan",
    "top20_nse": "Top20_NSE",
    "top20_us": "Top20_US",
    "elite": "Elite_Compounders",
    "emerging": "Emerging_Compounders",
    "exit": "Exit_Candidates",
    "run_log": "Run_Log",
}
