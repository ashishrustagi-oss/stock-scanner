"""
Local diagnostic v4 — fetches a REAL XBRL shareholding filing (using the
nse library's session, which we've now confirmed works) and runs it through
the project's existing _extract_pct_from_xbrl() parser, to verify that
parser actually produces correct mf_pct/fii_pct values once pointed at a
real filing URL (instead of the dead endpoint it was getting 404s from
before).

This is the second half of the diagnosis: v3 confirmed the `nse` library
can fetch the filing LIST (with a real xbrl URL per quarter). This script
checks whether the existing parsing logic in shareholding.py correctly
extracts MF%/FII% out of that actual file.

Run this on your Windows machine, from the repo root, so it can import
shareholding.py directly.

Usage:
    python diagnostics/shareholding_api_probe_v4.py
    python diagnostics/shareholding_api_probe_v4.py RELIANCE
"""

import sys

from nse import NSE

# Import the project's real parser, unmodified — this is the actual
# function used in production, not a reimplementation.
sys.path.insert(0, ".")
from shareholding import _extract_pct_from_xbrl  # noqa: E402


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    print(f"Fetching real XBRL filing for: {symbol}")
    print("-" * 70)

    import tempfile
    with tempfile.TemporaryDirectory() as tmp_dir:
        with NSE(tmp_dir) as nse_client:
            records = nse_client.shareholding(symbol)

        if not records:
            print("No shareholding records found for this symbol.")
            return

        latest = records[0]
        xbrl_url = latest.get("xbrl")
        print(f"Latest filing date: {latest.get('date')}")
        print(f"XBRL URL: {xbrl_url}")
        print(f"Top-level pr_and_prgrp (Promoter): {latest.get('pr_and_prgrp')}")
        print(f"Top-level public_val (Public):     {latest.get('public_val')}")

        if not xbrl_url:
            print("\nNo XBRL URL on this record — cannot test the parser.")
            return

        print(f"\nDownloading XBRL file via nse.download_document()...")
        # Use the library's own documented download method rather than
        # reaching into its private session attribute.
        try:
            saved_path = nse_client.download_document(xbrl_url, folder=tmp_dir)
            print(f"  Saved to: {saved_path}")
            with open(saved_path, "rb") as f:
                xbrl_bytes = f.read()
            print(f"  Size: {len(xbrl_bytes)} bytes")
        except Exception as exc:
            print(f"  FAILED to download XBRL file: {exc}")
            return

    print("\nRunning the file through shareholding.py's _extract_pct_from_xbrl()...")
    result = _extract_pct_from_xbrl(xbrl_bytes)
    print(f"\nRESULT: {result}")

    if result["mf_pct"] is not None or result["fii_pct"] is not None:
        print("\nSUCCESS — the existing parser extracted at least one value.")
        print("This confirms the fix is: point shareholding.py at the real")
        print("filing-list + xbrl URL (via the `nse` library), instead of")
        print("the old dead /api/corporate-shareholding-pattern endpoint.")
    else:
        print("\nThe parser returned None for both fields — the keyword")
        print("matching logic may need adjusting for this filing's actual")
        print("tag names. Worth printing the raw XML tag names to see what")
        print("this specific filer's software actually calls these fields.")
        print("\nDumping all numeric-valued tags containing 'percentage' or 'pct'")
        print("for inspection:")
        from lxml import etree
        try:
            root = etree.fromstring(xbrl_bytes)
            for elem in root.iter():
                tag = etree.QName(elem).localname.lower() if elem.tag is not None else ""
                text = (elem.text or "").strip()
                if not text:
                    continue
                try:
                    float(text)
                except ValueError:
                    continue
                if "percentage" in tag or "pct" in tag:
                    print(f"  {tag} = {text}")
        except Exception as exc:
            print(f"  Could not dump tags: {exc}")


if __name__ == "__main__":
    main()
