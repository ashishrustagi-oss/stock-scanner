"""
Orchestrates a full daily scan:
  1. Load NSE500 + S&P500 universes
  2. Fetch price history (+ benchmark index history) for each
  3. Compute all technical indicators per ticker
  4. Fetch/merge fundamentals, apply qualifying filter
  5. Compute composite score, categorize
  6. Build output tabs and push to Google Sheets

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
import sheets_export
import universe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("main")


def build_metrics_row(yf_ticker: str, df: pd.DataFrame, index_close: pd.Series) -> dict:
    """Computes every technical indicator for one ticker and returns a flat dict."""
    close = df["Close"]

    obv_series = ind.obv(df)
    weekly = ind.resample_weekly(df)

    daily_macd, daily_signal, daily_hist = ind.macd(close)
    weekly_macd, weekly_signal, weekly_hist = ind.macd(weekly["Close"])

    st_slow_line, st_slow_dir = ind.supertrend(df, **config.SUPERTREND_SLOW)
    st_fast_line, st_fast_dir = ind.supertrend(df, **config.SUPERTREND_FAST)

    ema20 = ind.ema(close, config.EMA_PERIOD)

    return {
        "yf_ticker": yf_ticker,
        "close": float(close.iloc[-1]),
        "obv": float(obv_series.iloc[-1]),
        "obv_slope_20d": ind.obv_slope(obv_series, config.OBV_SLOPE_SHORT_WINDOW),
        "obv_slope_50d": ind.obv_slope(obv_series, config.OBV_SLOPE_LONG_WINDOW),
        "daily_macd": float(daily_macd.iloc[-1]),
        "daily_signal": float(daily_signal.iloc[-1]),
        "daily_hist": float(daily_hist.iloc[-1]),
        "weekly_macd": float(weekly_macd.iloc[-1]) if len(weekly_macd) else np.nan,
        "weekly_signal": float(weekly_signal.iloc[-1]) if len(weekly_signal) else np.nan,
        "weekly_hist": float(weekly_hist.iloc[-1]) if len(weekly_hist) else np.nan,
        "supertrend_10_3_value": float(st_slow_line.iloc[-1]),
        "supertrend_10_3_dir": float(st_slow_dir.iloc[-1]),
        "supertrend_2_1_value": float(st_fast_line.iloc[-1]),
        "supertrend_2_1_dir": float(st_fast_dir.iloc[-1]),
        "ema20": float(ema20.iloc[-1]),
        "rs_score": ind.relative_strength_score(close, index_close),
        "pct_from_52w_high": ind.pct_from_52w_high(close),
    }


def process_universe(label: str, universe_df: pd.DataFrame, index_ticker: str) -> pd.DataFrame:
    logger.info("=== Processing %s universe (%d tickers) ===", label, len(universe_df))

    yf_tickers = universe_df["yf_ticker"].tolist()
    price_data = data_fetch.fetch_price_history(yf_tickers)
    index_df = data_fetch.fetch_index_history(index_ticker)
    index_close = index_df["Close"]

    rows = []
    for yf_ticker, df in price_data.items():
        try:
            rows.append(build_metrics_row(yf_ticker, df, index_close))
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

    metrics_df = sc.compute_composite(metrics_df)
    metrics_df["category"] = metrics_df.apply(
        lambda r: sc.categorize(r["composite_score"], r["fundamentally_qualified"]), axis=1
    )
    metrics_df["universe"] = label
    metrics_df = metrics_df.sort_values("composite_score", ascending=False).reset_index(drop=True)

    logger.info(
        "%s done: %d scored, %d fundamentally qualified",
        label, len(metrics_df), int(metrics_df["fundamentally_qualified"].sum()),
    )
    return metrics_df


DISPLAY_COLUMNS = [
    "ticker", "name", "sector", "universe", "close",
    "composite_score", "category", "fundamentally_qualified",
    "score_obv", "score_macd_weekly", "score_macd_daily", "score_trend", "score_rs",
    "obv_slope_20d", "obv_slope_50d",
    "daily_macd", "daily_signal", "weekly_macd", "weekly_signal",
    "supertrend_10_3_dir", "supertrend_2_1_dir", "ema20",
    "rs_score", "pct_from_52w_high",
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

    top20_nse = view(nse_df.head(config.TOP_N)) if not nse_df.empty else pd.DataFrame()
    top20_us = view(us_df.head(config.TOP_N)) if not us_df.empty else pd.DataFrame()
    elite = view(combined[combined["category"] == "Elite Compounder"]) if not combined.empty else pd.DataFrame()
    emerging = view(combined[combined["category"] == "Emerging Compounder"]) if not combined.empty else pd.DataFrame()
    exit_candidates = view(combined[combined["category"] == "Exit Candidate"]) if not combined.empty else pd.DataFrame()

    run_log = pd.DataFrame([{
        "run_timestamp_utc": run_started.isoformat(),
        "nse_tickers_scored": len(nse_df),
        "us_tickers_scored": len(us_df),
        "nse_qualified": int(nse_df["fundamentally_qualified"].sum()) if not nse_df.empty else 0,
        "us_qualified": int(us_df["fundamentally_qualified"].sum()) if not us_df.empty else 0,
        "elite_count": len(elite),
        "emerging_count": len(emerging),
        "exit_count": len(exit_candidates),
    }])

    tabs = {
        config.SHEET_TABS["nse_full"]: view(nse_df),
        config.SHEET_TABS["us_full"]: view(us_df),
        config.SHEET_TABS["top20_nse"]: top20_nse,
        config.SHEET_TABS["top20_us"]: top20_us,
        config.SHEET_TABS["elite"]: elite,
        config.SHEET_TABS["emerging"]: emerging,
        config.SHEET_TABS["exit"]: exit_candidates,
        config.SHEET_TABS["run_log"]: run_log,
    }

    sheets_export.export_to_sheets(tabs)
    logger.info("Scan complete and exported to Google Sheets.")


if __name__ == "__main__":
    main()
