"""
Local diagnostic — probes NSE's shareholding-pattern API directly for one
symbol and prints the raw response shape. Run this on your Windows machine
(NOT in any sandbox — NSE blocks most cloud/datacenter IPs), since this is
exactly the kind of NSE-archive-dependent check that has to run locally.

Usage:
    python diagnostics/shareholding_api_probe.py
    python diagnostics/shareholding_api_probe.py RELIANCE

This does NOT touch the cache file or write anything — pure read-only probe.
"""

import json
import sys

import requests

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

NSE_HOME_URL = "https://www.nseindia.com"
NSE_SHAREHOLDING_API_URL = "https://www.nseindia.com/api/corporate-shareholding-pattern?index=equities&symbol={symbol}"


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    print(f"Probing NSE shareholding API for symbol: {symbol}")
    print("-" * 70)

    session = requests.Session()
    session.headers.update(NSE_HEADERS)

    print(f"Step 1: GET {NSE_HOME_URL} (priming cookies)...")
    try:
        home_resp = session.get(NSE_HOME_URL, timeout=10)
        print(f"  Status: {home_resp.status_code}, cookies set: {len(session.cookies)} cookie(s)")
    except Exception as exc:
        print(f"  FAILED: {exc}")
        print("\nCould not even reach nseindia.com — likely a network/firewall issue, not a parsing bug.")
        return

    url = NSE_SHAREHOLDING_API_URL.format(symbol=symbol)
    print(f"\nStep 2: GET {url}")
    try:
        resp = session.get(url, timeout=15)
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
