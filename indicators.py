"""
Technical indicators, implemented directly (no third-party TA library) so the
exact formula is visible and auditable, and so nothing breaks on a library
version bump.

All functions take/return pandas Series or DataFrames indexed by date.
"""

import numpy as np
import pandas as pd

import config


# ----------------------------------------------------------------------------
# OBV
# ----------------------------------------------------------------------------
def obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["Close"].diff()).fillna(0)
    return (direction * df["Volume"]).cumsum()


def obv_slope(obv_series: pd.Series, window: int) -> float:
    """
    Linear-regression slope of OBV over the last `window` bars, normalized by
    the mean absolute OBV level over that window so it's comparable across
    stocks of wildly different volume scale. Returned as a %-per-day figure.
    """
    recent = obv_series.dropna().iloc[-window:]
    if len(recent) < window:
        return np.nan
    x = np.arange(len(recent))
    slope, _intercept = np.polyfit(x, recent.values, 1)
    scale = np.abs(recent).mean()
    if scale == 0 or np.isnan(scale):
        return np.nan
    return float(slope / scale * 100)


# ----------------------------------------------------------------------------
# EMA / MACD
# ----------------------------------------------------------------------------
def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def macd(series: pd.Series, fast=None, slow=None, signal=None):
    fast = fast or config.MACD_FAST
    slow = slow or config.MACD_SLOW
    signal = signal or config.MACD_SIGNAL
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def resample_weekly(df: pd.DataFrame) -> pd.DataFrame:
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    weekly = df.resample("W-FRI").agg(agg).dropna(how="all")
    return weekly


# ----------------------------------------------------------------------------
# ATR / Supertrend
# ----------------------------------------------------------------------------
def atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def supertrend(df: pd.DataFrame, period: int, multiplier: float):
    """
    Standard Supertrend. Returns (supertrend_line, direction) where
    direction == 1 means bullish (price above the band) and -1 bearish.
    """
    hl2 = (df["High"] + df["Low"]) / 2
    atr_val = atr(df, period)
    upper_basic = hl2 + multiplier * atr_val
    lower_basic = hl2 - multiplier * atr_val

    upper = upper_basic.copy()
    lower = lower_basic.copy()
    close = df["Close"]

    for i in range(1, len(df)):
        if close.iloc[i - 1] > upper.iloc[i - 1]:
            upper.iloc[i] = upper_basic.iloc[i]
        else:
            upper.iloc[i] = min(upper_basic.iloc[i], upper.iloc[i - 1])

        if close.iloc[i - 1] < lower.iloc[i - 1]:
            lower.iloc[i] = lower_basic.iloc[i]
        else:
            lower.iloc[i] = max(lower_basic.iloc[i], lower.iloc[i - 1])

    direction = pd.Series(index=df.index, dtype="float64")
    st_line = pd.Series(index=df.index, dtype="float64")
    direction.iloc[0] = 1
    st_line.iloc[0] = lower.iloc[0]

    for i in range(1, len(df)):
        if close.iloc[i] > upper.iloc[i - 1]:
            direction.iloc[i] = 1
        elif close.iloc[i] < lower.iloc[i - 1]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[i - 1]
            # tighten the active band toward price once flipped
            if direction.iloc[i] == 1 and lower.iloc[i] < lower.iloc[i - 1]:
                lower.iloc[i] = lower.iloc[i - 1]
            if direction.iloc[i] == -1 and upper.iloc[i] > upper.iloc[i - 1]:
                upper.iloc[i] = upper.iloc[i - 1]

        st_line.iloc[i] = lower.iloc[i] if direction.iloc[i] == 1 else upper.iloc[i]

    return st_line, direction


# ----------------------------------------------------------------------------
# Relative Strength vs benchmark
# ----------------------------------------------------------------------------
def relative_strength_score(stock_close: pd.Series, index_close: pd.Series, lookbacks=None) -> float:
    """
    Blended outperformance of the stock vs the benchmark index across several
    lookback windows. Returns the average %-points of outperformance
    (stock return minus index return, in percent) across the lookbacks.
    Positive = outperforming, negative = underperforming.
    """
    lookbacks = lookbacks or config.RS_LOOKBACKS
    aligned = pd.concat([stock_close, index_close], axis=1, join="inner").dropna()
    aligned.columns = ["stock", "index"]
    if len(aligned) < max(lookbacks) + 1:
        lookbacks = [lb for lb in lookbacks if lb < len(aligned)]
        if not lookbacks:
            return np.nan

    outperf = []
    for lb in lookbacks:
        stock_ret = aligned["stock"].iloc[-1] / aligned["stock"].iloc[-lb - 1] - 1
        index_ret = aligned["index"].iloc[-1] / aligned["index"].iloc[-lb - 1] - 1
        outperf.append((stock_ret - index_ret) * 100)
    return float(np.mean(outperf))


# ----------------------------------------------------------------------------
# 52-week high distance
# ----------------------------------------------------------------------------
def pct_from_52w_high(close: pd.Series) -> float:
    window = close.dropna().iloc[-252:]
    if window.empty:
        return np.nan
    high_52w = window.max()
    last = window.iloc[-1]
    return float((last - high_52w) / high_52w * 100)
