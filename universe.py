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


def _fetch_nse_index_csv(session: requests.Session, url: str, min_rows: int) -> pd.DataFrame:
    """Shared fetch+parse logic for any ind_*list.csv NSE index file."""
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            session.get(config.NSE_HOME_URL, timeout=10)  # primes cookies
            resp = session.get(url, timeout=15)
            resp.raise_for_status()
            df = pd.read_csv(io.StringIO(resp.text))
            df = df.rename(
                columns={"Symbol": "ticker", "Company Name": "name", "Industry": "sector"}
            )
            df["yf_ticker"] = df["ticker"].astype(str).str.strip() + ".NS"
            cols = [c for c in ["ticker", "yf_ticker", "name", "sector"] if c in df.columns]
            out = df[cols].drop_duplicates(subset="ticker")
            if len(out) < min_rows:
                raise ValueError(f"Unexpectedly small list ({len(out)} rows) from {url} — likely a bad response")
            return out
        except Exception as exc:  # noqa: BLE001 - retry on anything, fall back at the caller
            logger.warning("Fetch attempt %d/%d failed for %s: %s", attempt, config.MAX_RETRIES, url, exc)
            time.sleep(config.RETRY_BACKOFF_SECONDS)
    raise RuntimeError(f"All fetch attempts failed for {url}")


def get_nse_smallmicro_universe() -> pd.DataFrame:
    """
    Returns DataFrame with columns: ticker, yf_ticker, name, sector — Nifty
    Smallcap 250 + Nifty Microcap 250 combined and de-duplicated.

    Deliberately NOT merged with NSE500 anywhere in the pipeline. Smallcap
    250 names mostly already exist in NSE500 (by NSE's own index rule);
    Microcap 250 names are compulsorily EXCLUDED from Nifty 500, so this is
    where the genuinely new tickers come from. See config.py for the source
    URLs and rationale, and README for why this stays a separate sheet tab
    with no composite_score / EliteCompounderScore.
    """
    session = requests.Session()
    session.headers.update(NSE_HEADERS)

    try:
        smallcap = _fetch_nse_index_csv(session, config.NSE_SMALLCAP250_SOURCE_URL, min_rows=100)
        logger.info("Fetched live Smallcap 250 list: %d tickers", len(smallcap))
    except Exception as exc:  # noqa: BLE001
        logger.error("Smallcap 250 fetch failed entirely: %s", exc)
        smallcap = pd.DataFrame(columns=["ticker", "yf_ticker", "name", "sector"])

    if config.NSE_MICROCAP_ENABLED:
        try:
            microcap = _fetch_nse_index_csv(session, config.NSE_MICROCAP250_SOURCE_URL, min_rows=100)
            logger.info("Fetched live Microcap 250 list: %d tickers", len(microcap))
        except Exception as exc:  # noqa: BLE001
            logger.error("Microcap 250 fetch failed entirely: %s", exc)
            microcap = pd.DataFrame(columns=["ticker", "yf_ticker", "name", "sector"])
    else:
        # Skip the attempt entirely rather than retry 3x against a URL known
        # to IP-block GitHub Actions runners — see config.NSE_MICROCAP_ENABLED.
        logger.info(
            "Microcap 250 fetch skipped (config.NSE_MICROCAP_ENABLED=False) — "
            "running with Smallcap 250 only. See config.py for why."
        )
        microcap = pd.DataFrame(columns=["ticker", "yf_ticker", "name", "sector"])

    combined = pd.concat([smallcap, microcap], ignore_index=True).drop_duplicates(subset="ticker")

    if len(combined) < 100:
        logger.error(
            "Both Smallcap 250 and Microcap 250 fetches failed or returned too few rows. "
            "Falling back to a %d-ticker seed list. This is NOT the real universe — fix the live fetch.",
            len(config.NSE_SMALLMICRO_FALLBACK_TICKERS),
        )
        return pd.DataFrame(
            {
                "ticker": config.NSE_SMALLMICRO_FALLBACK_TICKERS,
                "yf_ticker": [t + ".NS" for t in config.NSE_SMALLMICRO_FALLBACK_TICKERS],
                "name": config.NSE_SMALLMICRO_FALLBACK_TICKERS,
                "sector": ["UNKNOWN"] * len(config.NSE_SMALLMICRO_FALLBACK_TICKERS),
            }
        )

    logger.info(
        "NSE Small/Micro universe ready: %d unique tickers (%d smallcap, %d microcap%s)",
        len(combined), len(smallcap), len(microcap),
        ", overlap-deduped" if config.NSE_MICROCAP_ENABLED else " — microcap fetch disabled",
    )
    return combined
