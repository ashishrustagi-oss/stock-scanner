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
    monthly = ind.resample_monthly(df)

    daily_macd, daily_signal, daily_hist = ind.macd(close)
    weekly_macd_s, weekly_signal_s, weekly_hist_s = ind.macd(weekly["Close"])
    monthly_macd_s, monthly_signal_s, monthly_hist_s = ind.macd(monthly["Close"])

    weekly_hist_val = float(weekly_hist_s.iloc[-1]) if len(weekly_hist_s) else np.nan
    monthly_macd_val = float(monthly_macd_s.iloc[-1]) if len(monthly_macd_s) else np.nan
    monthly_signal_val = float(monthly_signal_s.iloc[-1]) if len(monthly_signal_s) else np.nan
    monthly_hist_val = float(monthly_hist_s.iloc[-1]) if len(monthly_hist_s) else np.nan

    st_slow_line, st_slow_dir   = ind.supertrend(df, **config.SUPERTREND_SLOW)
    st_fast_line, st_fast_dir   = ind.supertrend(df, **config.SUPERTREND_FAST)
    st_weekly_line, st_weekly_dir = ind.weekly_supertrend(df, **config.SUPERTREND_WEEKLY)

    ema10 = ind.ema(close, config.EMA10_PERIOD)
    ema20 = ind.ema(close, config.EMA_PERIOD)
    monthly_ema20 = ind.ema(monthly["Close"], config.MONTHLY_EMA_FAST)
    monthly_ema50 = ind.ema(monthly["Close"], config.MONTHLY_EMA_SLOW)
    monthly_ema20_val = float(monthly_ema20.iloc[-1]) if len(monthly_ema20) else np.nan
    monthly_ema50_val = float(monthly_ema50.iloc[-1]) if len(monthly_ema50) else np.nan

    # ── Elite Compounder Early Detection modules ──
    rs_nifty_series = ind.rs_ratio_series(close, index_close)
    rs_sector_series = ind.rs_ratio_series(close, sector_close)

    atr_comp_ratio = ind.atr_compression_ratio(df, config.ATR_PERIOD, config.VOLATILITY_COMPRESSION_LOOKBACK_DAYS)
    atr_comp_pctile = ind.atr_compression_percentile(df, config.ATR_PERIOD, config.VOLATILITY_COMPRESSION_LOOKBACK_DAYS)

    obv_slope_13w_val = ind.obv_slope(obv_series, config.WEEKS_13_IN_DAYS)
    obv_slope_26w_val = ind.obv_slope(obv_series, config.WEEKS_26_IN_DAYS)
    price_chg_13w = ind.price_pct_change(close, config.WEEKS_13_IN_DAYS)
    # Chart-study signal (NOT statistically validated, see indicators.py
    # docstring + README): short = ~13w/3mo OBV slope, long = ~26w/6mo
    # baseline, per your own chart review of Redington/RR Kabel/HDFC AMC —
    # you specified "2-3 months" for the short window, which maps to the
    # already-computed 13-week slope rather than the 20-day one originally
    # assumed; no new OBV windows needed, both already existed.
    obv_accel = ind.obv_acceleration_quiet_base(
        obv_slope_13w_val, obv_slope_26w_val, price_chg_13w,
        config.OBV_ACCELERATION_RATIO_THRESHOLD, config.OBV_ACCELERATION_PRICE_FLAT_BAND_PCT,
    )

    # Mirror-image CAUTION signal (chart-study, unvalidated — see
    # indicators.obv_divergence_decaying() and README): OBV's own rate of
    # accumulation already peaked and is decaying, even as price is still
    # rising. Second design — see indicators.py's "DESIGN NOTE" in
    # obv_divergence_decaying()'s docstring for why the first attempt
    # (peak-anchored obv_price_divergence history) was replaced.
    obv_slope_decay_window_val = ind.obv_slope(obv_series, config.OBV_DIVERGENCE_DECAY_WINDOW)
    obv_slope_history = ind.obv_slope_series(
        obv_series, config.OBV_DIVERGENCE_DECAY_WINDOW, config.OBV_DIVERGENCE_DECAY_LOOKBACK_DAYS,
    )
    obv_slope_recent_high_val = float(obv_slope_history.max()) if len(obv_slope_history) else np.nan
    price_chg_decay_window = ind.price_pct_change(close, config.OBV_DIVERGENCE_DECAY_WINDOW)
    obv_decay = ind.obv_divergence_decaying(
        obv_slope_decay_window_val, obv_slope_recent_high_val, price_chg_decay_window,
        config.OBV_DIVERGENCE_DECAY_SLOPE_RATIO_THRESHOLD, config.OBV_DIVERGENCE_DECAY_MIN_RECENT_HIGH_PCT,
        config.OBV_DIVERGENCE_DECAY_PRICE_RISING_THRESHOLD_PCT,
    )

    row = {
        "yf_ticker":              yf_ticker,
        "close":                  float(close.iloc[-1]),
        # ── OBV (original) ──
        "obv":                    float(obv_series.iloc[-1]),
        "obv_slope_20d":          ind.obv_slope(obv_series, config.OBV_SLOPE_SHORT_WINDOW),
        "obv_slope_50d":          ind.obv_slope(obv_series, config.OBV_SLOPE_LONG_WINDOW),
        "obv_slope_200d":         ind.obv_slope(obv_series, config.OBV_SLOPE_VERY_LONG_WINDOW),
        "obv_52w_range_pct":      ind.obv_52w_range_pct(obv_series),
        # ── OBV Leadership module ──
        "obv_52w_high":           ind.is_at_nbar_high(obv_series, config.WEEKS_52_IN_DAYS),
        "obv_26w_high":           ind.is_at_nbar_high(obv_series, config.WEEKS_26_IN_DAYS),
        "obv_slope_13w":          obv_slope_13w_val,
        "obv_slope_26w":          obv_slope_26w_val,
        "obv_price_divergence":   ind.obv_price_divergence(close, obv_series),
        # ── OBV Acceleration / Quiet Base (chart-study, unvalidated — see
        # indicators.obv_acceleration_quiet_base() and README) ──
        "price_chg_13w":          price_chg_13w,
        "obv_acceleration_quiet_base": "🟢" if obv_accel["qualifies"] else "",
        "obv_acceleration_basis": obv_accel["basis"],
        # ── OBV Divergence Decaying (chart-study CAUTION signal,
        # unvalidated — see indicators.obv_divergence_decaying() and
        # README). Mirror-image of the acceleration flag above: 🔴, not
        # 🟢, matching the same convention Trend Death uses to be
        # visually distinct as a warning rather than an opportunity. ──
        "obv_slope_42d":                 obv_slope_decay_window_val,
        "obv_slope_42d_recent_high":     obv_slope_recent_high_val,
        "price_chg_42d":                 price_chg_decay_window,
        "obv_divergence_decaying":       "🔴" if obv_decay["qualifies"] else "",
        "obv_divergence_decay_basis":    obv_decay["basis"],
        # ── Liquidity (built for the NSE Small/Micro-cap tier's score gate —
        # see scoring.py compute_smallmicro_score; NSE500/SP500 never needed
        # this since every constituent there is liquid by default) ──
        "avg_daily_traded_value": ind.avg_daily_traded_value(df, config.LIQUIDITY_LOOKBACK_DAYS),
        # ── Daily MACD (original) ──
        "daily_macd":             float(daily_macd.iloc[-1]),
        "daily_signal":           float(daily_signal.iloc[-1]),
        "daily_hist":             float(daily_hist.iloc[-1]),
        # ── Early MACD module ──
        "macd_early_bullish":     ind.macd_early_bullish(daily_macd, daily_signal, config.MACD_EARLY_LOOKBACK_DAYS),
        # ── Trend Death / Distribution Detection module (mirror of Early MACD) ──
        "macd_early_bearish":     ind.macd_early_bearish(daily_macd, daily_signal, config.MACD_EARLY_LOOKBACK_DAYS),
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
        # ── Monthly Trend Confirmation module (Phase 1, Module 5) ──
        "monthly_macd":           monthly_macd_val,
        "monthly_signal":         monthly_signal_val,
        "monthly_hist":           monthly_hist_val,
        "monthly_ema20":          monthly_ema20_val,
        "monthly_ema50":          monthly_ema50_val,
        "monthly_bullish": (
            1.0 if (
                not np.isnan(monthly_macd_val) and not np.isnan(monthly_signal_val)
                and not np.isnan(monthly_ema20_val) and not np.isnan(monthly_ema50_val)
                and monthly_macd_val > monthly_signal_val
                and monthly_ema20_val > monthly_ema50_val
            ) else (np.nan if (np.isnan(monthly_macd_val) or np.isnan(monthly_ema20_val)) else 0.0)
        ),
    }
    return row
