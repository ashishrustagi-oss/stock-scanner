"""
Exports a dict of {tab_name: DataFrame} to a single Google Sheet, one tab per
DataFrame. Creates tabs that don't exist yet; clears and rewrites tabs that do.

Requires:
  - A Google Cloud service account with the Sheets API (and Drive API, for
    metadata) enabled.
  - The target Google Sheet shared with the service account's email
    (found in the JSON key as "client_email") with Editor access.
  - GOOGLE_SHEET_ID and a path to the service account JSON, both supplied via
    config.py / environment variables.
"""

import logging

import gspread
import pandas as pd
from google.oauth2.service_account import Credentials

import config

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]


def _get_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(
        config.GOOGLE_SERVICE_ACCOUNT_JSON_PATH, scopes=SCOPES
    )
    return gspread.authorize(creds)


def export_to_sheets(tabs: dict[str, pd.DataFrame]):
    if not config.GOOGLE_SHEET_ID:
        raise RuntimeError("GOOGLE_SHEET_ID is not set — see README for setup.")

    client = _get_client()
    spreadsheet = client.open_by_key(config.GOOGLE_SHEET_ID)

    for tab_name, df in tabs.items():
        df_out = df.copy()
        # gspread can't serialize NaN/NaT — convert to empty strings for clean display
        df_out = df_out.fillna("")

        try:
            worksheet = spreadsheet.worksheet(tab_name)
            worksheet.clear()
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(
                title=tab_name, rows=max(len(df_out) + 10, 100), cols=max(len(df_out.columns) + 2, 20)
            )

        values = [df_out.columns.tolist()] + df_out.astype(str).values.tolist()
        worksheet.update(values, value_input_option="USER_ENTERED")

        # Freeze the header row + first column (ticker) so they stay visible
        # while scrolling through the many indicator columns to the right.
        # Re-applied every run since clear()/update() don't touch this
        # setting, but it's cheap and guarantees it's always correct even
        # on brand-new tabs.
        try:
            worksheet.freeze(rows=1, cols=1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not freeze header/ticker column on '%s': %s", tab_name, exc)

        logger.info("Exported %d rows to tab '%s'", len(df_out), tab_name)
