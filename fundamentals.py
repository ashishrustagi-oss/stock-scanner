"""
Fundamental metrics from yfinance financial statements:
  - Sales CAGR, Profit CAGR (from income statement, across available years)
  - ROCE = EBIT / (Total Assets - Current Liabilities)
  - Debt/Equity = Total Debt / Total Stockholder Equity
  - Earnings Acceleration (Phase 3 / Module 1): quarter-over-quarter EPS and
    revenue growth-rate change, see _extract_earnings_acceleration() below
    for the QoQ-vs-YoY design choice and why.

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


def _qoq_growth(current: float, prior: float) -> float:
    if current is None or prior is None or prior == 0:
        return np.nan
    if (current < 0) != (prior < 0) and prior < 0:
        # Sign flips around a negative base aren't meaningful as a % growth figure
        return np.nan
    return float((current - prior) / abs(prior) * 100)


def _extract_earnings_acceleration(tk: "yf.Ticker") -> dict:
    """
    Module 1 (Phase 3): is this stock's quarterly growth rate itself
    speeding up or slowing down?

    DESIGN CHOICE — quarter-over-quarter (QoQ), not year-over-year (YoY):
    True YoY-based acceleration (comparing this quarter's YoY growth to last
    quarter's YoY growth) needs 6 quarters of history (current, prior, and
    the same-quarter-last-year pair for each). Yahoo Finance's quarterly
    statements typically only expose ~4-5 trailing quarters via yfinance,
    which usually isn't enough for that. QoQ only needs 3 quarters, which is
    much more likely to actually be available — but it trades that
    reliability for a real limitation: QoQ growth is sensitive to
    seasonality. A retailer's Q4-vs-Q3 will look artificially strong every
    single year regardless of underlying business health, purely because of
    the holiday quarter. Treat earnings_acceleration_score with that caveat
    in mind, especially for seasonal businesses — it's a genuine trade-off,
    not a hidden bug.
    """
    out = {
        "eps_growth_latest_qtr": np.nan, "eps_growth_prev_qtr": np.nan, "eps_acceleration": np.nan,
        "revenue_growth_latest_qtr": np.nan, "revenue_growth_prev_qtr": np.nan, "revenue_acceleration": np.nan,
        "earnings_data_quality": "missing",
    }
    try:
        q_income = tk.quarterly_income_stmt
        if q_income is None or q_income.empty or q_income.shape[1] < 3:
            return out  # need at least 3 quarters (Q0, Q1, Q2)

        quarters = q_income.columns.sort_values(ascending=True)  # oldest -> newest
        q0, q1, q2 = quarters[-1], quarters[-2], quarters[-3]    # newest, prior, two back

        fields_found = 0

        eps_row = None
        for candidate in ["Diluted EPS", "Basic EPS"]:
            if candidate in q_income.index:
                eps_row = candidate
                break
        if eps_row:
            eps0 = q_income.loc[eps_row, q0]
            eps1 = q_income.loc[eps_row, q1]
            eps2 = q_income.loc[eps_row, q2]
            out["eps_growth_latest_qtr"] = _qoq_growth(eps0, eps1)
            out["eps_growth_prev_qtr"] = _qoq_growth(eps1, eps2)
            if not np.isnan(out["eps_growth_latest_qtr"]) and not np.isnan(out["eps_growth_prev_qtr"]):
                out["eps_acceleration"] = out["eps_growth_latest_qtr"] - out["eps_growth_prev_qtr"]
                fields_found += 1

        if "Total Revenue" in q_income.index:
            rev0 = q_income.loc["Total Revenue", q0]
            rev1 = q_income.loc["Total Revenue", q1]
            rev2 = q_income.loc["Total Revenue", q2]
            out["revenue_growth_latest_qtr"] = _qoq_growth(rev0, rev1)
            out["revenue_growth_prev_qtr"] = _qoq_growth(rev1, rev2)
            if not np.isnan(out["revenue_growth_latest_qtr"]) and not np.isnan(out["revenue_growth_prev_qtr"]):
                out["revenue_acceleration"] = out["revenue_growth_latest_qtr"] - out["revenue_growth_prev_qtr"]
                fields_found += 1

        out["earnings_data_quality"] = "ok" if fields_found == 2 else ("partial" if fields_found == 1 else "missing")

    except Exception as exc:  # noqa: BLE001
        logger.debug("Earnings acceleration extraction failed: %s", exc)

    return out


def _get_fundamentals_single(yf_ticker: str) -> dict:
    out = {
        "ticker": yf_ticker,
        "sales_cagr": np.nan,
        "profit_cagr": np.nan,
        "roce": np.nan,
        "debt_equity": np.nan,
        "data_quality": "missing",
        "eps_growth_latest_qtr": np.nan, "eps_growth_prev_qtr": np.nan, "eps_acceleration": np.nan,
        "revenue_growth_latest_qtr": np.nan, "revenue_growth_prev_qtr": np.nan, "revenue_acceleration": np.nan,
        "earnings_data_quality": "missing",
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

        # Phase 3 (Module 1): earnings acceleration, isolated so a failure
        # here can't take down the annual fundamentals computed above.
        out.update(_extract_earnings_acceleration(tk))

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
