"""
Composite scoring.

Each indicator bucket is converted to a 0-100 sub-score via cross-sectional
percentile rank WITHIN its own universe (NSE500 ranked against NSE500, S&P500
against S&P500). This is what makes "OBV slope" (a tiny number) and "RS score"
(a %-points number) comparable and combinable — we care about a stock's
*relative* standing on each metric, not its raw value.

Composite = 0.30*OBV + 0.20*WeeklyMACD + 0.15*DailyMACD + 0.20*Trend + 0.15*RS
"""

import numpy as np
import pandas as pd

import config


def _pct_rank(series: pd.Series) -> pd.Series:
    """0-100 percentile rank, NaNs preserved as NaN (not penalized or rewarded)."""
    return series.rank(pct=True, na_option="keep") * 100


def score_obv(df: pd.DataFrame) -> pd.Series:
    """Blends the 20d and 50d OBV slope ranks equally."""
    r20 = _pct_rank(df["obv_slope_20d"])
    r50 = _pct_rank(df["obv_slope_50d"])
    return (r20 + r50) / 2


def _macd_state_score(macd_val: pd.Series, signal_val: pd.Series, hist_val: pd.Series) -> pd.Series:
    """
    Raw directional score per stock before cross-sectional ranking:
    bullish crossover (MACD > signal) plus rising histogram momentum.
    We rank the histogram value itself cross-sectionally, which captures
    both "how bullish" and "how strongly accelerating" in one ranked metric.
    """
    return hist_val + (macd_val - signal_val)  # combine state + momentum into one raw scalar


def score_macd(df: pd.DataFrame, prefix: str) -> pd.Series:
    raw = _macd_state_score(df[f"{prefix}_macd"], df[f"{prefix}_signal"], df[f"{prefix}_hist"])
    return _pct_rank(raw)


def score_trend(df: pd.DataFrame) -> pd.Series:
    st_slow = (df["supertrend_10_3_dir"] + 1) / 2 * 100   # -1/1 -> 0/100
    st_fast = (df["supertrend_2_1_dir"] + 1) / 2 * 100
    above_ema = (df["close"] > df["ema20"]).astype(float) * 100
    return (
        config.TREND_SUBWEIGHT_SUPERTREND_SLOW * st_slow
        + config.TREND_SUBWEIGHT_SUPERTREND_FAST * st_fast
        + config.TREND_SUBWEIGHT_EMA20 * above_ema
    )


def score_relative_strength(df: pd.DataFrame) -> pd.Series:
    return _pct_rank(df["rs_score"])


def compute_composite(df: pd.DataFrame) -> pd.DataFrame:
    """
    Takes the raw per-ticker metrics DataFrame (one universe at a time) and
    appends sub-score and composite_score columns.
    """
    df = df.copy()
    df["score_obv"] = score_obv(df)
    df["score_macd_weekly"] = score_macd(df, "weekly")
    df["score_macd_daily"] = score_macd(df, "daily")
    df["score_trend"] = score_trend(df)
    df["score_rs"] = score_relative_strength(df)

    df["composite_score"] = (
        config.WEIGHT_OBV * df["score_obv"]
        + config.WEIGHT_MACD_WEEKLY * df["score_macd_weekly"]
        + config.WEIGHT_MACD_DAILY * df["score_macd_daily"]
        + config.WEIGHT_TREND * df["score_trend"]
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
