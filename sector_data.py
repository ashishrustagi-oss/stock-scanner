"""
Sector benchmark fetching, for the RS_SECTOR calculation in the Elite
Compounder Early Detection System.

Fetches each UNIQUE sector benchmark's price history once per universe (not
once per stock — there are only a handful of distinct sectors vs. hundreds
of stocks), and falls back to the broad market index for any sector whose
benchmark ticker can't be resolved or fails to fetch. The fallback is always
visible downstream via the `source` returned for each sector — never silent.

US sector mapping (GICS Sector -> SPDR ETF) is well-established and reliable.
<<<<<<< HEAD
NSE sector mapping (industry label -> NSE sector index) is best-effort; some
tickers may not resolve via yfinance. Check the `sector_index_source` column
in the output after the first live run.
"""

import logging
=======

NSE sector mapping uses an EXACT (normalized) match against the real 20
"Sector" labels NSE actually uses across the NSE500 list — confirmed from a
live sheet on 2026-06-19, not guessed. Matching is done on a normalized form
(lowercase, "&"/"and" stripped, punctuation removed, whitespace collapsed) so
minor formatting differences in the source CSV don't break the match.
Several sectors are intentionally left unmapped (Capital Goods, Chemicals,
Construction, etc.) because there's no free index ticker I can confidently
confirm resolves on Yahoo Finance for them — these fall back to RS vs. Nifty
50, which is an honest limitation of free data rather than a bug to chase.
"""

import logging
import re
>>>>>>> 03c5cc34f7ef9d7e7eadf5834ebb208ad360f07a

import pandas as pd
import yfinance as yf

import config

logger = logging.getLogger(__name__)


<<<<<<< HEAD
def _match_ticker(sector_label: str, mapping: dict) -> str | None:
    if not sector_label or pd.isna(sector_label):
        return None
    label_lower = str(sector_label).lower()
    for key, ticker in mapping.items():
        if key.lower() in label_lower:
=======
def _normalize_label(s: str) -> str:
    """Lowercase, strip '&'/'and', remove punctuation, collapse whitespace."""
    s = str(s).lower()
    s = s.replace("&", " ")
    s = re.sub(r"\band\b", " ", s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _match_ticker(sector_label: str, mapping: dict) -> str | None:
    """
    Exact match on the normalized label first (reliable, since `mapping`'s
    keys are now the real labels NSE/GICS use). Falls back to substring
    containment as a safety net for any label variant not seen before.
    """
    if not sector_label or pd.isna(sector_label):
        return None
    normalized = _normalize_label(sector_label)

    for key, ticker in mapping.items():
        if _normalize_label(key) == normalized:
            return ticker

    # Safety-net substring fallback for unexpected label variants
    for key, ticker in mapping.items():
        if _normalize_label(key) in normalized:
>>>>>>> 03c5cc34f7ef9d7e7eadf5834ebb208ad360f07a
            return ticker
    return None


def _fetch_ticker_close(ticker: str) -> pd.Series:
    df = yf.download(
        ticker, period=config.PRICE_HISTORY_PERIOD, interval="1d",
        auto_adjust=True, progress=False,
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.dropna(how="all")
    if df.empty or "Close" not in df.columns:
        raise ValueError(f"No usable data returned for sector ticker {ticker}")
    return df["Close"]


def get_sector_close_map(universe_label: str, sector_labels: list[str], fallback_close: pd.Series) -> dict:
    """
    Returns {sector_label: (close_series, source_str)} for every unique,
    non-null sector label passed in. `source_str` is either the resolved
    ticker symbol (e.g. "XLK") or "FALLBACK_BROAD_INDEX" if no mapping
    existed or the fetch failed.
    """
    mapping = config.SECTOR_INDEX_MAP_NSE if universe_label == "NSE500" else config.SECTOR_INDEX_MAP_US
    unique_sectors = sorted({s for s in sector_labels if s and not pd.isna(s)})

    sector_to_ticker = {s: _match_ticker(s, mapping) for s in unique_sectors}
    unique_tickers = sorted({t for t in sector_to_ticker.values() if t})

    ticker_close = {}
    for t in unique_tickers:
        try:
            ticker_close[t] = _fetch_ticker_close(t)
            logger.info("Sector benchmark fetched: %s", t)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Sector benchmark fetch failed for %s: %s — will fall back to broad index", t, exc)

    result = {}
    for s in unique_sectors:
        t = sector_to_ticker.get(s)
        if t and t in ticker_close:
            result[s] = (ticker_close[t], t)
        else:
            result[s] = (fallback_close, "FALLBACK_BROAD_INDEX")

    n_real = sum(1 for _, src in result.values() if src != "FALLBACK_BROAD_INDEX")
    logger.info(
        "%s sector benchmarks: %d/%d sectors resolved to a real sector index, %d fell back to broad market",
        universe_label, n_real, len(result), len(result) - n_real,
    )
    return result
