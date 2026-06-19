"""
Orchestrates a full daily scan:
  1. Load NSE500 + S&P500 universes
  2. Fetch price history (+ benchmark index + sector benchmark history) for each
  3. Compute all technical indicators per ticker, including the Elite Compounder
     Early Detection modules (RS leadership, OBV leadership, early MACD,
     volatility compression, early EMA structure, near-breakout)
  4. Fetch/merge fundamentals, apply qualifying filter
  5. Compute the original composite score AND the new EliteCompounderScore
  6. Build output tabs (original + new) and push to Google Sheets

Run with: python main.py
"""

import datetime
import logging
import sys

import numpy as np
import pandas as pd

import config
import data_fetch
import fundamentals as fnd
import indicators as ind
import scoring as sc
import sector_data
import sheets_export
import universe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("main")


def _bool_flag(value, threshold, le=True):
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
        # ── OBV Leadership module (NEW) ──
        "obv_52w_high":           ind.is_at_nbar_high(obv_series, config.WEEKS_52_IN_DAYS),
        "obv_26w_high":           ind.is_at_nbar_high(obv_series, config.WEEKS_26_IN_DAYS),
        "obv_slope_13w":          ind.obv_slope(obv_series, config.WEEKS_13_IN_DAYS),
        "obv_slope_26w":          ind.obv_slope(obv_series, config.WEEKS_26_IN_DAYS),
        # ── Daily MACD (original) ──
        "daily_macd":             float(daily_macd.iloc[-1]),
        "daily_signal":           float(daily_signal.iloc[-1]),
        "daily_hist":             float(daily_hist.iloc[-1]),
        # ── Early MACD module (NEW) ──
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
        # ── EMA (original EMA20 + NEW EMA10 / early alignment) ──
        "ema10":                  float(ema10.iloc[-1]),
        "ema20":                  float(ema20.iloc[-1]),
        "early_ema_alignment":    ind.early_ema_alignment(ema10, ema20, config.EMA20_SLOPE_WINDOW),
        # ── Relative Strength — original blended-outperformance metric ──
        "rs_score":               ind.relative_strength_score(close, index_close),
        # ── RS Leadership module (NEW) — vs Nifty/SPX and vs Sector ──
        "rs_nifty_52w_high":      ind.is_at_nbar_high(rs_nifty_series, config.WEEKS_52_IN_DAYS),
        "rs_nifty_chg_13w":       ind.rs_pct_change(rs_nifty_series, config.WEEKS_13_IN_DAYS),
        "rs_nifty_chg_26w":       ind.rs_pct_change(rs_nifty_series, config.WEEKS_26_IN_DAYS),
        "rs_sector_52w_high":     ind.is_at_nbar_high(rs_sector_series, config.WEEKS_52_IN_DAYS),
        "rs_sector_chg_13w":      ind.rs_pct_change(rs_sector_series, config.WEEKS_13_IN_DAYS),
        "rs_sector_chg_26w":      ind.rs_pct_change(rs_sector_series, config.WEEKS_26_IN_DAYS),
        "sector_index_source":    sector_source,
        # ── 52-week high metrics (original + NEW near-breakout at 15%) ──
        "pct_from_52w_high":      ind.pct_from_52w_high(close),
        "near_52w_high":          ind.near_52w_high(close, config.NEAR_52W_HIGH_THRESHOLD_PCT),
        "near_breakout_15pct":    ind.near_52w_high(close, config.NEAR_BREAKOUT_THRESHOLD_PCT),
        # ── Volatility Compression module (NEW) ──
        "atr_compression_ratio":      atr_comp_ratio,
        "atr_compression_percentile": atr_comp_pctile,
        "range_compression_ratio":    ind.range_compression_ratio(df),
        "volatility_compression":     _bool_flag(
            atr_comp_pctile, config.VOLATILITY_COMPRESSION_PERCENTILE_THRESHOLD, le=True
        ),
    }
    return row


def process_universe(label: str, universe_df: pd.DataFrame, index_ticker: str) -> pd.DataFrame:
    logger.info("=== Processing %s universe (%d tickers) ===", label, len(universe_df))

    yf_tickers = universe_df["yf_ticker"].tolist()
    price_data = data_fetch.fetch_price_history(yf_tickers)
    index_df = data_fetch.fetch_index_history(index_ticker)
    index_close = index_df["Close"]

    # Resolve each unique sector to a benchmark series ONCE per universe
    # (RS Leadership module needs RS_SECTOR = stock / sector benchmark).
    ticker_sector_map = dict(zip(universe_df["yf_ticker"], universe_df.get("sector", pd.Series(dtype=object))))
    unique_sectors = list(pd.Series(list(ticker_sector_map.values())).dropna().unique())
    sector_close_map = sector_data.get_sector_close_map(label, unique_sectors, index_close)

    rows = []
    for yf_ticker, df in price_data.items():
        try:
            sector_label = ticker_sector_map.get(yf_ticker)
            sector_close, sector_source = sector_close_map.get(sector_label, (index_close, "NO_SECTOR_LABEL"))
            rows.append(build_metrics_row(yf_ticker, df, index_close, sector_close, sector_source))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Indicator computation failed for %s: %s", yf_ticker, exc)

    metrics_df = pd.DataFrame(rows)
    if metrics_df.empty:
        logger.error("No tickers produced usable metrics for %s — aborting this universe", label)
        return metrics_df

    metrics_df = metrics_df.merge(universe_df, on="yf_ticker", how="left")

    fundamentals_map = fnd.get_fundamentals(metrics_df["yf_ticker"].tolist())
    fund_df = pd.DataFrame(fundamentals_map.values())
    metrics_df = metrics_df.merge(fund_df, left_on="yf_ticker", right_on="ticker", how="left", suffixes=("", "_fund"))
    metrics_df["fundamentally_qualified"] = metrics_df.apply(
        lambda r: fnd.passes_fundamental_filter(r.to_dict()), axis=1
    )

    # Original composite scoring system — unchanged
    metrics_df = sc.compute_composite(metrics_df)
    metrics_df["category"] = metrics_df.apply(
        lambda r: sc.categorize(r["composite_score"], r["fundamentally_qualified"]), axis=1
    )

    # Elite Compounder Early Detection scoring — additive, runs after the above
    metrics_df = sc.compute_elite_compounder_score(metrics_df)

    # Single, clearly-labeled RS column: each stock vs ITS OWN home broad
    # index only (Nifty 50 for NSE, S&P 500 for US) — distinct from the
    # sector-specific RS fields above. This is the same underlying
    # outperformance figure as `rs_score` (blended over ~1m/3m/6m), just
    # surfaced under an unambiguous name for quick reading in the sheet.
    metrics_df["RS_vs_Broad_Index_pct"] = metrics_df["rs_score"]

    metrics_df["universe"] = label
    metrics_df = metrics_df.sort_values("composite_score", ascending=False).reset_index(drop=True)

    logger.info(
        "%s done: %d scored, %d fundamentally qualified, %d Category A elite compounders",
        label, len(metrics_df), int(metrics_df["fundamentally_qualified"].sum()),
        int((metrics_df["elite_category"] == "Category A: Elite Compounder").sum()),
    )
    return metrics_df


DISPLAY_COLUMNS = [
    "ticker", "name", "sector", "universe", "close",
    "composite_score", "category", "fundamentally_qualified",
    # ── Elite Compounder Early Detection — headline fields ──
    "EliteCompounderScore", "elite_category",
    "RS_vs_Broad_Index_pct",   # single clear RS-vs-home-index column
    "flag_obv_leader", "flag_rs_leader", "flag_early_macd",
    "flag_compression", "flag_ema_alignment", "flag_near_breakout",
    # Original sub-scores
    "score_obv", "score_macd_weekly", "score_macd_daily", "score_trend", "score_rs",
    # Elite Compounder sub-scores
    "elite_score_obv", "elite_score_rs", "elite_score_macd_early",
    "elite_score_ema_alignment", "elite_score_compression",
    "elite_score_supertrend", "elite_score_weekly_macd",
    "elite_score_above_ema20", "elite_score_fundamentals",
    # OBV (original + Leadership module)
    "obv_slope_20d", "obv_slope_50d", "obv_52w_range_pct",
    "obv_52w_high", "obv_26w_high", "obv_slope_13w", "obv_slope_26w",
    # Daily MACD (original + Early module)
    "daily_macd", "daily_signal", "daily_hist", "macd_early_bullish",
    # Weekly MACD
    "weekly_macd", "weekly_signal", "weekly_hist", "weekly_macd_positive",
    # Supertrend (daily + weekly)
    "supertrend_10_3_dir", "supertrend_2_1_dir", "supertrend_weekly_dir",
    # EMA structure (original + Early Structure module)
    "ema10", "ema20", "early_ema_alignment",
    # 52-week high / breakout
    "pct_from_52w_high", "near_52w_high", "near_breakout_15pct",
    # Relative Strength (original + Leadership module)
    "rs_score",
    "rs_nifty_52w_high", "rs_nifty_chg_13w", "rs_nifty_chg_26w",
    "rs_sector_52w_high", "rs_sector_chg_13w", "rs_sector_chg_26w",
    "sector_index_source",
    # Volatility Compression module
    "atr_compression_ratio", "atr_compression_percentile",
    "range_compression_ratio", "volatility_compression",
    # Fundamentals
    "sales_cagr", "profit_cagr", "roce", "debt_equity", "data_quality",
]


def main():
    run_started = datetime.datetime.utcnow()
    logger.info("Scan started at %s UTC", run_started.isoformat())

    nse_universe = universe.get_nse500_universe()
    sp500_universe = universe.get_sp500_universe()

    nse_df = process_universe("NSE500", nse_universe, config.INDEX_TICKER_NSE)
    us_df = process_universe("S&P500", sp500_universe, config.INDEX_TICKER_US)

    combined = pd.concat([nse_df, us_df], ignore_index=True) if not nse_df.empty and not us_df.empty else (
        nse_df if not nse_df.empty else us_df
    )

    def view(df, cols=DISPLAY_COLUMNS):
        existing = [c for c in cols if c in df.columns]
        return df[existing]

    # ── Original tabs — unchanged logic ──
    top20_nse = view(nse_df.head(config.TOP_N)) if not nse_df.empty else pd.DataFrame()
    top20_us = view(us_df.head(config.TOP_N)) if not us_df.empty else pd.DataFrame()
    elite = view(combined[combined["category"] == "Elite Compounder"]) if not combined.empty else pd.DataFrame()
    emerging = view(combined[combined["category"] == "Emerging Compounder"]) if not combined.empty else pd.DataFrame()
    exit_candidates = view(combined[combined["category"] == "Exit Candidate"]) if not combined.empty else pd.DataFrame()

    # ── New: Elite Compounder Early Detection tabs ──
    if not combined.empty:
        strict_filter = (
            (combined["obv_52w_high"] == 1.0)
            & (combined["rs_nifty_52w_high"] == 1.0)
            & (combined["macd_early_bullish"] == 1.0)
        )
        elite_early_detect = view(
            combined[strict_filter].sort_values("EliteCompounderScore", ascending=False)
        )
        category_a = view(
            combined[combined["elite_category"] == "Category A: Elite Compounder"]
            .sort_values("EliteCompounderScore", ascending=False)
        )
        category_b = view(
            combined[combined["elite_category"] == "Category B: Emerging Leader"]
            .sort_values("EliteCompounderScore", ascending=False)
        )
        category_c = view(
            combined[combined["elite_category"] == "Category C: Watchlist"]
            .sort_values("EliteCompounderScore", ascending=False)
        )
    else:
        elite_early_detect = category_a = category_b = category_c = pd.DataFrame()

    run_log = pd.DataFrame([{
        "run_timestamp_utc": run_started.isoformat(),
        "nse_tickers_scored": len(nse_df),
        "us_tickers_scored": len(us_df),
        "nse_qualified": int(nse_df["fundamentally_qualified"].sum()) if not nse_df.empty else 0,
        "us_qualified": int(us_df["fundamentally_qualified"].sum()) if not us_df.empty else 0,
        "elite_count": len(elite),
        "emerging_count": len(emerging),
        "exit_count": len(exit_candidates),
        # New counts
        "elite_early_detect_count": len(elite_early_detect),
        "category_a_count": len(category_a),
        "category_b_count": len(category_b),
        "category_c_count": len(category_c),
    }])

    tabs = {
        # Original tabs — unchanged
        config.SHEET_TABS["nse_full"]: view(nse_df),
        config.SHEET_TABS["us_full"]: view(us_df),
        config.SHEET_TABS["top20_nse"]: top20_nse,
        config.SHEET_TABS["top20_us"]: top20_us,
        config.SHEET_TABS["elite"]: elite,
        config.SHEET_TABS["emerging"]: emerging,
        config.SHEET_TABS["exit"]: exit_candidates,
        config.SHEET_TABS["run_log"]: run_log,
        # New: Elite Compounder Early Detection tabs
        config.SHEET_TABS["elite_early_detect"]: elite_early_detect,
        config.SHEET_TABS["category_a"]: category_a,
        config.SHEET_TABS["category_b"]: category_b,
        config.SHEET_TABS["category_c"]: category_c,
    }

    sheets_export.export_to_sheets(tabs)
    logger.info("Scan complete and exported to Google Sheets.")


if __name__ == "__main__":
    main()
