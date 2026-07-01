"""
Dhan daily token refresh — runs at 8:45 AM IST via GitHub Actions.

Flow:
  1. Generate fresh 24-hour access token using DhanLogin.generate_token(pin, totp)
  2. Update the runner's IP as the whitelisted static IP for order placement
  3. Store the new access token as GitHub secret DHAN_ACCESS_TOKEN
     (so the trade_scan workflow picks it up automatically)

Credentials needed (GitHub secrets):
  DHAN_CLIENT_ID   — Dhan client ID
  DHAN_API_KEY     — API key from DhanHQ portal
  DHAN_API_SECRET  — API secret from DhanHQ portal
  DHAN_PIN         — Dhan login PIN (4-6 digits)
  DHAN_TOTP_SECRET — Raw TOTP secret (base32 string)
  GH_TOKEN_PAT     — GitHub Personal Access Token with repo scope
  GITHUB_REPOSITORY— Set automatically by GitHub Actions (owner/repo)
"""

import logging
import os
import urllib.request

import pyotp
from dhanhq import DhanLogin
from github import Github

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def get_runner_ip() -> str:
    """Get current GitHub Actions runner's public IP."""
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=10) as resp:
            return resp.read().decode().strip()
    except Exception as exc:
        logger.error("Failed to get runner IP: %s", exc)
        return ""


def update_github_secret(token: str, secret_name: str, secret_value: str) -> None:
    """Update a GitHub Actions secret using PyGithub."""
    try:
        repo_name = os.environ.get("GITHUB_REPOSITORY", "")
        g = Github(token)
        repo = g.get_repo(repo_name)
        repo.create_secret(secret_name, secret_value)
        logger.info("Updated GitHub secret: %s", secret_name)
    except Exception as exc:
        logger.error("Failed to update GitHub secret %s: %s", secret_name, exc)
        raise


def main():
    logger.info("=== Dhan token refresh starting ===")

    # Load credentials
    client_id   = os.environ.get("DHAN_CLIENT_ID", "")
    api_key     = os.environ.get("DHAN_API_KEY", "")
    api_secret  = os.environ.get("DHAN_API_SECRET", "")
    pin         = os.environ.get("DHAN_PIN", "")
    totp_secret = os.environ.get("DHAN_TOTP_SECRET", "")
    gh_pat      = os.environ.get("GH_TOKEN_PAT", "")

    if not all([client_id, api_key, api_secret, pin, totp_secret, gh_pat]):
        missing = [k for k, v in {
            "DHAN_CLIENT_ID": client_id, "DHAN_API_KEY": api_key,
            "DHAN_API_SECRET": api_secret, "DHAN_PIN": pin,
            "DHAN_TOTP_SECRET": totp_secret, "GH_TOKEN_PAT": gh_pat,
        }.items() if not v]
        logger.error("Missing secrets: %s", missing)
        raise SystemExit(1)

    # Step 1: Generate fresh TOTP code
    totp_code = pyotp.TOTP(totp_secret).now()
    logger.info("Generated TOTP code via pyotp")

    # Step 2: Generate access token
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

    # Step 3: Get runner IP and whitelist it
    runner_ip = get_runner_ip()
    if runner_ip:
        logger.info("Runner IP: %s — setting as Dhan static IP", runner_ip)
        try:
            dhan_login.set_ip(access_token, runner_ip, "PRIMARY", client_id)
            logger.info("Static IP set successfully")
        except Exception as exc:
            logger.warning("IP setting failed (non-fatal): %s", exc)
    else:
        logger.warning("Could not determine runner IP — IP not updated")

    # Step 4: Store access token as GitHub secret
    logger.info("Updating DHAN_ACCESS_TOKEN GitHub secret...")
    update_github_secret(gh_pat, "DHAN_ACCESS_TOKEN", access_token)

    logger.info("=== Dhan token refresh complete ===")


if __name__ == "__main__":
    main()
