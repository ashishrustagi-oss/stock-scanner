"""
Pre-signal watchlist — sent at 9:15 AM IST daily via Telegram.

Shows qualified stocks that are closest to triggering a Layer 3 entry
(Daily ST(2,1) bullish crossover), split into two sections:

  🎯 IMMINENT  — ST(2,1) currently bearish but price within 2% of the
                  ST(2,1) line. One strong candle could trigger entry.

  ⚡ RECENT    — ST(2,1) turned bullish within the last 2 trading days
                  AND ST(10,3) also bullish. All conditions met — these
                  may have been missed by the exact-crossover detector or
                  are still valid intraday entry candidates today.

Only stocks passing Layer 1 (scanner qualified) and Layer 2
(Weekly + Daily ST(10,3) both bullish) are shown — same filters as
the live trade system.

Called by the trade_scan_dhan workflow at 9:15 AM IST.
"""

import datetime
import json
import logging
import os

import pandas as pd
import requests
import yfinance as yf

import config
from market_hours import is_within_trading_window, now_ist, MARKET_OPEN
from supertrend import compute_supertrend, supertrend_signals

logger = logging.getLogger(__name__)

ST_SLOW_PERIOD = 10; ST_SLOW_MULT = 3.0
ST_FAST_PERIOD = 2;  ST_FAST_MULT = 1.0
IMMINENT_THRESHOLD_PCT = 2.0   # price within 2% of ST(2,1) line
RECENT_DAYS = 2                # crossover within last N trading days

WATCHLIST_STATE_PATH     = "cache/watchlist_state.json"
WATCHLIST_CATCHUP_CUTOFF = datetime.time(11, 0)  # give up catching up after this (IST)


def _fetch_ohlcv(symbol: str, interval: str, period: str) -> pd.DataFrame | None:
    try:
        df = yf.Ticker(f"{symbol}.NS").history(interval=interval, period=period)
        if df.empty:
            return None
        df.columns = [c.lower() for c in df.columns]
        return df
    except Exception as exc:
        logger.debug("watchlist: _fetch_ohlcv(%s) failed: %s", symbol, exc)
        return None


def _check_stock(symbol: str) -> dict | None:
    """
    Returns a dict with watchlist status for this stock, or None if it
    fails Layer 2 filters or has insufficient data.

    Returned dict keys:
      symbol, current_price, st_fast_value, st_fast_bullish,
      pct_from_st_fast, crossover_days_ago,
      category: 'imminent' | 'recent' | None
    """
    # Weekly ST(10,3) filter
    weekly_df = _fetch_ohlcv(symbol, "1wk", "2y")
    if weekly_df is None or len(weekly_df) < 15:
        return None
    weekly_st = compute_supertrend(weekly_df, ST_SLOW_PERIOD, ST_SLOW_MULT, prefix="w_")
    if not weekly_st.iloc[-1]["w_st_bullish"]:
        return None   # Layer 2 weekly filter failed

    # Daily ST(10,3) and ST(2,1)
    daily_df = _fetch_ohlcv(symbol, "1d", "1y")
    if daily_df is None or len(daily_df) < 15:
        return None

    daily_slow = compute_supertrend(daily_df, ST_SLOW_PERIOD, ST_SLOW_MULT, prefix="d10_")
    if not daily_slow.iloc[-1]["d10_st_bullish"]:
        return None   # Layer 2 daily filter failed

    daily_fast = compute_supertrend(daily_df, ST_FAST_PERIOD, ST_FAST_MULT, prefix="d2_")
    daily_fast = supertrend_signals(daily_fast, prefix="d2_")

    last = daily_fast.iloc[-1]
    current_price = float(last["close"])
    st_fast_value = float(last["d2_st_value"]) if last["d2_st_value"] else None
    st_fast_bullish = bool(last["d2_st_bullish"])

    if not st_fast_value:
        return None

    pct_from_st = abs(current_price - st_fast_value) / st_fast_value * 100

    # Check IMMINENT: bearish but within threshold of ST(2,1) line
    if not st_fast_bullish and pct_from_st <= IMMINENT_THRESHOLD_PCT:
        return {
            "symbol": symbol,
            "current_price": current_price,
            "st_fast_value": st_fast_value,
            "pct_from_st": pct_from_st,
            "st_fast_bullish": False,
            "crossover_days_ago": None,
            "category": "imminent",
        }

    # Check RECENT: bullish crossover within last N days
    if st_fast_bullish:
        recent = daily_fast.tail(RECENT_DAYS + 1)
        crossover_rows = recent[recent["d2_st_cross_up"]]
        if not crossover_rows.empty:
            days_ago = len(daily_fast) - daily_fast.index.get_loc(crossover_rows.index[-1]) - 1
            return {
                "symbol": symbol,
                "current_price": current_price,
                "st_fast_value": st_fast_value,
                "pct_from_st": pct_from_st,
                "st_fast_bullish": True,
                "crossover_days_ago": days_ago,
                "category": "recent",
            }

    return None   # passes Layer 2 but not in either watchlist category


def build_watchlist() -> dict[str, list[dict]]:
    """
    Reads today's qualified stock list and checks each stock.
    Returns {"imminent": [...], "recent": [...]} sorted by
    proximity to trigger (closest first).
    """
    csv_path = config.TRADE_QUALIFIED_CSV_PATH
    if not os.path.exists(csv_path):
        logger.warning("watchlist: qualified CSV not found")
        return {"imminent": [], "recent": []}

    try:
        df = pd.read_csv(csv_path)
        df["ticker"] = df["ticker"].str.replace(r"\.NS$", "", regex=True).str.upper()

        elite_set = set(df[df["EliteCompounderScore"] >= 65]["ticker"].tolist())
        combo_mask = (
            df["obv_leadership_rank"].notna() & (df["obv_leadership_rank"] > 90) &
            df["rs_rank"].notna() & (df["rs_rank"] > 90)
        )
        combo_set = set(df[combo_mask]["ticker"].tolist())
        all_qualified = list(elite_set | combo_set)

        logger.info("watchlist: checking %d qualified stocks", len(all_qualified))
    except Exception as exc:
        logger.error("watchlist: failed to read CSV: %s", exc)
        return {"imminent": [], "recent": []}

    imminent = []
    recent = []

    for symbol in all_qualified:
        try:
            result = _check_stock(symbol)
            if result is None:
                continue
            # Add strategy label
            strategies = []
            if symbol in elite_set:
                strategies.append("Elite")
            if symbol in combo_set:
                strategies.append("Combo")
            result["strategy"] = "+".join(strategies)

            if result["category"] == "imminent":
                imminent.append(result)
            elif result["category"] == "recent":
                recent.append(result)
        except Exception as exc:
            logger.debug("watchlist: %s check failed: %s", symbol, exc)

    # Sort imminent by closest to trigger (smallest pct_from_st first)
    imminent.sort(key=lambda x: x["pct_from_st"])
    # Sort recent by most recent crossover first
    recent.sort(key=lambda x: x.get("crossover_days_ago", 99))

    logger.info("watchlist: imminent=%d, recent=%d", len(imminent), len(recent))
    return {"imminent": imminent, "recent": recent}


def send_watchlist_alert(watchlist: dict[str, list[dict]]) -> None:
    """Formats and sends the watchlist to Telegram."""
    imminent = watchlist.get("imminent", [])
    recent   = watchlist.get("recent", [])

    header_time = now_ist().strftime("%H:%M IST")

    if not imminent and not recent:
        msg = (
            f"👁 *PRE-SIGNAL WATCHLIST — {header_time}*\n"
            "No stocks currently in trigger zone.\n"
            "All qualified stocks either already in position or "
            "ST(2,1) not yet approaching crossover."
        )
    else:
        lines = [f"👁 *PRE-SIGNAL WATCHLIST — {header_time}*\n"]

        if imminent:
            lines.append("🎯 *IMMINENT* — price within 2% of ST(2,1) line:")
            for s in imminent[:8]:
                direction = "↑" if s["current_price"] > s["st_fast_value"] else "↓"
                lines.append(
                    f"  • *{s['symbol']}* ₹{s['current_price']:,.2f} "
                    f"| ST(2,1): ₹{s['st_fast_value']:,.2f} "
                    f"| {s['pct_from_st']:.1f}% away {direction} "
                    f"| [{s['strategy']}]"
                )
            if len(imminent) > 8:
                lines.append(f"  ...and {len(imminent)-8} more")

        if recent:
            if imminent:
                lines.append("")
            lines.append(f"⚡ *RECENT CROSSOVER* — ST(2,1) turned bullish (last {RECENT_DAYS} days):")
            for s in recent[:8]:
                days_txt = "today" if s["crossover_days_ago"] == 0 else f"{s['crossover_days_ago']}d ago"
                lines.append(
                    f"  • *{s['symbol']}* ₹{s['current_price']:,.2f} "
                    f"| crossed {days_txt} "
                    f"| [{s['strategy']}]"
                )
            if len(recent) > 8:
                lines.append(f"  ...and {len(recent)-8} more")

        lines.append("\n_These stocks pass Layer 1+2 filters. Entry fires when ST(2,1) crosses up during market hours._")
        msg = "\n".join(lines)

    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.warning("watchlist: Telegram not configured")
        return

    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg, "parse_mode": "Markdown"},
            timeout=15,
        )
        logger.info("watchlist: Telegram alert sent")
    except Exception as exc:
        logger.error("watchlist: Telegram send failed: %s", exc)


def _load_watchlist_state() -> dict:
    try:
        with open(WATCHLIST_STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_watchlist_state(state: dict) -> None:
    os.makedirs(os.path.dirname(WATCHLIST_STATE_PATH), exist_ok=True)
    with open(WATCHLIST_STATE_PATH, "w") as f:
        json.dump(state, f)


def _should_send_watchlist() -> bool:
    """
    Self-healing check, replacing the old "only on the literal 9:15 AM
    cron string" condition. That approach silently failed whenever
    GitHub dropped/skipped that exact scheduled trigger under load —
    no later run would ever match, so the whole day went without a
    watchlist even though other trade cycles ran fine.

    Instead: has today's watchlist already been sent? If not, and
    we're still within a reasonable catch-up window after market open,
    send it now. Whichever run fires first after 9:15 AM catches it,
    regardless of which specific cron slot GitHub actually delivered.
    """
    ist = now_ist()
    today = ist.date().isoformat()

    state = _load_watchlist_state()
    if state.get("last_sent_date") == today:
        return False

    if ist.time() < MARKET_OPEN:
        return False

    if ist.time() > WATCHLIST_CATCHUP_CUTOFF:
        logger.info(
            "watchlist: past catch-up cutoff (%s IST) and not yet sent today — giving up for today",
            WATCHLIST_CATCHUP_CUTOFF
        )
        return False

    return True


def run_watchlist() -> None:
    """Main entry point — called every trade_scan_dhan cycle; self-decides
    whether today's watchlist still needs to be sent."""
    # This is only meant to fire once, right at market open. If the cron
    # got delayed and is now firing well into (or after) trading hours,
    # skip it rather than sending a stale "9:15 AM" watchlist hours late.
    if not is_within_trading_window():
        return

    if not _should_send_watchlist():
        return

    logger.info("watchlist: building pre-signal watchlist...")
    watchlist = build_watchlist()
    send_watchlist_alert(watchlist)
    logger.info("watchlist: done — imminent=%d, recent=%d",
                len(watchlist["imminent"]), len(watchlist["recent"]))

    _save_watchlist_state({"last_sent_date": now_ist().date().isoformat()})


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    run_watchlist()
