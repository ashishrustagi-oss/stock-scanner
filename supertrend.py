"""
Supertrend indicator calculator.

Computes Supertrend for any (period, multiplier) pair on any OHLCV DataFrame.
Used by trade.py for three variants:
  - Weekly ST(10, 3)  — trend filter, must be bullish before any entry
  - Daily  ST(10, 3)  — trend filter + exit trigger (crossover from above = exit)
  - Daily  ST(2,  1)  — entry trigger (crossover from below = entry)

All functions accept a pandas DataFrame with columns:
  open, high, low, close, volume  (lowercase)
and return a DataFrame with additional columns added in-place.

Supertrend algorithm:
  1. Compute ATR(period)
  2. Basic upper band = (high + low) / 2 + multiplier * ATR
  3. Basic lower band = (high + low) / 2 - multiplier * ATR
  4. Final bands are adjusted so they can only tighten, never widen,
     when the prior close is on the same side of the band
  5. Supertrend = lower band when bullish, upper band when bearish
  6. Direction flips when price crosses the active band

The crossover signals (what trade.py actually uses) are derived from
consecutive direction changes — see supertrend_signals() below.
"""

import numpy as np
import pandas as pd


def compute_atr(df: pd.DataFrame, period: int) -> pd.Series:
    """
    Wilder's smoothed ATR (same as TradingView's default ATR).
    Uses RMA (Wilder's moving average) not simple EMA.
    """
    high = df["high"]
    low  = df["low"]
    prev_close = df["close"].shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)

    # Wilder's RMA: first value is SMA, subsequent values use 1/period smoothing
    atr = tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return atr


def compute_supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0,
                       prefix: str = "") -> pd.DataFrame:
    """
    Adds Supertrend columns to df (in-place copy) and returns it.

    Added columns (prefix allows multiple ST variants in one DataFrame):
      {prefix}st_upper     — upper band
      {prefix}st_lower     — lower band
      {prefix}st_value     — active Supertrend line (the one price is tracking)
      {prefix}st_bullish   — True when trend is bullish (price above ST)

    Parameters
    ----------
    df          : OHLCV DataFrame, index should be datetime
    period      : ATR period (e.g. 10)
    multiplier  : ATR multiplier (e.g. 3.0)
    prefix      : Column name prefix, e.g. "st10_3_" to avoid collisions
                  when computing multiple variants on the same DataFrame
    """
    df = df.copy()
    atr = compute_atr(df, period)

    hl2 = (df["high"] + df["low"]) / 2
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    n = len(df)
    upper  = np.full(n, np.nan)
    lower  = np.full(n, np.nan)
    st     = np.full(n, np.nan)
    bull   = np.full(n, True, dtype=bool)

    close = df["close"].values
    bu    = basic_upper.values
    bl    = basic_lower.values

    for i in range(1, n):
        if np.isnan(bu[i]) or np.isnan(bl[i]):
            continue

        if np.isnan(upper[i-1]):
            # First bar with valid ATR data — there's no previous band to
            # tighten against yet, so seed the bands directly from the
            # basic (untightened) values instead of trying to compare
            # against NaN (which always evaluates False and would lock
            # upper/lower at NaN forever from this point on).
            upper[i] = bu[i]
            lower[i] = bl[i]
            bull[i]  = close[i] >= lower[i]
        else:
            # Upper band: only tighten (move down) if previous close was below it
            upper[i] = bu[i] if (bu[i] < upper[i-1] or close[i-1] > upper[i-1]) else upper[i-1]
            # Lower band: only tighten (move up) if previous close was above it
            lower[i] = bl[i] if (bl[i] > lower[i-1] or close[i-1] < lower[i-1]) else lower[i-1]

            # Direction
            if bull[i-1]:
                # Was bullish: stay bullish unless price drops below lower band
                bull[i] = close[i] >= lower[i]
            else:
                # Was bearish: flip to bullish if price rises above upper band
                bull[i] = close[i] > upper[i]

        st[i] = lower[i] if bull[i] else upper[i]

    df[f"{prefix}st_upper"]   = upper
    df[f"{prefix}st_lower"]   = lower
    df[f"{prefix}st_value"]   = st
    df[f"{prefix}st_bullish"] = bull.astype(bool)

    return df


def supertrend_signals(df: pd.DataFrame, prefix: str = "") -> pd.DataFrame:
    """
    Derives crossover signals from a DataFrame that already has ST columns
    (i.e. compute_supertrend() has already been called on it).

    Added columns:
      {prefix}st_cross_up   — True on the bar where direction flipped bullish
                              (price crossed above ST from below — ENTRY signal)
      {prefix}st_cross_down — True on the bar where direction flipped bearish
                              (price crossed below ST from above — EXIT signal)

    Only the most recent bar's signals matter for live trading decisions,
    but the full history is useful for backtesting.
    """
    bull_col = f"{prefix}st_bullish"
    prev_bull = df[bull_col].shift(1).fillna(False).astype(bool)

    df[f"{prefix}st_cross_up"]   = df[bull_col] & ~prev_bull   # False -> True
    df[f"{prefix}st_cross_down"] = ~df[bull_col] & prev_bull   # True -> False

    return df


def get_supertrend_state(df: pd.DataFrame, period: int, multiplier: float,
                         prefix: str = "") -> dict:
    """
    Convenience wrapper: computes ST on df and returns a summary dict
    about the most recent completed bar. Used by trade.py to check
    filter conditions and crossover signals without dealing with raw columns.

    Returns
    -------
    dict with keys:
      bullish       : bool  — is the most recent bar bullish?
      st_value      : float — current Supertrend line value
      cross_up      : bool  — did a bullish crossover just occur?
      cross_down    : bool  — did a bearish crossover just occur?
      bars_available: int   — number of bars computed (to flag insufficient data)
    """
    if len(df) < period + 5:
        return {
            "bullish": None, "st_value": None,
            "cross_up": False, "cross_down": False,
            "bars_available": len(df),
        }

    result = compute_supertrend(df, period=period, multiplier=multiplier, prefix=prefix)
    result = supertrend_signals(result, prefix=prefix)

    last = result.iloc[-1]
    return {
        "bullish":        bool(last[f"{prefix}st_bullish"]),
        "st_value":       float(last[f"{prefix}st_value"]) if not np.isnan(last[f"{prefix}st_value"]) else None,
        "cross_up":       bool(last[f"{prefix}st_cross_up"]),
        "cross_down":     bool(last[f"{prefix}st_cross_down"]),
        "bars_available": len(result),
    }
