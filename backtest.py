"""
Walk-forward backtest of the scanner's signals — measures whether stocks
flagged by a given signal (Trend Birth, Elite Compounder Score thresholds,
OBV 52w-high, etc.) actually went on to outperform afterward, across a real
universe and many historical dates. This is the rigorous counterpart to
eyeballing 8 winning charts: every signal gets tested across hundreds of
stocks and dozens of historical snapshots, with losing/flat stocks included
automatically (not just survivors).

CRITICAL DESIGN PRINCIPLE — no lookahead bias:
At each historical "as-of" date, every indicator is computed using ONLY
price data up to and including that date (df.loc[:asof_date]) — exactly
what would have been known at the time. Forward returns are then measured
using data AFTER that date, which is correct: that's the outcome being
tested, not an input to the signal itself.

SIMPLIFICATION — fundamentals are not historically reconstructed:
`fundamentally_qualified` is set True for every row in this backtest. This
means EliteCompounderScore and composite_score here measure the TECHNICAL
signal's predictive power in isolation, not combined with the fundamental
gate (point-in-time historical fundamentals are a much harder data problem
than this backtest needs to solve to be useful). Keep this in mind when
reading the results — real-world categorization also requires passing the
fundamental filter, which this backtest doesn't test.

PERFORMANCE: this is much more expensive than a daily scan, since every
indicator gets recomputed at every snapshot date. Defaults are deliberately
conservative (see config.BACKTEST_* settings) — widen them only once you've
confirmed a smaller run completes in a reasonable time. Run via the separate
`backtest_workflow.yml` (manual trigger only), not as part of the daily scan.
"""

import datetime
import logging
import sys

import numpy as np
import pandas as pd

import config
import data_fetch
import metrics_builder
import scoring as sc
import sheets_export
import universe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("backtest")


# ----------------------------------------------------------------------------
# Forward return measurement (point-in-time safe — uses only FUTURE data,
# which is correct here: this is the outcome, not the signal input)
# ----------------------------------------------------------------------------
def forward_return(price_series: pd.Series, asof_date, horizon_days: int) -> float:
    if asof_date not in price_series.index:
        return np.nan
    future = price_series.loc[price_series.index > asof_date]
    if len(future) < horizon_days:
        return np.nan  # not enough future history yet (e.g. too close to today)
    asof_price = price_series.loc[asof_date]
    target_price = future.iloc[horizon_days - 1]
    if pd.isna(asof_price) or asof_price == 0 or pd.isna(target_price):
        return np.nan
    return float((target_price - asof_price) / asof_price * 100)


# ----------------------------------------------------------------------------
# Per-snapshot signal computation — reuses the SAME functions the live daily
# scan uses, just called on a point-in-time-sliced DataFrame instead of the
# full history, so every computed indicator is faithful to what would have
# actually been visible on that historical date.
# ----------------------------------------------------------------------------
def compute_signals_for_snapshot(
    price_data: dict[str, pd.DataFrame], index_close_full: pd.Series, asof_date,
) -> pd.DataFrame:
    rows = []
    index_close_asof = index_close_full.loc[:asof_date]
    if len(index_close_asof) < 100:
        return pd.DataFrame()

    for ticker, df_full in price_data.items():
        df_asof = df_full.loc[:asof_date]
        if len(df_asof) < config.BACKTEST_MIN_HISTORY_DAYS:
            continue
        try:
            row = metrics_builder.build_metrics_row(
                ticker, df_asof, index_close_asof, sector_close=index_close_asof,
                sector_source="BACKTEST_NO_SECTOR",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Signal computation failed for %s @ %s: %s", ticker, asof_date, exc)
            continue
        row["ticker"] = ticker
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    metrics_df = pd.DataFrame(rows)
    metrics_df["fundamentally_qualified"] = True  # see module docstring — simplification
    metrics_df["sector"] = "BACKTEST"  # sector leadership isn't meaningfully testable without
                                        # historical sector labels; placeholder so compute_phase1_additions
                                        # doesn't error. Sector-leadership-specific results aren't tested here.
    metrics_df = sc.compute_composite(metrics_df)
    metrics_df = sc.compute_elite_compounder_score(metrics_df)
    metrics_df["RS_vs_Broad_Index_pct"] = metrics_df["rs_score"]
    metrics_df = sc.compute_phase1_additions(metrics_df)
    metrics_df = sc.compute_trend_death(metrics_df)
    return metrics_df


# ----------------------------------------------------------------------------
# Signal definitions to test — (name, boolean mask function)
# ----------------------------------------------------------------------------
SIGNAL_DEFINITIONS = {
    "trend_birth": lambda df: df["trend_birth_flag"] == 1.0,
    "trend_death": lambda df: df["trend_death_flag"] == 1.0,
    "near_breakout_15pct": lambda df: df["near_breakout_15pct"] == 1.0,
    "volatility_compression": lambda df: df["volatility_compression"] == 1.0,
    "obv_52w_high": lambda df: df["obv_52w_high"] == 1.0,
    "macd_early_bullish": lambda df: df["macd_early_bullish"] == 1.0,
    "elite_score_above_80": lambda df: df["EliteCompounderScore"] > 80,
    "elite_score_above_65": lambda df: df["EliteCompounderScore"] > 65,
    "composite_score_above_85": lambda df: df["composite_score"] > 85,
    "composite_score_below_50": lambda df: df["composite_score"] < 50,
    "baseline_all_stocks": lambda df: pd.Series(True, index=df.index),
}


def run_backtest(
    tickers: list[str], price_data: dict[str, pd.DataFrame], index_close: pd.Series,
    snapshot_dates: list, horizons_days: dict[str, int],
) -> pd.DataFrame:
    """
    Returns a long-format DataFrame: one row per (ticker, snapshot_date)
    with every signal's boolean value and every horizon's forward return.
    """
    records = []
    for i, asof_date in enumerate(snapshot_dates):
        logger.info("Snapshot %d/%d: %s", i + 1, len(snapshot_dates), asof_date.date())
        metrics_df = compute_signals_for_snapshot(price_data, index_close, asof_date)
        if metrics_df.empty:
            continue

        signal_flags = {name: fn(metrics_df) for name, fn in SIGNAL_DEFINITIONS.items()}

        for idx, row in metrics_df.iterrows():
            ticker = row["ticker"]
            price_series = price_data[ticker]["Close"]
            rec = {"ticker": ticker, "asof_date": asof_date}
            for h_name, h_days in horizons_days.items():
                rec[f"fwd_return_{h_name}"] = forward_return(price_series, asof_date, h_days)
            for sig_name, mask in signal_flags.items():
                rec[sig_name] = bool(mask.loc[idx])
            records.append(rec)

    # Benchmark (index) forward return on the same snapshot dates, for context
    bench_records = []
    for asof_date in snapshot_dates:
        rec = {"asof_date": asof_date}
        for h_name, h_days in horizons_days.items():
            rec[f"fwd_return_{h_name}"] = forward_return(index_close, asof_date, h_days)
        bench_records.append(rec)

    long_df = pd.DataFrame(records)
    bench_df = pd.DataFrame(bench_records)
    return long_df, bench_df


def aggregate_results(long_df: pd.DataFrame, bench_df: pd.DataFrame, horizons_days: dict[str, int]) -> pd.DataFrame:
    """Per-signal summary: sample size, mean/median/hit-rate per horizon, vs benchmark excess return."""
    if long_df.empty:
        return pd.DataFrame()

    summary_rows = []
    for sig_name in SIGNAL_DEFINITIONS:
        subset = long_df[long_df[sig_name] == True]  # noqa: E712
        row = {"signal": sig_name, "sample_size": len(subset)}
        for h_name in horizons_days:
            col = f"fwd_return_{h_name}"
            vals = subset[col].dropna()
            bench_vals = bench_df[col].dropna()
            row[f"{h_name}_mean_return_pct"] = round(vals.mean(), 2) if len(vals) else np.nan
            row[f"{h_name}_median_return_pct"] = round(vals.median(), 2) if len(vals) else np.nan
            row[f"{h_name}_hit_rate_pct"] = round((vals > 0).mean() * 100, 1) if len(vals) else np.nan
            row[f"{h_name}_benchmark_mean_pct"] = round(bench_vals.mean(), 2) if len(bench_vals) else np.nan
            row[f"{h_name}_excess_vs_benchmark_pct"] = (
                round(row[f"{h_name}_mean_return_pct"] - row[f"{h_name}_benchmark_mean_pct"], 2)
                if len(vals) and len(bench_vals) else np.nan
            )
        summary_rows.append(row)

    return pd.DataFrame(summary_rows)


def main():
    universe_label = config.BACKTEST_UNIVERSE
    logger.info("Backtest starting — universe=%s, lookback=%dy", universe_label, config.BACKTEST_LOOKBACK_YEARS)

    if universe_label == "NSE500":
        uni_df = universe.get_nse500_universe()
        index_ticker = config.INDEX_TICKER_NSE
    else:
        uni_df = universe.get_sp500_universe()
        index_ticker = config.INDEX_TICKER_US

    tickers = uni_df["yf_ticker"].tolist()
    if config.BACKTEST_MAX_TICKERS:
        tickers = tickers[: config.BACKTEST_MAX_TICKERS]
    logger.info("Backtest universe size: %d tickers", len(tickers))

    # Fetch with extended history for backtest purposes
    original_period = config.PRICE_HISTORY_PERIOD
    config.PRICE_HISTORY_PERIOD = f"{config.BACKTEST_LOOKBACK_YEARS + 2}y"  # +2y buffer for indicator warmup
    price_data = data_fetch.fetch_price_history(tickers)
    index_df = data_fetch.fetch_index_history(index_ticker)
    config.PRICE_HISTORY_PERIOD = original_period
    index_close = index_df["Close"]

    # Monthly snapshot dates over the configured lookback, leaving enough
    # room at the end for the longest forward-return horizon to resolve
    all_dates = index_close.index
    end_buffer_days = max(config.BACKTEST_HORIZONS_DAYS.values())
    usable_dates = all_dates[: -end_buffer_days] if len(all_dates) > end_buffer_days else all_dates
    start_date = usable_dates[-1] - pd.DateOffset(years=config.BACKTEST_LOOKBACK_YEARS)
    snapshot_dates = pd.date_range(start=start_date, end=usable_dates[-1], freq=config.BACKTEST_SNAPSHOT_FREQ)
    snapshot_dates = [d for d in snapshot_dates if d in all_dates] or \
        [all_dates[all_dates.searchsorted(d)] for d in snapshot_dates if all_dates.searchsorted(d) < len(all_dates)]
    logger.info("Running %d snapshot dates from %s to %s", len(snapshot_dates), snapshot_dates[0].date(), snapshot_dates[-1].date())

    long_df, bench_df = run_backtest(tickers, price_data, index_close, snapshot_dates, config.BACKTEST_HORIZONS_DAYS)
    summary_df = aggregate_results(long_df, bench_df, config.BACKTEST_HORIZONS_DAYS)

    logger.info("Backtest complete. Signal summary:\n%s", summary_df.to_string())

    out_path = "backtest_results.csv"
    summary_df.to_csv(out_path, index=False)
    logger.info("Saved summary to %s", out_path)

    if config.GOOGLE_SHEET_ID:
        try:
            sheets_export.export_to_sheets({config.BACKTEST_RESULTS_TAB_NAME: summary_df})
            logger.info("Exported summary to Google Sheets tab '%s'", config.BACKTEST_RESULTS_TAB_NAME)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not export to Google Sheets (CSV was still saved): %s", exc)


if __name__ == "__main__":
    main()
