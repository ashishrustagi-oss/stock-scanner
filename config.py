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

# Point-in-time S&P 500 membership (for backtesting only — avoids survivorship
# bias from applying today's constituent list to historical dates). See
# sp500_point_in_time.py for the full rationale and data-quality caveats.
SP500_HISTORICAL_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "S%26P%20500%20Historical%20Components%20%26%20Changes.csv"
)
SP500_CHANGES_SINCE_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/sp500_changes_since_2019.csv"
)
SP500_TIMELINE_CACHE_PATH = "cache/sp500_timeline_cache.json"

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

# ----------------------------------------------------------------------------
# NSE Small/Micro-cap universe — separate, NOT merged into NSE500
# ----------------------------------------------------------------------------
# Smallcap 250 is, by NSE's own index rule, drawn entirely from existing
# Nifty 500 members (must be in Nifty 500, must NOT be in Nifty 100 or
# Midcap 150) — so on its own it adds ~zero genuinely new tickers, just a
# label for stocks already in NSE500_Full_Scan.
#
# Microcap 250 is different: by NSE's rule, stocks already in/entering
# Nifty 500 are INELIGIBLE for Microcap 250 — it's compulsorily built from
# the rank ~351-675 band BEYOND Nifty 500. This is the piece that actually
# adds new names. Both are fetched and combined here so a name doesn't
# silently get skipped if it crosses tiers between semi-annual NSE rebalances.
#
# Deliberately kept OUT of `combined` in main.py — see README "NSE
# Small/Micro-cap tier" section for why (percentile-rank contamination risk
# for composite_score / EliteCompounderScore, both tuned against NSE500/
# SP500 liquidity and data-quality patterns).
NSE_SMALLCAP250_SOURCE_URL = "https://nsearchives.nseindia.com/content/indices/ind_niftysmallcap250list.csv"
# Microcap 250 is NOT served from nsearchives.nseindia.com or niftyindices.com's
# usual /IndexConstituent/ path the way every other Nifty index list is. The
# real source is niftyindices.com's own backend, reached directly via its raw
# Azure App Service hostname rather than the custom domain — found by
# manually clicking the Download button on
# https://www.niftyindices.com/indices/equity/broad-based-indices/nifty-microcap-250
# and capturing the URL it actually hits. The URL itself is correct (works
# from a regular browser) — but see NSE_MICROCAP_ENABLED below for why this
# is currently disabled regardless.
NSE_MICROCAP250_SOURCE_URL = "https://nseindex-prod-app.azurewebsites.net/IndexConstituent/ind_niftymicrocap250_list.csv"
# Microcap 250 fetch is DISABLED by default (22-06-2026): the Azure backend
# above 403s ("Ip Forbidden") specifically for GitHub Actions' runner IPs —
# an IP-range block, not a header/cookie/URL problem, which can't be fixed
# in code. Smallcap 250 fetches cleanly with zero issues. Decision: ship
# Smallcap 250 only for now rather than build around a fetch that fails on
# every single automated run. To re-enable once a workaround exists (e.g. a
# self-hosted runner, or NSE/niftyindices changes their blocking), flip this
# to True — no other code changes needed, get_nse_smallmicro_universe()
# already handles both states.
NSE_MICROCAP_ENABLED = False

# Same emergency-only purpose as NSE_FALLBACK_TICKERS above — a handful of
# liquid-ish small/microcap names so the pipeline doesn't crash if both live
# fetch attempts fail. NOT a substitute for the real lists.
NSE_SMALLMICRO_FALLBACK_TICKERS = [
    "CAMS", "MTARTECH", "POLYCAB", "PERSISTENT", "KPITTECH",
    "RAINBOW", "CLEAN", "ANGELONE", "AARTIIND", "BEML",
]

# ----------------------------------------------------------------------------
# SmallMicroScore — a SEPARATE scoring system for the NSE Small/Micro-cap
# tier only. Deliberately NOT a reweighted copy of composite_score or
# EliteCompounderScore: those were tuned/backtested specifically against
# NSE500+SP500 liquidity and data-quality patterns (see README "Backtest
# Framework"), and there is zero backtest evidence for any formula on this
# thinner, less-liquid, more data-sparse universe. Every weight below is a
# DELIBERATELY LABELED, UNVALIDATED DEFAULT — same epistemic status as a
# first cut, not a tuned result. Revisit once this tier has its own
# walk-forward backtest (see backtest.py methodology).
#
# Liquidity gate (computed FIRST, before any score): small/microcap stocks
# can show a great-looking MACD crossover or OBV slope on a handful of
# thinly-traded days that mean nothing tradeable — NSE500/SP500 never
# needed this gate because every constituent there is liquid enough by
# default. No precise, citable "right" threshold exists for this (checked);
# this number is a deliberately conservative starting point for you to
# tune after seeing real output, exactly like MIN_SALES_CAGR/MIN_ROCE below.
LIQUIDITY_LOOKBACK_DAYS = 20                  # trading days averaged for avg_daily_traded_value
MIN_AVG_DAILY_TRADED_VALUE_INR = 5_000_000    # ₹50 lakh/day — scoring-eligibility floor, UNVALIDATED, tune freely
LIQUIDITY_DATA_QUALITY_MIN_DAYS = 10          # need at least this many real trading days in the window to trust the average

# SmallMicroScore component weights (must sum to 100). No shareholding
# weight exists here (NSE_SMALLMICRO never gets MF/FII data — see README).
#
# 3rd revision (24-06-2026), driven by the FIRST real backtest evidence on
# this tier (Backtest_Results_SmallMicro, n=2,626 OBV / n=410 RS / n=2,325
# near-52w-high / n=410 liquidity at the 12m horizon):
#   - RS (smallmicro_rs_top_decile) was the strongest single component:
#     +38.27pp excess at 12m — beat OBV's +26.68pp by ~12pp. Weights
#     swapped to match: OBV 40->25, RS 25->40.
#   - smallmicro_near_52w_high was the 2nd-strongest: +25.97pp excess,
#     clean monotonic hit-rate climb across horizons. Weight raised 15->20.
#   - smallmicro_high_liquidity showed ~zero predictive value as a SCORED
#     component: +1.14pp excess at 12m, hit rate 48.7% (below 50%) — a real
#     disappointment relative to the others. Weight cut 10->5 rather than
#     to 0, since one backtest run isn't (yet) the two-run standard of
#     evidence OBV earned on NSE500/SP500 before being trusted there; this
#     is a partial response to a real but still single data point, not a
#     full reversal. NOTE: this is the SCORED liquidity component only —
#     liquidity_qualified (the scoring-eligibility floor) and the strict
#     checklist's turnover bar are both UNAFFECTED by this change.
#   - smallmicro_earnings_accelerating showed n=0 — earnings acceleration
#     isn't historically reconstructed in the backtest (see backtest.py /
#     README), so this component remains untested either way. Left
#     unchanged at 10 pending real evidence either direction.
# Still carries the same UNVALIDATED status as every prior revision until
# this tier has TWO backtest runs confirming the same direction, the
# standard OBV itself had to meet on NSE500/SP500 before being trusted.
SMALLMICRO_SCORE_WEIGHTS = {
    "obv_leadership": 25,      # obv_52w_range_pct — outperformed by RS in the first SmallMicro backtest;
                               # demoted from 40, but still meaningfully weighted pending a 2nd confirming run either way
    "rs": 40,                  # rs_score vs Nifty 50, percentile-ranked within this universe —
                               # strongest single component in the first backtest (+38.27pp excess at 12m, n=410)
    "near_52w_high": 20,       # pct_from_52w_high, inverted + percentile-ranked — 2nd-strongest in the first backtest
    "earnings_acceleration": 10,  # earnings_acceleration_score, rescaled — untested in the backtest (n=0, see above)
    "liquidity": 5,            # avg_daily_traded_value, percentile-ranked — showed ~zero predictive value
                               # as a scored component in the first backtest; cut but not removed
}
assert sum(SMALLMICRO_SCORE_WEIGHTS.values()) == 100, "SMALLMICRO_SCORE_WEIGHTS must sum to 100"

# Strict pass/fail checklist — a SEPARATE flag from smallmicro_score, not a
# pre-filter on it (every liquidity-qualified stock still gets a full score
# regardless of whether it passes this). All four must be true for
# smallmicro_strict_pass to be True. "Top decile" uses the same >=90th
# percentile convention as OBV_LEADERSHIP_RANK_TOP_DECILE_THRESHOLD above,
# for consistency with the rest of this codebase.
SMALLMICRO_STRICT_TOP_DECILE_THRESHOLD = 90       # OBV and RS percentile must be >= this
SMALLMICRO_STRICT_MIN_TURNOVER_INR = 20_000_000   # ₹2 crore/day — much stricter than the scoring floor above, by design
# "Within 15% of 52-week high" reuses the existing near_breakout_15pct
# column (built from NEAR_BREAKOUT_THRESHOLD_PCT = 15.0 below) rather than
# a new constant — same threshold, already computed, no need to duplicate it.

# Category thresholds — deliberately different category NAMES from
# composite_score's (Elite Compounder/Emerging/Exit/Watch), not just
# different numbers, so a "Strong" here is never mistaken for the
# backtested "Elite Compounder" category on NSE500/SP500. Same
# UNVALIDATED-DEFAULT status as the weights above.
SMALLMICRO_STRONG_THRESHOLD = 70   # >= this -> "Strong"
SMALLMICRO_WATCH_THRESHOLD = 50    # >= this (and < STRONG) -> "Watch"; below -> "Weak"

INDEX_TICKER_NSE = "^NSEI"   # Nifty 50, used as RS benchmark for NSE names
INDEX_TICKER_US = "^GSPC"    # S&P 500 index (use "SPY" if you prefer the ETF)

# ----------------------------------------------------------------------------
# DATA FETCH
# ----------------------------------------------------------------------------
PRICE_HISTORY_PERIOD = "5y"     # bumped from 3y: monthly EMA50 needs ~50 monthly bars to be reasonably stable
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
# DEPRECATED — kept only so old cache entries / external references don't
# break on a missing name. This endpoint was a guessed URL that turned out
# to be dead (confirmed 404 even after fixing an underlying WAF/TLS-
# fingerprint block — see diagnostics/shareholding_api_probe_*.py). The
# module now uses the maintained `nse` library (pip install nse) instead,
# which fetches the real filing list and a working XBRL attachment URL per
# quarter. Not used anywhere in shareholding.py anymore.
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
# MY PORTFOLIO (manually-imported Zerodha holdings — see portfolio.py)
# ----------------------------------------------------------------------------
# Tab where you import your Zerodha holdings XLSX via Google Sheets'
# File > Import. This script only ever READS this tab, never writes to it,
# so re-importing whenever you trade never conflicts with anything here.
MY_HOLDINGS_TAB_NAME = "My_Holdings"

# ════════════════════════════════════════════════════════════════════════════
# PHASE 1 — Elite Compounder Discovery System v2.0
# All built from data already fetched — no new external data sources.
# Entirely additive; doesn't change composite_score or EliteCompounderScore.
# ════════════════════════════════════════════════════════════════════════════

# --- Module 3: RS Percentile Rank ---
# rs_rank is the percentile rank (0-100) of RS_vs_Broad_Index_pct WITHIN the
# stock's own universe — one column, not split by universe name, since each
# stock only ever belongs to one universe anyway.
RS_RANK_SCORE_THRESHOLD_TOP    = 95   # rank > 95 -> +15
RS_RANK_SCORE_POINTS_TOP       = 15
RS_RANK_SCORE_THRESHOLD_HIGH   = 90   # rank 90-95 -> +10
RS_RANK_SCORE_POINTS_HIGH      = 10
RS_RANK_SCORE_THRESHOLD_MID    = 80   # rank 80-90 -> +5
RS_RANK_SCORE_POINTS_MID       = 5
RS_RANK_TOP_DECILE_THRESHOLD   = 90   # flag_rs_top_decile fires above this

# --- Module 4: Trend Birth Detection ---
TREND_BIRTH_PCT_FROM_HIGH_FLOOR = -25.0   # must be within 25% of 52w high (not deeply broken down)
TREND_BIRTH_SCORE_POINTS = 10

# --- Module 5: Monthly Trend Confirmation ---
MONTHLY_EMA_FAST = 20
MONTHLY_EMA_SLOW = 50
MONTHLY_TREND_SCORE_POINTS = 10

# --- Module 6: Sector Leadership Engine ---
# Ranks stocks within their own (universe, sector) group by EliteCompounderScore
# — chosen because that's the system specifically built for leadership/early
# detection, and it's already a normalized 0-100 score safe to compare directly
# within a small group. Change this in scoring.py's compute_sector_leadership()
# if you'd rather rank by composite_score or pure RS-vs-sector instead.
SECTOR_LEADER_SCORE_RANK_1 = 15
SECTOR_LEADER_SCORE_RANK_2 = 10
SECTOR_LEADER_SCORE_RANK_3 = 5
SECTOR_LEADER_TOP_N_FOR_FLAG = 3     # flag_sector_leader fires for ranks 1-3
SECTOR_LEADER_TOP_N_FOR_TAB = 5      # the SECTOR_LEADERS tab shows top 5 per sector

# --- Module 2 extension (Phase 2): Institutional Accumulation scoring ---
# MF and FII each contribute UP TO 10 points (not 5+10=15 stacked — a
# 2-quarter streak already implies the 1-quarter signal, so the streak tier
# REPLACES rather than adds to the single-quarter tier). MF max 10 + FII
# max 10 = 20 total, matching the original spec's "Maximum = 20".
INSTITUTIONAL_SCORE_SINGLE_QTR_POINTS = 5
INSTITUTIONAL_SCORE_TWO_QTR_STREAK_POINTS = 10
INSTITUTIONAL_ACCUMULATION_FLAG_THRESHOLD = 10   # flag fires when score > this

# --- Module 1 (Phase 3): Earnings Acceleration Engine ---
# EPS and Revenue acceleration are independent signals and DO stack here
# (unlike Module 2's MF/FII tiers) — max 10 + max 10 = 20, matching the spec.
EARNINGS_ACCEL_EPS_THRESHOLD_HIGH = 20.0    # EPS acceleration >20% -> +10
EARNINGS_ACCEL_EPS_POINTS_HIGH = 10
EARNINGS_ACCEL_EPS_THRESHOLD_MID = 10.0     # 10-20% -> +5
EARNINGS_ACCEL_EPS_POINTS_MID = 5
EARNINGS_ACCEL_REVENUE_THRESHOLD_HIGH = 15.0   # Revenue acceleration >15% -> +10
EARNINGS_ACCEL_REVENUE_POINTS_HIGH = 10
EARNINGS_ACCEL_REVENUE_THRESHOLD_MID = 5.0     # 5-15% -> +5
EARNINGS_ACCEL_REVENUE_POINTS_MID = 5
EARNINGS_ACCELERATION_FLAG_THRESHOLD = 10   # flag fires when score > this

# ════════════════════════════════════════════════════════════════════════════
# CHART STUDY ADDITIONS — Trend Death (Distribution Detection) + OBV divergence
# Built from studying real winner charts (BEL, Bharat Forge, CAMS, MTAR, etc.)
# — see README for the qualitative analysis behind these two modules.
# Entirely additive: standalone scores, never folded into composite_score or
# EliteCompounderScore, consistent with every other phase in this project.
# ════════════════════════════════════════════════════════════════════════════

# --- Trend Death / Distribution Detection (mirror of Trend Birth) ---
# Tighter ceiling than Trend Birth's floor (-25%) — this is deliberately
# meant to catch the START of a top, while the stock is still relatively
# close to its highs, not stocks that have already broken down hard.
TREND_DEATH_PCT_FROM_HIGH_CEILING = -15.0
TREND_DEATH_SCORE_POINTS = 10

# --- OBV-Price Divergence (the CAMS-chart pattern) ---
OBV_DIVERGENCE_MIN_PULLBACK_PCT = -5.0    # need at least a 5% pullback for divergence to be meaningful
OBV_DIVERGENCE_BULLISH_THRESHOLD = 10.0   # percentage points of divergence needed to flag as bullish

# ════════════════════════════════════════════════════════════════════════════
# OBV LEADERSHIP RANK — added based on real backtest evidence (not chart
# reading): OBV proved to be the most consistently predictive signal in this
# system across both the 100-ticker and 300-ticker backtest runs. Smooths the
# binary OBV_52W_HIGH flag into a continuous 0-100 percentile rank of OBV
# momentum (blended 13w + 26w slope), so the system can distinguish "barely
# qualifies" from "genuinely strong accumulation" rather than treating every
# stock above the 52-week-high threshold identically.
# ════════════════════════════════════════════════════════════════════════════
OBV_LEADERSHIP_RANK_TOP_DECILE_THRESHOLD = 90   # flag fires above this percentile
OBV_LEADERS_TAB_TOP_N = 30                       # how many stocks the OBV_LEADERS tab shows

# ----------------------------------------------------------------------------
# PHASE 3 (Module 1): Earnings Acceleration tab
# ----------------------------------------------------------------------------
# Diagnostic run (diagnostics/earnings_accel_coverage_check.py, 19-ticker
# mixed NSE/US sample, 22-06-2026): 79% "ok", 21% "partial", 0% "missing" —
# coverage is good enough for a dedicated tab. Reminder: QoQ seasonality
# caveat is real, not theoretical — seasonal US retail names (TGT/BBY/DECK/TPR)
# showed large negative acceleration purely from the holiday quarter rolling
# off, not necessarily deteriorating fundamentals. See fundamentals.py.
EARNINGS_ACCELERATING_TAB_TOP_N = 30             # how many stocks the EARNINGS_ACCELERATING tab shows

# ════════════════════════════════════════════════════════════════════════════
# BACKTEST FRAMEWORK — see backtest.py module docstring for full design notes
# Deliberately conservative defaults — this is far more compute-intensive
# than the daily scan (every indicator recomputed at every snapshot date).
# Widen these only after confirming a smaller run completes in reasonable
# time. Run manually via backtest_workflow.yml, never as part of daily scan.
# ════════════════════════════════════════════════════════════════════════════
BACKTEST_UNIVERSE = "NSE500"          # "NSE500", "SP500", or "NSE_SmallMicro" — one at a time
# Widened 300->None (full NSE500, ~500 tickers) and 3y->5y lookback
# (26-06-2026), specifically for a genuine SECOND confirming run on the OBV
# Divergence Decaying signal — its first two "runs" turned out to be the
# exact same tickers/lookback/snapshot-dates re-computed, so they produced
# byte-identical results (33.08pp excess both times) and didn't actually
# confirm anything new, the same mistake this would have been if OBV
# Leadership's 100-ticker validation had just been re-run on the same 100
# tickers a second time instead of widening to 300. This mirrors that same
# widen-for-a-real-second-data-point pattern. Expect roughly 2.5-3x the
# runtime of the 300-ticker/3y run (~7m43s) — somewhere in the 20-25 minute
# range, still well within a manual GitHub Actions run.
BACKTEST_MAX_TICKERS = None           # None = full universe; widened from 300 for a genuinely different 2nd run, not just a re-run
BACKTEST_LOOKBACK_YEARS = 5           # how far back snapshot dates go; widened from 3 to also cover more market regimes, not just more names

# ----------------------------------------------------------------------------
# Date-range backtest mode — test a SPECIFIC historical window (e.g. a bear
# market) instead of "the last N years from today." Built 29-06-2026: every
# backtest run so far has been mostly bull-market-heavy, since
# BACKTEST_LOOKBACK_YEARS always counts back from TODAY — there was no way
# to target a fixed historical period like the COVID crash once enough time
# passed that it fell outside any reasonable "years back from today" window.
#
# When BACKTEST_DATE_RANGE_MODE is True, main() ignores BACKTEST_LOOKBACK_YEARS
# entirely and uses BACKTEST_DATE_RANGE_START/END instead — snapshot dates
# are generated within that fixed window, and price data is fetched via
# data_fetch.fetch_price_history_range()/fetch_index_history_range() (start=/
# end= based, NOT the relative period= used everywhere else) with an extra
# buffer BEFORE the start date for indicator warmup, same spirit as the
# existing "+2y buffer" already used in lookback-years mode.
#
# Two ready-made windows below (commented out, pick one and uncomment, or
# write your own) — chosen specifically for yfinance data-coverage
# practicality, not just historical significance: the 2008 GFC and 2000-02
# dot-com crash were considered and rejected as first targets, since most
# NSE500 constituents' yfinance history doesn't reliably reach that far back
# (the SAME survivorship-style data-depth issue already documented for the
# NSE_SmallMicro backtest elsewhere in this file applies here too, just on
# the TIME axis instead of the universe-membership axis).
BACKTEST_DATE_RANGE_MODE = True      # False = normal "N years back from today" mode (existing behavior, unchanged)
BACKTEST_DATE_RANGE_START = "2020-01-01"   # COVID crash window: captures the Jan 2020 peak, the Feb-Apr crash, and into the recovery
BACKTEST_DATE_RANGE_END = "2021-01-31"
# Alternative — 2015-16 correction (milder, slower bear phase, different
# character from COVID's sharp shock): BACKTEST_DATE_RANGE_START =
# "2015-03-01", BACKTEST_DATE_RANGE_END = "2017-03-31"
BACKTEST_DATE_RANGE_WARMUP_YEARS = 1.5  # extra history fetched BEFORE start date, for indicator warmup (200d OBV slope, 252-bar 52w-range, etc. all need real history before the first snapshot date, not just the window itself)

BACKTEST_SNAPSHOT_FREQ = "MS"         # "MS" = monthly (1st of month); "W" = weekly (much slower)
BACKTEST_MIN_HISTORY_DAYS = 300       # minimum days of price history needed before a date is usable
BACKTEST_HORIZONS_DAYS = {
    "1m": 21, "3m": 63, "6m": 126, "12m": 252,
}
BACKTEST_RESULTS_TAB_NAME = "Backtest_Results"
# Separate tab so a NSE_SmallMicro backtest run never overwrites NSE500/
# SP500 results (or vice versa) if you switch BACKTEST_UNIVERSE and re-run
# without remembering to rename anything.
BACKTEST_SMALLMICRO_RESULTS_TAB_NAME = "Backtest_Results_SmallMicro"

# ----------------------------------------------------------------------------
# INDICATOR PARAMETERS
# ----------------------------------------------------------------------------
OBV_SLOPE_SHORT_WINDOW = 20
OBV_SLOPE_LONG_WINDOW = 50
OBV_SLOPE_VERY_LONG_WINDOW = 200   # ~1y trading days; added to mirror the Pine Script dashboard's 3rd OBV slope window

# ----------------------------------------------------------------------------
# OBV Acceleration / Quiet Base — chart-study signal (25-06-2026), NOT
# statistically validated. See indicators.obv_acceleration_quiet_base()'s
# docstring for the full pattern this catches (built from reviewing
# Redington, RR Kabel, and HDFC AMC charts) and README for how it's wired
# in. Deliberately separate from composite_score/EliteCompounderScore/
# smallmicro_score — this is meant to flag candidates EARLIER than those
# scores typically do, at the cost of being unvalidated and presumably
# lower hit-rate. Both numbers below are UNVALIDATED starting defaults —
# no backtest exists for this signal yet — tune freely after seeing real
# output, same status as MIN_SALES_CAGR/MIN_ROCE/MIN_AVG_DAILY_TRADED_VALUE_INR.
OBV_ACCELERATION_RATIO_THRESHOLD = 2.0    # short-term OBV slope must be >= this many times the long-term baseline slope
OBV_ACCELERATION_PRICE_FLAT_BAND_PCT = 8.0  # price's own % change over the short window must stay within +/- this to count as "quiet"

# ----------------------------------------------------------------------------
# OBV Calm Continuation — RELABELED 26-06-2026 (was "OBV Divergence
# Decaying," built as a chart-study CAUTION signal). The original
# hypothesis was that sustained OBV deceleration while price keeps rising
# signals exhaustion. WRONG, per evidence: two independent NSE500 backtest
# runs (genuinely different ticker counts/lookback years, not a re-run of
# the same one) both showed this predicting STRONG POSITIVE excess return
# (+33.08pp and +33.78pp at 12m) — the opposite of the caution hypothesis.
# Real-data mechanism investigation (diagnostics/divergence_decaying_mechanism_check.py)
# found flagged stocks run calmer (mean atr_compression_percentile 72.4 vs
# 61.0 unflagged) AND already have stronger RS (15.42 vs 8.78) — but also
# found a real sector-concentration risk (Healthcare ~3.5x overrepresented
# in one live check). Relabeled as a bullish continuation signal because
# that's what the evidence says it predicts, but the mechanism is only
# PARTIALLY understood — see indicators.obv_calm_continuation()'s
# docstring for the full evidence trail and README for the
# sector-concentration caveat, which should travel with this signal
# wherever it's used. The mechanics below (constant names kept as
# OBV_DIVERGENCE_DECAY_* since they accurately describe HOW the signal is
# computed — sustained slope decay against a rolling high-water-mark —
# even though WHAT it predicts turned out to be the opposite of the
# original name) are UNCHANGED by the relabeling; only the interpretation
# and the function/column names that express that interpretation changed.
OBV_DIVERGENCE_DECAY_WINDOW = 42                      # ~2 months trading days — both the OBV-slope-history window and the price-change window
OBV_DIVERGENCE_DECAY_LOOKBACK_DAYS = 150               # how far back obv_slope_series() builds the slope trajectory
OBV_DIVERGENCE_DECAY_CONSECUTIVE_DAYS = 15             # decay must hold for (most of) this many consecutive trading days, not just today.
                                                        # 20 was tried first and found too long: even a textbook-clean,
                                                        # genuinely decaying slope only spends ~70% of a 20-day transition
                                                        # window already below the ratio threshold (the transition itself
                                                        # takes time) — 15 catches the pattern once it's mostly, not
                                                        # entirely, established. Verified directly against the same
                                                        # synthetic clean-decay case before settling on this number.
OBV_DIVERGENCE_DECAY_ROLLING_HIGH_WINDOW = 20          # the "recent high" each day is compared against is a ROLLING max over this many trailing days, not one fixed peak for the whole lookback
OBV_DIVERGENCE_DECAY_SLOPE_RATIO_THRESHOLD = 0.5      # current OBV slope must be at/below this fraction of its OWN rolling recent high, every day, for the full consecutive-day window
OBV_DIVERGENCE_DECAY_MIN_FRACTION_REQUIRED = 0.9      # at least this fraction (90% = 18 of 20 days) of the consecutive-day window must satisfy the ratio, not literally every single day — see indicators.obv_slope_sustained_decay()'s docstring for the brittleness bug this fixes
OBV_DIVERGENCE_DECAY_MIN_RECENT_HIGH_PCT = 0.3        # the rolling high itself must have cleared this throughout the consecutive-day window to count as a real peak worth decaying from
OBV_DIVERGENCE_DECAY_PRICE_RISING_THRESHOLD_PCT = 3.0  # price's % change over the same window must clear this to count as "still rising"

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

# ----------------------------------------------------------------------------
# US GARP COMPOSITE SCORE (Growth At a Reasonable Price) — separate from the
# NSE/base composite above. See README_US.md for the full design rationale.
# Growth leads (45%), Value and technical timing are guardrails/triggers,
# not hard gates — no disqualifying thresholds, by design (see README_US.md
# "No hard disqualifying gates initially" — backtest evidence decides that,
# not an assumption made upfront).
# ----------------------------------------------------------------------------
US_WEIGHT_GROWTH     = 45
US_WEIGHT_VALUE       = 25
US_WEIGHT_QUALITY     = 15
US_WEIGHT_TECHNICAL   = 15

assert US_WEIGHT_GROWTH + US_WEIGHT_VALUE + US_WEIGHT_QUALITY + US_WEIGHT_TECHNICAL == 100

# Growth sub-weights. Estimate-revisions data isn't built yet (see
# README_US.md build status) — its sub-weight is defined here so it's a
# one-line change to activate once that data source exists, but
# score_growth_us() currently redistributes it across the other two
# whenever revisions data is unavailable for a stock, rather than silently
# scoring that stock lower for a component nobody's data source provides yet.
US_GROWTH_SUBWEIGHT_SALES_CAGR   = 0.40
US_GROWTH_SUBWEIGHT_PROFIT_CAGR  = 0.35
US_GROWTH_SUBWEIGHT_REVISIONS    = 0.25   # inactive until estimate-revisions data is built

assert abs(
    US_GROWTH_SUBWEIGHT_SALES_CAGR + US_GROWTH_SUBWEIGHT_PROFIT_CAGR + US_GROWTH_SUBWEIGHT_REVISIONS - 1.0
) < 1e-9, "US Growth sub-weights must sum to 1.0"

# Value sub-weights. Both PEG and EV/EBITDA are "lower is better" — scored
# as inverted percentile ranks. EV/EBITDA is ranked WITHIN SECTOR (not
# against the whole universe) since "cheap" means different things in
# software vs. utilities — see score_value_us() in scoring_us.py.
US_VALUE_SUBWEIGHT_PEG        = 0.50
US_VALUE_SUBWEIGHT_EV_EBITDA  = 0.50

assert abs(US_VALUE_SUBWEIGHT_PEG + US_VALUE_SUBWEIGHT_EV_EBITDA - 1.0) < 1e-9

# Quality sub-weights
US_QUALITY_SUBWEIGHT_FCF_TREND       = 0.40
US_QUALITY_SUBWEIGHT_DEBT_EQUITY     = 0.30   # reuses the existing debt_equity field
US_QUALITY_SUBWEIGHT_MARGIN_TREND    = 0.30   # blends gross + operating margin trend equally

assert abs(
    US_QUALITY_SUBWEIGHT_FCF_TREND + US_QUALITY_SUBWEIGHT_DEBT_EQUITY + US_QUALITY_SUBWEIGHT_MARGIN_TREND - 1.0
) < 1e-9

# Technical timing sub-weights — deliberately simpler than the NSE Trend
# bucket (no fast Supertrend, no EMA20): this is a weeks-to-months hold, not
# a swing setup, so only higher-timeframe trend + leadership signals apply.
US_TECHNICAL_SUBWEIGHT_SUPERTREND_WEEKLY = 0.40
US_TECHNICAL_SUBWEIGHT_SUPERTREND_DAILY  = 0.30
US_TECHNICAL_SUBWEIGHT_RS                = 0.20
US_TECHNICAL_SUBWEIGHT_OBV               = 0.10

assert abs(
    US_TECHNICAL_SUBWEIGHT_SUPERTREND_WEEKLY + US_TECHNICAL_SUBWEIGHT_SUPERTREND_DAILY
    + US_TECHNICAL_SUBWEIGHT_RS + US_TECHNICAL_SUBWEIGHT_OBV - 1.0
) < 1e-9

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
    "my_portfolio": "My_Portfolio_Scored",
    "trend_birth": "TREND_BIRTH",
    "sector_leaders": "SECTOR_LEADERS",
    "trend_death": "TREND_DEATH",
    "obv_leaders": "OBV_LEADERS",
    "earnings_accelerating": "EARNINGS_ACCELERATING",
    # NSE Small/Micro-cap tier — deliberately separate from nse_full. Raw
    # indicators + fundamentals only, NO composite_score / EliteCompounderScore
    # (those formulas are tuned/backtested against NSE500+SP500 liquidity and
    # data-quality patterns — see README "NSE Small/Micro-cap tier" section).
    "nse_smallmicro_full": "NSE_SmallMicro_Full_Scan",
}

# ════════════════════════════════════════════════════════════════════════════
# DAILY NOTIFICATION (Telegram + Email) — three-section daily digest sent
# after the scan completes. Purely a presentation/delivery layer on top of
# already-computed columns; does not change any scoring logic.
#
#   Section 1 — Elite: EliteCompounderScore >= ELITE_NOTIFY_SCORE_THRESHOLD
#               (NSE500 + S&P500 only — backtested n=532 evidence for 65,
#               see README "Backtest Framework")
#   Section 2 — SmallMicro strict pass (smallmicro_strict_pass == True)
#   Section 3 — Fresh OBV+RS combo (NSE500 + S&P500 ONLY — SmallMicro has no
#               obv_leadership_rank/rs_rank by design, see main.py
#               process_universe docstring). Gated on both ranks > 90th
#               percentile, then split into three MUTUALLY EXCLUSIVE bands by
#               pct_from_52w_high (bands are [0,15), [15,25), [25,inf) — note
#               pct_from_52w_high is stored as a negative-or-zero number, so
#               "distance off high" = abs(pct_from_52w_high)).
#
# Delivery: Telegram Bot API (instant push) AND Gmail SMTP (backup/log),
# both sent from the same build_message() text. Either channel can fail
# independently without blocking the other or failing the scan — see
# notify.py send_daily_notification().
# ════════════════════════════════════════════════════════════════════════════
ELITE_NOTIFY_SCORE_THRESHOLD = 65

NOTIFY_COMBO_RANK_THRESHOLD = 90   # both obv_leadership_rank and rs_rank must exceed this

NOTIFY_BREAKOUT_BAND_PCT = 15.0     # Bucket A: 0-15% off 52w high
NOTIFY_CONFIRMED_BAND_PCT = 25.0    # Bucket B: 15-25% off 52w high
                                     # Bucket C: >25% off 52w high

NOTIFY_MAX_TICKERS_PER_SECTION = 15   # keeps message reasonably sized for both channels

# ----------------------------------------------------------------------------
# TRADE MODULE (trade.py + trade_scan.yml)
# ----------------------------------------------------------------------------
# Path to most recent NSE500 scan output CSV — trade.py reads this to build
# the qualified stock list. main.py writes here after each NSE500 scan.
TRADE_QUALIFIED_CSV_PATH = "cache/nse500_latest.csv"
TRADE_STATE_PATH = "cache/trade_state.json"

# SmallMicro latest scan output for MTF strategy
TRADE_SMALLMICRO_CSV_PATH = "cache/smallmicro_latest.csv"
