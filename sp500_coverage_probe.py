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

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import data_fetch  # noqa: E402
import sp500_point_in_time as spt  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("sp500_coverage_probe")

BACKTEST_START = "2008-01-01"
BACKTEST_END = datetime.date.today().strftime("%Y-%m-%d")
REPORT_PATH = "cache/sp500_coverage_report.json"


def collect_all_members_in_window(start: str, end: str) -> dict[str, list[str]]:
    """
    Returns {ticker: [first_seen_date, last_seen_date]} for every ticker that
    appeared in the point-in-time universe at least once between start/end.
    This tells us the membership window we EXPECT price data to cover,
    which is different (and usually shorter) than "IPO to delisting."
    """
    timeline = spt.get_timeline()
    window = [row for row in timeline if start <= row["date"] <= end]

    first_seen: dict[str, str] = {}
    last_seen: dict[str, str] = {}
    for row in window:
        for t in row["tickers"]:
            if t not in first_seen:
                first_seen[t] = row["date"]
            last_seen[t] = row["date"]

    return {t: [first_seen[t], last_seen[t]] for t in first_seen}


def check_coverage(membership: dict[str, list[str]]) -> dict:
    tickers = list(membership.keys())
    logger.info("Checking yfinance coverage for %d tickers (%s to %s)...",
                len(tickers), BACKTEST_START, BACKTEST_END)

    price_data = data_fetch.fetch_price_history_range(tickers, BACKTEST_START, BACKTEST_END)

    no_data = []
    partial_coverage = []
    ok = []

    for ticker, (first, last) in membership.items():
        if ticker not in price_data or price_data[ticker].empty:
            no_data.append({"ticker": ticker, "expected_window": [first, last]})
            continue

        df = price_data[ticker]
        actual_first = df.index.min().strftime("%Y-%m-%d")
        actual_last = df.index.max().strftime("%Y-%m-%d")

        # Allow a generous grace window (60 days) since exact IPO/delisting-day
        # data can be legitimately thin without indicating a real gap.
        expected_first = pd.Timestamp(first)
        expected_last = pd.Timestamp(last)
        actual_first_ts = pd.Timestamp(actual_first)
        actual_last_ts = pd.Timestamp(actual_last)

        gap_at_start = (actual_first_ts - expected_first).days > 60
        gap_at_end = (expected_last - actual_last_ts).days > 60

        if gap_at_start or gap_at_end:
            partial_coverage.append({
                "ticker": ticker,
                "expected_window": [first, last],
                "actual_window": [actual_first, actual_last],
                "gap_at_start_days": max(0, (actual_first_ts - expected_first).days),
                "gap_at_end_days": max(0, (expected_last - actual_last_ts).days),
            })
        else:
            ok.append(ticker)

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
