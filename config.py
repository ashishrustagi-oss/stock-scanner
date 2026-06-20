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
# MF / FII SHAREHOLDING PATTERN (highest-risk module — see shareholding.py)
# ----------------------------------------------------------------------------
# Best-effort NSE corporate-filings endpoint for shareholding pattern filings.
# {symbol} is substituted with the bare NSE symbol (no .NS suffix). This is
# the single most likely thing to need fixing first if nothing resolves —
# check the Actions log for the actual HTTP status/response on a failure.
NSE_SHAREHOLDING_API_URL = "https://www.nseindia.com/api/corporate-shareholding-pattern?index=equities&symbol={symbol}"
SHAREHOLDING_CACHE_PATH = "cache/shareholding_history.json"
SHAREHOLDING_CACHE_MAX_AGE_DAYS = 75   # ~quarterly; avoids re-fetching every day
SHAREHOLDING_SLEEP_SECONDS = 1.5       # gentle pacing against a fragile endpoint
# Learned from a real run: NSE rate-limits hard after ~300 sequential
# requests in one burst (observed an 8x slowdown). Cap how much this module
# attempts per run — full ~500-ticker coverage builds up over several days,
# which is fine since the underlying data only changes quarterly anyway.
SHAREHOLDING_MAX_FETCHES_PER_RUN = 60
SHAREHOLDING_MAX_RUN_SECONDS = 600     # hard stop at 10 min regardless of count, protects total workflow time
SHAREHOLDING_SAVE_EVERY_N = 15         # incremental cache checkpoint, so a cancelled run loses minimal progress

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

# Sub-weights within the "Trend" bucket
# Now includes Weekly Supertrend and Near-52w-High
TREND_SUBWEIGHT_SUPERTREND_SLOW    = 0.30   # daily supertrend (10,3)
TREND_SUBWEIGHT_SUPERTREND_FAST    = 0.10   # daily supertrend (2,1)
TREND_SUBWEIGHT_EMA20              = 0.15   # price vs EMA20
TREND_SUBWEIGHT_SUPERTREND_WEEKLY  = 0.30   # weekly supertrend (10,3) — higher timeframe confirmation
TREND_SUBWEIGHT_NEAR_52W_HIGH      = 0.15   # price within 10% of 52-week high

assert abs(
    TREND_SUBWEIGHT_SUPERTREND_SLOW + TREND_SUBWEIGHT_SUPERTREND_FAST +
    TREND_SUBWEIGHT_EMA20 + TREND_SUBWEIGHT_SUPERTREND_WEEKLY +
    TREND_SUBWEIGHT_NEAR_52W_HIGH - 1.0
) < 1e-9, "Trend sub-weights must sum to 1.0"

# Sub-weights within the "OBV" bucket
# Now includes OBV 52-week range position
OBV_SUBWEIGHT_SLOPE_20D     = 0.35   # short-term OBV momentum
OBV_SUBWEIGHT_SLOPE_50D     = 0.30   # medium-term OBV momentum
OBV_SUBWEIGHT_52W_RANGE     = 0.35   # OBV position in its own 52-week range

assert abs(
    OBV_SUBWEIGHT_SLOPE_20D + OBV_SUBWEIGHT_SLOPE_50D + OBV_SUBWEIGHT_52W_RANGE - 1.0
) < 1e-9, "OBV sub-weights must sum to 1.0"

# Sub-weights within the "Weekly MACD" bucket
# Now includes the binary positive/negative flag alongside the ranked histogram
MACD_WEEKLY_SUBWEIGHT_RANKED   = 0.60   # cross-sectional rank of histogram magnitude
MACD_WEEKLY_SUBWEIGHT_POSITIVE = 0.40   # binary: histogram > 0 (0 or 100)

assert abs(MACD_WEEKLY_SUBWEIGHT_RANKED + MACD_WEEKLY_SUBWEIGHT_POSITIVE - 1.0) < 1e-9

# Threshold for "near 52-week high" (percentage below high, expressed as positive number)
NEAR_52W_HIGH_THRESHOLD_PCT = 10.0

# Weekly Supertrend parameters (same period/multiplier as daily slow by default)
SUPERTREND_WEEKLY = dict(period=10, multiplier=3)

# ----------------------------------------------------------------------------
# OUTPUT CATEGORIES (original composite-score system — unchanged)
# ----------------------------------------------------------------------------
ELITE_THRESHOLD = 85
EMERGING_THRESHOLD_LOW = 75
EMERGING_THRESHOLD_HIGH = 85
EXIT_THRESHOLD = 50
TOP_N = 20

# ════════════════════════════════════════════════════════════════════════════
# ELITE COMPOUNDER EARLY DETECTION SYSTEM
# Everything below is ADDITIVE — it runs alongside the original composite
# score above and does not change or remove anything in it.
# ════════════════════════════════════════════════════════════════════════════

# --- Lookback windows (in trading days) used across the early-detection modules ---
WEEKS_13_IN_DAYS = 65
WEEKS_26_IN_DAYS = 130
WEEKS_52_IN_DAYS = 252

# --- Early MACD module ---
MACD_EARLY_LOOKBACK_DAYS = 3   # how many recent bars to scan for a fresh bullish crossover

# --- Volatility Compression module ---
ATR_PERIOD = 14
VOLATILITY_COMPRESSION_LOOKBACK_DAYS = 252       # 'last year' for the percentile calc
VOLATILITY_COMPRESSION_PERCENTILE_THRESHOLD = 25  # TRUE if in the lowest 25% of the year

# --- Early EMA Structure module ---
EMA10_PERIOD = 10
EMA20_SLOPE_WINDOW = 10   # bars used to judge whether EMA20 itself is sloping up

# --- Near Breakout module ---
NEAR_BREAKOUT_THRESHOLD_PCT = 15.0   # distinct from the 10% used in the base Trend bucket

# --- Sector benchmark mapping for RS_SECTOR ---
# US: GICS Sector -> SPDR sector ETF (reliable, well-established 1:1 mapping)
SECTOR_INDEX_MAP_US = {
    "Information Technology":  "XLK",
    "Health Care":              "XLV",
    "Financials":                "XLF",
    "Consumer Discretionary":    "XLY",
    "Communication Services":    "XLC",
    "Industrials":                "XLI",
    "Consumer Staples":           "XLP",
    "Energy":                      "XLE",
    "Utilities":                    "XLU",
    "Real Estate":                  "XLRE",
    "Materials":                     "XLB",
}

# NSE: EXACT (normalized) sector label -> yfinance index ticker.
# These 20 labels are the REAL, COMPLETE set of NSE "Sector" values confirmed
# from a live NSE500_Full_Scan sheet on 2026-06-19 — not guessed. Only sectors
# I can reasonably confirm have a working free index ticker are mapped; the
# rest are left out on purpose and fall back to RS vs. Nifty 50 (visible via
# `sector_index_source` = FALLBACK_BROAD_INDEX), which is an honest data
# limitation rather than something to keep guessing tickers for:
#
#   Mapped (10):  Automobile and Auto Components, Fast Moving Consumer Goods,
#                 Financial Services, Healthcare, Information Technology,
#                 Media Entertainment & Publication, Metals & Mining,
#                 Oil Gas & Consumable Fuels, Realty, Consumer Services
#   Intentionally unmapped (10): Capital Goods, Chemicals, Construction,
#                 Construction Materials, Consumer Durables, Diversified,
#                 Power, Services, Telecommunication, Textiles
SECTOR_INDEX_MAP_NSE = {
    "Automobile and Auto Components":   "^CNXAUTO",      # Nifty Auto
    "Fast Moving Consumer Goods":       "^CNXFMCG",      # Nifty FMCG
    "Financial Services":               "^CNXFIN",       # Nifty Financial Services
    "Healthcare":                       "^CNXPHARMA",    # closest available proxy (pharma-heavy)
    "Information Technology":           "^CNXIT",        # Nifty IT
    "Media Entertainment & Publication": "^CNXMEDIA",    # Nifty Media
    "Metals & Mining":                  "^CNXMETAL",     # Nifty Metal
    "Oil Gas & Consumable Fuels":       "^CNXENERGY",    # Nifty Energy (closest proxy)
    "Realty":                           "^CNXREALTY",    # Nifty Realty
    "Consumer Services":                "^CNXCONSUM",    # Nifty Consumption (loose proxy)
}

# --- Elite Compounder Score weights (must sum to 100) ---
ELITE_WEIGHT_OBV_LEADERSHIP         = 20   # OBV 52w high / 13w / 26w rising
ELITE_WEIGHT_RS_LEADERSHIP          = 20   # RS vs Nifty + Sector: 52w high / 13w / 26w rising
ELITE_WEIGHT_MACD_EARLY             = 10   # early bullish crossover below zero
ELITE_WEIGHT_EMA_ALIGNMENT          = 5    # EMA10>EMA20 + EMA20 sloping up
ELITE_WEIGHT_VOLATILITY_COMPRESSION = 10   # ATR compression in lowest quartile of the year
ELITE_WEIGHT_SUPERTREND             = 10   # existing daily Supertrend(10,3)+(2,1), rescaled
ELITE_WEIGHT_WEEKLY_MACD            = 10   # existing weekly MACD score, rescaled
ELITE_WEIGHT_ABOVE_EMA20            = 5    # price > EMA20
ELITE_WEIGHT_FUNDAMENTALS           = 10   # existing fundamental qualifying filter

assert (
    ELITE_WEIGHT_OBV_LEADERSHIP + ELITE_WEIGHT_RS_LEADERSHIP + ELITE_WEIGHT_MACD_EARLY
    + ELITE_WEIGHT_EMA_ALIGNMENT + ELITE_WEIGHT_VOLATILITY_COMPRESSION
    + ELITE_WEIGHT_SUPERTREND + ELITE_WEIGHT_WEEKLY_MACD + ELITE_WEIGHT_ABOVE_EMA20
    + ELITE_WEIGHT_FUNDAMENTALS == 100
), "Elite Compounder Score weights must sum to 100"

# Within the OBV Leadership sub-score (max 20): 52w-high=10, 13w-rising=5, 26w-rising=5
ELITE_OBV_POINTS_52W_HIGH = 10
ELITE_OBV_POINTS_13W_RISING = 5
ELITE_OBV_POINTS_26W_RISING = 5

# Within the RS Leadership sub-score (max 20): 52w-high=10, 13w-rising=5, 26w-rising=5
# Each of these is itself split 50/50 between the Nifty-relative and
# Sector-relative versions of the signal, so full marks require BOTH to agree.
ELITE_RS_POINTS_52W_HIGH = 10
ELITE_RS_POINTS_13W_RISING = 5
ELITE_RS_POINTS_26W_RISING = 5

# Within the daily-Supertrend sub-score (max 10): weight slow vs fast supertrend
ELITE_SUPERTREND_SUBWEIGHT_SLOW = 0.65
ELITE_SUPERTREND_SUBWEIGHT_FAST = 0.35

# --- New watchlist category thresholds (based on EliteCompounderScore, 0-100) ---
ELITE_CATEGORY_A_THRESHOLD = 80    # Category A: Elite Compounders, score > 80
ELITE_CATEGORY_B_LOW = 65          # Category B: Emerging Leaders, 65-80
ELITE_CATEGORY_B_HIGH = 80
ELITE_CATEGORY_C_LOW = 50          # Category C: Watchlist, 50-65
ELITE_CATEGORY_C_HIGH = 65

# ----------------------------------------------------------------------------
# GOOGLE SHEETS
# ----------------------------------------------------------------------------
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_SERVICE_ACCOUNT_JSON_PATH = os.environ.get(
    "GOOGLE_SERVICE_ACCOUNT_JSON_PATH", "service_account.json"
)

SHEET_TABS = {
    # Original tabs — unchanged, still produced exactly as before
    "nse_full": "NSE500_Full_Scan",
    "us_full": "SP500_Full_Scan",
    "top20_nse": "Top20_NSE",
    "top20_us": "Top20_US",
    "elite": "Elite_Compounders",
    "emerging": "Emerging_Compounders",
    "exit": "Exit_Candidates",
    "run_log": "Run_Log",
    # New tabs — Elite Compounder Early Detection System
    "elite_early_detect": "Elite_Compounders_EarlyDetect",   # strict 3-flag AND filter
    "category_a": "Category_A_Elite_Compounders",
    "category_b": "Category_B_Emerging_Leaders",
    "category_c": "Category_C_Watchlist",
}
