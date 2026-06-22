"""
Standalone diagnostic — NOT part of the daily pipeline.

Purpose: before building a dedicated EARNINGS_ACCELERATING sheet tab, check
how many tickers actually get usable eps_acceleration / revenue_acceleration
values out of fundamentals.py's _extract_earnings_acceleration(). If
coverage is too thin (e.g. mostly "missing"), a dedicated tab would mostly
show blanks and isn't worth the extra API quota / sheet clutter.

Run this from a machine/runner with real internet access (NOT this sandbox —
query1/query2.finance.yahoo.com are not in the sandbox's network allowlist).
Local machine or a one-off GitHub Actions step both work.

Usage:
    python diagnostics/earnings_accel_coverage_check.py

Output: a per-ticker table + summary coverage stats, printed to stdout.
No sheets export, no caching side effects, no merge into the real pipeline.
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd
import yfinance as yf

import fundamentals as fnd

# Deliberately mixed sample: mega-caps (should have clean data), mid-caps
# (realistic NSE coverage test), and known seasonal businesses (to see the
# QoQ seasonality caveat in action, not just in theory).
SAMPLE_TICKERS = {
    "NSE_largecap": ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS"],
    "NSE_midcap": ["CAMS.NS", "MTARTECH.NS", "BHARATFORG.NS", "PERSISTENT.NS", "POLYCAB.NS"],
    "US_largecap": ["AAPL", "MSFT", "GOOGL", "NVDA", "JPM"],
    "US_seasonal": ["TGT", "BBY", "DECK", "TPR"],  # retail-heavy, Q4 holiday distortion expected
}


def run():
    rows = []
    for group, tickers in SAMPLE_TICKERS.items():
        for t in tickers:
            try:
                tk = yf.Ticker(t)
                data = fnd._extract_earnings_acceleration(tk)
            except Exception as exc:  # noqa: BLE001
                data = {"earnings_data_quality": f"ERROR: {exc}"}
            rows.append({"group": group, "ticker": t, **data})

    df = pd.DataFrame(rows)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 160)

    print("\n=== Per-ticker results ===")
    print(df.to_string(index=False))

    print("\n=== Coverage summary by data_quality ===")
    print(df["earnings_data_quality"].value_counts())

    print("\n=== Coverage summary by group ===")
    print(df.groupby("group")["earnings_data_quality"].value_counts())

    ok_pct = (df["earnings_data_quality"] == "ok").mean() * 100
    partial_pct = (df["earnings_data_quality"] == "partial").mean() * 100
    missing_pct = (df["earnings_data_quality"] == "missing").mean() * 100
    print(f"\nok: {ok_pct:.0f}%  partial: {partial_pct:.0f}%  missing: {missing_pct:.0f}%")

    if ok_pct + partial_pct < 50:
        print(
            "\n>>> WARNING: coverage looks too thin for a dedicated tab to be "
            "worth it right now. The full-scan columns are probably the "
            "better home for this data until coverage improves."
        )
    else:
        print(
            "\n>>> Coverage looks reasonable. A dedicated "
            "EARNINGS_ACCELERATING tab (filtering on flag_earnings_accelerating) "
            "is probably worth building."
        )


if __name__ == "__main__":
    run()
