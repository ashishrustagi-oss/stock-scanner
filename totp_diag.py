"""
totp_diag.py — run this locally to isolate the "Invalid TOTP" cause.
Doesn't call Dhan at all, just checks your local setup.
"""
import datetime
import os

import pyotp

secret = os.environ.get("DHAN_TOTP_SECRET", "")
print(f"Secret length: {len(secret)} chars (should be ~32, no spaces/newlines)")
print(f"Secret (first/last 4 chars only, for a sanity check): "
      f"{secret[:4]}...{secret[-4:]}" if len(secret) >= 8 else "TOO SHORT / EMPTY")
print()
print(f"Local system time (UTC): {datetime.datetime.utcnow()}")
print(f"Local system time (should match IST): {datetime.datetime.now()}")
print()

try:
    totp = pyotp.TOTP(secret)
    code = totp.now()
    print(f"Generated TOTP code: {code}")
    print(f"This code is valid until: {datetime.datetime.fromtimestamp(totp.interval * (int(totp.timecode(datetime.datetime.now())) + 1))}")
except Exception as exc:
    print(f"ERROR generating code — secret is likely malformed: {exc}")
