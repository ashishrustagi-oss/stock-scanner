"""
Local diagnostic v2 — probes NSE's shareholding-pattern API using curl_cffi's
browser impersonation (real Chrome TLS/HTTP2 fingerprint) instead of plain
`requests`, to test whether NSE's WAF is blocking based on TLS fingerprint
rather than just the User-Agent header.

Background: v1 of this probe (plain `requests`) got a 403 with zero cookies
set on the very first request to nseindia.com — before even reaching the
shareholding API. That's consistent with NSE's WAF rejecting the connection
at the TLS/HTTP layer, which a User-Agent string alone can't fix, since
Python's `requests`/urllib3 has a distinctive TLS fingerprint regardless of
what headers you send. curl_cffi uses a real browser's TLS stack instead.

Run this on your Windows machine (NOT in any sandbox), since this needs a
real residential connection to test properly.

Usage:
    python diagnostics/shareholding_api_probe.py
    python diagnostics/shareholding_api_probe.py RELIANCE

Requires: pip install curl_cffi
This does NOT touch the cache file or write anything — pure read-only probe.
"""

import json
import sys

from curl_cffi import requests as cffi_requests

NSE_HOME_URL = "https://www.nseindia.com"
NSE_SHAREHOLDING_API_URL = "https://www.nseindia.com/api/corporate-shareholding-pattern?index=equities&symbol={symbol}"

# Full browser-realistic header set — NSE's WAF may check for the presence
# of these, not just User-Agent. A real Chrome browser sends all of these.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "sec-ch-ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}

# Headers for the API call itself — browsers send a different Accept and a
# Referer pointing back at the page that "triggered" the API call.
API_HEADERS = {
    **BROWSER_HEADERS,
    "Accept": "application/json, text/plain, */*",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Referer": "https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern",
}


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    print(f"Probing NSE shareholding API (curl_cffi, Chrome impersonation) for symbol: {symbol}")
    print("-" * 70)

    session = cffi_requests.Session(impersonate="chrome124")

    print(f"Step 1: GET {NSE_HOME_URL} (priming cookies)...")
    try:
        home_resp = session.get(NSE_HOME_URL, headers=BROWSER_HEADERS, timeout=15)
        print(f"  Status: {home_resp.status_code}, cookies set: {len(session.cookies)} cookie(s)")
        if home_resp.status_code != 200:
            print(f"  Response length: {len(home_resp.content)} bytes")
            print(f"  Raw response (first 500 chars): {home_resp.text[:500]}")
    except Exception as exc:
        print(f"  FAILED: {exc}")
        print("\nCould not even reach nseindia.com.")
        return

    # Also visit the shareholding-pattern listing page first, like a real
    # user would before the page's JS fires the API call. Some WAFs check
    # that the Referer page was actually visited in-session.
    listing_url = "https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern"
    print(f"\nStep 1b: GET {listing_url} (visiting the page that would trigger this API)...")
    try:
        listing_resp = session.get(listing_url, headers=BROWSER_HEADERS, timeout=15)
        print(f"  Status: {listing_resp.status_code}, cookies now: {len(session.cookies)} cookie(s)")
    except Exception as exc:
        print(f"  FAILED (non-fatal, continuing): {exc}")

    url = NSE_SHAREHOLDING_API_URL.format(symbol=symbol)
    print(f"\nStep 2: GET {url}")
    try:
        resp = session.get(url, headers=API_HEADERS, timeout=15)
        print(f"  Status: {resp.status_code}")
        print(f"  Content-Type: {resp.headers.get('Content-Type')}")
        print(f"  Response length: {len(resp.content)} bytes")
    except Exception as exc:
        print(f"  FAILED: {exc}")
        return

    print("\nStep 3: Attempting to parse as JSON...")
    try:
        data = resp.json()
        print("  Parsed OK. Top-level type:", type(data).__name__)
        if isinstance(data, dict):
            print("  Top-level keys:", list(data.keys())[:20])
        elif isinstance(data, list):
            print(f"  List with {len(data)} item(s).")
            if data:
                print("  First item keys:", list(data[0].keys()) if isinstance(data[0], dict) else type(data[0]))
        print("\nFull response (first 3000 chars):")
        print(json.dumps(data, indent=2)[:3000])
    except Exception as exc:
        print(f"  FAILED to parse as JSON: {exc}")
        print("\nRaw response text (first 2000 chars) — likely an HTML error/captcha page:")
        print(resp.text[:2000])


if __name__ == "__main__":
    main()
