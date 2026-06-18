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
