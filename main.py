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
import metrics_builder
import portfolio
import scoring as sc
import sector_data
import shareholding
import sheets_export
import universe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("main")


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
            rows.append(metrics_builder.build_metrics_row(yf_ticker, df, index_close, sector_close, sector_source))
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

    # MF/FII shareholding trend — NSE-specific (SEBI quarterly filing concept,
    # no equivalent free data source built for US institutional holdings
    # here; US uses a different framework — SEC 13F — out of scope).
    # Informational only: does NOT feed into composite_score or
    # EliteCompounderScore, so it can't disrupt either already-tuned system.
    if label == "NSE500":
        share_trends = shareholding.get_shareholding_trends(metrics_df["ticker"].tolist())
        share_df = pd.DataFrame(
            [{"ticker": t, **v} for t, v in share_trends.items()]
        ).rename(columns={
            "mf_pct": "mf_holding_pct", "fii_pct": "fii_holding_pct",
            "mf_pct_prev": "mf_holding_pct_prev_qtr", "fii_pct_prev": "fii_holding_pct_prev_qtr",
            "quarter_end": "shareholding_quarter_end", "data_quality": "shareholding_data_quality",
        })
        metrics_df = metrics_df.merge(share_df, on="ticker", how="left")
        metrics_df["flag_mf_increasing"] = metrics_df["mf_holding_increasing"].apply(
            lambda v: "🟢" if v is True else ""
        )
        metrics_df["flag_fii_increasing"] = metrics_df["fii_holding_increasing"].apply(
            lambda v: "🟢" if v is True else ""
        )
    else:
        for col in [
            "mf_holding_pct", "fii_holding_pct", "mf_holding_pct_prev_qtr", "fii_holding_pct_prev_qtr",
            "mf_holding_increasing", "fii_holding_increasing", "shareholding_quarter_end",
            "shareholding_data_quality", "flag_mf_increasing", "flag_fii_increasing",
        ]:
            metrics_df[col] = None

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
    # MF/FII shareholding trend (NSE-only, informational — see shareholding.py)
    "mf_holding_pct", "mf_holding_pct_prev_qtr", "mf_holding_increasing", "flag_mf_increasing",
    "fii_holding_pct", "fii_holding_pct_prev_qtr", "fii_holding_increasing", "flag_fii_increasing",
    "shareholding_quarter_end", "shareholding_data_quality",
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

    # ── My Portfolio (manually-imported Zerodha holdings) — additive, never
    # blocks the rest of the pipeline if the holdings tab doesn't exist yet ──
    try:
        nifty_index_close = data_fetch.fetch_index_history(config.INDEX_TICKER_NSE)["Close"]
        my_portfolio = portfolio.build_my_portfolio_tab(nse_df, nifty_index_close)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Portfolio enrichment failed, skipping: %s", exc)
        my_portfolio = pd.DataFrame()

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

    # Only add the portfolio tab if there was something to show — avoids
    # creating an empty/confusing tab before the user has set up My_Holdings.
    if not my_portfolio.empty:
        tabs[config.SHEET_TABS["my_portfolio"]] = my_portfolio

    sheets_export.export_to_sheets(tabs)
    logger.info("Scan complete and exported to Google Sheets.")


if __name__ == "__main__":
    main()
