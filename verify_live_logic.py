"""
verify_live_logic.py — standalone, run LOCALLY with a live Dhan session.

Purpose: confirm the EXACT functions your live trade_dhan.py and
trade_dhan_mtf.py use every cycle — check_supertrend_conditions() and
check_mtf_conditions() — now get real data instead of the DH-902 error,
WITHOUT waiting for Monday and WITHOUT touching/bypassing the
market_hours weekend guard (that guard stays intact; this script
simply calls the underlying condition-check functions directly,
the same way run_trade_cycle() does internally, just skipping the
"is it currently market hours" wrapper around them).

This does NOT place any orders and does NOT modify any state file.

Requires local env vars (same as before):
  DHAN_CLIENT_ID, DHAN_PIN, DHAN_TOTP_SECRET

Usage:
    python verify_live_logic.py
"""

import logging

import time

import pyotp
from dhanhq import DhanContext, DhanLogin, dhanhq

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s %(name)s: %(message)s",
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("requests").setLevel(logging.WARNING)

import os

import trade_dhan
import trade_dhan_mtf
from dhan_data import DhanData


def login() -> dhanhq:
    client_id   = os.environ["DHAN_CLIENT_ID"]
    pin         = os.environ["DHAN_PIN"]
    totp_secret = os.environ["DHAN_TOTP_SECRET"]

    totp_code = pyotp.TOTP(totp_secret).now()
    dhan_login = DhanLogin(client_id)
    result = dhan_login.generate_token(pin, totp_code)
    access_token = (
        result.get("accessToken") or result.get("access_token") or
        result.get("data", {}).get("accessToken") or ""
    )
    if not access_token:
        raise RuntimeError(f"Dhan login failed: {result}")

    ctx = DhanContext(client_id, access_token)
    print("Logged in to Dhan (local session, not touching GitHub secrets).\n")
    return dhanhq(ctx)


# A handful of real, currently-qualified symbols to spot-check
TEST_SYMBOLS = ["NAVINFLUOR", "RADICO", "CPPLUS", "HONASA", "NUVAMA"]


def main():
    client = login()
    dd = DhanData(client)

    # Wire up the same module-level _dhan_data global that
    # run_trade_cycle() normally sets — this is the ONLY thing we're
    # doing manually here; everything downstream (check_supertrend_
    # conditions, check_mtf_conditions) is the exact unmodified live code.
    trade_dhan._dhan_data = dd
    trade_dhan_mtf._dhan_data = dd

    print("=" * 70)
    print("STRATEGY 1 — check_supertrend_conditions() [live function, real call]")
    print("=" * 70)
    for symbol in TEST_SYMBOLS:
        result = trade_dhan.check_supertrend_conditions(symbol)
        time.sleep(0.4)  # throttle to stay under Dhan's 5 req/sec cap
        status = "ERROR: " + result["error"] if result.get("error") else "OK"
        print(f"{symbol:12s} {status:35s} "
              f"weekly_bull={result['weekly_10_3_bullish']}  "
              f"daily_bull={result['daily_10_3_bullish']}  "
              f"eligible={result['eligible_for_entry']}  "
              f"cross_up={result['daily_2_1_cross_up']}")

    print("\n" + "=" * 70)
    print("STRATEGY 2 — check_mtf_conditions() [live function, real call]")
    print("=" * 70)
    for symbol in TEST_SYMBOLS:
        result = trade_dhan_mtf.check_mtf_conditions(symbol)
        time.sleep(0.4)  # throttle to stay under Dhan's 5 req/sec cap
        status = "ERROR: " + result["error"] if result.get("error") else "OK"
        print(f"{symbol:12s} {status:35s} "
              f"eligible={result.get('eligible_for_entry')}  "
              f"intra_cross_up={result.get('intra_10_3_cross_up')}")

    print("\nIf you see real True/False values above instead of ERROR lines,")
    print("the live trading logic is confirmed working end-to-end with real")
    print("Dhan data — this is the exact same code path Monday's live cycles")
    print("will run, just called directly instead of waiting for a scheduled trigger.")


if __name__ == "__main__":
    main()
