"""
strategy2_15min_backtest.py — standalone, run LOCALLY with a live Dhan
session. Not for GitHub Actions, not for this sandbox (needs your real
TOTP-based login, same as dhan_token_refresh.py).

Fills the one gap the earlier entry_frequency_check.py diagnostic
explicitly couldn't cover: Strategy 2's actual entry frequency
including layer 5 (the 15-min ST(10,3) crossover-with-candle-close
trigger). That script used yfinance, capped at ~60 days of 15-min
data — nowhere near enough. Dhan's own API gives up to 5 years of
intraday history via /v2/charts/intraday, fetched in <=90-day chunks
(the API's per-request limit), which is what this script does.

This only reads data — no orders, no state changes, no GitHub secrets
touched. It generates its own short-lived LOCAL session token and
does NOT set up IP whitelisting (that's only required for Order APIs,
not data APIs).

Requires local env vars (same names as dhan_token_refresh.py):
  DHAN_CLIENT_ID, DHAN_PIN, DHAN_TOTP_SECRET
(DHAN_API_KEY / DHAN_API_SECRET aren't needed for this login path —
 DhanLogin only needs client_id + pin + totp, same as the refresh script.)

Usage:
    set DHAN_CLIENT_ID=...
    set DHAN_PIN=...
    set DHAN_TOTP_SECRET=...
    python strategy2_15min_backtest.py

Adjust LOOKBACK_YEARS below if you want a longer/shorter window —
1 year keeps runtime reasonable for a first pass; Dhan supports up to 5.
"""

import datetime
import logging
import os
import time

import pandas as pd
import pyotp
from dhanhq import DhanContext, DhanLogin, dhanhq

from dhan_data import DhanData
from supertrend import compute_supertrend, supertrend_signals

logging.basicConfig(
    level=logging.DEBUG,
    format="%(levelname)s %(name)s: %(message)s",
)

ST_SLOW_PERIOD, ST_SLOW_MULT = 10, 3.0
ST_FAST_PERIOD, ST_FAST_MULT = 2, 1.0
INTRA_INTERVAL  = 15   # minutes
LOOKBACK_YEARS  = 1    # Dhan supports up to 5; start smaller, widen later
CHUNK_DAYS      = 85   # stay safely under Dhan's 90-day-per-call cap


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
    print("Logged in to Dhan (local session, not touching GitHub secrets).")
    return dhanhq(ctx)


def get_qualified_pool() -> list[str]:
    df = pd.read_csv("cache/nse500_latest.csv")
    df["ticker"] = df["ticker"].str.replace(r"\.NS$", "", regex=True).str.upper()

    elite = set(df[df["EliteCompounderScore"] >= 65]["ticker"])
    combo_mask = (
        df["obv_leadership_rank"].notna() & (df["obv_leadership_rank"] > 90) &
        df["rs_rank"].notna() & (df["rs_rank"] > 90)
    )
    combo = set(df[combo_mask]["ticker"])
    return sorted(elite | combo)


def fetch_intraday_extended(dd: DhanData, client: dhanhq, symbol: str,
                             years: int) -> pd.DataFrame | None:
    """
    Pages through Dhan's intraday endpoint in <=CHUNK_DAYS windows to
    build up a multi-year 15-min history. dhan_data.get_intraday() only
    fetches the last few days by default — this replicates its parsing
    but loops across the full lookback window.
    """
    sid = dd.get_security_id(symbol)
    if not sid:
        return None

    frames = []
    end = datetime.date.today()
    start_bound = end - datetime.timedelta(days=years * 365)

    while end > start_bound:
        chunk_start = max(end - datetime.timedelta(days=CHUNK_DAYS), start_bound)
        try:
            resp = client.intraday_minute_data(
                security_id=sid,
                exchange_segment=dd.EXCHANGE,
                instrument_type=dd.INSTRUMENT,
                from_date=chunk_start.strftime("%Y-%m-%d"),
                to_date=end.strftime("%Y-%m-%d"),
                interval=INTRA_INTERVAL,
            )
            df = dd._parse_response(resp, symbol)
            if df is not None and not df.empty:
                frames.append(df)
        except Exception as exc:
            print(f"    ! chunk {chunk_start}..{end} failed: {exc}")

        end = chunk_start - datetime.timedelta(days=1)
        time.sleep(0.3)  # be polite to Dhan's API

    if not frames:
        return None

    full = pd.concat(frames).sort_index()
    full = full[~full.index.duplicated(keep="first")]
    return full


def true_strategy2_entry_days(weekly, daily, intraday) -> tuple[int, int]:
    """
    Approximates check_mtf_conditions() over history:
    layers 1-4 (weekly ST10,3+ST2,1, daily ST10,3+ST2,1) all bullish AND
    at least one 15-min bar that trading day had a genuine ST(10,3)
    cross_up with candle close above the line.

    Approximation note: live checks use the most recently completed
    daily bar's state at the moment of each intraday cycle; this uses
    same-day daily/weekly state as a close approximation, which is
    slightly generous (today's daily direction may not be fully known
    intraday) but reasonable for an aggregate frequency estimate.
    """
    w10 = compute_supertrend(weekly, ST_SLOW_PERIOD, ST_SLOW_MULT, prefix="w10_")
    w2  = compute_supertrend(weekly, ST_FAST_PERIOD, ST_FAST_MULT, prefix="w2_")
    d10 = compute_supertrend(daily, ST_SLOW_PERIOD, ST_SLOW_MULT, prefix="d10_")
    d2  = compute_supertrend(daily, ST_FAST_PERIOD, ST_FAST_MULT, prefix="d2_")

    w10_bull = w10["w10_st_bullish"].reindex(daily.index, method="ffill").fillna(False)
    w2_bull  = w2["w2_st_bullish"].reindex(daily.index, method="ffill").fillna(False)

    eligible_days = (
        w10_bull &
        w2_bull &
        d10["d10_st_bullish"].fillna(False) &
        d2["d2_st_bullish"].fillna(False)
    )
    eligible_dates = set(eligible_days.index[eligible_days].date)

    intra_st = compute_supertrend(intraday, ST_SLOW_PERIOD, ST_SLOW_MULT, prefix="i10_")
    intra_st = supertrend_signals(intra_st, prefix="i10_")
    close_above = intra_st["close"] > intra_st["i10_st_value"]
    trigger = intra_st["i10_st_cross_up"] & close_above.fillna(False)

    trigger_dates = set(intra_st.index[trigger].date)
    true_entry_dates = eligible_dates & trigger_dates

    return len(true_entry_dates), len(eligible_dates)


def main():
    client = login()
    dd = DhanData(client)
    pool = get_qualified_pool()
    print(f"\nToday's qualified pool: {len(pool)} tickers\n")

    results = {}
    debug_printed = False
    for i, symbol in enumerate(pool, 1):
        print(f"[{i}/{len(pool)}] {symbol}...")
        daily = dd.get_daily(symbol, days=LOOKBACK_YEARS * 365 + 30)
        if daily is None or len(daily) < 60:
            print(f"  skipped (insufficient daily data — got "
                  f"{0 if daily is None else len(daily)} rows)")
            if not debug_printed:
                sid = dd.get_security_id(symbol)
                print(f"  DEBUG: security_id for {symbol} = {sid!r}")
                if sid:
                    raw = client.historical_daily_data(
                        security_id=sid, exchange_segment=dd.EXCHANGE,
                        instrument_type=dd.INSTRUMENT,
                        from_date=(datetime.date.today() - datetime.timedelta(days=400)).strftime("%Y-%m-%d"),
                        to_date=datetime.date.today().strftime("%Y-%m-%d"),
                    )
                    print(f"  DEBUG: raw API response = {raw}")
                debug_printed = True
            continue
        weekly = dd.get_weekly(symbol, weeks=LOOKBACK_YEARS * 52 + 10)
        if weekly is None or len(weekly) < 15:
            print("  skipped (insufficient weekly data)")
            continue

        intraday = fetch_intraday_extended(dd, client, symbol, LOOKBACK_YEARS)
        if intraday is None or len(intraday) < 100:
            print("  skipped (insufficient 15-min data)")
            continue

        true_days, eligible_days = true_strategy2_entry_days(weekly, daily, intraday)
        results[symbol] = (true_days, eligible_days)
        print(f"  layers 1-4 eligible: {eligible_days} days, "
              f"TRUE entry (layers 1-4 + 15-min trigger): {true_days} days")

    print("\n" + "=" * 70)
    print(f"STRATEGY 2 — TRUE ENTRY FREQUENCY (all 5 layers, ~{LOOKBACK_YEARS}y, real Dhan 15-min data)")
    print("=" * 70)
    if results:
        total_true = sum(v[0] for v in results.values())
        print(f"Total true entry days across pool: {total_true} over ~{LOOKBACK_YEARS}y")
        print(f"Aggregate: ~{total_true/(LOOKBACK_YEARS*12):.1f} true entry events/month across the whole pool\n")
        for t, (true_days, elig) in sorted(results.items(), key=lambda x: -x[1][0]):
            print(f"  {t:12s} {true_days} true entry days ({elig} layers-1-4-eligible days)")
    else:
        print("No valid results.")


if __name__ == "__main__":
    main()
