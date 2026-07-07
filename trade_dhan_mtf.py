"""
Strategy 2 — Multi-Timeframe Momentum (MTF) trading module.

Same scanner qualification as Strategy 1 (Elite Compounder + OBV/RS Combo)
but uses a 5-layer Supertrend filter for higher-conviction entries:

  Layer 1 — Scanner qualification (NSE500 + SmallMicro)
  Layer 2 — Weekly ST(10,3) bullish
  Layer 3 — Weekly ST(2,1) bullish
  Layer 4 — Daily ST(10,3) bullish
  Layer 5 — Daily ST(2,1) bullish
  Entry   — 15-min ST(10,3) bullish crossover AND candle closes above it

Exit:
  - Daily ST(2,1) bearish crossover (primary exit)
  - 10% hard stop-loss (safety floor)

Capital: Rs 1,00,000 — max 5 positions x Rs 20,000 each
Orders:  CNC delivery via Dhan API
Universe: NSE500 + SmallMicro (both scanner lists)

All price data fetched via Dhan API (dhan_data.py), not yfinance.
"""

import datetime
import json
import logging
import math
import os
import time

import pandas as pd
import requests
from dhanhq import DhanContext, dhanhq

import config
from dhan_data import DhanData
from market_hours import is_within_trading_window
from supertrend import get_supertrend_state, compute_supertrend, supertrend_signals

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BUDGET           = 1_00_000   # total capital for this strategy
TARGET_POSITION  = 20_000     # target per position
MAX_POSITIONS    = 5
STOP_LOSS_PCT    = 0.10

ST_SLOW_PERIOD   = 10; ST_SLOW_MULT = 3.0
ST_FAST_PERIOD   = 2;  ST_FAST_MULT = 1.0
INTRA_INTERVAL   = 15         # 15-minute candles for entry trigger

STATE_KEY        = "mtf"      # key in trade_state.json

# ---------------------------------------------------------------------------
# Dhan client + data layer
# ---------------------------------------------------------------------------

_dhan_data: DhanData | None = None


def get_dhan_client() -> dhanhq | None:
    client_id    = os.environ.get("DHAN_CLIENT_ID", "")
    access_token = os.environ.get("DHAN_ACCESS_TOKEN", "")
    if not client_id or not access_token:
        logger.error("trade_mtf: DHAN_CLIENT_ID or DHAN_ACCESS_TOKEN missing")
        _send_alert("⚠️ *MTF trade cycle skipped*\n`DHAN_ACCESS_TOKEN` missing.")
        return None
    try:
        ctx = DhanContext(client_id, access_token)
        return dhanhq(ctx)
    except Exception as exc:
        logger.error("trade_mtf: client init failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Multi-timeframe Supertrend check
# ---------------------------------------------------------------------------

def check_mtf_conditions(symbol: str) -> dict:
    """
    Checks all 5 Supertrend layers plus the 15-min entry trigger.

    Returns dict:
      weekly_10_3_bullish, weekly_2_1_bullish,
      daily_10_3_bullish,  daily_2_1_bullish,
      daily_2_1_cross_down  — EXIT signal
      intra_10_3_cross_up   — ENTRY trigger (15-min ST(10,3) bullish crossover
                               with candle close above the line)
      eligible_for_entry    — True if layers 2-5 all pass
      error                 — string if data missing, else None
    """
    result = {
        "weekly_10_3_bullish":  None,
        "weekly_2_1_bullish":   None,
        "daily_10_3_bullish":   None,
        "daily_2_1_bullish":    None,
        "daily_2_1_cross_down": False,
        "intra_10_3_cross_up":  False,
        "eligible_for_entry":   False,
        "error":                None,
    }

    if _dhan_data is None:
        result["error"] = "DhanData not initialised"
        return result

    # Weekly data — layers 2 + 3
    weekly_df = _dhan_data.get_weekly(symbol, weeks=104)
    if weekly_df is None or len(weekly_df) < 15:
        result["error"] = "insufficient weekly data"
        return result

    w_slow = get_supertrend_state(weekly_df, ST_SLOW_PERIOD, ST_SLOW_MULT, prefix="w10_")
    w_fast = get_supertrend_state(weekly_df, ST_FAST_PERIOD, ST_FAST_MULT, prefix="w2_")
    result["weekly_10_3_bullish"] = w_slow["bullish"]
    result["weekly_2_1_bullish"]  = w_fast["bullish"]

    time.sleep(0.15)  # micro-throttle between internal Data API calls

    # Daily data — layers 4 + 5 + exit signal
    daily_df = _dhan_data.get_daily(symbol, days=400)
    if daily_df is None or len(daily_df) < 15:
        result["error"] = "insufficient daily data"
        return result

    d_slow = get_supertrend_state(daily_df, ST_SLOW_PERIOD, ST_SLOW_MULT, prefix="d10_")
    d_fast = get_supertrend_state(daily_df, ST_FAST_PERIOD, ST_FAST_MULT, prefix="d2_")
    result["daily_10_3_bullish"]   = d_slow["bullish"]
    result["daily_2_1_bullish"]    = d_fast["bullish"]
    result["daily_2_1_cross_down"] = d_fast["cross_down"]   # EXIT signal

    # All 4 higher-timeframe filters must pass before checking 15-min
    result["eligible_for_entry"] = bool(
        result["weekly_10_3_bullish"] and
        result["weekly_2_1_bullish"]  and
        result["daily_10_3_bullish"]  and
        result["daily_2_1_bullish"]
    )

    if not result["eligible_for_entry"]:
        return result   # skip 15-min fetch if higher TF filters fail

    time.sleep(0.15)  # micro-throttle between internal Data API calls

    # 15-min data — entry trigger
    intra_df = _dhan_data.get_intraday(symbol, interval=INTRA_INTERVAL, days=5)
    if intra_df is None or len(intra_df) < 15:
        result["error"] = "insufficient 15-min data"
        return result

    # Compute ST(10,3) on 15-min and check for bullish crossover on the
    # most recent CLOSED candle (iloc[-1] is the last completed bar since
    # we run at 15-min intervals, aligned with candle close)
    intra_st = compute_supertrend(intra_df, ST_SLOW_PERIOD, ST_SLOW_MULT, prefix="i10_")
    intra_st = supertrend_signals(intra_st, prefix="i10_")

    last = intra_st.iloc[-1]
    # Entry condition: crossover occurred on this bar AND close > ST line
    # (close > ST line is implicit when ST is bullish, but we verify explicitly)
    cross_up   = bool(last["i10_st_cross_up"])
    close_above = float(last["close"]) > float(last["i10_st_value"]) if last["i10_st_value"] else False
    result["intra_10_3_cross_up"] = cross_up and close_above

    return result


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------

def place_order(symbol: str, qty: int, transaction_type: str,
                client: dhanhq) -> dict:
    sid = _dhan_data.get_security_id(symbol)
    if not sid:
        raise ValueError(f"No security_id for {symbol}")
    logger.info("trade_mtf: %s %s x%d CNC MARKET", transaction_type, symbol, qty)
    resp = client.place_order(
        security_id=sid,
        exchange_segment=dhanhq.NSE,
        transaction_type=dhanhq.BUY if transaction_type == "BUY" else dhanhq.SELL,
        quantity=qty,
        order_type=dhanhq.MARKET,
        product_type=dhanhq.CNC,
        price=0,
        tag="AshishMTF",
    )
    logger.info("trade_mtf: order response: %s", resp)
    return resp


def compute_qty(price: float) -> int:
    if price <= 0:
        return 0
    return max(1, math.floor(TARGET_POSITION / price))


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _load_state() -> dict:
    path = config.TRADE_STATE_PATH
    if os.path.exists(path):
        with open(path) as f:
            data = json.load(f)
            return data.get(STATE_KEY, {})
    return {}


def _save_state(positions: dict) -> None:
    os.makedirs("cache", exist_ok=True)
    path = config.TRADE_STATE_PATH
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        data = {}
    data[STATE_KEY] = positions
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------------------
# Qualified stocks — NSE500 + SmallMicro
# ---------------------------------------------------------------------------

def get_qualified_stocks() -> list[str]:
    """
    Returns combined qualified stock list from NSE500 + SmallMicro scans.
    Uses both TRADE_QUALIFIED_CSV_PATH (NSE500) and
    TRADE_SMALLMICRO_CSV_PATH (SmallMicro) if available.
    """
    symbols = set()

    # NSE500
    nse_path = config.TRADE_QUALIFIED_CSV_PATH
    if os.path.exists(nse_path):
        try:
            df = pd.read_csv(nse_path)
            df["ticker"] = df["ticker"].str.replace(r"\.NS$", "", regex=True).str.upper()
            elite = set(df[df["EliteCompounderScore"] >= 65]["ticker"])
            combo_mask = (
                df["obv_leadership_rank"].notna() & (df["obv_leadership_rank"] > 90) &
                df["rs_rank"].notna() & (df["rs_rank"] > 90)
            )
            combo = set(df[combo_mask]["ticker"])
            symbols |= elite | combo
            logger.info("trade_mtf: NSE500 qualified: %d", len(elite | combo))
        except Exception as exc:
            logger.error("trade_mtf: NSE500 CSV read failed: %s", exc)

    # SmallMicro
    sm_path = getattr(config, "TRADE_SMALLMICRO_CSV_PATH", "cache/smallmicro_latest.csv")
    if os.path.exists(sm_path):
        try:
            df = pd.read_csv(sm_path)
            df["ticker"] = df["ticker"].str.replace(r"\.NS$", "", regex=True).str.upper()
            sm_qualified = set(df[df.get("smallmicro_strict_pass", False) == True]["ticker"])
            symbols |= sm_qualified
            logger.info("trade_mtf: SmallMicro qualified: %d", len(sm_qualified))
        except Exception as exc:
            logger.debug("trade_mtf: SmallMicro CSV read failed (may not exist yet): %s", exc)

    logger.info("trade_mtf: total qualified: %d", len(symbols))
    return list(symbols)


# ---------------------------------------------------------------------------
# Telegram alerts
# ---------------------------------------------------------------------------

def _send_alert(message: str) -> None:
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as exc:
        logger.debug("trade_mtf: Telegram failed: %s", exc)


def _entry_alert(symbol: str, qty: int, price: float, cond: dict) -> None:
    w10 = "✅" if cond.get("weekly_10_3_bullish") else "❌"
    w2  = "✅" if cond.get("weekly_2_1_bullish")  else "❌"
    d10 = "✅" if cond.get("daily_10_3_bullish")  else "❌"
    d2  = "✅" if cond.get("daily_2_1_bullish")   else "❌"
    _send_alert(
        f"🟢 *MTF ENTRY — {symbol}*\n"
        f"Signal   : 15-min ST(10,3) bullish crossover + candle close above\n"
        f"Price    : ₹{price:,.2f}\n"
        f"Qty      : {qty} shares (~₹{qty*price:,.0f})\n"
        f"Filters  : {w10} W-ST(10,3) | {w2} W-ST(2,1) | {d10} D-ST(10,3) | {d2} D-ST(2,1)\n"
        f"Order    : *BUY {qty} {symbol} CNC MARKET — placed via Dhan*"
    )


def _exit_alert(symbol: str, qty: int, entry_price: float,
                exit_price: float, reason: str) -> None:
    pnl     = (exit_price - entry_price) * qty
    pnl_pct = (exit_price - entry_price) / entry_price * 100
    emoji   = "🟩" if pnl >= 0 else "🟥"
    _send_alert(
        f"🔴 *MTF EXIT — {symbol}*\n"
        f"Reason : {reason}\n"
        f"Entry  : ₹{entry_price:,.2f} → Exit: ₹{exit_price:,.2f}\n"
        f"P&L    : {emoji} ₹{pnl:+,.0f} ({pnl_pct:+.1f}%) on {qty} shares\n"
        f"Order  : *SELL {qty} {symbol} CNC MARKET — placed via Dhan*"
    )


def _cycle_summary(positions: dict) -> None:
    now_ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    _send_alert(
        f"📈 *MTF trade cycle — {now_ist.strftime('%H:%M IST')}*\n"
        f"Positions: {len(positions)}/{MAX_POSITIONS} open"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_trade_cycle() -> None:
    global _dhan_data

    logger.info("trade_mtf: === starting MTF cycle %s UTC ===",
                datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"))

    # GitHub Actions cron triggers can fire late (queue delays). If this
    # run woke up outside the actual trading window, bail out before any
    # API calls or Telegram alerts — a delayed cron should be a no-op.
    if not is_within_trading_window():
        return

    client = get_dhan_client()
    if client is None:
        return

    _dhan_data = DhanData(client)

    positions  = _load_state()
    qualified  = get_qualified_stocks()

    # --- Check exits for open positions ---
    for symbol, pos in list(positions.items()):
        entry_price = pos["entry_price"]
        qty         = pos["qty"]

        ltp = _dhan_data.get_ltp(symbol)
        time.sleep(0.2)  # throttle — rate-limit protection
        if ltp is None:
            continue

        # Hard stop-loss
        if ltp <= entry_price * (1 - STOP_LOSS_PCT):
            logger.info("trade_mtf: STOP LOSS %s", symbol)
            try:
                place_order(symbol, qty, "SELL", client)
            except Exception as exc:
                logger.error("trade_mtf: SELL failed %s: %s", symbol, exc)
            _exit_alert(symbol, qty, entry_price, ltp,
                        f"Hard stop-loss ({STOP_LOSS_PCT*100:.0f}%)")
            del positions[symbol]
            continue

        # Daily ST(2,1) bearish crossover — EXIT signal
        cond = check_mtf_conditions(symbol)
        if cond.get("daily_2_1_cross_down"):
            logger.info("trade_mtf: D-ST(2,1) EXIT %s", symbol)
            try:
                place_order(symbol, qty, "SELL", client)
            except Exception as exc:
                logger.error("trade_mtf: SELL failed %s: %s", symbol, exc)
            _exit_alert(symbol, qty, entry_price, ltp,
                        "Daily ST(2,1) bearish crossover")
            del positions[symbol]
            continue

        # Dropped off scanner list
        if symbol not in qualified:
            logger.info("trade_mtf: %s dropped off qualified list", symbol)
            try:
                place_order(symbol, qty, "SELL", client)
            except Exception as exc:
                logger.error("trade_mtf: SELL failed %s: %s", symbol, exc)
            _exit_alert(symbol, qty, entry_price, ltp,
                        "Dropped off scanner qualified list")
            del positions[symbol]

    # --- Check entries ---
    if len(positions) >= MAX_POSITIONS:
        logger.info("trade_mtf: portfolio full (%d/%d)", len(positions), MAX_POSITIONS)
        _save_state(positions)
        _cycle_summary(positions)
        return

    candidates = [s for s in qualified if s not in positions]
    _error_counts: dict[str, int] = {}

    for symbol in candidates:
        if len(positions) >= MAX_POSITIONS:
            break

        time.sleep(0.3)  # throttle BEFORE get_ltp — was firing with zero
                          # delay and hitting Dhan's rate limit
        ltp = _dhan_data.get_ltp(symbol)
        if ltp is None:
            _error_counts["no_ltp"] = _error_counts.get("no_ltp", 0) + 1
            continue

        cond = check_mtf_conditions(symbol)
        time.sleep(0.3)  # throttle — Dhan Data APIs cap at 5 req/sec; each
                          # check makes 2-3 calls (weekly+daily+intraday), so
                          # without this, scanning many candidates back to
                          # back can silently hit the rate limit and look
                          # identical to a real data-fetch failure

        if cond.get("error"):
            logger.debug("trade_mtf: %s error: %s", symbol, cond["error"])
            _error_counts[cond["error"]] = _error_counts.get(cond["error"], 0) + 1
            continue

        if not cond.get("eligible_for_entry"):
            logger.debug("trade_mtf: %s failed higher TF filters", symbol)
            _error_counts["not_eligible"] = _error_counts.get("not_eligible", 0) + 1
            continue

        if not cond.get("intra_10_3_cross_up"):
            logger.debug("trade_mtf: %s no 15-min ST(10,3) crossover", symbol)
            _error_counts["no_cross_up"] = _error_counts.get("no_cross_up", 0) + 1
            continue

        # All 5 layers passed — place entry
        qty = compute_qty(ltp)
        if qty == 0:
            continue

        logger.info("trade_mtf: ENTRY %s qty=%d @ Rs %.2f", symbol, qty, ltp)
        try:
            place_order(symbol, qty, "BUY", client)
            positions[symbol] = {
                "qty":         qty,
                "entry_price": ltp,
                "entry_date":  datetime.date.today().isoformat(),
            }
            _entry_alert(symbol, qty, ltp, cond)
        except Exception as exc:
            logger.error("trade_mtf: BUY failed %s: %s", symbol, exc)

        time.sleep(0.5)

    if _error_counts:
        logger.info("trade_mtf: scan skip reasons: %s", _error_counts)

    _save_state(positions)
    _cycle_summary(positions)
    logger.info("trade_mtf: cycle done — %d/%d positions open",
                len(positions), MAX_POSITIONS)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    run_trade_cycle()
