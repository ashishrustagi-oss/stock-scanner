"""
Point-in-time S&P 500 index membership — solves the survivorship-bias problem
that universe.get_sp500_universe() has for backtesting: that function returns
today's constituents, so applying it to a 2008 backtest would silently assume
every stock currently in the index was ALSO in the index in 2008, and would
completely omit every name that was removed since (bankruptcies, acquisitions,
underperformers dropped from the index) — the single biggest source of
inflated returns in a naive equity backtest.

DATA SOURCE:
  https://github.com/fja05680/sp500 — free, community-maintained, two files:
    1. "S&P 500 Historical Components & Changes.csv" — snapshot rows from
       1996-01-02 to 2019-01-11. Each row is (date, comma-delimited ticker
       list). Originally sourced from Andreas Clenow's "Trading Evolved" and
       merged with Wikipedia's changes list by the maintainer.
    2. "sp500_changes_since_2019.csv" — (date, add, remove) rows from
       2019-01-18 to present, used to roll the 2019-01-11 snapshot forward.

KNOWN DATA-QUALITY CAVEATS (from the maintainer's own notes — flagging here
rather than treating this as pristine, per project convention):
  - The maintainer states Wikipedia's "Selected changes" list is incomplete on
    its own; his file is cross-checked against Wikipedia's *current* list on
    each update, not a guarantee every historical change is perfectly dated.
  - The earliest years (1996-2001) may have a few missing symbols the
    maintainer has no independent way to verify. NOT a concern for this
    project since the backtest range starts 2008.
  - Ticker symbols in the historical file sometimes carry a "-YYYYMM" suffix
    (e.g. "AAMRQ-201312") noting when that specific ticker later left the
    index. This is metadata, not part of the tradeable symbol — stripped
    before use.

USAGE:
    from sp500_point_in_time import get_point_in_time_sp500_universe
    df = get_point_in_time_sp500_universe(pd.Timestamp("2008-09-15"))
    # -> DataFrame with columns: ticker, yf_ticker, name, sector
    # (name/sector best-effort joined from the current constituents list;
    # delisted/renamed tickers not in the current list get "UNKNOWN")

This intentionally mirrors universe.py's DataFrame shape so it can be dropped
into backtest.py wherever get_sp500_universe() is currently called for a
historical (not live) run.
"""

import json
import logging
import os
import re
from bisect import bisect_right

import pandas as pd
import requests

import config

logger = logging.getLogger(__name__)

TICKER_SUFFIX_RE = re.compile(r"-\d{6}$")  # matches trailing "-YYYYMM"


def _clean_ticker(raw: str) -> str:
    """Strip the informational "-YYYYMM" removal-date suffix, if present."""
    return TICKER_SUFFIX_RE.sub("", raw.strip())


def _fetch_csv_text(url: str) -> str:
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            resp = requests.get(url, timeout=20)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:  # noqa: BLE001 - retry on anything
            logger.warning(
                "S&P500 point-in-time fetch attempt %d/%d failed for %s: %s",
                attempt, config.MAX_RETRIES, url, exc,
            )
    raise RuntimeError(f"All fetch attempts failed for {url}")


def _build_timeline() -> list[dict]:
    """
    Returns a sorted list of {"date": "YYYY-MM-DD", "tickers": [...]}
    snapshots spanning 1996-01-02 to the most recent recorded change.

    Snapshots only exist on dates where the index actually changed — querying
    any date in between should use the most recent snapshot at or before it
    (see get_sp500_members_asof below).
    """
    import io

    hist_text = _fetch_csv_text(config.SP500_HISTORICAL_URL)
    hist_df = pd.read_csv(io.StringIO(hist_text))

    timeline = []
    for _, row in hist_df.iterrows():
        tickers = sorted({_clean_ticker(t) for t in row["tickers"].split(",") if t.strip()})
        timeline.append({"date": row["date"], "tickers": tickers})

    timeline.sort(key=lambda r: r["date"])
    current_set = set(timeline[-1]["tickers"])
    last_date = timeline[-1]["date"]

    changes_text = _fetch_csv_text(config.SP500_CHANGES_SINCE_URL)
    changes_df = pd.read_csv(io.StringIO(changes_text))
    changes_df = changes_df[changes_df["date"] > last_date].sort_values("date")

    for _, row in changes_df.iterrows():
        adds = {_clean_ticker(t) for t in str(row.get("add", "")).split(",") if t.strip()}
        removes = {_clean_ticker(t) for t in str(row.get("remove", "")).split(",") if t.strip()}
        current_set = (current_set - removes) | adds
        timeline.append({"date": row["date"], "tickers": sorted(current_set)})

    logger.info(
        "S&P500 point-in-time timeline built: %d snapshots, %s to %s",
        len(timeline), timeline[0]["date"], timeline[-1]["date"],
    )
    return timeline


def _load_cached_timeline() -> list[dict] | None:
    if os.path.exists(config.SP500_TIMELINE_CACHE_PATH):
        try:
            with open(config.SP500_TIMELINE_CACHE_PATH) as f:
                return json.load(f)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to read S&P500 timeline cache, rebuilding: %s", exc)
    return None


def _save_cached_timeline(timeline: list[dict]) -> None:
    os.makedirs(os.path.dirname(config.SP500_TIMELINE_CACHE_PATH), exist_ok=True)
    with open(config.SP500_TIMELINE_CACHE_PATH, "w") as f:
        json.dump(timeline, f)


def get_timeline(force_refresh: bool = False) -> list[dict]:
    """
    Returns the cached timeline, building and caching it on first call
    (or when force_refresh=True). The underlying source files are small and
    change infrequently, so this is safe to refresh on a weekly cadence —
    call with force_refresh=True from the weekly scan, not every run.
    """
    if not force_refresh:
        cached = _load_cached_timeline()
        if cached:
            return cached

    timeline = _build_timeline()
    _save_cached_timeline(timeline)
    return timeline


def get_sp500_members_asof(asof_date, timeline: list[dict] | None = None) -> list[str]:
    """
    Returns the list of tickers that were S&P 500 members on asof_date.

    asof_date : str "YYYY-MM-DD" or pandas-compatible date
    timeline  : pass in a pre-loaded timeline (from get_timeline()) when
                calling this many times in a backtest loop, to avoid
                re-reading the cache file on every call.
    """
    if timeline is None:
        timeline = get_timeline()

    asof_str = pd.Timestamp(asof_date).strftime("%Y-%m-%d")
    dates = [row["date"] for row in timeline]

    idx = bisect_right(dates, asof_str) - 1
    if idx < 0:
        logger.warning(
            "Requested date %s is before the earliest available snapshot (%s) — "
            "returning empty list.", asof_str, dates[0],
        )
        return []

    return timeline[idx]["tickers"]


def get_all_members_in_window(start, end, timeline: list[dict] | None = None) -> dict[str, list[str]]:
    """
    Returns {yf_ticker: [first_seen_date, last_seen_date]} for every ticker
    that was an S&P 500 member at least once between start and end.

    This answers a DIFFERENT question than get_sp500_members_asof(): that
    function asks "who was in the index on THIS SPECIFIC DATE"; this one
    asks "who should my price-data fetch cover for this WHOLE BACKTEST
    WINDOW". Used to build the ticker list to fetch once, before a
    multi-snapshot backtest — then get_sp500_members_asof() is used inside
    the per-snapshot loop to correctly restrict to only the tickers that
    were ACTUALLY members on each individual snapshot date. Using this
    function's output as if it were valid on every date within the window
    would reintroduce the exact look-ahead-inclusion bias this module
    exists to prevent (a stock added to the index in 2020 would incorrectly
    appear "eligible" in a 2010 snapshot).

    Tickers are yfinance-formatted (dots -> dashes) — matching
    get_point_in_time_sp500_universe().
    """
    if timeline is None:
        timeline = get_timeline()

    start_str = pd.Timestamp(start).strftime("%Y-%m-%d")
    end_str = pd.Timestamp(end).strftime("%Y-%m-%d")
    window = [row for row in timeline if start_str <= row["date"] <= end_str]

    first_seen: dict[str, str] = {}
    last_seen: dict[str, str] = {}
    for row in window:
        for raw_t in row["tickers"]:
            t = raw_t.replace(".", "-")
            if t not in first_seen:
                first_seen[t] = row["date"]
            last_seen[t] = row["date"]

    return {t: [first_seen[t], last_seen[t]] for t in first_seen}


def get_point_in_time_sp500_universe(asof_date, timeline: list[dict] | None = None) -> pd.DataFrame:
    """
    Returns a DataFrame shaped like universe.get_sp500_universe():
    columns ticker, yf_ticker, name, sector — but reflecting membership as it
    actually was on asof_date, not today.

    name/sector are best-effort joined from the CURRENT constituents list
    (config.SP500_SOURCE_URL). For tickers that have since been delisted,
    renamed, or removed from the index, name/sector will show "UNKNOWN" since
    that lookup only has today's roster — this does not affect price/
    indicator computation, only display metadata.
    """
    members = get_sp500_members_asof(asof_date, timeline=timeline)
    if not members:
        return pd.DataFrame(columns=["ticker", "yf_ticker", "name", "sector"])

    try:
        current = pd.read_csv(config.SP500_SOURCE_URL)
        current = current.rename(
            columns={"Symbol": "ticker", "Security": "name", "GICS Sector": "sector"}
        )
        current["ticker"] = current["ticker"].str.replace(".", "-", regex=False)
        name_map = current.set_index("ticker")["name"].to_dict()
        sector_map = current.set_index("ticker")["sector"].to_dict()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch current constituents for name/sector join: %s", exc)
        name_map, sector_map = {}, {}

    df = pd.DataFrame({"ticker": members})
    df["ticker"] = df["ticker"].str.replace(".", "-", regex=False)
    df["yf_ticker"] = df["ticker"]
    df["name"] = df["ticker"].map(name_map).fillna("UNKNOWN")
    df["sector"] = df["ticker"].map(sector_map).fillna("UNKNOWN")

    return df[["ticker", "yf_ticker", "name", "sector"]].drop_duplicates(subset="ticker")
