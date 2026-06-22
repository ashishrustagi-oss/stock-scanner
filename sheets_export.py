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
  - Detail/sub-score columns (column R onward — everything past the
    headline ticker/score/flag columns) grouped into a collapsible outline.

IMPORTANT — learned from a real run: doing each of the above as a separate
gspread convenience call (freeze(), format(), set_basic_filter(), etc.) adds
up to 5-6 API calls per tab. With 13 tabs that's 70-90 calls fired in rapid
succession, which blows past Google Sheets API's default "60 write requests
per minute per user" quota — the actual data still exports fine, but the
formatting calls start failing with HTTP 429 partway through. Fixed by
combining all of one tab's formatting requests into a SINGLE raw
batch_update() call instead of several separate ones — cuts the call count
roughly in half and keeps related operations atomic per tab.

Requires:
  - A Google Cloud service account with the Sheets API (and Drive API, for
    metadata) enabled.
  - The target Google Sheet shared with the service account's email
    (found in the JSON key as "client_email") with Editor access.
  - GOOGLE_SHEET_ID and a path to the service account JSON, both supplied via
    config.py / environment variables.
"""

import logging
import time

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

# Columns A through Y (1-25) are the headline view, including all visual
# flags through the chart-study additions (Trend Death, OBV divergence).
# Column Z onward is detail — grouped into a collapsible outline.
HEADLINE_COLUMN_COUNT = 25

# Small pause between tabs' formatting batch calls — with the batching fix
# this isn't strictly required to stay under quota, but it's cheap insurance
# against bursts, especially on accounts sharing the quota with other apps.
INTER_TAB_PAUSE_SECONDS = 0.5


def _get_client() -> gspread.Client:
    creds = Credentials.from_service_account_file(
        config.GOOGLE_SERVICE_ACCOUNT_JSON_PATH, scopes=SCOPES
    )
    return gspread.authorize(creds)


def _build_formatting_requests(sheet_id: int, n_rows: int, n_cols: int, include_grouping: bool = True) -> list[dict]:
    """
    Builds the full list of raw Sheets API request objects for one tab:
    freeze header+ticker column, bold/colored header row, basic filter
    (sort/filter dropdowns), and collapsible grouping of detail columns.
    Combined into one list so the caller can send them as a single
    batch_update() instead of 4-5 separate API calls.

    include_grouping=False skips the column-R-onward grouping — used for
    the portfolio tab, whose narrower layout has live-price columns near
    the end that should stay visible rather than tucked into a group.
    """
    requests = [
        {
            "updateSheetProperties": {
                "properties": {
                    "sheetId": sheet_id,
                    "gridProperties": {"frozenRowCount": 1, "frozenColumnCount": 1},
                },
                "fields": "gridProperties.frozenRowCount,gridProperties.frozenColumnCount",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 0, "endRowIndex": 1,
                    "startColumnIndex": 0, "endColumnIndex": n_cols,
                },
                "cell": {"userEnteredFormat": HEADER_FORMAT},
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment)",
            }
        },
        {
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0, "endRowIndex": n_rows + 1,
                        "startColumnIndex": 0, "endColumnIndex": n_cols,
                    }
                }
            }
        },
    ]

    if include_grouping and n_cols > HEADLINE_COLUMN_COUNT:
        col_range = {
            "sheetId": sheet_id, "dimension": "COLUMNS",
            "startIndex": HEADLINE_COLUMN_COUNT, "endIndex": n_cols,
        }
        # Delete any existing group first (no-op if none exists — caller
        # sends this in the same batch as a best-effort, errors on this
        # specific sub-request don't get individually caught since it's
        # bundled, but an absent group simply has nothing to delete and
        # Sheets tolerates that without failing the whole batch).
        requests.append({"deleteDimensionGroup": {"range": col_range}})
        requests.append({"addDimensionGroup": {"range": col_range}})

    return requests


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
        n_rows = len(df_out)

        # All formatting (freeze, header style, sort filter, column grouping)
        # combined into ONE batch_update call per tab — see module docstring
        # for why this matters (Sheets API write-quota).
        try:
            include_grouping = tab_name != config.SHEET_TABS.get("my_portfolio")
            requests = _build_formatting_requests(worksheet.id, n_rows, n_cols, include_grouping)
            spreadsheet.batch_update({"requests": requests})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not apply formatting on '%s': %s", tab_name, exc)

        logger.info("Exported %d rows to tab '%s'", len(df_out), tab_name)
        time.sleep(INTER_TAB_PAUSE_SECONDS)
