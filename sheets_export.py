"""
Exports a dict of {tab_name: DataFrame} to a single Google Sheet, one tab per
DataFrame. Creates tabs that don't exist yet; clears and rewrites tabs that do.

Every tab also gets, automatically and on every run:
  - Header row + first column (ticker) frozen, so they stay visible while
    scrolling through the many indicator columns.
  - Header row formatted bold with a colored background, for quick visual
    distinction from the data rows.
  - A basic filter applied across the full data range, which gives every
    column a dropdown arrow for sorting ascending/descending or filtering
    by value — built into Sheets, no formulas needed.

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
import gspread.utils as gs_utils
import pandas as pd
from google.oauth2.service_account import Credentials

import config

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

# Header row styling — dark blue background, bold white text
HEADER_FORMAT = {
    "backgroundColor": {"red": 0.18, "green": 0.30, "blue": 0.55},
    "textFormat": {
        "bold": True,
        "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
    },
    "horizontalAlignment": "CENTER",
}


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

        n_cols = max(len(df_out.columns), 1)
        last_col_a1 = gs_utils.rowcol_to_a1(1, n_cols)  # e.g. "BT1" for 72 columns

        # Freeze the header row + first column (ticker) so they stay visible
        # while scrolling through the many indicator columns to the right.
        try:
            worksheet.freeze(rows=1, cols=1)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not freeze header/ticker column on '%s': %s", tab_name, exc)

        # Bold, colored header row for quick visual distinction from data.
        try:
            worksheet.format(f"A1:{last_col_a1}", HEADER_FORMAT)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not format header row on '%s': %s", tab_name, exc)

        # Basic filter across the full data range — gives every column a
        # dropdown for sorting ascending/descending or filtering by value.
        # Re-applying each run is harmless and keeps it correct even if the
        # row/column count changed since the last run.
        try:
            worksheet.clear_basic_filter()
        except Exception:  # noqa: BLE001 - fine if there wasn't one yet
            pass
        try:
            worksheet.set_basic_filter()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not apply sort/filter dropdowns on '%s': %s", tab_name, exc)

        logger.info("Exported %d rows to tab '%s'", len(df_out), tab_name)
