"""
entry_frequency_check.py — standalone diagnostic, run LOCALLY (not on
GitHub Actions, not in a sandbox with restricted network access).

Purpose: answer "is it normal that no trades have fired yet, or is
something broken?" with real numbers instead of a guess.

For today's actual qualified pools (Elite Compounder + OBV/RS Combo),
pulls 2 years of daily history via yfinance and counts how often the
ST(2,1) bullish crossover entry trigger has historically fired —
per stock and in aggregate across the whole pool.

For Strategy 2 (MTF), also checks how often all 4 higher-timeframe
filters (Weekly ST10,3, Weekly ST2,1, Daily ST10,3, Daily ST2,1) have
historically aligned simultaneously — the 15-min entry trigger on top
of that can't be backtested this way (yfinance only gives ~60 days of
15-min data), but "layers 1-4 aligned" is a hard upper bound on how
often Strategy 2 could possibly enter, which is still informative.

Usage:
    cd stock-scanner
    python entry_frequency_check.py

Requires: pandas, numpy, yfinance (pip install if missing)
Reads:    cache/nse500_latest.csv (today's scanner output, already in repo)
Writes:   nothing — prints a report to stdout
"""

import sys
import time

import numpy as np
import pandas as pd
import yfinance as yf

from supertrend import compute_supertrend, supertrend_signals

LOOKBACK_YEARS = 2
ST_SLOW_PERIOD, ST_SLOW_MULT = 10, 3.0
ST_FAST_PERIOD, ST_FAST_MULT = 2, 1.0


def get_qualified_pools() -> dict[str, list[str]]:
    df = pd.read_csv("cache/nse500_latest.csv")
    df["ticker"] = df["ticker"].str.replace(r"\.NS$", "", regex=True).str.upper()

    elite = df[df["EliteCompounderScore"] >= 65].sort_values(
        "EliteCompounderScore", ascending=False)["ticker"].tolist()

    combo_mask = (
        df["obv_leadership_rank"].notna() & (df["obv_leadership_rank"] > 90) &
        df["rs_rank"].notna() & (df["rs_rank"] > 90)
    )
    combo = df[combo_mask]["ticker"].tolist()

    return {"elite": elite, "combo": combo}


def fetch_daily(ticker: str) -> pd.DataFrame | None:
    try:
        df = yf.download(f"{ticker}.NS", period=f"{LOOKBACK_YEARS}y",
                          interval="1d", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns=str.lower)
        return df[["open", "high", "low", "close", "volume"]].dropna()
    except Exception as exc:
        print(f"  ! {ticker}: fetch failed ({exc})")
        return None


def fetch_weekly(ticker: str) -> pd.DataFrame | None:
    daily = fetch_daily(ticker)
    if daily is None or len(daily) < 20:
        return None
    weekly = daily.resample("W").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    return weekly


def strategy1_crossover_dates(daily: pd.DataFrame) -> list:
    """Days ST(2,1) flipped bullish — the Strategy 1 entry trigger."""
    st = compute_supertrend(daily, ST_FAST_PERIOD, ST_FAST_MULT, prefix="f_")
    st = supertrend_signals(st, prefix="f_")
    return st.index[st["f_st_cross_up"]].tolist()


def strategy2_aligned_days(daily: pd.DataFrame, weekly: pd.DataFrame) -> int:
    """Count of daily bars where all 4 higher-TF layers were simultaneously bullish."""
    d = compute_supertrend(daily, ST_SLOW_PERIOD, ST_SLOW_MULT, prefix="d10_")
    d = compute_supertrend(d, ST_FAST_PERIOD, ST_FAST_MULT, prefix="d2_")

    w = compute_supertrend(weekly, ST_SLOW_PERIOD, ST_SLOW_MULT, prefix="w10_")
    w = compute_supertrend(w, ST_FAST_PERIOD, ST_FAST_MULT, prefix="w2_")

    # Forward-fill weekly bullish flags onto daily index (weekly state
    # holds until the next weekly bar closes)
    w_bull = w[["w10_st_bullish", "w2_st_bullish"]].reindex(d.index, method="ffill")

    aligned = (
        d["d10_st_bullish"].fillna(False) &
        d["d2_st_bullish"].fillna(False) &
        w_bull["w10_st_bullish"].fillna(False) &
        w_bull["w2_st_bullish"].fillna(False)
    )
    return int(aligned.sum()), len(d)


def main():
    pools = get_qualified_pools()
    all_tickers = sorted(set(pools["elite"]) | set(pools["combo"]))
    print(f"Today's qualified universe: {len(pools['elite'])} elite, "
          f"{len(pools['combo'])} combo, {len(all_tickers)} unique tickers\n")

    s1_crossover_counts = {}
    s2_aligned_counts = {}
    s2_total_bars = {}

    for i, ticker in enumerate(all_tickers, 1):
        print(f"[{i}/{len(all_tickers)}] {ticker}...", end=" ")
        daily = fetch_daily(ticker)
        if daily is None or len(daily) < 60:
            print("skipped (insufficient data)")
            continue

        crosses = strategy1_crossover_dates(daily)
        s1_crossover_counts[ticker] = len(crosses)

        weekly = fetch_weekly(ticker)
        if weekly is not None and len(weekly) >= 20:
            aligned, total = strategy2_aligned_days(daily, weekly)
            s2_aligned_counts[ticker] = aligned
            s2_total_bars[ticker] = total
        else:
            s2_aligned_counts[ticker] = None

        print(f"ST(2,1) crossovers: {len(crosses)} over ~{LOOKBACK_YEARS}y")
        time.sleep(0.3)  # be polite to Yahoo Finance

    print("\n" + "=" * 70)
    print("STRATEGY 1 (Elite/Combo, daily ST(2,1) crossover entry)")
    print("=" * 70)
    valid = {k: v for k, v in s1_crossover_counts.items() if v is not None}
    if valid:
        total_crosses = sum(valid.values())
        avg_per_stock = total_crosses / len(valid)
        trading_days = LOOKBACK_YEARS * 252
        print(f"Stocks analyzed: {len(valid)}")
        print(f"Total ST(2,1) bullish crossovers across pool: {total_crosses} "
              f"over ~{LOOKBACK_YEARS} years")
        print(f"Average per stock: {avg_per_stock:.1f} crossovers / {LOOKBACK_YEARS}y "
              f"(~1 every {trading_days/avg_per_stock:.0f} trading days per stock, if >0)"
              if avg_per_stock > 0 else "Average per stock: 0")
        print(f"Aggregate: ~{total_crosses/(LOOKBACK_YEARS*12):.1f} entry-eligible "
              f"events/month across the whole current pool (today's pool, "
              f"applied retroactively — actual historical pool composition "
              f"would have differed, so treat as an approximation)")
        print("\nPer-stock breakdown:")
        for t, c in sorted(valid.items(), key=lambda x: -x[1]):
            print(f"  {t:12s} {c} crossovers")
    else:
        print("No valid data.")

    print("\n" + "=" * 70)
    print("STRATEGY 2 (MTF, 4 higher-TF layers aligned — upper bound on entry rate)")
    print("=" * 70)
    valid2 = {k: v for k, v in s2_aligned_counts.items() if v is not None}
    if valid2:
        for t, aligned in sorted(valid2.items(), key=lambda x: -x[1]):
            total = s2_total_bars[t]
            pct = 100 * aligned / total if total else 0
            print(f"  {t:12s} aligned on {aligned}/{total} days ({pct:.1f}% of the time)")
        avg_pct = np.mean([100 * s2_aligned_counts[t] / s2_total_bars[t]
                            for t in valid2 if s2_total_bars.get(t)])
        print(f"\nAverage across pool: {avg_pct:.1f}% of trading days had all "
              f"4 higher-TF layers aligned.")
        print("Note: the 15-min entry trigger (layer 5) further restricts this — "
              "actual entries will be fewer than this count, since alignment "
              "must also coincide with a fresh 15-min ST(10,3) crossover.")
    else:
        print("No valid data.")


if __name__ == "__main__":
    main()
