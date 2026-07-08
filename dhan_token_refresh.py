"""
Dhan daily token refresh — runs at ~8:45 AM IST via VPS cron.
Flow:
  1. Generate fresh 24-hour access token using DhanLogin.generate_token(pin, totp)
  2. Check current public IP against the expected Dhan static IP (warn only, no auto-change)
  3. Write the new access token to a local env file for trade_dhan.py / trade_dhan_mtf.py
     cron jobs to source before running.
Credentials needed (~/.env on VPS):
  DHAN_CLIENT_ID   — Dhan client ID
  DHAN_API_KEY     — API key from DhanHQ portal
  DHAN_API_SECRET  — API secret from DhanHQ portal
  DHAN_PIN         — Dhan login PIN (4-6 digits)
  DHAN_TOTP_SECRET — Raw TOTP secret (base32 string)
  DHAN_STATIC_IP   — The IP registered with Dhan (for mismatch warning only)
"""
import logging
import os
import urllib.request
import pyotp
from dhanhq import DhanLogin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

TOKEN_FILE = os.path.expanduser("~/.dhan_token.env")


def get_current_ip() -> str:
    """Get this VPS's current public IP."""
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=10) as resp:
            return resp.read().decode().strip()
    except Exception as exc:
        logger.error("Failed to get current IP: %s", exc)
        return ""


def write_local_token(access_token: str) -> None:
    """Write the access token to a local file for cron jobs to source."""
    try:
        with open(TOKEN_FILE, "w") as f:
            f.write(f'export DHAN_ACCESS_TOKEN="{access_token}"\n')
        os.chmod(TOKEN_FILE, 0o600)
        logger.info("Access token written to %s", TOKEN_FILE)
    except Exception as exc:
        logger.error("Failed to write local token file: %s", exc)
        raise


def main():
    logger.info("=== Dhan token refresh starting ===")

    client_id   = os.environ.get("DHAN_CLIENT_ID", "")
    api_key     = os.environ.get("DHAN_API_KEY", "")
    api_secret  = os.environ.get("DHAN_API_SECRET", "")
    pin         = os.environ.get("DHAN_PIN", "")
    totp_secret = os.environ.get("DHAN_TOTP_SECRET", "")
    expected_ip = os.environ.get("DHAN_STATIC_IP", "")

    if not all([client_id, api_key, api_secret, pin, totp_secret]):
        missing = [k for k, v in {
            "DHAN_CLIENT_ID": client_id, "DHAN_API_KEY": api_key,
            "DHAN_API_SECRET": api_secret, "DHAN_PIN": pin,
            "DHAN_TOTP_SECRET": totp_secret,
        }.items() if not v]
        logger.error("Missing secrets: %s", missing)
        raise SystemExit(1)

    totp_code = pyotp.TOTP(totp_secret).now()
    logger.info("Generated TOTP code via pyotp")

    logger.info("Generating Dhan access token...")
    dhan_login = DhanLogin(client_id)
    try:
        result = dhan_login.generate_token(pin, totp_code)
        access_token = (
            result.get("accessToken") or
            result.get("access_token") or
            result.get("data", {}).get("accessToken") or ""
        )
        if not access_token:
            logger.error("No access token in response: %s", result)
            raise SystemExit(1)
        logger.info("Access token generated successfully")
    except Exception as exc:
        logger.error("Token generation failed: %s", exc)
        raise SystemExit(1)

    current_ip = get_current_ip()
    if current_ip:
        if expected_ip and current_ip != expected_ip:
            logger.warning(
                "IP MISMATCH: current VPS IP is %s but expected static IP is %s. "
                "Dhan order placement may fail if this isn't whitelisted.",
                current_ip, expected_ip,
            )
        else:
            logger.info("Current IP %s matches expected static IP", current_ip)
    else:
        logger.warning("Could not determine current IP for comparison")

    write_local_token(access_token)

    logger.info("=== Dhan token refresh complete ===")


if __name__ == "__main__":
    main()
