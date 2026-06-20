"""
Reads the user's manually-imported Zerodha holdings from a Google Sheet tab
(named `config.MY_HOLDINGS_TAB_NAME`, default "My_Holdings") and enriches
each holding with the full technical/fundamental scoring pipeline.

Two paths per holding:
  - FAST PATH: the stock is already in this run's NSE500 scan (likely for
    most large/mid-cap holdings) — just look up its already-computed row,
    zero extra API calls.
  - SLOW PATH: the stock isn't in the top-500 scan universe — fetch its
    price history fresh and compute the same absolute-value indicators
    (OBV, MACD, Supertrend, RS vs Nifty, etc). composite_score and
    EliteCompounderScore are deliberately NOT computed for these — both are
    cross-sectional percentile scores that are statistically meaningless
    ranked against a peer group of 1-2 stocks, so showing a number there
    would be actively misleading. They're marked "Outside scan universe"
    instead.

How holdings get into the sheet: manually, via Google Sheets' own
File → Import feature, importing the XLSX exported from Zerodha Console
(Holdings → Export). This module never writes to that tab — only reads it —
so re-importing updated holdings whenever you trade never conflicts with
anything this script does.
"""

import logging
import re

import gspread
import numpy as np
import pandas as pd
from google.oauth2.service_account import Credentials

import config
import data_fetch
import fundamentals as fnd
import indicators as ind
import metrics_builder

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# Flexible column-name matching — Zerodha's export header wording has
# varied slightly across versions of Console, so match by keyword rather
# than an exact string.
SYMBOL_COL_KEYWORDS = ["instrument", "symbol", "tradingsymbol"]
QTY_COL_KEYWORDS = ["qty", "quantity"]
AVG_COST_COL_KEYWORDS = ["avg. cost", "avg cost", "average price", "avg price", "average cost"]


def _get_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(
        config.GOOGLE_SERVICE_ACCOUNT_JSON_PATH, scopes=SCOPES
    )
    return gspread.authorize(creds)


def _find_col(headers: list[str], keywords: list[str]) -> int | None:
    for i, h in enumerate(headers):
        h_lower = str(h).strip().lower()
        if any(k in h_lower for k in keywords):
            return i
    return None


def _parse_holdings_rows(raw_rows: list[list[str]]) -> pd.DataFrame:
    """
    Zerodha's exported XLSX (once imported into a Sheets tab) usually has
    the real header row a few rows down, not row 1 — finds whichever row
    contains a recognizable "Instrument"-like header and parses from there.
    """
    header_row_idx = None
    for i, row in enumerate(raw_rows):
        if _find_col(row, SYMBOL_COL_KEYWORDS) is not None:
            header_row_idx = i
            break

    if header_row_idx is None:
        logger.warning(
            "Could not find a recognizable header row (looked for a column "
            "matching %s) in the holdings tab — check the import.", SYMBOL_COL_KEYWORDS
        )
        return pd.DataFrame()

    headers = raw_rows[header_row_idx]
    sym_idx = _find_col(headers, SYMBOL_COL_KEYWORDS)
    qty_idx = _find_col(headers, QTY_COL_KEYWORDS)
    cost_idx = _find_col(headers, AVG_COST_COL_KEYWORDS)

    records = []
    for row in raw_rows[header_row_idx + 1:]:
        if sym_idx is None or sym_idx >= len(row) or not row[sym_idx].strip():
            continue
        symbol = row[sym_idx].strip().upper()
        try:
            qty = float(row[qty_idx]) if qty_idx is not None and qty_idx < len(row) and row[qty_idx] else None
        except ValueError:
            qty = None
        try:
            avg_cost = float(row[cost_idx]) if cost_idx is not None and cost_idx < len(row) and row[cost_idx] else None
        except ValueError:
            avg_cost = None
        records.append({"symbol": symbol, "qty": qty, "avg_cost": avg_cost})

    return pd.DataFrame(records)


def _fetch_fresh_indicators(symbol: str, index_close: pd.Series) -> dict:
    """Slow path: fetch + compute absolute-value indicators for a holding outside the scan universe."""
    yf_ticker = symbol + ".NS"
    try:
        price_data = data_fetch.fetch_price_history([yf_ticker])
        df = price_data.get(yf_ticker)
        if df is None or df.empty:
            return {"data_quality": "missing"}

        row = metrics_builder.build_metrics_row(
            yf_ticker, df, index_close, sector_close=index_close, sector_source="OUTSIDE_SCAN_UNIVERSE"
        )

        fundamentals_map = fnd.get_fundamentals([yf_ticker])
        fund = fundamentals_map.get(yf_ticker, {})
        row.update(fund)
        row["fundamentally_qualified"] = fnd.passes_fundamental_filter(fund)
        row["in_scan_universe"] = False
        return row
    except Exception as exc:  # noqa: BLE001
        logger.warning("Fresh fetch failed for holding %s: %s", symbol, exc)
        return {"data_quality": "missing", "in_scan_universe": False}


def build_my_portfolio_tab(nse_full_df: pd.DataFrame, index_close: pd.Series) -> pd.DataFrame:
    """
    Returns a DataFrame ready for the My_Portfolio_Scored tab, or an empty
    DataFrame if the holdings tab doesn't exist / can't be read — this is
    designed to never raise and never block the rest of the pipeline.
    """
    try:
        client = _get_client()
        spreadsheet = client.open_by_key(config.GOOGLE_SHEET_ID)
        worksheet = spreadsheet.worksheet(config.MY_HOLDINGS_TAB_NAME)
    except gspread.exceptions.WorksheetNotFound:
        logger.info(
            "No '%s' tab found — skipping portfolio enrichment. "
            "Create it via Google Sheets' File > Import to enable this.",
            config.MY_HOLDINGS_TAB_NAME,
        )
        return pd.DataFrame()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read holdings tab: %s", exc)
        return pd.DataFrame()

    raw_rows = worksheet.get_all_values()
    holdings = _parse_holdings_rows(raw_rows)
    if holdings.empty:
        logger.info("Holdings tab found but no parseable rows in it.")
        return pd.DataFrame()

    logger.info("Found %d holdings to enrich", len(holdings))

    scan_lookup = nse_full_df.set_index("ticker") if not nse_full_df.empty else pd.DataFrame()

    rows = []
    for _, h in holdings.iterrows():
        symbol = h["symbol"]
        if symbol in scan_lookup.index:
            scan_row = scan_lookup.loc[symbol].to_dict()
            scan_row["in_scan_universe"] = True
        else:
            logger.info("'%s' not in NSE500 scan universe — fetching fresh", symbol)
            scan_row = _fetch_fresh_indicators(symbol, index_close)

        qty = h["qty"] or 0
        avg_cost = h["avg_cost"] or 0
        close = scan_row.get("close")

        invested_value = qty * avg_cost if (qty and avg_cost) else None
        current_value = qty * close if (qty and close) else None
        pnl_pct = ((close / avg_cost - 1) * 100) if (close and avg_cost) else None

        out_row = {
            "symbol": symbol,
            "qty": qty,
            "avg_cost": avg_cost,
            "last_close": close,
            "invested_value": invested_value,
            "current_value": current_value,
            "pnl_pct": pnl_pct,
            "in_scan_universe": scan_row.get("in_scan_universe", False),
            "composite_score": scan_row.get("composite_score") if scan_row.get("in_scan_universe") else "Outside scan universe",
            "category": scan_row.get("category") if scan_row.get("in_scan_universe") else "N/A",
            "EliteCompounderScore": scan_row.get("EliteCompounderScore") if scan_row.get("in_scan_universe") else "Outside scan universe",
            "elite_category": scan_row.get("elite_category") if scan_row.get("in_scan_universe") else "N/A",
            "RS_vs_Broad_Index_pct": scan_row.get("rs_score") if "rs_score" in scan_row else scan_row.get("RS_vs_Broad_Index_pct"),
            "obv_slope_20d": scan_row.get("obv_slope_20d"),
            "obv_52w_high": scan_row.get("obv_52w_high"),
            "supertrend_10_3_dir": scan_row.get("supertrend_10_3_dir"),
            "supertrend_weekly_dir": scan_row.get("supertrend_weekly_dir"),
            "macd_early_bullish": scan_row.get("macd_early_bullish"),
            "pct_from_52w_high": scan_row.get("pct_from_52w_high"),
            "data_quality": scan_row.get("data_quality", "ok" if scan_row.get("in_scan_universe") else "missing"),
            # Live price via Google Finance — written as a formula string with
            # USER_ENTERED, so it keeps updating live in the browser even
            # though this script only runs once a day. NSE: prefix assumes
            # Indian holdings; adjust manually in-sheet for any US holdings.
            "live_price": f'=GOOGLEFINANCE("NSE:{symbol}","price")',
            "live_day_change_pct": f'=GOOGLEFINANCE("NSE:{symbol}","changepct")',
        }
        rows.append(out_row)

    result = pd.DataFrame(rows)
    if not result.empty and "current_value" in result.columns:
        result = result.sort_values("current_value", ascending=False, na_position="last").reset_index(drop=True)
    return result
