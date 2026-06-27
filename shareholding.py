"""
MF (Mutual Fund) and FII/FPI (Foreign Institutional/Portfolio Investor)
shareholding percentage tracking, with quarter-over-quarter trend detection.

⚠️ HIGHEST-RISK MODULE IN THIS PROJECT. Read this before debugging it.

Unlike price data (yfinance) or sector indices (standard tickers), shareholding
pattern data has no clean free API. NSE publishes it as quarterly regulatory
filings (SEBI-mandated XBRL, sometimes with a PDF attachment) per company —
closer to scraping a government filing than calling a price API. This module:

  1. Uses the maintained `nse` library (pip install nse) to fetch each
     symbol's shareholding pattern filing list, including a direct XBRL
     attachment URL per quarter. (PREVIOUSLY: hand-rolled requests against
     a guessed endpoint, config.NSE_SHAREHOLDING_API_URL, that turned out
     to be a dead URL — confirmed via diagnostics/shareholding_api_probe_*.py
     to 404 even after fixing an earlier TLS-fingerprint WAF block. This
     was the root cause of mf_holding_pct/fii_holding_pct being 100% blank
     for every NSE500 ticker in production. The `nse` library handles
     session/cookie/fingerprint concerns and points at NSE's real,
     currently-working filing-list mechanism instead.)
  2. Parses the most recent filing's XBRL attachment using a CONTEXT-BASED
     extraction (see _extract_pct_from_xbrl docstring) — this also
     replaces an earlier, fundamentally incorrect approach that matched on
     the percentage FACT's own tag name. Every category in a real filing
     (Promoter, MF, FII, Banks, etc.) uses the identical generic tag; the
     category lives in a separate <context> element instead. The old
     approach could never have worked, regardless of which endpoint it
     was pointed at — this was verified directly against a real RELIANCE
     filing during the fix.
  3. Falls back to parsing the PDF attachment's summary table if XBRL isn't
     available, using keyword-based row matching since exact table layouts
     vary by filing software.
  4. Caches each quarter's result permanently (cache/shareholding_history.json)
     so quarter-over-quarter trend (MF_Holding_Increasing / FII_Holding_Increasing)
     can be computed once at least 2 quarters are on file.

mf_pct / fii_pct are stored as PERCENTAGES (e.g. 9.78, not 0.0978) — the
raw XBRL fact is a fraction; _extract_pct_from_xbrl() multiplies by 100.
"""

import datetime
import io
import json
import logging
import os
import re
import tempfile
import time

import pandas as pd
import requests
from nse import NSE

import config

logger = logging.getLogger(__name__)

# Kept only for the PDF-parsing fallback path's keyword matching — no
# longer used for XBRL (see _extract_pct_from_xbrl, which now matches on
# the standardized taxonomy member name via <context>, not free-text
# keywords in a tag name).
MF_KEYWORDS = ["mutual fund"]
FII_KEYWORDS = ["foreign portfolio investor", "foreign institutional investor", "fii", "fpi"]


def _get_filing_list(nse_client: NSE, nse_symbol: str) -> list[dict]:
    """
    Returns the symbol's quarterly shareholding-pattern filings via the
    `nse` library, most recent first (this is the library's own ordering;
    NSE.shareholding() docstring: "Returns quarterly shareholding details
    with the latest quarter first."). Each dict's keys (date, xbrl, etc.)
    are the library's own field names, matched against in
    _pick_latest_filing / _get_attachment_url below.
    """
    try:
        return nse_client.shareholding(nse_symbol)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Shareholding filing-list fetch failed for %s: %s", nse_symbol, exc)
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
    Context-based XBRL parse.

    IMPORTANT — this replaces an earlier version that matched on the
    PERCENTAGE FACT's own tag name (e.g. looking for "mutualfund" inside
    the tag). That approach was fundamentally wrong and is why this module
    returned mf_pct/fii_pct = None for 100% of NSE500 tickers in production
    (verified directly against a real filing — see diagnostics/
    shareholding_api_probe_v4/v5/v6.py and the conversation that produced
    this fix). In real NSE/BSE shareholding-pattern XBRL filings, EVERY
    category (Promoter, Mutual Funds, FII, Banks, Insurance, etc.) uses the
    IDENTICAL generic fact tag —
    "ShareholdingAsAPercentageOfTotalNumberOfShares" — repeated once per
    category row. The category itself lives in a separate <context>
    element, referenced by the fact's contextRef attribute, via an
    <explicitMember dimension="...:CategoryOfShareholdersAxis">
    ...Member</explicitMember> child using the standardized BSE/NSE XBRL
    taxonomy (namespace prefix typically "in-bse-shp").

    Verified against a real RELIANCE filing (31-MAR-2026 quarter):
      - MF holding  -> context whose explicitMember is
        "...:MutualFundsOrUTIMember" (NOT the "_Context15"/"_Context16"
        per-shareholder-row variants under the same category, which are
        already summed INTO this headline figure — using them too would
        double-count).
      - FII holding -> context whose explicitMember is
        "...:InstitutionsForeignMember" — this is the aggregate "Total
        Foreign Institutions" rollup (confirmed ≈ sum of its own
        CategoryOne + CategoryTwo + OtherInstitutionsForeign
        sub-contexts), chosen over summing those sub-categories manually
        because it's simpler and matches how MF/FII % is commonly reported
        on financial sites (Screener, Trendlyne, etc.) — see conversation
        for the explicit choice between these two definitions.

    Matching is done on the explicitMember text (the standardized taxonomy
    element name) rather than the locally-chosen context `id` attribute,
    since the taxonomy member name is the more stable, filer-independent
    identifier — context `id` strings are just local labels and could
    differ across filing software vendors even when the underlying
    taxonomy concept is identical.

    UNIT SCALE: also handles a real-world inconsistency where some filings
    store this fact as a true fraction (0.0978) and others store it
    already as a percentage (5.54) — see the inline comment above the
    `value = raw_value if raw_value > 1.5 else raw_value * 100` line for
    the detection heuristic and how it was discovered (ABBOTINDIA, a real
    production run).

    KNOWN SEPARATE LIMITATION (not fixed by this function — a filing-LIST
    issue, not a parsing issue): for some symbols, the `nse` library's
    shareholding() call appears to only return filings up through ~2018,
    even though the company has obviously filed every quarter since (e.g.
    ABBOTINDIA — verified via diagnostics/shareholding_api_probe_v7.py).
    This means quarter_end/data for those symbols will be 8 years stale
    rather than current. Root cause not yet identified (NSE-side data gap
    vs. a symbol-matching quirk) — if it turns out to affect many symbols
    rather than being rare/isolated, worth a follow-up investigation.
    """
    from lxml import etree

    result = {"mf_pct": None, "fii_pct": None}
    try:
        root = etree.fromstring(xbrl_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.debug("XBRL parse failed: %s", exc)
        return result

    # Step 1: build context_id -> explicitMember text (e.g.
    # "in-bse-shp:MutualFundsOrUTIMember"), lowercased for matching.
    context_to_member = {}
    for elem in root.iter():
        tag = etree.QName(elem).localname.lower() if elem.tag is not None else ""
        if tag != "context":
            continue
        cid = elem.get("id", "")
        if not cid:
            continue
        for sub in elem.iter():
            sub_tag = etree.QName(sub).localname.lower() if sub.tag is not None else ""
            if "explicitmember" in sub_tag and sub.text:
                context_to_member[cid] = sub.text.strip().lower()
                break

    # Step 2: walk the headline percentage facts, look up each one's
    # category via its contextRef, and assign to mf_pct/fii_pct using the
    # first exact match found (there should only ever be one "Mutual
    # Funds" aggregate context and one "Foreign Institutions" aggregate
    # context per filing).
    MF_MEMBER_SUFFIX = "mutualfundsorutimember"
    FII_MEMBER_SUFFIX = "institutionsforeignmember"

    for elem in root.iter():
        tag = etree.QName(elem).localname.lower() if elem.tag is not None else ""
        if tag != "shareholdingasapercentageoftotalnumberofshares":
            continue
        text = (elem.text or "").strip()
        if not text:
            continue
        try:
            raw_value = float(text)
        except ValueError:
            continue

        # UNIT-SCALE BUG FIX: most filings (verified: RELIANCE 31-MAR-2026)
        # store this as a true fraction (0.0978 = 9.78%), but some older
        # filings from different filing software (verified: ABBOTINDIA's
        # 2016-2018 filings) store it already AS a percentage (5.54 means
        # 5.54%, not 554%). Blindly multiplying by 100 produced an
        # impossible 554.0 for ABBOTINDIA — caught via a real production
        # run, see diagnostics/shareholding_api_probe_v7.py.
        #
        # A holding percentage for ANY single category can never exceed
        # 100, and in practice for MF/FII specifically is virtually always
        # well under 50 — so any raw value already above 1.5 is assumed to
        # already be a percentage (no multiplication), while anything at
        # or below 1.5 is assumed to be a fraction (multiply by 100). The
        # 1.5 threshold (rather than 1.0) gives a small safety margin for
        # genuine fractional values very close to but not exceeding 1.0,
        # without being so high that it would misclassify a real small
        # percentage-scale value (e.g. a true 1.2% holding stored as 1.2,
        # not 0.012) as a fraction.
        value = raw_value if raw_value > 1.5 else raw_value * 100

        context_ref = elem.get("contextRef", "")
        member = context_to_member.get(context_ref, "")
        if not member:
            continue

        if member.endswith(MF_MEMBER_SUFFIX) and result["mf_pct"] is None:
            result["mf_pct"] = value
        elif member.endswith(FII_MEMBER_SUFFIX) and result["fii_pct"] is None:
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


def _fetch_one(nse_symbol: str, nse_client: NSE, tmp_dir: str) -> dict:
    out = {
        "ticker": nse_symbol, "mf_pct": None, "fii_pct": None,
        "quarter_end": None, "data_quality": "missing",
    }
    try:
        filings = _get_filing_list(nse_client, nse_symbol)
        latest = _pick_latest_filing(filings)
        if not latest:
            return out

        attachment_url = _get_attachment_url(latest)
        out["quarter_end"] = (
            latest.get("date") or latest.get("toDate") or latest.get("period") or latest.get("quarterEnded")
        )

        if not attachment_url:
            return out

        saved_path = nse_client.download_document(attachment_url, folder=tmp_dir)
        with open(saved_path, "rb") as f:
            content = f.read()

        if attachment_url.lower().endswith((".xml", ".xbrl")):
            parsed = _extract_pct_from_xbrl(content)
        elif attachment_url.lower().endswith(".pdf"):
            parsed = _extract_pct_from_pdf(content)
        else:
            # Unknown extension — try XBRL parse first (cheap), then PDF
            parsed = _extract_pct_from_xbrl(content)
            if parsed["mf_pct"] is None and parsed["fii_pct"] is None:
                parsed = _extract_pct_from_pdf(content)

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

    IMPORTANT — learned from a real run: NSE rate-limits hard after roughly
    300 sequential requests in one burst (observed an 8x slowdown). Rather
    than try to fetch all ~500 NSE tickers in a single run, this is
    deliberately budget-capped per run (SHAREHOLDING_MAX_FETCHES_PER_RUN) and
    time-capped (SHAREHOLDING_MAX_RUN_SECONDS) — full coverage builds up over
    several days instead, which is completely fine since this data only
    changes quarterly anyway. Progress is also saved incrementally so a
    cancelled or timed-out run never loses what it already fetched.
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

    # Prioritize symbols with NO history at all over ones just due for a
    # refresh — first-time coverage is more valuable than re-confirming a
    # value we already have.
    to_fetch.sort(key=lambda s: 0 if not history.get(s) else 1)

    total_due = len(to_fetch)
    to_fetch = to_fetch[: config.SHAREHOLDING_MAX_FETCHES_PER_RUN]
    logger.info(
        "Shareholding: %d cached/fresh, %d due for fetch, processing %d this run (budget-capped)",
        len(nse_symbols) - total_due, total_due, len(to_fetch),
    )

    if to_fetch:
        start_time = time.time()
        processed = 0

        with tempfile.TemporaryDirectory() as tmp_dir, NSE(tmp_dir) as nse_client:
            for i, sym in enumerate(to_fetch):
                if time.time() - start_time > config.SHAREHOLDING_MAX_RUN_SECONDS:
                    logger.warning(
                        "Shareholding fetch hit the %ds time budget after %d/%d — "
                        "stopping early, remainder will be picked up on a future run",
                        config.SHAREHOLDING_MAX_RUN_SECONDS, i, len(to_fetch),
                    )
                    break

                result = _fetch_one(sym, nse_client, tmp_dir)
                entries = history.setdefault(sym, [])
                quarter_end = result["quarter_end"]
                already_have = any(e.get("quarter_end") == quarter_end for e in entries) if quarter_end else False

                if result["mf_pct"] is not None or result["fii_pct"] is not None:
                    if not already_have:
                        entries.append({
                            "quarter_end": quarter_end,
                            "mf_pct": result["mf_pct"],
                            "fii_pct": result["fii_pct"],
                            "fetched_on": today.isoformat(),
                        })
                        entries.sort(key=lambda e: e.get("quarter_end") or "")
                else:
                    # Record the attempt even on failure (with no figures), so a
                    # permanently-failing ticker is retried after the normal
                    # refresh interval instead of every single run forever.
                    entries.append({
                        "quarter_end": None, "mf_pct": None, "fii_pct": None,
                        "fetched_on": today.isoformat(),
                    })

                processed += 1
                if processed % config.SHAREHOLDING_SAVE_EVERY_N == 0:
                    _save_history(history)
                    logger.info("Shareholding fetch progress: %d/%d (saved)", processed, len(to_fetch))

                time.sleep(config.SHAREHOLDING_SLEEP_SECONDS)

        _save_history(history)  # final save covers any remainder since the last checkpoint
        logger.info("Shareholding fetch finished this run: %d processed and saved", processed)

    out = {}
    for sym in nse_symbols:
        entries = history.get(sym, [])
        if not entries:
            out[sym] = {
                "mf_pct": None, "fii_pct": None, "mf_pct_prev": None, "fii_pct_prev": None,
                "mf_holding_increasing": None, "fii_holding_increasing": None,
                "mf_holding_change_qoq": None, "fii_holding_change_qoq": None,
                "mf_increasing_2q_streak": None, "fii_increasing_2q_streak": None,
                "quarter_end": None, "data_quality": "missing",
            }
            continue

        latest = entries[-1]
        prev = entries[-2] if len(entries) >= 2 else None
        prev2 = entries[-3] if len(entries) >= 3 else None  # needed for 2-quarter streak detection

        mf_inc = fii_inc = None
        mf_chg_qoq = fii_chg_qoq = None
        if prev:
            if latest.get("mf_pct") is not None and prev.get("mf_pct") is not None:
                mf_inc = latest["mf_pct"] > prev["mf_pct"]
                mf_chg_qoq = latest["mf_pct"] - prev["mf_pct"]
            if latest.get("fii_pct") is not None and prev.get("fii_pct") is not None:
                fii_inc = latest["fii_pct"] > prev["fii_pct"]
                fii_chg_qoq = latest["fii_pct"] - prev["fii_pct"]

        # Module 2 (Phase 2): was it ALSO increasing the quarter before that?
        # Needs 3 quarters of real history — for most stocks this stays None
        # for a while yet, since the shareholding cache only started
        # accumulating recently. Not a bug; just needs time to build up.
        mf_streak = fii_streak = None
        if prev and prev2:
            if (latest.get("mf_pct") is not None and prev.get("mf_pct") is not None
                    and prev2.get("mf_pct") is not None):
                mf_streak = mf_inc and (prev["mf_pct"] > prev2["mf_pct"])
            if (latest.get("fii_pct") is not None and prev.get("fii_pct") is not None
                    and prev2.get("fii_pct") is not None):
                fii_streak = fii_inc and (prev["fii_pct"] > prev2["fii_pct"])

        out[sym] = {
            "mf_pct": latest.get("mf_pct"),
            "fii_pct": latest.get("fii_pct"),
            "mf_pct_prev": prev.get("mf_pct") if prev else None,
            "fii_pct_prev": prev.get("fii_pct") if prev else None,
            "mf_holding_increasing": mf_inc,
            "fii_holding_increasing": fii_inc,
            "mf_holding_change_qoq": mf_chg_qoq,
            "fii_holding_change_qoq": fii_chg_qoq,
            "mf_increasing_2q_streak": mf_streak,
            "fii_increasing_2q_streak": fii_streak,
            "quarter_end": latest.get("quarter_end"),
            "data_quality": "ok" if (latest.get("mf_pct") is not None and latest.get("fii_pct") is not None) else "partial",
        }

    return out
