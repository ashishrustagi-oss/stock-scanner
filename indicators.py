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


def resample_monthly(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calendar-month resampling for the Monthly Trend Confirmation module.
    With 5 years of daily history this gives ~60 monthly bars — enough for
    a reasonably stable monthly EMA50, though still less converged than a
    multi-decade history would give; treat monthly EMA50 as directionally
    useful rather than perfectly precise.
    """
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    monthly = df.resample("ME").agg(agg).dropna(how="all")
    return monthly


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
# 52-week high distance + near-high flag
# ----------------------------------------------------------------------------
def pct_from_52w_high(close: pd.Series) -> float:
    window = close.dropna().iloc[-252:]
    if window.empty:
        return np.nan
    high_52w = window.max()
    last = window.iloc[-1]
    return float((last - high_52w) / high_52w * 100)


def near_52w_high(close: pd.Series, threshold_pct: float = 10.0) -> float:
    """
    Returns 1.0 if the latest close is within `threshold_pct`% of the
    52-week high, 0.0 otherwise. Used as a 0/100 binary in the trend score.
    """
    dist = pct_from_52w_high(close)
    if np.isnan(dist):
        return np.nan
    return 1.0 if dist >= -threshold_pct else 0.0


# ----------------------------------------------------------------------------
# OBV 52-week range position
# ----------------------------------------------------------------------------
def obv_52w_range_pct(obv_series: pd.Series) -> float:
    """
    Where is the current OBV value within its own 52-week (252-bar) range?
    Returns 0-100:  100 = OBV is at its 52-week high (maximum accumulation),
                      0 = OBV is at its 52-week low.
    This is the volume equivalent of asking 'is price near its 52-week high?'
    A high reading means buying pressure over the past year is at peak levels.
    """
    window = obv_series.dropna().iloc[-252:]
    if len(window) < 50:          # need enough history to be meaningful
        return np.nan
    lo, hi = window.min(), window.max()
    rng = hi - lo
    if rng == 0 or np.isnan(rng):
        return np.nan
    return float((window.iloc[-1] - lo) / rng * 100)


# ----------------------------------------------------------------------------
# Weekly Supertrend
# ----------------------------------------------------------------------------
def weekly_supertrend(df: pd.DataFrame, period: int, multiplier: float):
    """
    Computes Supertrend on weekly-resampled candles.
    Returns (st_line, direction) on the weekly timeframe.
    Direction 1 = weekly bullish, -1 = weekly bearish.
    """
    weekly = resample_weekly(df)
    if len(weekly) < period + 5:
        empty = pd.Series(dtype="float64")
        return empty, empty
    return supertrend(weekly, period=period, multiplier=multiplier)


# ----------------------------------------------------------------------------
# Weekly MACD positive flag
# ----------------------------------------------------------------------------
def weekly_macd_positive(weekly_hist: float) -> float:
    """
    Returns 1.0 if the weekly MACD histogram is positive (bullish momentum),
    0.0 if negative, NaN if unavailable.
    Kept as a separate explicit flag so it shows clearly in the output sheet.
    """
    if weekly_hist is None or np.isnan(weekly_hist):
        return np.nan
    return 1.0 if weekly_hist > 0 else 0.0


# ════════════════════════════════════════════════════════════════════════════
# ELITE COMPOUNDER EARLY DETECTION MODULES
# Designed to flag institutional accumulation and leadership BEFORE trend
# confirmation tools (Supertrend, weekly MACD positive) catch up — i.e. the
# accumulation/early-transition phase, not the breakout-confirmed phase.
# ════════════════════════════════════════════════════════════════════════════

# ----------------------------------------------------------------------------
# Generic "is the series at an N-bar high" check — used for both OBV and RS
# ----------------------------------------------------------------------------
def is_at_nbar_high(series: pd.Series, window: int) -> float:
    """
    Returns 1.0 if the latest value is the maximum of the trailing `window`
    bars (i.e. a fresh N-bar high, including ties), 0.0 otherwise, NaN if
    insufficient history. Used for OBV_52W_HIGH, OBV_26W_HIGH,
    RS_NIFTY_52W_HIGH, RS_SECTOR_52W_HIGH.
    """
    recent = series.dropna().iloc[-window:]
    if len(recent) < min(window, 50):   # require at least 50 bars of real history
        return np.nan
    return 1.0 if recent.iloc[-1] >= recent.max() else 0.0


# ----------------------------------------------------------------------------
# Relative Strength series (stock / benchmark ratio) + leadership flags
# ----------------------------------------------------------------------------
def rs_ratio_series(stock_close: pd.Series, bench_close: pd.Series) -> pd.Series:
    """Aligned stock/benchmark close-price ratio, the raw RS line."""
    aligned = pd.concat([stock_close, bench_close], axis=1, join="inner").dropna()
    aligned.columns = ["stock", "bench"]
    if aligned.empty:
        return pd.Series(dtype="float64")
    return aligned["stock"] / aligned["bench"]


def rs_pct_change(rs_series: pd.Series, window: int) -> float:
    """% change in the RS ratio over the trailing `window` bars."""
    s = rs_series.dropna()
    if len(s) < window + 1:
        return np.nan
    old, new = s.iloc[-window - 1], s.iloc[-1]
    if old == 0 or np.isnan(old):
        return np.nan
    return float((new / old - 1) * 100)


# ----------------------------------------------------------------------------
# Early MACD bullish crossover (still below the zero line — the early phase)
# ----------------------------------------------------------------------------
def macd_early_bullish(macd_line: pd.Series, signal_line: pd.Series, lookback_days: int = 3) -> float:
    """
    True if: MACD is currently above Signal AND MACD is still below zero
    AND a bullish crossover (MACD going from <=Signal to >Signal) occurred
    within the trailing `lookback_days` bars.

    This is the classic 'early' signal — momentum turning up before the
    MACD line has even crossed zero, which is well before a standard
    'MACD bullish' screen (MACD>0) would catch it.
    """
    if len(macd_line) < lookback_days + 2 or len(signal_line) < lookback_days + 2:
        return np.nan
    diff = (macd_line - signal_line).dropna()
    if len(diff) < lookback_days + 2:
        return np.nan
    recent = diff.iloc[-(lookback_days + 1):]
    now_bullish = recent.iloc[-1] > 0
    crossed_recently = (recent.iloc[:-1] <= 0).any()
    still_below_zero = macd_line.dropna().iloc[-1] < 0
    return 1.0 if (now_bullish and crossed_recently and still_below_zero) else 0.0


# ----------------------------------------------------------------------------
# Volatility compression (ATR ratio + range compression)
# ----------------------------------------------------------------------------
def atr_compression_ratio(df: pd.DataFrame, atr_period: int, lookback_days: int) -> float:
    """Current ATR divided by its own trailing N-day average. <1 = below-average volatility."""
    atr_series = atr(df, atr_period).dropna()
    if len(atr_series) < lookback_days:
        return np.nan
    window = atr_series.iloc[-lookback_days:]
    avg = window.mean()
    if avg == 0 or np.isnan(avg):
        return np.nan
    return float(atr_series.iloc[-1] / avg)


def atr_compression_percentile(df: pd.DataFrame, atr_period: int, lookback_days: int) -> float:
    """
    Where does TODAY's ATR-compression ratio rank within the stock's own
    trailing `lookback_days` history of that ratio? Returns 0-100.
    Low values (e.g. <=25) mean volatility is unusually compressed relative
    to the stock's own recent past — the classic pre-breakout signature.
    """
    atr_series = atr(df, atr_period).dropna()
    if len(atr_series) < lookback_days + 20:
        return np.nan
    rolling_avg = atr_series.rolling(lookback_days).mean()
    ratio_series = (atr_series / rolling_avg).dropna()
    window = ratio_series.iloc[-lookback_days:]
    if len(window) < 50:
        return np.nan
    current = window.iloc[-1]
    return float((window <= current).mean() * 100)


def range_compression_ratio(df: pd.DataFrame) -> float:
    """
    (13-week trading range) / (52-week trading range). Lower = the recent
    13 weeks have traded in a much narrower band than the full year —
    a contracting-range signature often preceding a directional breakout.
    """
    close = df["Close"].dropna()
    high = df["High"].dropna()
    low = df["Low"].dropna()
    if len(close) < 252:
        return np.nan
    high_52w, low_52w = high.iloc[-252:].max(), low.iloc[-252:].min()
    high_13w, low_13w = high.iloc[-65:].max(), low.iloc[-65:].min()
    range_52w = high_52w - low_52w
    range_13w = high_13w - low_13w
    if range_52w == 0 or np.isnan(range_52w):
        return np.nan
    return float(range_13w / range_52w)


# ----------------------------------------------------------------------------
# Early EMA structure (EMA10 > EMA20 AND EMA20 sloping up)
# ----------------------------------------------------------------------------
def ema_slope_positive(ema_series: pd.Series, window: int) -> float:
    """1.0 if the linear-regression slope of the EMA over the trailing window is positive."""
    recent = ema_series.dropna().iloc[-window:]
    if len(recent) < window:
        return np.nan
    x = np.arange(len(recent))
    slope, _ = np.polyfit(x, recent.values, 1)
    return 1.0 if slope > 0 else 0.0


def early_ema_alignment(ema10: pd.Series, ema20: pd.Series, slope_window: int) -> float:
    """1.0 if EMA10 > EMA20 (short-term leading) AND EMA20's own slope is positive."""
    if ema10.dropna().empty or ema20.dropna().empty:
        return np.nan
    ema10_above = ema10.iloc[-1] > ema20.iloc[-1]
    ema20_slope_up = ema_slope_positive(ema20, slope_window)
    if np.isnan(ema20_slope_up):
        return np.nan
    return 1.0 if (ema10_above and ema20_slope_up == 1.0) else 0.0
