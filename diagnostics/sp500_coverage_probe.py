"""
Diagnostic: checks yfinance price-data coverage against the point-in-time
S&P 500 universe, for a given backtest window. This is the "flag the gaps
explicitly" step from the US-strategy backtest plan — silently dropping
tickers with missing data would understate survivorship bias risk rather
than surface it.

NOTE ON RUNNING THIS: requires real internet access to Yahoo Finance via
yfinance. It will NOT run inside a network-restricted sandbox — run it from
your normal local environment or a GitHub Actions job with full internet.

WHAT IT CHECKS, per ticker that was ever a member during the window:
  1. Does yfinance return ANY data for this exact ticker symbol?
  2. If yes, does the returned date range actually cover the ticker's
     membership window (or as much of it as should exist given IPO/
     delisting), or is there a meaningful gap?
  3. Tickers with zero data are the tickers most likely to be exactly the
     "blew up and dropped out" names — the ones a naive backtest would most
     want to have, and most likely to be silently missing from a free data
     source. These get the biggest section in the report.

OUTPUT: a JSON report to cache/sp500_coverage_report.json plus a printed
summary, so this can be re-run periodically without re-reading a wall of
console text every time.
"""

import datetime
import json
import logging
import os
import sys
import time

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import data_fetch  # noqa: E402
import sp500_point_in_time as spt  # noqa: E402
from known_ticker_renames import KNOWN_TICKER_RENAMES  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sp500_coverage_probe")

BACKTEST_START = "2008-01-01"
BACKTEST_END = datetime.date.today().strftime("%Y-%m-%d")
REPORT_PATH = "cache/sp500_coverage_report.json"


def collect_all_members_in_window(start: str, end: str) -> dict[str, list[str]]:
    """
    Thin wrapper — the actual logic now lives in
    sp500_point_in_time.get_all_members_in_window() so backtest.py can reuse
    the exact same implementation rather than each script carrying its own
    (slightly-drifting) copy. Kept as a wrapper here so this script's
    existing call sites below don't need to change.
    """
    return spt.get_all_members_in_window(start, end)


def check_coverage(membership: dict[str, list[str]]) -> dict:
    tickers = list(membership.keys())
    logger.info("Checking yfinance coverage for %d tickers (%s to %s)...",
                len(tickers), BACKTEST_START, BACKTEST_END)

    price_data = data_fetch.fetch_price_history_range(tickers, BACKTEST_START, BACKTEST_END)

    no_data = []
    partial_coverage = []
    ok = []
    initial_failures = [t for t in tickers if t not in price_data or price_data[t].empty]

    if initial_failures:
        logger.info(
            "%d tickers failed in the main batch run — retrying individually "
            "(isolated, slower calls) before treating them as genuinely missing, "
            "since large-batch runs can trip Yahoo's rate limiting even for "
            "valid tickers.", len(initial_failures),
        )
        recovered = 0
        for t in initial_failures:
            time.sleep(1.0)  # deliberately slow — one ticker at a time, not a batch
            retry_result = data_fetch.fetch_price_history_range([t], BACKTEST_START, BACKTEST_END)
            if t in retry_result and not retry_result[t].empty:
                price_data[t] = retry_result[t]
                recovered += 1
        logger.info(
            "Retry pass recovered %d/%d initially-failed tickers — these were "
            "rate-limiting artifacts, not real gaps.", recovered, len(initial_failures),
        )

    for raw_ticker, (first, last) in membership.items():
        # Check known renames too: if the raw ticker itself has no data but a
        # documented successor ticker does, treat the successor's data as
        # this entity's continuation rather than a gap. See known_renames.py.
        ticker = raw_ticker
        renamed_to = KNOWN_TICKER_RENAMES.get(raw_ticker)

        candidates = [ticker] + ([renamed_to] if renamed_to else [])
        found = None
        for c in candidates:
            if c in price_data and not price_data[c].empty:
                found = c
                break

        if found is None:
            entry = {"ticker": raw_ticker, "expected_window": [first, last]}
            if renamed_to:
                entry["note"] = f"mapped to {renamed_to} in KNOWN_TICKER_RENAMES but still no data — verify manually"
            no_data.append(entry)
            continue

        df = price_data[found]
        actual_first = df.index.min().strftime("%Y-%m-%d")
        actual_last = df.index.max().strftime("%Y-%m-%d")

        # Allow a generous grace window (60 days) since exact IPO/delisting-day
        # data can be legitimately thin without indicating a real gap.
        expected_first = pd.Timestamp(first)
        expected_last = pd.Timestamp(last)
        actual_first_ts = pd.Timestamp(actual_first)
        actual_last_ts = pd.Timestamp(actual_last)

        # If we're using a renamed successor ticker, only check the START of
        # the window against the ORIGINAL ticker's membership start — the
        # successor's own trading history naturally won't extend back to
        # cover it, that's expected, not a gap. The end-of-window check still
        # applies normally to the successor's data.
        gap_at_start = (actual_first_ts - expected_first).days > 60 if not renamed_to else False
        gap_at_end = (expected_last - actual_last_ts).days > 60

        if gap_at_start or gap_at_end:
            partial_coverage.append({
                "ticker": raw_ticker,
                "resolved_via_rename": renamed_to if found == renamed_to else None,
                "expected_window": [first, last],
                "actual_window": [actual_first, actual_last],
                "gap_at_start_days": max(0, (actual_first_ts - expected_first).days),
                "gap_at_end_days": max(0, (expected_last - actual_last_ts).days),
            })
        else:
            ok.append(raw_ticker)

    report = {
        "generated_at": datetime.datetime.now().isoformat(),
        "backtest_window": [BACKTEST_START, BACKTEST_END],
        "total_tickers_checked": len(tickers),
        "fully_covered": len(ok),
        "partial_coverage_count": len(partial_coverage),
        "no_data_count": len(no_data),
        "coverage_pct": round(len(ok) / len(tickers) * 100, 1) if tickers else 0,
        "no_data_tickers": sorted(no_data, key=lambda r: r["ticker"]),
        "partial_coverage_tickers": sorted(partial_coverage, key=lambda r: r["ticker"]),
    }
    return report


def main():
    membership = collect_all_members_in_window(BACKTEST_START, BACKTEST_END)
    logger.info("Found %d unique tickers ever in the S&P 500 during the window", len(membership))

    report = check_coverage(membership)

    os.makedirs(os.path.dirname(REPORT_PATH), exist_ok=True)
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    print()
    print("=" * 70)
    print(f"S&P 500 POINT-IN-TIME PRICE COVERAGE REPORT ({BACKTEST_START} to {BACKTEST_END})")
    print("=" * 70)
    print(f"Total tickers checked:     {report['total_tickers_checked']}")
    print(f"Fully covered:             {report['fully_covered']} ({report['coverage_pct']}%)")
    print(f"Partial coverage (gaps):   {report['partial_coverage_count']}")
    print(f"No data at all:            {report['no_data_count']}")
    print()
    if report["no_data_tickers"]:
        print("Tickers with ZERO yfinance data (likely delisted/renamed/bankrupt names —")
        print("exactly the ones a naive backtest would most want and most likely misses):")
        for row in report["no_data_tickers"][:30]:
            print(f"  {row['ticker']:10s} expected {row['expected_window'][0]} to {row['expected_window'][1]}")
        if len(report["no_data_tickers"]) > 30:
            print(f"  ... and {len(report['no_data_tickers']) - 30} more (see {REPORT_PATH})")
    print()
    print(f"Full report written to {REPORT_PATH}")


if __name__ == "__main__":
    main()
