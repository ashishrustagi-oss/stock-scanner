"""
Standalone diagnostic — NOT part of the daily pipeline. Investigates the
REAL mechanism behind obv_divergence_decaying's surprising backtest result
(two independent runs both showed +33pp-ish 12m excess return — the
OPPOSITE of its intended caution/exhaustion purpose).

Synthetic testing (built into multiple chat sessions, not saved as a
script) suggested a working theory: this may be functioning as a "calm,
low-volatility uptrend continuation" signal rather than genuine OBV
exhaustion — but synthetic archetypes are necessarily guesses about what
real stocks look like. This script pulls REAL current data instead and
checks the working theory against it directly:
  - Do TODAY's flagged stocks show meaningfully different volatility
    compression (atr_compression_percentile) than unflagged stocks?
  - Are flagged stocks concentrated in particular sectors?
  - What does their RS / fundamentals profile look like, for context?

Run this from a machine/runner with real internet access (NOT the sandbox
used to build this script — same yfinance constraint as every other
diagnostic in this folder).

Usage:
    python diagnostics/divergence_decaying_mechanism_check.py

Note: this runs the real NSE500 universe fetch + indicator computation,
similar in spirit to a live scan but read-only — no Sheets export, no
cache writes, no side effects on the real pipeline.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

import config
import universe
import data_fetch
import metrics_builder
import sector_data
import scoring as sc


def main():
    print("Fetching live NSE500 universe...")
    uni_df = universe.get_nse500_universe()
    tickers = uni_df["yf_ticker"].tolist()
    print(f"Universe size: {len(tickers)} tickers")

    print("Fetching price history (this is the slow part, be patient)...")
    price_data = data_fetch.fetch_price_history(tickers)
    index_df = data_fetch.fetch_index_history(config.INDEX_TICKER_NSE)
    index_close = index_df["Close"]

    ticker_sector_map = dict(zip(uni_df["yf_ticker"], uni_df.get("sector", pd.Series(dtype=object))))
    unique_sectors = list(pd.Series(list(ticker_sector_map.values())).dropna().unique())
    sector_close_map = sector_data.get_sector_close_map("NSE500", unique_sectors, index_close)

    print("Computing indicators for every ticker...")
    rows = []
    for yf_ticker, df in price_data.items():
        try:
            sector_label = ticker_sector_map.get(yf_ticker)
            sector_close, sector_source = sector_close_map.get(sector_label, (index_close, "NO_SECTOR_LABEL"))
            row = metrics_builder.build_metrics_row(yf_ticker, df, index_close, sector_close, sector_source)
            row["sector"] = sector_label
            rows.append(row)
        except Exception as exc:  # noqa: BLE001
            continue

    metrics_df = pd.DataFrame(rows)
    print(f"Computed metrics for {len(metrics_df)} tickers\n")

    flagged = metrics_df[metrics_df["obv_divergence_decaying"] == "🔴"]
    unflagged = metrics_df[metrics_df["obv_divergence_decaying"] != "🔴"]

    print(f"=== {len(flagged)} stocks currently flagged by obv_divergence_decaying ===")
    if not flagged.empty:
        cols_to_show = ["ticker", "sector", "price_chg_42d", "obv_decay_current_ratio",
                         "atr_compression_percentile", "rs_score"]
        cols_to_show = [c for c in cols_to_show if c in flagged.columns]
        print(flagged[cols_to_show].sort_values("price_chg_42d", ascending=False).to_string())

    print("\n=== Working theory check: volatility compression ===")
    print("(If flagged stocks show MEANINGFULLY HIGHER atr_compression_percentile")
    print(" than unflagged, that supports the 'calm low-vol continuation' theory)")
    if "atr_compression_percentile" in metrics_df.columns:
        print(f"Flagged mean atr_compression_percentile:   {flagged['atr_compression_percentile'].mean():.1f}")
        print(f"Unflagged mean atr_compression_percentile: {unflagged['atr_compression_percentile'].mean():.1f}")
        print(f"Flagged median: {flagged['atr_compression_percentile'].median():.1f}  |  Unflagged median: {unflagged['atr_compression_percentile'].median():.1f}")

    print("\n=== Sector concentration check ===")
    print("(If flagged stocks cluster heavily in 1-2 sectors, that's worth knowing")
    print(" too — could mean a sector-specific effect, not a general pattern)")
    if not flagged.empty and "sector" in flagged.columns:
        print("Flagged sector distribution:")
        print(flagged["sector"].value_counts())
        print("\nFor comparison, overall universe sector distribution (top 10):")
        print(metrics_df["sector"].value_counts().head(10))

    print("\n=== RS / momentum profile comparison ===")
    if "rs_score" in metrics_df.columns:
        print(f"Flagged mean rs_score:   {flagged['rs_score'].mean():.2f}")
        print(f"Unflagged mean rs_score: {unflagged['rs_score'].mean():.2f}")

    print("\n=== Fundamentals profile comparison (if available) ===")
    if "fundamentally_qualified" in metrics_df.columns:
        flagged_fund_rate = flagged["fundamentally_qualified"].mean() if not flagged.empty else float("nan")
        unflagged_fund_rate = unflagged["fundamentally_qualified"].mean()
        print(f"Flagged % fundamentally_qualified:   {flagged_fund_rate * 100:.1f}%")
        print(f"Unflagged % fundamentally_qualified: {unflagged_fund_rate * 100:.1f}%")

    print("\nDone. Paste this whole output back for analysis.")


if __name__ == "__main__":
    main()
