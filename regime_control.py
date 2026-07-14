"""
regime_control.py — shared helper imported by trade_dhan.py, trade_dhan_mtf.py,
and trade_buynhold.py to check the master + regime pause state written by
telegram_control.py before running any live trading logic.

Usage in each strategy's run_cycle():
    from regime_control import is_active
    if not is_active("bullish", "trade_dhan"):
        return
"""
import json
import logging
import os

logger = logging.getLogger(__name__)

STATE_PATH = os.path.expanduser("~/stock-scanner/cache/control_state.json")


def is_active(regime: str, caller_name: str = "") -> bool:
    """
    Returns True if this strategy should proceed: master switch is ON AND
    the given regime is ON.

    Failure handling is deliberately split:
      - State file never existed (nothing configured yet) -> FAIL OPEN
        (defaults to active). No config reasonably means "nothing paused."
      - State file exists but is corrupted/unreadable (something that used
        to work broke) -> FAIL CLOSED (defaults to paused). A broken file
        after working normally is a red flag; safer to stop and let the
        person investigate than to silently trade through an unknown state.
    """
    if not os.path.exists(STATE_PATH):
        logger.info("%s: no control state file yet — defaulting to ACTIVE", caller_name)
        return True

    try:
        with open(STATE_PATH) as f:
            state = json.load(f)
    except Exception as exc:
        logger.error("%s: control state file exists but is unreadable/corrupted — "
                     "defaulting to PAUSED (fail-closed) until fixed: %s",
                     caller_name, exc)
        return False

    if not state.get("master", True):
        logger.info("%s: skipped — master switch is PAUSED", caller_name)
        return False

    regime_state = state.get("regimes", {}).get(regime, True)
    if not regime_state:
        logger.info("%s: skipped — '%s' regime is PAUSED", caller_name, regime)
        return False

    return True
