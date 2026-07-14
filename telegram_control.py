"""
telegram_control.py — polls Telegram for pause/resume commands, updates the
shared control state file that all trading strategies check before running.

Runs every 1-2 min via cron, all day, every day (not restricted to market
hours — you may want to pause the night before or first thing in the
morning before market open).

Commands (only accepted from TELEGRAM_CHAT_ID — anyone else is ignored):
  /pause                  — master OFF, stops ALL strategies immediately
  /resume                 — master ON
  /pause <regime>         — pause one regime (bullish, bearish, all_time,
                             sideways, high_volatility)
  /resume <regime>        — resume one regime
  /status                 — replies with current master + regime states

State file: cache/control_state.json
  {
    "master": true,
    "regimes": {
      "bullish": true, "bearish": true, "all_time": true,
      "sideways": true, "high_volatility": true
    },
    "last_update_id": 12345
  }

Default state (if file doesn't exist yet): everything ON.
"""
import json
import logging
import os

import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

STATE_PATH = os.path.expanduser("~/stock-scanner/cache/control_state.json")
VALID_REGIMES = ["bullish", "bearish", "all_time", "sideways", "high_volatility"]

DEFAULT_STATE = {
    "master": True,
    "regimes": {r: True for r in VALID_REGIMES},
    "last_update_id": 0,
}


def _load_state() -> dict:
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH) as f:
                state = json.load(f)
            for r in VALID_REGIMES:
                state.setdefault("regimes", {}).setdefault(r, True)
            state.setdefault("master", True)
            state.setdefault("last_update_id", 0)
            return state
        except Exception as exc:
            logger.error("control: failed to load state, using default: %s", exc)
    return json.loads(json.dumps(DEFAULT_STATE))


def _save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _send_reply(token: str, chat_id: str, text: str, parse_mode: str = "Markdown") -> None:
    try:
        payload = {"chat_id": chat_id, "text": text}
        if parse_mode:
            payload["parse_mode"] = parse_mode
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=10,
        )
        if resp.status_code != 200:
            logger.error("control: Telegram send failed (%s): %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.warning("control: failed to send reply: %s", exc)

def _format_status(state: dict) -> str:
    lines = [f"*System status*", f"Master: {'🟢 ON' if state['master'] else '🔴 PAUSED'}", ""]
    for r in VALID_REGIMES:
        status = "🟢 ON" if state["regimes"][r] else "🔴 PAUSED"
        lines.append(f"{r}: {status}")
    lines.append("")
    lines.append("Strategy mapping:")
    lines.append("Strategy 1 (Elite/Combo) → bullish")
    lines.append("Strategy 2 (MTF) → bullish")
    lines.append("Strategy 3 (buy'n'hold) → all_time")
    return "\n".join(lines)

def process_commands() -> None:
    token   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        logger.error("control: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing")
        return

    state = _load_state()

    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": state["last_update_id"] + 1, "timeout": 5},
            timeout=15,
        )
        data = resp.json()
    except Exception as exc:
        logger.error("control: getUpdates failed: %s", exc)
        return

    if not data.get("ok"):
        logger.error("control: Telegram API error: %s", data)
        return

    updates = data.get("result", [])
    if not updates:
        return

    state_changed = False

    for update in updates:
        state["last_update_id"] = update["update_id"]

        msg = update.get("message", {})
        text = (msg.get("text") or "").strip()
        from_chat_id = str(msg.get("chat", {}).get("id", ""))

        if not text:
            continue

        if from_chat_id != str(chat_id):
            logger.warning("control: ignored command from unauthorized chat_id=%s", from_chat_id)
            continue

        parts = text.split()
        cmd = parts[0].lower()
        arg = parts[1].lower() if len(parts) > 1 else None

        if cmd == "/pause" and not arg:
            state["master"] = False
            state_changed = True
            _send_reply(token, chat_id, "🔴 *Master switch OFF* — all strategies paused.")
            logger.info("control: master PAUSED via Telegram")

        elif cmd == "/resume" and not arg:
            state["master"] = True
            state_changed = True
            _send_reply(token, chat_id, "🟢 *Master switch ON* — strategies resumed (subject to regime states).")
            logger.info("control: master RESUMED via Telegram")

        elif cmd == "/pause" and arg in VALID_REGIMES:
            state["regimes"][arg] = False
            state_changed = True
            _send_reply(token, chat_id, f"🔴 *{arg}* regime paused.")
            logger.info("control: regime %s PAUSED via Telegram", arg)

        elif cmd == "/resume" and arg in VALID_REGIMES:
            state["regimes"][arg] = True
            state_changed = True
            _send_reply(token, chat_id, f"🟢 *{arg}* regime resumed.")
            logger.info("control: regime %s RESUMED via Telegram", arg)

        elif cmd == "/pause" or cmd == "/resume":
            _send_reply(token, chat_id,
        f"Unknown regime `{arg}`. Valid: {', '.join(VALID_REGIMES)}")
        elif cmd == "/status":
            _send_reply(token, chat_id, _format_status(state), parse_mode=None)

    _save_state(state)
    if state_changed:
        logger.info("control: state updated and saved")


if __name__ == "__main__":
    process_commands()
