"""
Shared per-ticker indicator computation, used by both main.py (the universe
scan) and portfolio.py (personal holdings) so neither has to import the
other. This is exactly the same logic that used to live inline in main.py —
extracted here, not changed.
"""

import numpy as np
import pandas as pd

import config
import indicators as ind


def bool_flag(value, threshold, le=True):
    """NaN-safe boolean-as-float (1.0/0.0/nan) threshold check."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return np.nan
    return 1.0 if (value <= threshold if le else value >= threshold) else 0.0


def build_metrics_row(
    yf_ticker: str, df: pd.DataFrame, index_close: pd.Series,
    sector_close: pd.Series, sector_source: str,
) -> dict:
    """Computes every technical indicator for one ticker and returns a flat dict."""
    close = df["Close"]

    obv_series = ind.obv(df)
    weekly = ind.resample_weekly(df)

    daily_macd, daily_signal, daily_hist = ind.macd(close)
    weekly_macd_s, weekly_signal_s, weekly_hist_s = ind.macd(weekly["Close"])

    weekly_hist_val = float(weekly_hist_s.iloc[-1]) if len(weekly_hist_s) else np.nan

    st_slow_line, st_slow_dir   = ind.supertrend(df, **config.SUPERTREND_SLOW)
    st_fast_line, st_fast_dir   = ind.supertrend(df, **config.SUPERTREND_FAST)
    st_weekly_line, st_weekly_dir = ind.weekly_supertrend(df, **config.SUPERTREND_WEEKLY)

    ema10 = ind.ema(close, config.EMA10_PERIOD)
    ema20 = ind.ema(close, config.EMA_PERIOD)

    # ── Elite Compounder Early Detection modules ──
    rs_nifty_series = ind.rs_ratio_series(close, index_close)
    rs_sector_series = ind.rs_ratio_series(close, sector_close)

    atr_comp_ratio = ind.atr_compression_ratio(df, config.ATR_PERIOD, config.VOLATILITY_COMPRESSION_LOOKBACK_DAYS)
    atr_comp_pctile = ind.atr_compression_percentile(df, config.ATR_PERIOD, config.VOLATILITY_COMPRESSION_LOOKBACK_DAYS)

    row = {
        "yf_ticker":              yf_ticker,
        "close":                  float(close.iloc[-1]),
        # ── OBV (original) ──
        "obv":                    float(obv_series.iloc[-1]),
        "obv_slope_20d":          ind.obv_slope(obv_series, config.OBV_SLOPE_SHORT_WINDOW),
        "obv_slope_50d":          ind.obv_slope(obv_series, config.OBV_SLOPE_LONG_WINDOW),
        "obv_52w_range_pct":      ind.obv_52w_range_pct(obv_series),
        # ── OBV Leadership module ──
        "obv_52w_high":           ind.is_at_nbar_high(obv_series, config.WEEKS_52_IN_DAYS),
        "obv_26w_high":           ind.is_at_nbar_high(obv_series, config.WEEKS_26_IN_DAYS),
        "obv_slope_13w":          ind.obv_slope(obv_series, config.WEEKS_13_IN_DAYS),
        "obv_slope_26w":          ind.obv_slope(obv_series, config.WEEKS_26_IN_DAYS),
        # ── Daily MACD (original) ──
        "daily_macd":             float(daily_macd.iloc[-1]),
        "daily_signal":           float(daily_signal.iloc[-1]),
        "daily_hist":             float(daily_hist.iloc[-1]),
        # ── Early MACD module ──
        "macd_early_bullish":     ind.macd_early_bullish(daily_macd, daily_signal, config.MACD_EARLY_LOOKBACK_DAYS),
        # ── Weekly MACD (original) ──
        "weekly_macd":            float(weekly_macd_s.iloc[-1]) if len(weekly_macd_s) else np.nan,
        "weekly_signal":          float(weekly_signal_s.iloc[-1]) if len(weekly_signal_s) else np.nan,
        "weekly_hist":            weekly_hist_val,
        "weekly_macd_positive":   ind.weekly_macd_positive(weekly_hist_val),
        # ── Daily Supertrend (original) ──
        "supertrend_10_3_value":  float(st_slow_line.iloc[-1]),
        "supertrend_10_3_dir":    float(st_slow_dir.iloc[-1]),
        "supertrend_2_1_value":   float(st_fast_line.iloc[-1]),
        "supertrend_2_1_dir":     float(st_fast_dir.iloc[-1]),
        # ── Weekly Supertrend (original) ──
        "supertrend_weekly_dir":  float(st_weekly_dir.iloc[-1]) if len(st_weekly_dir) else np.nan,
        "supertrend_weekly_value":float(st_weekly_line.iloc[-1]) if len(st_weekly_line) else np.nan,
        # ── EMA (original EMA20 + EMA10 / early alignment) ──
        "ema10":                  float(ema10.iloc[-1]),
        "ema20":                  float(ema20.iloc[-1]),
        "early_ema_alignment":    ind.early_ema_alignment(ema10, ema20, config.EMA20_SLOPE_WINDOW),
        # ── Relative Strength — original blended-outperformance metric ──
        "rs_score":               ind.relative_strength_score(close, index_close),
        # ── RS Leadership module — vs Nifty/SPX and vs Sector ──
        "rs_nifty_52w_high":      ind.is_at_nbar_high(rs_nifty_series, config.WEEKS_52_IN_DAYS),
        "rs_nifty_chg_13w":       ind.rs_pct_change(rs_nifty_series, config.WEEKS_13_IN_DAYS),
        "rs_nifty_chg_26w":       ind.rs_pct_change(rs_nifty_series, config.WEEKS_26_IN_DAYS),
        "rs_sector_52w_high":     ind.is_at_nbar_high(rs_sector_series, config.WEEKS_52_IN_DAYS),
        "rs_sector_chg_13w":      ind.rs_pct_change(rs_sector_series, config.WEEKS_13_IN_DAYS),
        "rs_sector_chg_26w":      ind.rs_pct_change(rs_sector_series, config.WEEKS_26_IN_DAYS),
        "sector_index_source":    sector_source,
        # ── 52-week high metrics (original + near-breakout at 15%) ──
        "pct_from_52w_high":      ind.pct_from_52w_high(close),
        "near_52w_high":          ind.near_52w_high(close, config.NEAR_52W_HIGH_THRESHOLD_PCT),
        "near_breakout_15pct":    ind.near_52w_high(close, config.NEAR_BREAKOUT_THRESHOLD_PCT),
        # ── Volatility Compression module ──
        "atr_compression_ratio":      atr_comp_ratio,
        "atr_compression_percentile": atr_comp_pctile,
        "range_compression_ratio":    ind.range_compression_ratio(df),
        "volatility_compression":     bool_flag(
            atr_comp_pctile, config.VOLATILITY_COMPRESSION_PERCENTILE_THRESHOLD, le=True
        ),
    }
    return row
