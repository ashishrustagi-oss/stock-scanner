"""
Local diagnostic v5 — structural XBRL inspector. v4 proved the percentage
tags are generic (repeated identically for every shareholder-category row),
so the category label must live in a SIBLING or nearby element instead of
the tag name itself. This script finds the parent "context" or "tuple"
structure around each percentage value and prints nearby tags, so we can
see what actually identifies "this row is Mutual Funds" vs "this row is
FII/FPI" vs "this row is Promoter".

XBRL files are typically organized around <context> elements with
contextRef attributes that link a fact to a specific dimension/member
(e.g. a "TypeOfShareHolderAxis" with a member like "MutualFundsMember").
This script looks for that pattern specifically, since it's the standard
XBRL way of tagging categorical breakdowns — far more reliable than
hoping the category name appears in the fact's own tag.

Run from the repo root (needs the XBRL file already downloaded by v4, or
re-downloads it).

Usage:
    python diagnostics/shareholding_api_probe_v5.py
    python diagnostics/shareholding_api_probe_v5.py RELIANCE
"""

import sys
import tempfile

from lxml import etree
from nse import NSE


def main():
    symbol = sys.argv[1] if len(sys.argv) > 1 else "RELIANCE"
    print(f"Structural XBRL inspection for: {symbol}")
    print("-" * 70)

    with tempfile.TemporaryDirectory() as tmp_dir:
        with NSE(tmp_dir) as nse_client:
            records = nse_client.shareholding(symbol)
            if not records:
                print("No records found.")
                return
            xbrl_url = records[0].get("xbrl")
            if not xbrl_url:
                print("No XBRL URL on latest record.")
                return
            saved_path = nse_client.download_document(xbrl_url, folder=tmp_dir)
            with open(saved_path, "rb") as f:
                xbrl_bytes = f.read()

    root = etree.fromstring(xbrl_bytes)

    # Step 1: look for <context> elements and any dimension/member info
    # inside them — this is the standard XBRL pattern for categorical facts.
    print("Step 1: Searching for <context> elements with dimension/member info...")
    ns = root.nsmap
    contexts_with_members = []
    for elem in root.iter():
        tag = etree.QName(elem).localname.lower() if elem.tag is not None else ""
        if tag == "context":
            context_id = elem.get("id", "?")
            # Look inside this context for explicitMember or similar
            members = []
            for sub in elem.iter():
                subtag = etree.QName(sub).localname.lower() if sub.tag is not None else ""
                if "member" in subtag or "explicitmember" in subtag:
                    dim = sub.get("dimension", "")
                    members.append((subtag, dim, (sub.text or "").strip()))
            if members:
                contexts_with_members.append((context_id, members))

    print(f"  Found {len(contexts_with_members)} context(s) with dimension/member info.")
    for cid, members in contexts_with_members[:15]:
        print(f"\n  context id={cid}")
        for subtag, dim, text in members:
            print(f"    <{subtag}> dimension={dim!r} text={text!r}")

    if not contexts_with_members:
        print("\n  None found via 'member' keyword. Dumping ALL context IDs")
        print("  and their raw child structure for the first 5 contexts instead:")
        count = 0
        for elem in root.iter():
            tag = etree.QName(elem).localname.lower() if elem.tag is not None else ""
            if tag == "context":
                count += 1
                if count > 5:
                    break
                print(f"\n  context id={elem.get('id', '?')}")
                print(f"  {etree.tostring(elem, pretty_print=True).decode()[:800]}")

    # Step 2: find one of the percentage facts and show its contextRef,
    # so we can cross-reference which context (and thus which category)
    # it belongs to.
    print("\n" + "-" * 70)
    print("Step 2: Sample percentage facts with their contextRef...")
    count = 0
    for elem in root.iter():
        tag = etree.QName(elem).localname.lower() if elem.tag is not None else ""
        if tag == "shareholdingasapercentageoftotalnumberofshares":
            context_ref = elem.get("contextRef", "?")
            text = (elem.text or "").strip()
            print(f"  value={text!r}  contextRef={context_ref!r}")
            count += 1
            if count >= 10:
                break


if __name__ == "__main__":
    main()
