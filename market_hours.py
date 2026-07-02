"""
Shared trading-window guard — Ashish Capital Scanner.

GitHub Actions `schedule` (cron) triggers are best-effort: under load,
GitHub can delay a scheduled run by anywhere from a few minutes to over
an hour. It does NOT skip or cancel delayed runs — it just fires them
late. Without a runtime check, a cron meant for 3:30 PM IST can end up
actually executing at 4:50 PM IST, well after market close, and still
place orders / send Telegram alerts as if it were live.

This module gives every trade-cycle / watchlist entry point a single
cheap check to call first: "is it actually still inside the trading
window right now?" If not, log and exit before any API calls or
Telegram sends happen — turning a late-firing cron into a harmless
no-op instead of a spurious cycle running against a closed market.
"""

import datetime
import logging

logger = logging.getLogger(__name__)

MARKET_OPEN  = datetime.time(9, 15)
MARKET_CLOSE = datetime.time(15, 45)


def now_ist() -> datetime.datetime:
    """Current time in IST (UTC+5:30), naive datetime."""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)


def is_within_trading_window(grace_minutes: int = 0) -> bool:
    """
    True if right now (IST) is a weekday between MARKET_OPEN and
    MARKET_CLOSE (+ optional grace_minutes tolerance on the close side,
    for jobs that are expected to run slightly after 3:45 PM).
    """
    ist = now_ist()

    if ist.weekday() >= 5:  # Saturday=5, Sunday=6
        logger.info("market_hours: %s IST is a weekend — skipping", ist.strftime("%Y-%m-%d %H:%M"))
        return False

    close_cutoff = (
        datetime.datetime.combine(ist.date(), MARKET_CLOSE)
        + datetime.timedelta(minutes=grace_minutes)
    ).time()

    if not (MARKET_OPEN <= ist.time() <= close_cutoff):
        logger.info(
            "market_hours: %s IST is outside trading window (%s-%s, grace=%dm) — skipping",
            ist.strftime("%H:%M"), MARKET_OPEN, close_cutoff, grace_minutes
        )
        return False

    return True
