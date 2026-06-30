"""
Batched price-history fetcher. Pulls daily OHLCV for a list of tickers in
chunks (yfinance gets unreliable/blocked on very large single calls), with
retry/backoff. Returns a dict of {yf_ticker: DataFrame}.
"""

import logging
import time

import pandas as pd
import yfinance as yf

import config

logger = logging.getLogger(__name__)


def _chunk(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def fetch_price_history(yf_tickers: list[str]) -> dict[str, pd.DataFrame]:
    """
    Returns {ticker: DataFrame[Open, High, Low, Close, Volume]} for every
    ticker we could successfully fetch. Tickers that fail after retries are
    simply omitted (and logged) rather than crashing the whole run.
    """
    results: dict[str, pd.DataFrame] = {}

    for batch_num, batch in enumerate(_chunk(yf_tickers, config.BATCH_SIZE), start=1):
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                data = yf.download(
                    tickers=batch,
                    period=config.PRICE_HISTORY_PERIOD,
                    interval="1d",
                    group_by="ticker",
                    auto_adjust=True,
                    threads=True,
                    progress=False,
                )
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Batch %d attempt %d/%d failed: %s", batch_num, attempt, config.MAX_RETRIES, exc
                )
                time.sleep(config.RETRY_BACKOFF_SECONDS)
                data = None

        if data is None:
            logger.error("Batch %d failed all retries, skipping %d tickers", batch_num, len(batch))
            continue

        # yfinance returns a flat frame if batch size == 1, MultiIndex columns otherwise
        if len(batch) == 1:
            t = batch[0]
            df = data.dropna(how="all")
            if not df.empty:
                results[t] = df
        else:
            for t in batch:
                try:
                    df = data[t].dropna(how="all")
                    if not df.empty and df["Close"].notna().sum() > 60:
                        results[t] = df
                except (KeyError, TypeError):
                    logger.debug("No data returned for %s", t)

        logger.info("Batch %d/%d done — %d/%d tickers fetched so far",
                     batch_num, -(-len(yf_tickers) // config.BATCH_SIZE), len(results), len(yf_tickers))
        time.sleep(config.BATCH_SLEEP_SECONDS)

    return results


def fetch_index_history(index_ticker: str) -> pd.DataFrame:
    df = yf.download(
        tickers=index_ticker,
        period=config.PRICE_HISTORY_PERIOD,
        interval="1d",
        auto_adjust=True,
        progress=False,
    )
    # Single-ticker download can still come back with MultiIndex columns in some yfinance versions
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna(how="all")


# ----------------------------------------------------------------------------
# Date-range variants — for backtesting a SPECIFIC historical window (e.g. a
# bear-market period) rather than "however many years back from today."
# Built 29-06-2026 specifically because backtest.py's main() always computed
# start_date = today - BACKTEST_LOOKBACK_YEARS, which structurally can't
# reach a fixed historical window like the COVID crash (Feb-Oct 2020) once
# enough time has passed that it falls outside any reasonable "years back
# from today" range. yfinance's period="Ny" parameter is ALWAYS relative to
# the current date — there's no way to ask it for "data ending in early
# 2021" via that parameter, hence start=/end= here instead.
#
# Deliberately NEW functions, not modifications to fetch_price_history/
# fetch_index_history above — the daily scan and the "recent N years"
# backtest mode both keep using the original period= functions completely
# unchanged. Zero risk to anything currently working.
# ----------------------------------------------------------------------------

def fetch_price_history_range(
    yf_tickers: list[str], start: str, end: str,
) -> dict[str, pd.DataFrame]:
    """
    Same batching/retry/results-shape contract as fetch_price_history()
    above, but for an explicit [start, end) date window instead of "the
    last N years from today." start/end are 'YYYY-MM-DD' strings, passed
    straight to yfinance's start=/end= parameters.
    """
    results: dict[str, pd.DataFrame] = {}

    for batch_num, batch in enumerate(_chunk(yf_tickers, config.BATCH_SIZE), start=1):
        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                data = yf.download(
                    tickers=batch,
                    start=start,
                    end=end,
                    interval="1d",
                    group_by="ticker",
                    auto_adjust=True,
                    threads=True,
                    progress=False,
                )
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Range batch %d attempt %d/%d failed: %s", batch_num, attempt, config.MAX_RETRIES, exc
                )
                time.sleep(config.RETRY_BACKOFF_SECONDS)
                data = None

        if data is None:
            logger.error("Range batch %d failed all retries, skipping %d tickers", batch_num, len(batch))
            continue

        if len(batch) == 1:
            t = batch[0]
            df = data.dropna(how="all")
            if not df.empty:
                results[t] = df
        else:
            for t in batch:
                try:
                    df = data[t].dropna(how="all")
                    if not df.empty and df["Close"].notna().sum() > 60:
                        results[t] = df
                except (KeyError, TypeError):
                    logger.debug("No data returned for %s in range %s to %s", t, start, end)

        logger.info("Range batch %d/%d done — %d/%d tickers fetched so far",
                     batch_num, -(-len(yf_tickers) // config.BATCH_SIZE), len(results), len(yf_tickers))
        time.sleep(config.BATCH_SLEEP_SECONDS)

    return results


def fetch_index_history_range(index_ticker: str, start: str, end: str) -> pd.DataFrame:
    """Date-range variant of fetch_index_history() above — see module docstring."""
    df = yf.download(
        tickers=index_ticker,
        start=start,
        end=end,
        interval="1d",
        auto_adjust=True,
        progress=False,
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna(how="all")
