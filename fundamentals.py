"""
Fundamental metrics from yfinance financial statements:
  - Sales CAGR, Profit CAGR (from income statement, across available years)
  - ROCE = EBIT / (Total Assets - Current Liabilities)
  - Debt/Equity = Total Debt / Total Stockholder Equity

Coverage caveat: Yahoo Finance's fundamental data, especially for NSE-listed
companies, is inconsistent — some fields are missing for many names. Every
result carries a `data_quality` flag ("ok" / "partial" / "missing") so this
is visible downstream rather than silently treated as a fail.

Fundamentals are cached to disk and only refetched on a configurable weekday
(default Monday) since these don't change daily — this keeps daily runs fast
and avoids hammering Yahoo with ~1000 extra calls every day.
"""

import concurrent.futures
import datetime
import json
import logging
import os

import numpy as np
import yfinance as yf

import config

logger = logging.getLogger(__name__)


def _cagr(begin: float, end: float, years: float):
    if begin is None or end is None or years <= 0:
        return np.nan
    if begin <= 0 or end <= 0:
        # CAGR is not meaningful across a sign change (e.g. loss-making base year)
        return np.nan
    return float(((end / begin) ** (1 / years) - 1) * 100)


def _get_fundamentals_single(yf_ticker: str) -> dict:
    out = {
        "ticker": yf_ticker,
        "sales_cagr": np.nan,
        "profit_cagr": np.nan,
        "roce": np.nan,
        "debt_equity": np.nan,
        "data_quality": "missing",
    }
    try:
        tk = yf.Ticker(yf_ticker)
        income = tk.income_stmt
        balance = tk.balance_sheet

        fields_found = 0

        if income is not None and not income.empty:
            years_available = income.columns.sort_values(ascending=True)
            n_years = min(config.FUNDAMENTAL_CAGR_YEARS, len(years_available) - 1)
            if n_years >= 1:
                first_col, last_col = years_available[-1 - n_years], years_available[-1]

                if "Total Revenue" in income.index:
                    rev_begin = income.loc["Total Revenue", first_col]
                    rev_end = income.loc["Total Revenue", last_col]
                    out["sales_cagr"] = _cagr(rev_begin, rev_end, n_years)
                    fields_found += 1

                profit_row = None
                for candidate in ["Net Income", "Net Income Common Stockholders"]:
                    if candidate in income.index:
                        profit_row = candidate
                        break
                if profit_row:
                    p_begin = income.loc[profit_row, first_col]
                    p_end = income.loc[profit_row, last_col]
                    out["profit_cagr"] = _cagr(p_begin, p_end, n_years)
                    fields_found += 1

                ebit = None
                for candidate in ["EBIT", "Operating Income"]:
                    if candidate in income.index:
                        ebit = income.loc[candidate, last_col]
                        break
            else:
                ebit = None
        else:
            ebit = None

        if balance is not None and not balance.empty:
            latest_col = balance.columns.sort_values(ascending=True)[-1]

            total_assets = balance.loc["Total Assets", latest_col] if "Total Assets" in balance.index else None
            current_liab = (
                balance.loc["Current Liabilities", latest_col]
                if "Current Liabilities" in balance.index
                else None
            )
            if ebit is not None and total_assets is not None and current_liab is not None:
                capital_employed = total_assets - current_liab
                if capital_employed and capital_employed != 0:
                    out["roce"] = float(ebit / capital_employed * 100)
                    fields_found += 1

            total_debt = None
            for candidate in ["Total Debt"]:
                if candidate in balance.index:
                    total_debt = balance.loc[candidate, latest_col]
                    break
            total_equity = None
            for candidate in ["Stockholders Equity", "Total Equity Gross Minority Interest"]:
                if candidate in balance.index:
                    total_equity = balance.loc[candidate, latest_col]
                    break
            if total_debt is not None and total_equity and total_equity != 0:
                out["debt_equity"] = float(total_debt / total_equity)
                fields_found += 1

        if fields_found == 4:
            out["data_quality"] = "ok"
        elif fields_found > 0:
            out["data_quality"] = "partial"
        else:
            out["data_quality"] = "missing"

    except Exception as exc:  # noqa: BLE001
        logger.debug("Fundamentals fetch failed for %s: %s", yf_ticker, exc)

    return out


def _load_cache() -> dict:
    if os.path.exists(config.FUNDAMENTALS_CACHE_PATH):
        with open(config.FUNDAMENTALS_CACHE_PATH) as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict):
    os.makedirs(os.path.dirname(config.FUNDAMENTALS_CACHE_PATH), exist_ok=True)
    with open(config.FUNDAMENTALS_CACHE_PATH, "w") as f:
        json.dump(cache, f)


def get_fundamentals(yf_tickers: list[str], force_refresh: bool = False) -> dict:
    """
    Returns {yf_ticker: {sales_cagr, profit_cagr, roce, debt_equity, data_quality}}.
    Refetches everything if today is the configured refresh weekday or
    force_refresh=True; otherwise serves from cache for tickers already cached.
    """
    cache = _load_cache()
    today_is_refresh_day = datetime.date.today().weekday() == config.FUNDAMENTALS_REFRESH_WEEKDAY

    to_fetch = [
        t for t in yf_tickers
        if force_refresh or today_is_refresh_day or t not in cache
    ]
    logger.info("Fundamentals: %d cached, %d to fetch", len(yf_tickers) - len(to_fetch), len(to_fetch))

    if to_fetch:
        with concurrent.futures.ThreadPoolExecutor(max_workers=config.FUNDAMENTALS_MAX_WORKERS) as ex:
            for result in ex.map(_get_fundamentals_single, to_fetch):
                cache[result["ticker"]] = result
        _save_cache(cache)

    return {t: cache.get(t, {"data_quality": "missing"}) for t in yf_tickers}


def passes_fundamental_filter(f: dict) -> bool:
    """True only if ALL four thresholds are met with known (non-NaN) values."""
    try:
        return (
            f.get("sales_cagr", np.nan) > config.MIN_SALES_CAGR
            and f.get("profit_cagr", np.nan) > config.MIN_PROFIT_CAGR
            and f.get("roce", np.nan) > config.MIN_ROCE
            and f.get("debt_equity", np.nan) < config.MAX_DEBT_EQUITY
            and not any(
                np.isnan(f.get(k, np.nan)) for k in ["sales_cagr", "profit_cagr", "roce", "debt_equity"]
            )
        )
    except TypeError:
        return False
