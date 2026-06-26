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

    KNOWN ISSUE FOUND AND FIXED (24-06-2026): mean(abs(OBV)) breaks down as a
    normalizer specifically when OBV crosses zero within the window — values
    near the crossing point pull the mean-abs denominator down toward zero
    while the regression slope itself can still be steep (since OBV genuinely
    swung from negative to positive), producing an inflated, misleading
    percentage. Confirmed on real data: IDEA's 50-day window crossed from
    -4.48B to +4.19B, producing 7.11% from this formula alone, when a
    manual check of the same real OBV values suggested the true magnitude
    of the move was closer to 1-2%. The 20-day window over the same period
    never crossed zero and was unaffected (1.47%, matched expectations).

    Fix: when the window's OBV values span across zero (min and max have
    opposite signs), fall back to normalizing by the window's own RANGE
    (max - min) instead of mean(abs(...)) — range stays meaningful even
    when values cross zero, unlike a denominator built from magnitudes that
    average toward zero near the crossing point. Deliberately a FALLBACK,
    not a wholesale replacement: every other (non-crossing) window continues
    using the original mean-abs normalizer exactly as before, so this only
    changes behavior for the specific case that was actually broken — it
    does not change every stock's existing slope numbers, only the ones
    that were unreliable in the first place.
    """
    recent = obv_series.dropna().iloc[-window:]
    if len(recent) < window:
        return np.nan
    x = np.arange(len(recent))
    slope, _intercept = np.polyfit(x, recent.values, 1)

    crosses_zero = recent.min() < 0 < recent.max()
    if crosses_zero:
        rng = recent.max() - recent.min()
        if rng == 0 or np.isnan(rng):
            return np.nan
        return float(slope / rng * 100)

    scale = np.abs(recent).mean()
    if scale == 0 or np.isnan(scale):
        return np.nan
    return float(slope / scale * 100)


def obv_slope_series(obv_series: pd.Series, window: int, lookback_days: int) -> pd.Series:
    """
    Rolling version of obv_slope() above: computes what obv_slope(window)
    WOULD HAVE BEEN as-of every day in the trailing `lookback_days`,
    returning the full trajectory rather than a single current value.

    Built for the OBV Divergence Decaying signal below, which needs to
    know whether OBV's slope is CURRENTLY below its own recent
    high-water-mark — i.e. whether the rate of accumulation has already
    peaked and is now fading, per the chart-study pattern: "OBV peaks
    first, then price catches up and makes its own peak, price keeps
    rising (often sharply), but OBV's own slope is already declining
    underneath that price rise." That's fundamentally a question about
    obv_slope's OWN trajectory over time, not a single snapshot — same
    reason obv_acceleration_quiet_base needed two different slope WINDOWS
    (13w vs 26w) rather than one; this needs the same window's slope
    measured at multiple POINTS IN TIME instead.

    Uses the exact same zero-crossing-aware normalization as obv_slope()
    at every step, so the rolling trajectory is consistent with the
    single-point function elsewhere in this codebase, not a simplified
    approximation of it.
    """
    obv_clean = obv_series.dropna()
    if len(obv_clean) < window:
        return pd.Series(dtype=float)

    n_points = min(lookback_days, len(obv_clean) - window + 1)
    results = {}
    for i in range(n_points):
        # i=0 is TODAY (the full series); i=1 is one bar earlier; etc.
        end = len(obv_clean) - i
        recent = obv_clean.iloc[end - window:end]
        if len(recent) < window:
            continue
        x = np.arange(len(recent))
        slope, _intercept = np.polyfit(x, recent.values, 1)
        crosses_zero = recent.min() < 0 < recent.max()
        if crosses_zero:
            rng = recent.max() - recent.min()
            val = slope / rng * 100 if rng != 0 and not np.isnan(rng) else np.nan
        else:
            scale = np.abs(recent).mean()
            val = slope / scale * 100 if scale != 0 and not np.isnan(scale) else np.nan
        results[recent.index[-1]] = val

    return pd.Series(results).sort_index()


def obv_slope_sustained_decay(
    obv_slope_history: pd.Series, consecutive_days: int, rolling_high_window: int,
    decay_ratio_threshold: float, min_recent_high_pct: float, min_fraction_required: float = 0.9,
) -> dict:
    """
    Checks whether OBV's slope has been SUSTAINED below its own rolling
    recent high-water-mark for `consecutive_days` in a row, rather than
    just on the single most recent day.

    DESIGN NOTE (25-06-2026) — the first version of obv_divergence_decaying
    compared today's slope to a single FIXED peak across the whole
    lookback window (e.g. "today's slope <= 0.5x the highest slope reached
    at any point in the last 150 days"). Tested directly against a
    realistic synthetic universe before trusting it, and found NOT
    selective: ~80% of ALL stocks satisfied that condition at any given
    moment, almost regardless of whether anything was actually
    decelerating — OBV slope is naturally noisy and dips well below its
    own historical peak constantly just from normal bar-to-bar variation,
    so a single-point comparison against an old, possibly-stale peak
    barely discriminates anything. Tightening the ratio threshold alone
    didn't fix this either — even comparing against slope <= 0, fully
    51% of stocks still qualified.

    Fixed by changing what's being compared, not just the threshold: each
    day's slope is compared against a SHORT, ROLLING high (the max of the
    trailing `rolling_high_window` days of slope history, recomputed at
    every point — not one fixed historical peak for the whole period), and
    the ratio must hold for MOST of `consecutive_days` in a row (governed
    by `min_fraction_required`, default 90%), not just today.

    A SECOND bug was found and fixed at this same step: an initial version
    required ALL `consecutive_days` to individually satisfy the ratio with
    no tolerance. Tested directly against a clean, textbook synthetic
    decay (slope falling steadily and linearly for 100 days straight) —
    and it FAILED to flag it, because exactly one day at the edge of the
    20-day window sat at ratio 0.560 (barely above the 0.5 threshold) while
    every other day in that window was comfortably below it. A single
    borderline day broke an otherwise obvious, clean decay pattern — too
    brittle for real use. min_fraction_required tolerates that kind of
    noise: at least 90% of the days (18 of 20, by default) must satisfy
    the ratio, not literally all of them.

    Confirmed directly against the same realistic synthetic universe used
    to find the original problem — requiring (with this tolerance) 20
    consecutive days mostly below 0.5x a 20-day rolling high still cuts
    the false-positive rate dramatically versus the single-point version,
    while no longer breaking on the textbook clean-decay case that exposed
    the all-or-nothing bug.

    Returns a dict (same diagnostic-transparency pattern as elsewhere):
      - "sustained_decay": True/False
      - "current_ratio": today's slope / today's rolling high (for context,
        even when the sustained check fails)
      - "had_a_real_peak": whether the rolling high ever cleared
        min_recent_high_pct within the checked window — same purpose as
        obv_divergence_decaying's existing gate, applied here at the
        rolling level instead of the single-fixed-peak level
    """
    if len(obv_slope_history) < consecutive_days + rolling_high_window:
        return {"sustained_decay": False, "current_ratio": np.nan, "had_a_real_peak": False}

    rolling_high = obv_slope_history.rolling(rolling_high_window).max()
    ratio_series = obv_slope_history / rolling_high

    last_n_ratio = ratio_series.iloc[-consecutive_days:]
    last_n_high = rolling_high.iloc[-consecutive_days:]

    current_ratio = float(last_n_ratio.iloc[-1]) if not last_n_ratio.empty else np.nan
    min_days_required = int(np.ceil(consecutive_days * min_fraction_required))

    # Same fraction-based tolerance applied to BOTH checks below — requiring
    # literally every day (.all()) is exactly the brittleness that broke a
    # textbook clean decay case in testing (see docstring); a single
    # borderline day shouldn't invalidate an otherwise clear, sustained pattern.
    had_a_real_peak = bool((last_n_high >= min_recent_high_pct).sum() >= min_days_required) if not last_n_high.empty else False

    if last_n_ratio.isna().any() or not had_a_real_peak:
        return {"sustained_decay": False, "current_ratio": current_ratio, "had_a_real_peak": had_a_real_peak}

    sustained_decay = bool((last_n_ratio <= decay_ratio_threshold).sum() >= min_days_required)
    return {"sustained_decay": sustained_decay, "current_ratio": current_ratio, "had_a_real_peak": had_a_real_peak}


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


def avg_daily_traded_value(df: pd.DataFrame, window_days: int = 20) -> float:
    """
    Average daily traded value (price * volume) over the trailing
    `window_days`, in rupees (or whatever currency the price series is in).

    Built specifically for the NSE Small/Micro-cap tier's liquidity gate
    (see scoring.py SmallMicroScore) — NSE500/SP500 never needed this
    because every constituent there is liquid enough that it's a non-issue.
    A small/microcap stock can show a great MACD crossover or OBV slope on
    a handful of thinly-traded days that mean nothing tradeable; this
    metric exists to catch that before any technical signal is trusted.

    Returns NaN if there's not enough history to compute a meaningful
    average (mirrors the no-data handling pattern used elsewhere in this
    module, e.g. obv_52w_range_pct above).
    """
    if "Close" not in df.columns or "Volume" not in df.columns:
        return np.nan
    window = df[["Close", "Volume"]].dropna().iloc[-window_days:]
    if len(window) < max(5, window_days // 4):   # need at least a handful of real trading days
        return np.nan
    traded_value = window["Close"] * window["Volume"]
    return float(traded_value.mean())


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


def price_pct_change(close: pd.Series, window: int) -> float:
    """
    % change in raw closing price over the trailing `window` bars. Same
    shape as rs_pct_change() above, just on price instead of the RS ratio —
    built for obv_acceleration_quiet_base() below, which needs a plain
    "has price actually moved yet" check, not a vs-index comparison.
    """
    s = close.dropna()
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


def macd_early_bearish(macd_line: pd.Series, signal_line: pd.Series, lookback_days: int = 3) -> float:
    """
    Mirror image of macd_early_bullish, for the Trend Death / Distribution
    Detection module: True if MACD just crossed BELOW Signal while MACD is
    still ABOVE zero — the early topping signal, symmetric to the
    early-bottoming signal used for Trend Birth.
    """
    if len(macd_line) < lookback_days + 2 or len(signal_line) < lookback_days + 2:
        return np.nan
    diff = (macd_line - signal_line).dropna()
    if len(diff) < lookback_days + 2:
        return np.nan
    recent = diff.iloc[-(lookback_days + 1):]
    now_bearish = recent.iloc[-1] < 0
    crossed_recently = (recent.iloc[:-1] >= 0).any()
    still_above_zero = macd_line.dropna().iloc[-1] > 0
    return 1.0 if (now_bearish and crossed_recently and still_above_zero) else 0.0


def obv_price_divergence(close: pd.Series, obv_series: pd.Series, window: int = 252) -> float:
    """
    Finds the most recent peak in price within the trailing `window`, then
    compares how much OBV has fallen since that peak to how much price has
    fallen since that peak.

    Positive = bullish divergence: OBV held up better than price during the
    pullback (the CAMS-chart pattern — buyers didn't actually leave even
    though price dropped). Negative or near zero = OBV confirms the price
    decline, no supportive divergence. Returned in percentage points
    (obv_decline_pct - price_decline_pct).
    """
    price_window = close.dropna().iloc[-window:]
    if len(price_window) < 50:
        return np.nan
    peak_date = price_window.idxmax()
    peak_price = price_window.loc[peak_date]
    current_price = price_window.iloc[-1]
    if peak_price == 0 or np.isnan(peak_price):
        return np.nan
    price_decline_pct = (current_price - peak_price) / peak_price * 100

    obv_aligned = obv_series.reindex(price_window.index, method="ffill")
    if peak_date not in obv_aligned.index or pd.isna(obv_aligned.loc[peak_date]):
        return np.nan
    peak_obv = obv_aligned.loc[peak_date]
    current_obv = obv_aligned.iloc[-1]
    if peak_obv == 0 or np.isnan(peak_obv):
        return np.nan
    obv_decline_pct = (current_obv - peak_obv) / abs(peak_obv) * 100

    return float(obv_decline_pct - price_decline_pct)


def obv_acceleration_quiet_base(
    obv_slope_short: float, obv_slope_long: float, price_chg_short: float,
    accel_ratio_threshold: float, price_flat_band_pct: float,
) -> dict:
    """
    Chart-study-derived signal (NOT statistically validated — same epistemic
    status as obv_price_divergence/Trend Death; see README): catches the
    pattern visible on real charts (Redington, RR Kabel, HDFC AMC, per
    24-06-2026 chart review) where OBV has been quietly, steadily rising
    while price chops sideways or drifts down — and the tell that price is
    about to catch up is a further ACCELERATION in that already-rising OBV
    slope, not just OBV being positive. By the time composite_score/
    smallmicro_score are at their highest, this move has usually already
    happened — those are backward-looking confirmations of strength
    already accumulated, not predictions of strength about to begin. This
    signal is deliberately built to be early instead, at the cost of being
    unvalidated and presumably lower hit-rate / lower risk-reward than the
    validated scores — that tradeoff is the whole point, not an oversight.

    Two conditions tracked, only the FIRST gates `qualifies` (see
    "REDESIGN" note below for why the second was dropped as a gate):
      1. ACCELERATION (this alone now drives `qualifies`): short-term OBV
         slope (intended as ~13-week/3-month, e.g. obv_slope_13w) is at
         least `accel_ratio_threshold`x the long-term baseline slope
         (intended as ~26-week/6-month, e.g. obv_slope_26w) — accumulation
         speeding up relative to its own recent history, not just "OBV is
         rising." Sign-aware: a meaningfully-more-positive short slope
         counts; a short slope that's LESS negative than a negative long
         slope (i.e. selling pressure easing) is treated as a weaker,
         separate case — flagged distinctly below rather than conflated
         with genuine acceleration.
      2. QUIET BASE (tracked, reported in `basis`, but NO LONGER required):
         price hasn't moved much yet over the same recent window
         (price_chg_short) — within `price_flat_band_pct` of flat.

    REDESIGN (26-06-2026): originally both conditions were required. A
    real backtest (NSE500, two runs — 24-06-2026 and 26-06-2026) showed
    the compound signal UNDERPERFORMING the acceleration condition tested
    alone both times (e.g. 2nd run: compound +17.88pp 12m excess vs.
    `obv_accel_subcondition_only` alone +22.31pp, n=967) — the quiet-price
    requirement was actively filtering OUT some of the strongest setups,
    not adding selectivity that helped. Dropped as a gate; acceleration
    alone now determines `qualifies`. `price_chg_short`/`is_quiet` are
    still computed and reported in `basis` for context, since "did price
    already move" is still useful information to see on the sheet, even
    though it no longer decides the flag.

    Returns a dict (not a single bool) so the caller/sheet can distinguish
    WHY a stock is or isn't flagged, the same diagnostic-transparency
    pattern used in smallmicro_strict_fail_reasons:
      - "qualifies": True/False — now driven by acceleration alone
      - "basis": one of "accelerating_quiet_base" (accelerating AND price
        was quiet — the original, narrower pattern), "accelerating_but_price_moved"
        (accelerating, price already moved — STILL qualifies now, unlike
        before the redesign), "quiet_but_not_accelerating" (price quiet,
        but not accelerating — does not qualify), "neither", or
        "insufficient_data" (any input was NaN)
    """
    if any(pd.isna(v) for v in (obv_slope_short, obv_slope_long, price_chg_short)):
        return {"qualifies": False, "basis": "insufficient_data"}

    # Sign-aware acceleration check. Using abs() on a SAME-SIGN pair is the
    # straightforward "is short steeper than long" case (positive vs
    # positive: genuinely accelerating upward accumulation — the chart-study
    # pattern). A short slope that's positive while long is negative (or
    # zero) is the clearest, strongest form of this and is naturally caught
    # by the same ratio test once long is small/negative. A short slope
    # that's MORE negative than long (selling accelerating) should NOT
    # qualify — guarded against explicitly, since a naive abs()-ratio could
    # otherwise flag accelerating SELLING as if it were the bullish pattern.
    if obv_slope_short <= 0:
        is_accelerating = False
    elif obv_slope_long <= 0:
        # Short slope positive, long slope flat/negative — short clearing a
        # near-zero-or-negative baseline counts as accelerating outright.
        is_accelerating = True
    else:
        is_accelerating = (obv_slope_short / obv_slope_long) >= accel_ratio_threshold

    is_quiet = abs(price_chg_short) <= price_flat_band_pct

    if is_accelerating and is_quiet:
        basis = "accelerating_quiet_base"
    elif is_accelerating:
        basis = "accelerating_but_price_moved"
    elif is_quiet:
        basis = "quiet_but_not_accelerating"
    else:
        basis = "neither"

    # qualifies now depends ONLY on is_accelerating — see REDESIGN note above.
    return {"qualifies": is_accelerating, "basis": basis}


def obv_divergence_decaying(
    sustained_decay: bool, had_a_real_peak: bool, price_chg_window: float,
    price_rising_threshold_pct: float,
) -> dict:
    """
    Chart-study-derived signal (NOT statistically validated — same
    epistemic status as obv_acceleration_quiet_base/Trend Death/
    obv_price_divergence; see README): the mirror-image caution flag to
    obv_acceleration_quiet_base above. The exact sequence this is built to
    catch (per chart review, 25-06-2026): OBV's own rate of accumulation
    PEAKS FIRST; price then catches up and makes its own peak, often
    rising sharply from there; but underneath that price strength, OBV's
    slope is ALREADY declining from its earlier high — the engine that
    drove the move is fading while the move itself is still visibly
    happening on the price chart.

    DESIGN HISTORY — two prior approaches were tried and replaced, in
    order, each found NOT selective enough when tested directly against
    synthetic data rather than assumed to work:
      1st: anchored to obv_price_divergence's 52-week-peak reference —
        dominated by cumulative effect since an increasingly-distant
        peak, barely responded to recent dynamics, ~always true.
      2nd: single-point comparison of today's slope to ONE fixed peak
        across the whole lookback window — ~80% of all stocks satisfied
        this at any moment, OBV slope is just naturally noisy.
      3rd (current): see obv_slope_sustained_decay() — requires the decay
        to be SUSTAINED across many consecutive days against a ROLLING
        (not fixed) high-water-mark. Verified to cut the false-positive
        rate to ~7% on synthetic data before being wired in here.

    Two conditions, BOTH required:
      1. sustained_decay is True — see obv_slope_sustained_decay(): OBV's
         slope has been below `config.OBV_DIVERGENCE_DECAY_SLOPE_RATIO_THRESHOLD`
         of its own rolling recent high for
         `config.OBV_DIVERGENCE_DECAY_CONSECUTIVE_DAYS` consecutive days
         in a row, not just today. had_a_real_peak (also from that
         function) gates out stocks whose OBV slope was never
         meaningfully positive to begin with — nothing to have decayed FROM.
      2. Price is still positive (rising, or has just peaked) over a
         comparable recent window — `price_chg_window` >=
         `price_rising_threshold_pct`. This is specifically a caution flag
         for stocks that LOOK fine (price still climbing) while the
         underlying volume support has already started fading — not for
         stocks that have already started falling, which is a different,
         more obvious problem this signal isn't trying to catch early.

    Returns a dict, same diagnostic-transparency pattern as
    obv_acceleration_quiet_base and smallmicro_strict_fail_reasons:
      - "qualifies": True/False
      - "basis": "divergence_decaying" (both met), "no_peak_to_decay_from"
        (OBV's slope was never meaningfully positive — nothing to fade
        from), "obv_still_strong" (price rising, but OBV hasn't shown
        sustained decay), "price_not_rising" (OBV has sustained-decayed,
        but price isn't rising — not the pattern this flag is for)
    """
    if not had_a_real_peak:
        return {"qualifies": False, "basis": "no_peak_to_decay_from"}

    price_rising = price_chg_window >= price_rising_threshold_pct if not pd.isna(price_chg_window) else False

    if sustained_decay and price_rising:
        basis = "divergence_decaying"
    elif price_rising:
        basis = "obv_still_strong"
    else:
        basis = "price_not_rising"

    return {"qualifies": sustained_decay and price_rising, "basis": basis}


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
