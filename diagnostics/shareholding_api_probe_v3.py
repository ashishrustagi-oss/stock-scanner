"""
Local diagnostic v3 — uses the maintained `nse` library (pip install nse)
instead of hand-rolled requests/curl_cffi calls. This library is actively
maintained against NSE's current site and handles session/cookie/fingerprint
concerns internally, so it's a more reliable starting point than our own
URL-guessing (which was hitting the wrong endpoint entirely — confirmed via
v2 of this probe: curl_cffi got past NSE's WAF cleanly, but the URL itself
404'd, meaning config.NSE_SHAREHOLDING_API_URL was simply wrong).

Run this on your Windows machine (NOT in any sandbox).

Usage:
    python diagnostics/shareholding_api_probe_v3.py
    python diagnostics/shareholding_api_probe_v3.py RELIANCE

Requires: pip install nse
This does NOT touch the project's real cache file or write anything outside
its own temp download folder — pure read-only probe.
"""

import json
import sys
import tempfile

from nse import NSE


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    print(f"Probing NSE shareholding data (via 'nse' library) for symbol: {symbol}")
    print("-" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            with NSE(tmp_dir) as nse_client:
                print("Session created. Fetching shareholding data...")
                data = nse_client.shareholding(symbol)
        except Exception as exc:
            print(f"FAILED: {type(exc).__name__}: {exc}")
            print("\nIf this is a TimeoutError or ConnectionError, NSE may still be")
            print("blocking this connection at the network/WAF level.")
            return

    print(f"\nSUCCESS. Got {len(data)} quarterly record(s).")
    if data:
        print("\nMost recent quarter (first record):")
        print(json.dumps(data[0], indent=2))
        print(f"\nKey fields for this quarter:")
        print(f"  date (as-on):        {data[0].get('date')}")
        print(f"  pr_and_prgrp (Promoter): {data[0].get('pr_and_prgrp')}")
        print(f"  public_val (Public):     {data[0].get('public_val')}")
        print(f"  xbrl filing URL:     {data[0].get('xbrl')}")
        print("\nNOTE: mf_pct / fii_pct are NOT top-level fields in this response.")
        print("They are sub-categories under 'Public' inside the XBRL filing")
        print("linked above — our existing _extract_pct_from_xbrl() in")
        print("shareholding.py would need to parse THAT URL, not the old")
        print("guessed API endpoint.")
    else:
        print("Empty list returned — symbol may have no shareholding filings on record,")
        print("or NSE's response shape may have changed again.")


if __name__ == "__main__":
    main()
