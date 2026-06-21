"""
Composite scoring.

Each indicator bucket is converted to a 0-100 sub-score via cross-sectional
percentile rank WITHIN its own universe (NSE500 ranked against NSE500, S&P500
against S&P500). This is what makes "OBV slope" (a tiny number) and "RS score"
(a %-points number) comparable and combinable — we care about a stock's
*relative* standing on each metric, not its raw value.

Composite = 0.30*OBV + 0.20*WeeklyMACD + 0.15*DailyMACD + 0.20*Trend + 0.15*RS

OBV bucket now blends:   slope_20d (35%) + slope_50d (30%) + OBV 52w range (35%)
Trend bucket now blends: ST_slow_daily (30%) + ST_fast_daily (10%) + EMA20 (15%)
                         + ST_weekly (30%) + near_52w_high (15%)
Weekly MACD bucket now:  ranked histogram (60%) + positive_flag (40%)
"""

import numpy as np
import pandas as pd

import config


def _pct_rank(series: pd.Series) -> pd.Series:
    """0-100 percentile rank, NaNs preserved as NaN (not penalized or rewarded)."""
    return series.rank(pct=True, na_option="keep") * 100


def score_obv(df: pd.DataFrame) -> pd.Series:
    """
    Blends three OBV signals:
      - slope 20d  (35%): short-term accumulation momentum
      - slope 50d  (30%): medium-term accumulation trend
      - 52w range  (35%): is OBV near its 52-week high? (raw 0-100, no re-ranking needed)
    """
    r20  = _pct_rank(df["obv_slope_20d"])
    r50  = _pct_rank(df["obv_slope_50d"])
    r52w = df["obv_52w_range_pct"]          # already 0-100 by construction

    return (
        config.OBV_SUBWEIGHT_SLOPE_20D  * r20
        + config.OBV_SUBWEIGHT_SLOPE_50D  * r50
        + config.OBV_SUBWEIGHT_52W_RANGE  * r52w
    )


def _macd_state_score(macd_val: pd.Series, signal_val: pd.Series, hist_val: pd.Series) -> pd.Series:
    """
    Raw directional score per stock before cross-sectional ranking:
    bullish crossover (MACD > signal) plus rising histogram momentum.
    """
    return hist_val + (macd_val - signal_val)


def score_macd_daily(df: pd.DataFrame) -> pd.Series:
    """Daily MACD: unchanged — ranked histogram+crossover state."""
    raw = _macd_state_score(df["daily_macd"], df["daily_signal"], df["daily_hist"])
    return _pct_rank(raw)


def score_macd_weekly(df: pd.DataFrame) -> pd.Series:
    """
    Weekly MACD bucket (60% ranked magnitude + 40% positive binary flag).
    The positive flag explicitly rewards stocks where weekly MACD is above
    zero, regardless of how far above — higher-timeframe bullish confirmation.
    """
    ranked   = _pct_rank(_macd_state_score(df["weekly_macd"], df["weekly_signal"], df["weekly_hist"]))
    positive = df["weekly_macd_positive"] * 100   # 0 or 100

    return (
        config.MACD_WEEKLY_SUBWEIGHT_RANKED   * ranked
        + config.MACD_WEEKLY_SUBWEIGHT_POSITIVE * positive
    )


def score_trend(df: pd.DataFrame) -> pd.Series:
    """
    Trend bucket now uses five components:
      daily ST slow (30%) + daily ST fast (10%) + EMA20 (15%)
      + weekly ST (30%) + near 52w high (15%)
    All components produce 0 or 100 — no cross-sectional ranking needed
    because they are binary directional states.
    """
    st_slow   = (df["supertrend_10_3_dir"] + 1) / 2 * 100      # -1/1 → 0/100
    st_fast   = (df["supertrend_2_1_dir"]  + 1) / 2 * 100
    above_ema = (df["close"] > df["ema20"]).astype(float) * 100
    st_weekly = (df["supertrend_weekly_dir"] + 1) / 2 * 100     # NEW
    near_high = df["near_52w_high"] * 100                        # NEW: 0 or 100

    return (
        config.TREND_SUBWEIGHT_SUPERTREND_SLOW    * st_slow
        + config.TREND_SUBWEIGHT_SUPERTREND_FAST    * st_fast
        + config.TREND_SUBWEIGHT_EMA20              * above_ema
        + config.TREND_SUBWEIGHT_SUPERTREND_WEEKLY  * st_weekly
        + config.TREND_SUBWEIGHT_NEAR_52W_HIGH      * near_high
    )


def score_relative_strength(df: pd.DataFrame) -> pd.Series:
    return _pct_rank(df["rs_score"])


def compute_composite(df: pd.DataFrame) -> pd.DataFrame:
    """
    Takes the raw per-ticker metrics DataFrame (one universe at a time) and
    appends sub-score and composite_score columns.
    """
    df = df.copy()
    df["score_obv"]          = score_obv(df)
    df["score_macd_weekly"]  = score_macd_weekly(df)
    df["score_macd_daily"]   = score_macd_daily(df)
    df["score_trend"]        = score_trend(df)
    df["score_rs"]           = score_relative_strength(df)

    df["composite_score"] = (
        config.WEIGHT_OBV               * df["score_obv"]
        + config.WEIGHT_MACD_WEEKLY     * df["score_macd_weekly"]
        + config.WEIGHT_MACD_DAILY      * df["score_macd_daily"]
        + config.WEIGHT_TREND           * df["score_trend"]
        + config.WEIGHT_RELATIVE_STRENGTH * df["score_rs"]
    ) / 100

    return df


def categorize(score: float, fundamentally_qualified: bool) -> str:
    if pd.isna(score):
        return "Insufficient Data"
    if score < config.EXIT_THRESHOLD:
        return "Exit Candidate"
    if fundamentally_qualified and score >= config.ELITE_THRESHOLD:
        return "Elite Compounder"
    if fundamentally_qualified and config.EMERGING_THRESHOLD_LOW <= score < config.EMERGING_THRESHOLD_HIGH:
        return "Emerging Compounder"
    return "Watch"


# ════════════════════════════════════════════════════════════════════════════
# ELITE COMPOUNDER EARLY DETECTION SCORING
# Entirely additive — does not alter compute_composite() or categorize() above.
# These functions populate a separate EliteCompounderScore (0-100) designed to
# surface institutional accumulation and leadership BEFORE Supertrend-style
# trend-confirmation tools would flag the same stock.
# ════════════════════════════════════════════════════════════════════════════

def score_obv_leadership(df: pd.DataFrame) -> pd.Series:
    """
    Max 20: OBV_52W_HIGH (10) + OBV rising 13w (5) + OBV rising 26w (5).
    Each component is a 0/1 flag already computed upstream; this just
    applies the point values and sums them.
    """
    p52 = df["obv_52w_high"].fillna(0) * config.ELITE_OBV_POINTS_52W_HIGH
    p13 = (df["obv_slope_13w"] > 0).astype(float) * config.ELITE_OBV_POINTS_13W_RISING
    p26 = (df["obv_slope_26w"] > 0).astype(float) * config.ELITE_OBV_POINTS_26W_RISING
    # NaN-safety: if the underlying flag is NaN, don't silently score it as 0 —
    # propagate NaN so it's visible as "insufficient data" rather than "failed".
    mask_nan = df["obv_52w_high"].isna()
    total = p52 + p13 + p26
    total[mask_nan] = np.nan
    return total


def score_rs_leadership(df: pd.DataFrame) -> pd.Series:
    """
    Max 20: RS 52w-high (10) + RS rising 13w (5) + RS rising 26w (5).
    Each component blends the Nifty/SPX-relative and Sector-relative version
    of the signal 50/50 — full marks require BOTH benchmarks to agree, which
    is a stronger leadership confirmation than either alone.
    """
    def blend(nifty_flag, sector_flag, points):
        a = nifty_flag.fillna(0)
        b = sector_flag.fillna(0)
        return points * 0.5 * (a + b)

    p52 = blend(df["rs_nifty_52w_high"], df["rs_sector_52w_high"], config.ELITE_RS_POINTS_52W_HIGH)
    p13 = blend(
        (df["rs_nifty_chg_13w"] > 0).astype(float), (df["rs_sector_chg_13w"] > 0).astype(float),
        config.ELITE_RS_POINTS_13W_RISING,
    )
    p26 = blend(
        (df["rs_nifty_chg_26w"] > 0).astype(float), (df["rs_sector_chg_26w"] > 0).astype(float),
        config.ELITE_RS_POINTS_26W_RISING,
    )
    total = p52 + p13 + p26
    mask_nan = df["rs_nifty_52w_high"].isna() & df["rs_sector_52w_high"].isna()
    total[mask_nan] = np.nan
    return total


def score_macd_early(df: pd.DataFrame) -> pd.Series:
    """Max 10: flat points if MACD_EARLY_BULLISH is True."""
    return df["macd_early_bullish"] * config.ELITE_WEIGHT_MACD_EARLY


def score_ema_alignment_elite(df: pd.DataFrame) -> pd.Series:
    """Max 5: flat points if EARLY_EMA_ALIGNMENT is True."""
    return df["early_ema_alignment"] * config.ELITE_WEIGHT_EMA_ALIGNMENT


def score_volatility_compression(df: pd.DataFrame) -> pd.Series:
    """Max 10: flat points if VOLATILITY_COMPRESSION is True."""
    return df["volatility_compression"] * config.ELITE_WEIGHT_VOLATILITY_COMPRESSION


def score_supertrend_elite(df: pd.DataFrame) -> pd.Series:
    """
    Max 10: reuses the existing daily Supertrend(10,3) and (2,1) direction
    flags from the base system, rescaled into the Elite Score's 10-point
    budget for this bucket (slow weighted higher as the primary filter).
    """
    st_slow = (df["supertrend_10_3_dir"] + 1) / 2   # -1/1 -> 0/1
    st_fast = (df["supertrend_2_1_dir"] + 1) / 2
    return (
        config.ELITE_SUPERTREND_SUBWEIGHT_SLOW * st_slow
        + config.ELITE_SUPERTREND_SUBWEIGHT_FAST * st_fast
    ) * config.ELITE_WEIGHT_SUPERTREND


def score_weekly_macd_elite(df: pd.DataFrame) -> pd.Series:
    """
    Max 10: rescales the existing `score_macd_weekly` (already 0-100 from
    the base composite system) into this bucket's 10-point budget.
    Requires compute_composite() to have already run on this DataFrame.
    """
    return df["score_macd_weekly"] / 100 * config.ELITE_WEIGHT_WEEKLY_MACD


def score_above_ema20_elite(df: pd.DataFrame) -> pd.Series:
    """Max 5: flat points if price is above EMA20."""
    return (df["close"] > df["ema20"]).astype(float) * config.ELITE_WEIGHT_ABOVE_EMA20


def score_fundamentals_elite(df: pd.DataFrame) -> pd.Series:
    """Max 10: flat points if the stock passes the existing fundamental qualifying filter."""
    return df["fundamentally_qualified"].astype(float) * config.ELITE_WEIGHT_FUNDAMENTALS


def compute_elite_compounder_score(df: pd.DataFrame) -> pd.DataFrame:
    """
    Appends EliteCompounderScore (0-100) and elite_category to the DataFrame.
    Must be called AFTER compute_composite() (reuses score_macd_weekly) and
    AFTER all early-detection indicator columns have been merged in.
    """
    df = df.copy()
    df["elite_score_obv"]            = score_obv_leadership(df)
    df["elite_score_rs"]             = score_rs_leadership(df)
    df["elite_score_macd_early"]     = score_macd_early(df)
    df["elite_score_ema_alignment"]  = score_ema_alignment_elite(df)
    df["elite_score_compression"]    = score_volatility_compression(df)
    df["elite_score_supertrend"]     = score_supertrend_elite(df)
    df["elite_score_weekly_macd"]    = score_weekly_macd_elite(df)
    df["elite_score_above_ema20"]    = score_above_ema20_elite(df)
    df["elite_score_fundamentals"]   = score_fundamentals_elite(df)

    df["EliteCompounderScore"] = (
        df["elite_score_obv"].fillna(0)
        + df["elite_score_rs"].fillna(0)
        + df["elite_score_macd_early"].fillna(0)
        + df["elite_score_ema_alignment"].fillna(0)
        + df["elite_score_compression"].fillna(0)
        + df["elite_score_supertrend"].fillna(0)
        + df["elite_score_weekly_macd"].fillna(0)
        + df["elite_score_above_ema20"].fillna(0)
        + df["elite_score_fundamentals"].fillna(0)
    )

    df["elite_category"] = df["EliteCompounderScore"].apply(categorize_elite)

    # Visual flags for quick scanning in the sheet — 🟢 if true, blank if false/unknown
    def flag(col):
        return df[col].apply(lambda v: "🟢" if v == 1.0 else "")

    df["flag_obv_leader"]      = flag("obv_52w_high")
    df["flag_rs_leader"]       = df.apply(
        lambda r: "🟢" if (r.get("rs_nifty_52w_high") == 1.0 or r.get("rs_sector_52w_high") == 1.0) else "",
        axis=1,
    )
    df["flag_early_macd"]      = flag("macd_early_bullish")
    df["flag_compression"]     = flag("volatility_compression")
    df["flag_ema_alignment"]   = flag("early_ema_alignment")
    df["flag_near_breakout"]   = flag("near_breakout_15pct")

    return df


def categorize_elite(score: float) -> str:
    if pd.isna(score):
        return "Insufficient Data"
    if score > config.ELITE_CATEGORY_A_THRESHOLD:
        return "Category A: Elite Compounder"
    if config.ELITE_CATEGORY_B_LOW <= score <= config.ELITE_CATEGORY_B_HIGH:
        return "Category B: Emerging Leader"
    if config.ELITE_CATEGORY_C_LOW <= score < config.ELITE_CATEGORY_C_HIGH:
        return "Category C: Watchlist"
    return "Below Watchlist"
