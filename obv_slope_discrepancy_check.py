"""
Standalone diagnostic — NOT part of the daily pipeline. Investigates a
real discrepancy: live run showed obv_slope_50d = 7.11 for IDEA, but a
manual check using IDEA's actual OBV values from TradingView (11.89B ->
23.14B over 50 trading days) suggests the formula should produce roughly
1.3-1.6%, not 7.11. The formula itself was verified correct against
synthetic data; this script exists to see IDEA's REAL data and find where
the real pipeline's number actually comes from.

Run this from a machine/runner with real internet access (NOT the
sandbox used to build this script — yfinance is blocked there, same
constraint as other diagnostics in this folder).

Usage:
    python diagnostics/obv_slope_discrepancy_check.py

Prints, in order:
  1. The last 55 raw rows of IDEA's fetched price/volume data (so you can
     visually compare against TradingView's chart for the same dates)
  2. The full OBV series for those same rows
  3. The exact 50-bar window obv_slope() uses, with the regression slope,
     scale, and final result broken out step by step
  4. The same breakdown for the 20-day and 200-day windows, for comparison
  5. A flag for any single-day OBV jump larger than 30% of the period's
     mean — the most likely real-world cause of an inflated slope (a
     single outsized volume day, a data glitch, or an auto_adjust quirk
     around a corporate action)
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import yfinance as yf

import indicators as ind
import config

TICKER = "IDEA.NS"


def main():
    print(f"Fetching {TICKER} the same way data_fetch.py does (auto_adjust=True, 1y period)...")
    df = yf.download(
        tickers=TICKER,
        period=config.PRICE_HISTORY_PERIOD,
        interval="1d",
        auto_adjust=True,
        progress=False,
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(how="all")

    if df.empty:
        print("ERROR: no data returned. Check network access / ticker symbol.")
        return

    print(f"\nFetched {len(df)} rows, from {df.index[0].date()} to {df.index[-1].date()}")

    print("\n=== Last 55 rows of raw OHLCV (compare against TradingView's chart for these exact dates) ===")
    pd.set_option("display.max_rows", None)
    pd.set_option("display.width", 160)
    print(df[["Open", "High", "Low", "Close", "Volume"]].tail(55).to_string())

    obv_series = ind.obv(df)
    print("\n=== Last 55 OBV values ===")
    print(obv_series.tail(55).to_string())

    def breakdown(window_label, window):
        print(f"\n=== obv_slope_{window_label} breakdown (window={window}) ===")
        recent = obv_series.dropna().iloc[-window:]
        if len(recent) < window:
            print(f"Only {len(recent)} bars available, need {window} — returns NaN")
            return
        print(f"First value in window ({recent.index[0].date()}): {recent.iloc[0]:,.0f}")
        print(f"Last value in window  ({recent.index[-1].date()}): {recent.iloc[-1]:,.0f}")
        x = np.arange(len(recent))
        slope, intercept = np.polyfit(x, recent.values, 1)
        scale = np.abs(recent).mean()
        result = slope / scale * 100 if scale != 0 else float("nan")
        print(f"Regression slope (raw, OBV units/day): {slope:,.2f}")
        print(f"Mean abs OBV over window (the 'scale' denominator): {scale:,.2f}")
        print(f"Result (slope / scale * 100): {result:.4f}")

        # Flag the single biggest day-over-day OBV jump in this window —
        # the most likely real-world cause of an inflated slope, since a
        # single huge-volume day skews both the regression fit AND
        # (less so) the mean-abs scale.
        diffs = recent.diff().dropna()
        if not diffs.empty:
            biggest_jump_idx = diffs.abs().idxmax()
            biggest_jump = diffs.loc[biggest_jump_idx]
            pct_of_scale = abs(biggest_jump) / scale * 100 if scale != 0 else float("nan")
            print(f"Biggest single-day OBV change in window: {biggest_jump:,.0f} on {biggest_jump_idx.date()} "
                  f"({pct_of_scale:.1f}% of the mean-abs scale)")
            if pct_of_scale > 30:
                print(">>> FLAG: this single day is unusually large relative to the window's typical "
                      "OBV level — worth checking this date specifically against TradingView's volume "
                      "for a data glitch, corporate action, or auto_adjust artifact.")

    breakdown("20d", config.OBV_SLOPE_SHORT_WINDOW)
    breakdown("50d", config.OBV_SLOPE_LONG_WINDOW)

    print("\n=== Sanity check: actual library result ===")
    print("obv_slope_20d:", ind.obv_slope(obv_series, config.OBV_SLOPE_SHORT_WINDOW))
    print("obv_slope_50d:", ind.obv_slope(obv_series, config.OBV_SLOPE_LONG_WINDOW))


if __name__ == "__main__":
    main()
