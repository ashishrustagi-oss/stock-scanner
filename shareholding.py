"""
MF (Mutual Fund) and FII/FPI (Foreign Institutional/Portfolio Investor)
shareholding percentage tracking, with quarter-over-quarter trend detection.

⚠️ HIGHEST-RISK MODULE IN THIS PROJECT. Read this before debugging it.

Unlike price data (yfinance) or sector indices (standard tickers), shareholding
pattern data has no clean free API. NSE publishes it as quarterly regulatory
filings (SEBI-mandated XBRL, sometimes with a PDF attachment) per company —
closer to scraping a government filing than calling a price API. This module:

  1. Queries NSE's corporate-filings API for each symbol's shareholding
     pattern filings (best-effort endpoint — see config.NSE_SHAREHOLDING_API_URL;
     fix this first if nothing resolves).
  2. Tries to parse the most recent filing's XBRL attachment (structured XML,
     more reliable to parse than a PDF if available).
  3. Falls back to parsing the PDF attachment's summary table if XBRL isn't
     available, using keyword-based row matching since exact table layouts
     vary by filing software.
  4. Caches each quarter's result permanently (cache/shareholding_history.json)
     so quarter-over-quarter trend (MF_Holding_Increasing / FII_Holding_Increasing)
     can be computed once at least 2 quarters are on file.

Expect this to need fixing after the first live run, the same way the sector
index mapping did — likely more so. If it fails entirely, every stock will
just show data_quality="missing" for these fields; nothing else in the
pipeline is affected.
"""

import datetime
import io
import json
import logging
import os
import re
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
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Keywords used to find the right rows/tags regardless of exact filing-software
# wording (e.g. "Foreign Portfolio Investors", "FPI", "FII" all appear across
# different filers' submissions).
MF_KEYWORDS = ["mutual fund"]
FII_KEYWORDS = ["foreign portfolio investor", "foreign institutional investor", "fii", "fpi"]


def _get_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(NSE_HEADERS)
    session.get(config.NSE_HOME_URL, timeout=10)  # primes cookies, same pattern as universe.py
    return session


def _get_filing_list(session: requests.Session, nse_symbol: str) -> list[dict]:
    """
    Returns a list of filing metadata dicts for the symbol's shareholding
    pattern submissions, most recent first. Each dict's exact keys depend on
    NSE's API response — we handle that defensively in _pick_latest_filing.
    """
    url = config.NSE_SHAREHOLDING_API_URL.format(symbol=nse_symbol)
    resp = session.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    # NSE list-style corporate-filing endpoints commonly nest results under
    # a "data" key; fall back to the raw payload if it's already a list.
    if isinstance(data, dict):
        for key in ("data", "shareholding", "result"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return []
    if isinstance(data, list):
        return data
    return []


def _pick_latest_filing(filings: list[dict]) -> dict | None:
    if not filings:
        return None
    # Try common date field names to sort by most recent first.
    date_fields = ["toDate", "period", "quarterEnded", "submissionDate", "date"]

    def _get_date(f):
        for field in date_fields:
            if field in f and f[field]:
                for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y"):
                    try:
                        return datetime.datetime.strptime(str(f[field]), fmt)
                    except ValueError:
                        continue
        return datetime.datetime.min

    return sorted(filings, key=_get_date, reverse=True)[0]


def _get_attachment_url(filing: dict) -> str | None:
    for key in ["xbrlFile", "xbrl", "attachmentFile", "attachment", "fileUrl", "pdfLink"]:
        if key in filing and filing[key]:
            return filing[key]
    return None


def _extract_pct_from_xbrl(xbrl_bytes: bytes) -> dict:
    """
    Flexible XBRL/XML parse: looks for any tag whose (namespace-stripped) name
    contains both a category keyword (mutual fund / FII-FPI) and a number that
    looks like a holding percentage, rather than relying on exact tag paths
    which vary across filing software vendors.
    """
    from lxml import etree

    result = {"mf_pct": None, "fii_pct": None}
    try:
        root = etree.fromstring(xbrl_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.debug("XBRL parse failed: %s", exc)
        return result

    for elem in root.iter():
        tag = etree.QName(elem).localname.lower() if elem.tag is not None else ""
        text = (elem.text or "").strip()
        if not text:
            continue
        try:
            value = float(text)
        except ValueError:
            continue
        if "percentage" not in tag and "pct" not in tag:
            continue
        if any(k.replace(" ", "") in tag for k in ["mutualfund"]) and result["mf_pct"] is None:
            result["mf_pct"] = value
        if any(k.replace(" ", "") in tag for k in ["foreignportfolioinvestor", "foreigninstitutionalinvestor", "fii", "fpi"]) and result["fii_pct"] is None:
            result["fii_pct"] = value

    return result


def _extract_pct_from_pdf(pdf_bytes: bytes) -> dict:
    """
    Parses the standard SEBI shareholding-pattern summary table out of the
    filing PDF. Looks for rows whose first cell matches MF/FII keywords and
    takes the rightmost numeric-looking cell as the "% of total shares" value
    (that column is consistently the last one in the standard format).
    """
    import pdfplumber

    result = {"mf_pct": None, "fii_pct": None}
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables() or []:
                    for row in table:
                        if not row or not row[0]:
                            continue
                        label = str(row[0]).lower()
                        numeric_cells = [c for c in row if c and re.match(r"^-?\d+\.?\d*$", str(c).strip())]
                        if not numeric_cells:
                            continue
                        value = float(numeric_cells[-1])
                        if result["mf_pct"] is None and any(k in label for k in MF_KEYWORDS):
                            result["mf_pct"] = value
                        if result["fii_pct"] is None and any(k in label for k in FII_KEYWORDS):
                            result["fii_pct"] = value
    except Exception as exc:  # noqa: BLE001
        logger.debug("PDF parse failed: %s", exc)
    return result


def _fetch_one(nse_symbol: str, session: requests.Session) -> dict:
    out = {
        "ticker": nse_symbol, "mf_pct": None, "fii_pct": None,
        "quarter_end": None, "data_quality": "missing",
    }
    try:
        filings = _get_filing_list(session, nse_symbol)
        latest = _pick_latest_filing(filings)
        if not latest:
            return out

        attachment_url = _get_attachment_url(latest)
        out["quarter_end"] = (
            latest.get("toDate") or latest.get("period") or latest.get("quarterEnded")
        )

        if not attachment_url:
            return out

        resp = session.get(attachment_url, timeout=20)
        resp.raise_for_status()

        if attachment_url.lower().endswith((".xml", ".xbrl")):
            parsed = _extract_pct_from_xbrl(resp.content)
        elif attachment_url.lower().endswith(".pdf"):
            parsed = _extract_pct_from_pdf(resp.content)
        else:
            # Unknown extension — try XBRL parse first (cheap), then PDF
            parsed = _extract_pct_from_xbrl(resp.content)
            if parsed["mf_pct"] is None and parsed["fii_pct"] is None:
                parsed = _extract_pct_from_pdf(resp.content)

        out["mf_pct"] = parsed["mf_pct"]
        out["fii_pct"] = parsed["fii_pct"]
        if parsed["mf_pct"] is not None or parsed["fii_pct"] is not None:
            out["data_quality"] = "ok" if (parsed["mf_pct"] is not None and parsed["fii_pct"] is not None) else "partial"

    except Exception as exc:  # noqa: BLE001
        logger.debug("Shareholding fetch failed for %s: %s", nse_symbol, exc)

    return out


def _load_history() -> dict:
    if os.path.exists(config.SHAREHOLDING_CACHE_PATH):
        with open(config.SHAREHOLDING_CACHE_PATH) as f:
            return json.load(f)
    return {}


def _save_history(history: dict):
    os.makedirs(os.path.dirname(config.SHAREHOLDING_CACHE_PATH), exist_ok=True)
    with open(config.SHAREHOLDING_CACHE_PATH, "w") as f:
        json.dump(history, f)


def get_shareholding_trends(nse_symbols: list[str]) -> dict:
    """
    Returns {symbol: {mf_pct, fii_pct, mf_pct_prev, fii_pct_prev,
    mf_holding_increasing, fii_holding_increasing, quarter_end, data_quality}}.

    Only re-fetches a symbol if its cached entry is missing or older than
    SHAREHOLDING_CACHE_MAX_AGE_DAYS — this data only changes quarterly, so
    daily re-fetching would be wasteful and hammer NSE for no reason.
    """
    history = _load_history()
    today = datetime.date.today()

    to_fetch = []
    for sym in nse_symbols:
        entries = history.get(sym, [])
        if not entries:
            to_fetch.append(sym)
            continue
        last_fetched = entries[-1].get("fetched_on")
        if not last_fetched:
            to_fetch.append(sym)
            continue
        age_days = (today - datetime.date.fromisoformat(last_fetched)).days
        if age_days > config.SHAREHOLDING_CACHE_MAX_AGE_DAYS:
            to_fetch.append(sym)

    logger.info("Shareholding: %d cached/fresh, %d to fetch", len(nse_symbols) - len(to_fetch), len(to_fetch))

    if to_fetch:
        session = _get_session()
        for i, sym in enumerate(to_fetch):
            result = _fetch_one(sym, session)
            if result["mf_pct"] is not None or result["fii_pct"] is not None:
                entries = history.setdefault(sym, [])
                quarter_end = result["quarter_end"]
                already_have = any(e.get("quarter_end") == quarter_end for e in entries)
                if not already_have:
                    entries.append({
                        "quarter_end": quarter_end,
                        "mf_pct": result["mf_pct"],
                        "fii_pct": result["fii_pct"],
                        "fetched_on": today.isoformat(),
                    })
                    entries.sort(key=lambda e: e.get("quarter_end") or "")
            if (i + 1) % 50 == 0:
                logger.info("Shareholding fetch progress: %d/%d", i + 1, len(to_fetch))
            time.sleep(config.SHAREHOLDING_SLEEP_SECONDS)
        _save_history(history)

    out = {}
    for sym in nse_symbols:
        entries = history.get(sym, [])
        if not entries:
            out[sym] = {
                "mf_pct": None, "fii_pct": None, "mf_pct_prev": None, "fii_pct_prev": None,
                "mf_holding_increasing": None, "fii_holding_increasing": None,
                "quarter_end": None, "data_quality": "missing",
            }
            continue

        latest = entries[-1]
        prev = entries[-2] if len(entries) >= 2 else None

        mf_inc = fii_inc = None
        if prev:
            if latest.get("mf_pct") is not None and prev.get("mf_pct") is not None:
                mf_inc = latest["mf_pct"] > prev["mf_pct"]
            if latest.get("fii_pct") is not None and prev.get("fii_pct") is not None:
                fii_inc = latest["fii_pct"] > prev["fii_pct"]

        out[sym] = {
            "mf_pct": latest.get("mf_pct"),
            "fii_pct": latest.get("fii_pct"),
            "mf_pct_prev": prev.get("mf_pct") if prev else None,
            "fii_pct_prev": prev.get("fii_pct") if prev else None,
            "mf_holding_increasing": mf_inc,
            "fii_holding_increasing": fii_inc,
            "quarter_end": latest.get("quarter_end"),
            "data_quality": "ok" if (latest.get("mf_pct") is not None and latest.get("fii_pct") is not None) else "partial",
        }

    return out
