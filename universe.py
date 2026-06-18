"""
Loads the two stock universes (NSE500, S&P500) from live sources.

NSE blocks plain requests without browser-like headers and a primed session
cookie, so we visit the homepage first to pick up cookies before hitting the
CSV endpoint. If that fails twice, we fall back to a tiny seed list (see
config.NSE_FALLBACK_TICKERS) purely so the rest of the pipeline can still run
— this is NOT a real substitute for the full NSE500 and should be treated as
a signal that the live fetch needs attention.
"""

import io
import logging
import time

import pandas as pd
import requests

import config

logger = logging.getLogger(__name__)

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/csv,application/csv,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def get_sp500_universe() -> pd.DataFrame:
    """Returns DataFrame with columns: ticker, name, sector"""
    df = pd.read_csv(config.SP500_SOURCE_URL)
    df = df.rename(columns={"Symbol": "ticker", "Security": "name", "GICS Sector": "sector"})
    df["ticker"] = df["ticker"].str.replace(".", "-", regex=False)  # yfinance format, e.g. BRK.B -> BRK-B
    df["yf_ticker"] = df["ticker"]
    return df[["ticker", "yf_ticker", "name", "sector"]].drop_duplicates(subset="ticker")


def get_nse500_universe() -> pd.DataFrame:
    """Returns DataFrame with columns: ticker, yf_ticker, name, sector"""
    session = requests.Session()
    session.headers.update(NSE_HEADERS)

    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            session.get(config.NSE_HOME_URL, timeout=10)  # primes cookies
            resp = session.get(config.NSE500_SOURCE_URL, timeout=15)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
            df = df.rename(
                columns={
                    "Symbol": "ticker",
                    "Company Name": "name",
                    "Industry": "sector",
                }
            )
            df["yf_ticker"] = df["ticker"].astype(str).str.strip() + ".NS"
            cols = [c for c in ["ticker", "yf_ticker", "name", "sector"] if c in df.columns]
            out = df[cols].drop_duplicates(subset="ticker")
            if len(out) < 100:
                raise ValueError(f"Unexpectedly small NSE500 list ({len(out)} rows) — likely a bad response")
            logger.info("Fetched live NSE500 list: %d tickers", len(out))
            return out
        except Exception as exc:  # noqa: BLE001 - we want to retry on anything and fall back
            logger.warning("NSE500 fetch attempt %d/%d failed: %s", attempt, config.MAX_RETRIES, exc)
            time.sleep(config.RETRY_BACKOFF_SECONDS)

    logger.error(
        "All NSE500 fetch attempts failed. Falling back to a %d-ticker seed list. "
        "This is NOT the real NSE500 — fix the live fetch.",
        len(config.NSE_FALLBACK_TICKERS),
    )
    fallback = pd.DataFrame(
        {
            "ticker": config.NSE_FALLBACK_TICKERS,
            "yf_ticker": [t + ".NS" for t in config.NSE_FALLBACK_TICKERS],
            "name": config.NSE_FALLBACK_TICKERS,
            "sector": ["UNKNOWN"] * len(config.NSE_FALLBACK_TICKERS),
        }
    )
    return fallback
