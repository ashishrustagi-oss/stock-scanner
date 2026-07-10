"""
Strategy 3 — "buy'n'hold" — runs 3:00-3:15 PM IST (9:30-9:45 UTC), every 5 min, weekdays.

Universe : Ashish Stock Broadcast Google Sheet (fundamentally pre-qualified,
           dynamic — refreshed daily from price + Screener.in data).
Filter   : weekly ST(2,1) bullish AND daily ST(2,1) bullish
           (early positioning — fast Supertrend already turned on both timeframes)
Trigger  : daily ST(10,3) transitions bearish -> bullish since last check
           (confirmed momentum — the slower, more reliable Supertrend flips too)
Exit     : NONE. Pure buy and hold, indefinitely.
Sizing   : Rs 20,000 max per stock (same compute_qty() pattern as trade_dhan.py)
Cap      : Max 2 NEW positions per day
Rule     : Each symbol may be bought at most ONCE, ever, under this strategy —
           no re-entry even if it re-qualifies later.
Tie-break: If more than 2 symbols trigger the same day, prefer higher `stage`
           (Stage 2 > Stage 1), take the top 2.

Credentials needed (~/.env on VPS):
  DHAN_CLIENT_ID     — Dhan client ID
  DHAN_ACCESS_TOKEN  — sourced from ~/.dhan_token.env (written by dhan_token_refresh.py)
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — for entry / failure alerts

Sheet access:
  /home/opc/service_account.json — Google service account key
  BROADCAST_SHEET_ID — hardcoded below (update if the sheet ever moves)
"""
import datetime
import logging
import math
import os
import json
import time

import requests
import gspread
from google.oauth2.service_account import Credentials
from dhanhq import dhanhq, DhanContext

# Reuse tested, working implementations from trade_dhan.py rather than
# duplicating/guessing Dhan API call patterns — get_security_id() handles
# the NSE security-list CSV lookup+cache, place_order() handles the actual
# CNC MARKET order call. Both are already live-tested in Strategy 1.
import trade_dhan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BROADCAST_SHEET_ID = "1o0RurZ_8MulNxPSl5fAboE0mv6TVieP--PnWliDuYZU"
SERVICE_ACCOUNT_PATH = os.path.expanduser("~/service_account.json")
STATE_PATH = os.path.expanduser("~/stock-scanner/cache/strategy3_state.json")

TARGET_POSITION   = 20_000   # Rs per stock
MAX_NEW_PER_DAY   = 2

# Trading window guard — 3:00-3:15 PM IST only. Cron fires every 5 min in
# this window (9:30/9:35/9:40/9:45 UTC); this is a belt-and-suspenders
# in-script check in case cron fires slightly outside the intended range.
IST_WINDOW_START = datetime.time(15, 0)
IST_WINDOW_END   = datetime.time(15, 15)


def is_within_window() -> bool:
    now_utc = datetime.datetime.utcnow()
    now_ist = now_utc + datetime.timedelta(hours=5, minutes=30)
    if now_ist.weekday() >= 5:
        return False
    return IST_WINDOW_START <= now_ist.time() <= IST_WINDOW_END
# ---------------------------------------------------------------------------
# State — bought symbols (permanent) + last-seen daily ST(10,3) direction
# ---------------------------------------------------------------------------
def _load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                return json.load(f)
        except Exception as exc:
            logger.error("buynhold: failed to load state, starting fresh: %s", exc)
    return {"bought": {}, "last_daily_10_3_dir": {}, "daily_buy_count": {}}


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Dhan client
# ---------------------------------------------------------------------------
def get_dhan_client() -> dhanhq | None:
    client_id    = os.environ.get("DHAN_CLIENT_ID", "")
    access_token = os.environ.get("DHAN_ACCESS_TOKEN", "")
    if not client_id or not access_token:
        logger.error("buynhold: DHAN_CLIENT_ID or DHAN_ACCESS_TOKEN missing.")
        _send_alert(
            "⚠️ *buy'n'hold cycle skipped*\n"
            "`DHAN_ACCESS_TOKEN` is missing.\n"
            "Check that the token refresh ran today."
        )
        return None
    try:
        ctx = DhanContext(client_id, access_token)
        client = dhanhq(ctx)
        logger.info("buynhold: Dhan client initialised")
        return client
    except Exception as exc:
        logger.error("buynhold: failed to init Dhan client: %s", exc)
        return None


def place_order(symbol: str, qty: int, client: dhanhq) -> dict:
    """Thin wrapper — buy'n'hold only ever BUYs, never SELLs."""
    return trade_dhan.place_order(symbol, qty, "BUY", client)


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
        logger.warning("buynhold: Telegram failed: %s", exc)


def _entry_alert(symbol: str, qty: int, price: float, stage: int) -> None:
    _send_alert(
        f"🟢 *ENTRY — {symbol}*\n"
        f"Strategy : buy'n'hold\n"
        f"Signal   : Daily ST(10,3) bullish cross (weekly+daily ST(2,1) already bullish)\n"
        f"Stage    : {stage}\n"
        f"Price    : ₹{price:,.2f}\n"
        f"Qty      : {qty} shares (~₹{qty*price:,.0f})\n"
        f"Order    : *BUY {qty} {symbol} CNC MARKET — placed via Dhan*\n"
        f"Exit     : None — buy and hold"
    )


def _order_failed_alert(symbol: str, qty: int, price: float, reason: str) -> None:
    _send_alert(
        f"🔴 *buy'n'hold ORDER FAILED — {symbol}*\n"
        f"Attempted: BUY {qty} shares @ ~₹{price:,.2f}\n"
        f"Reason   : {reason}\n"
        f"Position was NOT recorded — Dhan rejected the order."
    )


def _order_confirmed(resp) -> tuple[bool, str | None, str | None]:
    if not isinstance(resp, dict):
        return False, None, f"non-dict response: {resp!r}"
    order_id = resp.get("data", {}).get("orderId") if isinstance(resp.get("data"), dict) else None
    if resp.get("status") == "success" and order_id:
        return True, order_id, None
    return False, None, str(resp.get("remarks") or resp)
# ---------------------------------------------------------------------------
# Google Sheet read
# ---------------------------------------------------------------------------
def get_broadcast_rows() -> list[dict]:
    scopes = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_PATH, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(BROADCAST_SHEET_ID)
    ws = sheet.get_worksheet(0)
    return ws.get_all_records()


# ---------------------------------------------------------------------------
# Position sizing
# ---------------------------------------------------------------------------
def compute_qty(price: float) -> int:
    return max(1, math.floor(TARGET_POSITION / price))


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------
def run_cycle() -> None:
    logger.info("buynhold: === starting cycle %s UTC ===",
                datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M"))

    if not is_within_window():
        logger.info("buynhold: outside 3:00-3:15 PM IST window — skipping")
        return

    state = _load_state()
    today = datetime.date.today().isoformat()
    bought_today = state["daily_buy_count"].get(today, 0)

    if bought_today >= MAX_NEW_PER_DAY:
        logger.info("buynhold: daily cap (%d) already reached — skipping", MAX_NEW_PER_DAY)
        return

    client = get_dhan_client()
    if client is None:
        return

    try:
        rows = get_broadcast_rows()
    except Exception as exc:
        logger.error("buynhold: failed to read broadcast sheet: %s", exc)
        return

    candidates = []
    for row in rows:
        symbol = row.get("symbol", "").strip().upper()
        if not symbol:
            continue
        if symbol in state["bought"]:
            continue  # never re-buy — permanent exclusion

        weekly_2_1 = row.get("weekly_st_2_1_dir", "")
        daily_2_1  = row.get("daily_st_2_1_dir", "")
        daily_10_3 = row.get("daily_st_10_3_dir", "")
        stage      = row.get("stage", 0)

        # Filter: both fast Supertrends bullish
        if weekly_2_1 != "bullish" or daily_2_1 != "bullish":
            continue

        # Trigger: daily ST(10,3) crossed bearish -> bullish since last check
        prev_dir = state["last_daily_10_3_dir"].get(symbol)
        state["last_daily_10_3_dir"][symbol] = daily_10_3  # update regardless of outcome
        if daily_10_3 != "bullish":
            continue
        if prev_dir != "bearish":
            # either first time seeing this symbol (prev_dir is None) or it
            # was already bullish last check — not a fresh cross, skip
            continue

        try:
            price = float(row.get("cmp", 0))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue

        candidates.append({"symbol": symbol, "price": price, "stage": stage})

    # Tie-break: Stage 2 > Stage 1 (higher stage first)
    candidates.sort(key=lambda c: c["stage"], reverse=True)

    slots_left = MAX_NEW_PER_DAY - bought_today
    to_buy = candidates[:slots_left]

    if not to_buy:
        logger.info("buynhold: no fresh triggers this cycle")
        _save_state(state)
        return

    for cand in to_buy:
        symbol, price, stage = cand["symbol"], cand["price"], cand["stage"]
        qty = compute_qty(price)

        logger.info("buynhold: ENTRY %s qty=%d @ Rs %.2f (stage %s)", symbol, qty, price, stage)
        try:
            resp = place_order(symbol, qty, client)
            confirmed, order_id, reason = _order_confirmed(resp)
        except Exception as exc:
            logger.error("buynhold: BUY failed %s: %s", symbol, exc)
            _order_failed_alert(symbol, qty, price, str(exc))
            continue

        if confirmed:
            state["bought"][symbol] = {
                "qty": qty, "entry_price": price,
                "entry_date": today, "stage": stage,
                "order_id": order_id,
            }
            state["daily_buy_count"][today] = state["daily_buy_count"].get(today, 0) + 1
            _entry_alert(symbol, qty, price, stage)
            logger.info("buynhold: order CONFIRMED %s orderId=%s", symbol, order_id)
        else:
            logger.error("buynhold: order REJECTED %s: %s", symbol, reason)
            _order_failed_alert(symbol, qty, price, reason)

        time.sleep(0.5)

    _save_state(state)
    logger.info("buynhold: cycle done — %d/%d positions bought today",
                state["daily_buy_count"].get(today, 0), MAX_NEW_PER_DAY)


if __name__ == "__main__":
    run_cycle()
