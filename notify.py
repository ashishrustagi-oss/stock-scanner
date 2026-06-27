"""
Daily notification — sends a 3-section digest of "buy" candidates after the
scan completes, via Telegram (instant push) AND Gmail SMTP (backup/log).
Both channels send the exact same message text, built once.

This module is PURELY a presentation/delivery layer. It reads already-scored
columns produced by main.py / scoring.py and does not compute, re-rank, or
alter any signal. See config.py "DAILY NOTIFICATION" section for the
threshold constants and the rationale for each section's scope.

Setup (one-time, see README "Daily Notification" section):

  Telegram:
    1. Message @BotFather on Telegram -> /newbot -> follow prompts -> you get
       a bot token immediately (no waiting, unlike CallMeBot's queue).
    2. Send any message to your new bot from your own Telegram account.
    3. Call https://api.telegram.org/bot<TOKEN>/getUpdates in a browser to
       find your numeric chat id in the response.
    4. Set GitHub Actions secrets: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

  Email (Gmail SMTP):
    1. Enable 2-Step Verification on the sending Gmail account.
    2. Generate an App Password (Google Account -> Security -> App
       Passwords) — a 16-character code, NOT your real Gmail password.
    3. Set GitHub Actions secrets: GMAIL_ADDRESS, GMAIL_APP_PASSWORD
       (GMAIL_ADDRESS is used as both sender and recipient unless
       NOTIFY_EMAIL_TO is also set, e.g. to send to a different inbox).

Each channel fails independently and silently (logged, not raised) — a
missing secret or a delivery error on one channel never blocks the other
channel, and never fails the scan itself.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText

import pandas as pd
import requests

import config

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_MAX_MESSAGE_CHARS = 3800   # Telegram's real limit is 4096; stay under it
GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 465


def _fmt_row(ticker: str, close: float, extra: str = "") -> str:
    close_str = f"{close:.2f}" if pd.notna(close) else "NA"
    line = f"• {ticker} (₹{close_str})" if extra == "" else f"• {ticker} (₹{close_str}) — {extra}"
    return line


def _section_lines(df: pd.DataFrame, extra_col: str | None = None, extra_label: str = "") -> list[str]:
    """
    Builds bullet lines for a section. NOTE: per explicit instruction, every
    qualifying stock is included in the full section list — nothing is
    dropped to make room for the Top 5 leaderboard, which is purely an
    additional summary built from this same data, not a replacement for it.
    The NOTIFY_MAX_TICKERS_PER_SECTION cap below is the same display cap
    that existed before this redesign (unrelated to the leaderboard).
    """
    if df.empty:
        return ["  (none today)"]
    capped = df.head(config.NOTIFY_MAX_TICKERS_PER_SECTION)
    lines = []
    for _, row in capped.iterrows():
        extra = ""
        if extra_col is not None and extra_col in row and pd.notna(row[extra_col]):
            extra = f"{extra_label}{row[extra_col]:.0f}"
        lines.append(_fmt_row(row.get("ticker", "?"), row.get("close", float("nan")), extra))
    omitted = len(df) - len(capped)
    if omitted > 0:
        lines.append(f"  ...and {omitted} more (see Google Sheet for full list)")
    return lines


def build_elite_section(combined_df: pd.DataFrame) -> list[str]:
    """Section 1 — EliteCompounderScore >= ELITE_NOTIFY_SCORE_THRESHOLD (NSE500+S&P500)."""
    if combined_df.empty or "EliteCompounderScore" not in combined_df.columns:
        return ["  (none today)"]
    elite = (
        combined_df[combined_df["EliteCompounderScore"] >= config.ELITE_NOTIFY_SCORE_THRESHOLD]
        .sort_values("EliteCompounderScore", ascending=False)
    )
    return _section_lines(elite, extra_col="EliteCompounderScore", extra_label="score ")


def build_smallmicro_section(smallmicro_df: pd.DataFrame) -> list[str]:
    """Section 2 — smallmicro_strict_pass == True (NSE Small/Microcap only)."""
    if smallmicro_df.empty or "smallmicro_strict_pass" not in smallmicro_df.columns:
        return ["  (none today)"]
    strict = (
        smallmicro_df[smallmicro_df["smallmicro_strict_pass"] == True]  # noqa: E712
        .sort_values("smallmicro_score", ascending=False)
    )
    return _section_lines(strict, extra_col="smallmicro_score", extra_label="score ")


def build_combo_buckets(combined_df: pd.DataFrame) -> dict[str, list[str]]:
    """
    Section 3 — fresh OBV+RS combo, NSE500+S&P500 ONLY (SmallMicro excluded
    by design — see config.py comment block above ELITE_NOTIFY_SCORE_THRESHOLD).
    Mutually exclusive bands by distance off 52w high.
    """
    required_cols = {"obv_leadership_rank", "rs_rank", "pct_from_52w_high"}
    if combined_df.empty or not required_cols.issubset(combined_df.columns):
        return {"breakout": ["  (none today)"], "confirmed": ["  (none today)"], "early": ["  (none today)"]}

    gated = combined_df[
        (combined_df["obv_leadership_rank"] > config.NOTIFY_COMBO_RANK_THRESHOLD)
        & (combined_df["rs_rank"] > config.NOTIFY_COMBO_RANK_THRESHOLD)
    ].copy()
    gated["dist_off_high"] = gated["pct_from_52w_high"].abs()

    breakout = gated[gated["dist_off_high"] < config.NOTIFY_BREAKOUT_BAND_PCT].sort_values(
        "obv_leadership_rank", ascending=False
    )
    confirmed = gated[
        (gated["dist_off_high"] >= config.NOTIFY_BREAKOUT_BAND_PCT)
        & (gated["dist_off_high"] < config.NOTIFY_CONFIRMED_BAND_PCT)
    ].sort_values("obv_leadership_rank", ascending=False)
    early = gated[gated["dist_off_high"] >= config.NOTIFY_CONFIRMED_BAND_PCT].sort_values(
        "obv_leadership_rank", ascending=False
    )

    return {
        "breakout": _section_lines(breakout, extra_col="dist_off_high", extra_label="off high "),
        "confirmed": _section_lines(confirmed, extra_col="dist_off_high", extra_label="off high "),
        "early": _section_lines(early, extra_col="dist_off_high", extra_label="off high "),
    }


def build_top5_leaderboard(combined_df: pd.DataFrame, smallmicro_df: pd.DataFrame) -> list[str]:
    """
    Top 5 leaderboard — a summary line, NOT a replacement for the full
    section lists below it. Pulls from the same Elite + SmallMicro
    qualifying rows already computed in build_elite_section /
    build_smallmicro_section, ranked by score across both (both scores are
    0-100 scales, so directly comparable). Combo-bucket entries have no
    single "score" field, so they are not part of this leaderboard — they
    are still fully listed in Section 3 below.
    """
    entries = []

    if not combined_df.empty and "EliteCompounderScore" in combined_df.columns:
        elite = combined_df[combined_df["EliteCompounderScore"] >= config.ELITE_NOTIFY_SCORE_THRESHOLD]
        for _, row in elite.iterrows():
            entries.append((row.get("ticker", "?"), row.get("EliteCompounderScore", 0)))

    if not smallmicro_df.empty and "smallmicro_strict_pass" in smallmicro_df.columns:
        strict = smallmicro_df[smallmicro_df["smallmicro_strict_pass"] == True]  # noqa: E712
        for _, row in strict.iterrows():
            entries.append((row.get("ticker", "?"), row.get("smallmicro_score", 0)))

    if not entries:
        return ["  (no qualifying stocks today)"]

    entries.sort(key=lambda x: x[1], reverse=True)
    top5 = entries[:5]
    medals = ["🥇", "🥈", "🥉"]
    lines = []
    for i, (ticker, score) in enumerate(top5):
        prefix = medals[i] if i < 3 else f"{i + 1}."
        lines.append(f"{prefix} {ticker} — {score:.0f}")
    return lines


def _count_qualifying(combined_df: pd.DataFrame, smallmicro_df: pd.DataFrame) -> dict[str, int]:
    """Real counts for the summary line — same gating logic as each section, just counted."""
    elite_n = 0
    if not combined_df.empty and "EliteCompounderScore" in combined_df.columns:
        elite_n = int((combined_df["EliteCompounderScore"] >= config.ELITE_NOTIFY_SCORE_THRESHOLD).sum())

    smallmicro_n = 0
    if not smallmicro_df.empty and "smallmicro_strict_pass" in smallmicro_df.columns:
        smallmicro_n = int((smallmicro_df["smallmicro_strict_pass"] == True).sum())  # noqa: E712

    combo_n = 0
    required_cols = {"obv_leadership_rank", "rs_rank"}
    if not combined_df.empty and required_cols.issubset(combined_df.columns):
        combo_n = int(
            (
                (combined_df["obv_leadership_rank"] > config.NOTIFY_COMBO_RANK_THRESHOLD)
                & (combined_df["rs_rank"] > config.NOTIFY_COMBO_RANK_THRESHOLD)
            ).sum()
        )

    return {"elite": elite_n, "smallmicro": smallmicro_n, "combo": combo_n}


def build_message_parts(combined_df: pd.DataFrame, smallmicro_df: pd.DataFrame, run_date: str) -> list[tuple[str, bool]]:
    """
    Assembles the message as a list of (line, is_heading) tuples — a single
    source of truth rendered two ways below: Markdown bold for Telegram,
    HTML <b> for email. is_heading=True marks title/section/sub-headers that
    should render bold; data rows (ticker lines, the counts line) are False.
    """
    elite_lines = build_elite_section(combined_df)
    smallmicro_lines = build_smallmicro_section(smallmicro_df)
    combo = build_combo_buckets(combined_df)
    top5_lines = build_top5_leaderboard(combined_df, smallmicro_df)
    counts = _count_qualifying(combined_df, smallmicro_df)

    parts: list[tuple[str, bool]] = [
        (f"🚨 ASHISH CAPITAL SCANNER — {run_date}", True),
        ("━━━━━━━━━━━━━━━━━━━━", False),
        (f"📊 Elite: {counts['elite']} | SmallMicro: {counts['smallmicro']} | Fresh Combo: {counts['combo']}", False),
        ("", False),
        ("🏆 TOP 5 TODAY", True),
        *((line, False) for line in top5_lines),
        ("━━━━━━━━━━━━━━━━━━━━", False),
        (f"🔥 ELITE (score >= {config.ELITE_NOTIFY_SCORE_THRESHOLD})", True),
        *((line, False) for line in elite_lines),
        ("━━━━━━━━━━━━━━━━━━━━", False),
        ("🧬 SMALLMICRO — STRICT PASS", True),
        *((line, False) for line in smallmicro_lines),
        ("━━━━━━━━━━━━━━━━━━━━", False),
        ("🚀 FRESH OBV+RS COMBO (NSE500/S&P500)", True),
        ("🟢 Breakout (0-15% off high)", True),
        *((line, False) for line in combo["breakout"]),
        ("🟡 Confirmed (15-25% off high)", True),
        *((line, False) for line in combo["confirmed"]),
        ("🔵 Early (>25% off high)", True),
        *((line, False) for line in combo["early"]),
    ]
    return parts


def render_telegram_markdown(parts: list[tuple[str, bool]]) -> str:
    """Renders parts as Telegram Markdown — headings wrapped in *bold*."""
    lines = []
    for text, is_heading in parts:
        if is_heading and text:
            # Escape literal underscores/asterisks in headings so Telegram's
            # Markdown parser doesn't choke on them (none currently appear,
            # but this keeps it safe if a future heading includes one).
            safe_text = text.replace("*", "").replace("_", "")
            lines.append(f"*{safe_text}*")
        else:
            lines.append(text)
    return "\n".join(lines)


def render_html_email(parts: list[tuple[str, bool]], run_date: str) -> str:
    """Renders parts as a simple HTML email — headings wrapped in <b>."""
    import html as _html

    body_lines = []
    for text, is_heading in parts:
        escaped = _html.escape(text)
        if is_heading and text:
            body_lines.append(f"<b>{escaped}</b>")
        elif text == "":
            body_lines.append("")
        else:
            body_lines.append(escaped)

    body_html = "<br>\n".join(body_lines)
    return (
        "<html><body style=\"font-family: monospace, sans-serif; white-space: pre-wrap;\">"
        f"{body_html}"
        "</body></html>"
    )


def build_message(combined_df: pd.DataFrame, smallmicro_df: pd.DataFrame, run_date: str) -> str:
    """
    Plain-text fallback (no bold) — kept for any caller that still wants a
    single plain string, e.g. logging or future channels without rich text.
    """
    parts = build_message_parts(combined_df, smallmicro_df, run_date)
    return "\n".join(text for text, _ in parts)


# ── Telegram ──────────────────────────────────────────────────────────────

def _chunk_message(message: str, max_chars: int) -> list[str]:
    """
    Splits on line boundaries only (never mid-line), so a chunk boundary
    can never land inside a *bold* marker and break Markdown parsing for
    that chunk.
    """
    lines = message.split("\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        # +1 for the newline that will join this line to the chunk
        added_len = len(line) + 1
        if current and current_len + added_len > max_chars:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += added_len
    if current:
        chunks.append("\n".join(current))
    return chunks or [message]


def _send_telegram(message: str, bot_token: str, chat_id: str) -> bool:
    """Sends via Telegram Bot API with Markdown parse mode (for *bold* headings). Splits into multiple messages if too long."""
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    chunks = _chunk_message(message, TELEGRAM_MAX_MESSAGE_CHARS)

    all_ok = True
    for chunk in chunks:
        try:
            resp = requests.post(
                url,
                data={"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"},
                timeout=30,
            )
            resp.raise_for_status()
            body = resp.json()
            if not body.get("ok", False):
                logger.warning("Telegram API returned ok=False: %s", body)
                all_ok = False
        except requests.RequestException as exc:
            logger.warning("Telegram send failed: %s", exc)
            all_ok = False
    return all_ok


def send_telegram_notification(message: str) -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        logger.warning("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set — skipping Telegram notification.")
        return
    try:
        ok = _send_telegram(message, bot_token, chat_id)
        logger.info("Telegram notification sent." if ok else "Telegram notification not confirmed as sent.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Telegram notification failed (non-fatal): %s", exc)


# ── Email (Gmail SMTP) ───────────────────────────────────────────────────

def _send_email(html_body: str, subject: str, sender: str, app_password: str, recipient: str) -> bool:
    """Sends an HTML email via Gmail SMTP over SSL (so <b> headings render as real bold)."""
    msg = MIMEText(html_body, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    try:
        with smtplib.SMTP_SSL(GMAIL_SMTP_HOST, GMAIL_SMTP_PORT, timeout=30) as server:
            server.login(sender, app_password)
            server.sendmail(sender, [recipient], msg.as_string())
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Email send failed: %s", exc)
        return False


def send_email_notification(html_body: str, run_date: str) -> None:
    sender = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    recipient = os.environ.get("NOTIFY_EMAIL_TO") or sender  # defaults to sending to self
    if not sender or not app_password:
        logger.warning("GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set — skipping email notification.")
        return
    try:
        ok = _send_email(html_body, f"Stock Scanner — {run_date}", sender, app_password, recipient)
        logger.info("Email notification sent." if ok else "Email notification not confirmed as sent.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Email notification failed (non-fatal): %s", exc)


# ── Entry point ───────────────────────────────────────────────────────────

def send_daily_notification(nse_df: pd.DataFrame, us_df: pd.DataFrame, smallmicro_df: pd.DataFrame, run_date: str) -> None:
    """
    Entry point called from main.py after the scan completes. Sends via both
    Telegram and email; each channel fails independently and silently if its
    secrets are missing or the send errors out. Never raises — a notification
    failure must not fail the scan or block the Sheets export.

    Builds one shared `parts` structure (line + is_heading), then renders it
    twice: Telegram Markdown (*bold*) and HTML email (<b>bold</b>) — so both
    channels show the same headings in bold without duplicating the
    section-building logic above.
    """
    combined = (
        pd.concat([nse_df, us_df], ignore_index=True)
        if not nse_df.empty and not us_df.empty
        else (nse_df if not nse_df.empty else us_df)
    )

    try:
        parts = build_message_parts(combined, smallmicro_df, run_date)
        telegram_text = render_telegram_markdown(parts)
        html_body = render_html_email(parts, run_date)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to build notification message (non-fatal): %s", exc)
        return

    send_telegram_notification(telegram_text)
    send_email_notification(html_body, run_date)
