"""
Local diagnostic v7 — targeted investigation of a single anomalous result.
ABBOTINDIA came back with mf_holding_pct=554.0 (impossible — max is 100)
and shareholding_quarter_end=31-Dec-2018 (8 years stale) from the real
production run. This script re-fetches ABBOTINDIA's full filing list and
inspects the chosen "latest" filing plus its raw XBRL contexts, to find
exactly where this went wrong: wrong filing picked, or a real parsing
issue specific to this filer's XBRL structure.

Run from the repo root.

Usage:
    python diagnostics/shareholding_api_probe_v7.py
    python diagnostics/shareholding_api_probe_v7.py ABBOTINDIA
"""

import sys
import tempfile

from lxml import etree
from nse import NSE

sys.path.insert(0, ".")
from shareholding import _pick_latest_filing, _get_attachment_url, _extract_pct_from_xbrl  # noqa: E402


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "ABBOTINDIA"
    print(f"Investigating: {symbol}")
    print("-" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        with NSE(tmp_dir) as nse_client:
            filings = nse_client.shareholding(symbol)
            print(f"Total filings returned by library: {len(filings)}")

            print("\nAll filing dates (in the order the library returned them):")
            for f in filings[:10]:
                print(f"  date={f.get('date')!r}  pr_and_prgrp={f.get('pr_and_prgrp')!r}  "
                      f"public_val={f.get('public_val')!r}  xbrl={'yes' if f.get('xbrl') else 'NO XBRL'}")
            if len(filings) > 10:
                print(f"  ... and {len(filings) - 10} more")

            latest = _pick_latest_filing(filings)
            print(f"\n_pick_latest_filing() chose: date={latest.get('date')!r}")

            attachment_url = _get_attachment_url(latest)
            print(f"Attachment URL: {attachment_url}")

            if not attachment_url:
                print("No attachment URL on the chosen filing — that's the bug right there.")
                return

            print(f"\nDownloading and parsing this filing's XBRL...")
            saved_path = nse_client.download_document(attachment_url, folder=tmp_dir)
            with open(saved_path, "rb") as f:
                xbrl_bytes = f.read()

        result = _extract_pct_from_xbrl(xbrl_bytes)
        print(f"\n_extract_pct_from_xbrl() result: {result}")

        # Dump the actual MF context's raw value to see if 554 came from
        # the parser or was already wrong in the source filing.
        root = etree.fromstring(xbrl_bytes)
        context_to_member = {}
        for elem in root.iter():
            tag = etree.QName(elem).localname.lower() if elem.tag is not None else ""
            if tag == "context":
                cid = elem.get("id", "")
                for sub in elem.iter():
                    sub_tag = etree.QName(sub).localname.lower() if sub.tag is not None else ""
                    if "explicitmember" in sub_tag and sub.text:
                        context_to_member[cid] = sub.text.strip().lower()
                        break

        print("\nAll percentage facts whose context matches 'mutualfund':")
        for elem in root.iter():
            tag = etree.QName(elem).localname.lower() if elem.tag is not None else ""
            if tag != "shareholdingasapercentageoftotalnumberofshares":
                continue
            cref = elem.get("contextRef", "")
            member = context_to_member.get(cref, "")
            if "mutualfund" in member:
                print(f"  contextRef={cref!r}  member={member!r}  raw_text={elem.text!r}")


if __name__ == "__main__":
    main()
