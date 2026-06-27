"""
Local diagnostic v6 — final structural check before writing the real fix.

v5 confirmed the pattern: each percentage fact's contextRef points to a
context whose id follows "<CategoryName>_ContextI", and the context's
explicitMember confirms the category (e.g. MutualFundsOrUTI_ContextI ->
in-bse-shp:MutualFundsOrUTIMember).

This script builds a COMPLETE map of every context id -> percentage value,
so we can see:
  1. Whether FII/FPI is split into multiple sub-categories (CategoryOne/Two/
     Three) that need summing for a single "fii_pct" figure, or if there's
     a single combined FPI context.
  2. The exact, full list of context ids actually present in this filing,
     so the real shareholding.py rewrite uses correct, verified context-id
     substrings rather than assumptions.

Run from the repo root.

Usage:
    python diagnostics/shareholding_api_probe_v6.py
    python diagnostics/shareholding_api_probe_v6.py RELIANCE
"""

import sys
import tempfile

from lxml import etree
from nse import NSE


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    print(f"Full context-to-value map for: {symbol}")
    print("-" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        with NSE(tmp_dir) as nse_client:
            records = nse_client.shareholding(symbol)
            if not records:
                print("No records found.")
                return
            xbrl_url = records[0].get("xbrl")
            saved_path = nse_client.download_document(xbrl_url, folder=tmp_dir)
            with open(saved_path, "rb") as f:
                xbrl_bytes = f.read()

    root = etree.fromstring(xbrl_bytes)

    # Build context_id -> member_text map
    context_to_member = {}
    for elem in root.iter():
        tag = etree.QName(elem).localname.lower() if elem.tag is not None else ""
        if tag == "context":
            cid = elem.get("id", "")
            for sub in elem.iter():
                subtag = etree.QName(sub).localname.lower() if sub.tag is not None else ""
                if "explicitmember" in subtag:
                    context_to_member[cid] = (sub.text or "").strip()

    # Build context_id -> percentage value map (using the main "% of total
    # shares" fact, which is the headline figure for each category)
    context_to_value = {}
    for elem in root.iter():
        tag = etree.QName(elem).localname.lower() if elem.tag is not None else ""
        if tag == "shareholdingasapercentageoftotalnumberofshares":
            cref = elem.get("contextRef", "")
            text = (elem.text or "").strip()
            if cref and text:
                context_to_value[cref] = text

    print(f"Total contexts with member info: {len(context_to_member)}")
    print(f"Total percentage facts found: {len(context_to_value)}")

    print("\n--- ALL context_id -> member -> value ---")
    for cid in sorted(context_to_value.keys()):
        member = context_to_member.get(cid, "(no member found)")
        value = context_to_value[cid]
        print(f"  {cid:55s} member={member:55s} value={value}")

    print("\n--- Specifically looking for FII / FPI / Foreign Portfolio related contexts ---")
    for cid, value in context_to_value.items():
        if "foreign" in cid.lower() or "fpi" in cid.lower() or "fii" in cid.lower():
            member = context_to_member.get(cid, "?")
            print(f"  {cid:55s} member={member:55s} value={value}")

    print("\n--- Specifically looking for Mutual Fund related contexts ---")
    for cid, value in context_to_value.items():
        if "mutual" in cid.lower():
            member = context_to_member.get(cid, "?")
            print(f"  {cid:55s} member={member:55s} value={value}")


if __name__ == "__main__":
    main()
