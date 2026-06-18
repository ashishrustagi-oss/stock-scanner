# Automated Stock Compounder Scanner

Daily scan of NSE500 + S&P500 for technical strength + fundamental quality,
scored and pushed to Google Sheets, fully automated via GitHub Actions.

**This tool surfaces data and a heuristic ranking — it is not financial
advice and the composite score is not a guarantee of future performance.**
Verify anything material before acting on it.

---
## What it computes, per stock, every trading day

| # | Metric |
|---|---|
| 1 | OBV (On-Balance Volume) |
| 2 | OBV 20-day slope (normalized) |
| 3 | OBV 50-day slope (normalized) |
| 4 | Weekly MACD (12,26,9 on weekly closes) |
| 5 | Daily MACD (12,26,9) |
| 6 | Supertrend (10, 3) |
| 7 | Supertrend (2, 1) |
| 8 | EMA20 |
| 9 | Relative Strength vs Nifty 50 (NSE names) / S&P 500 index (US names) |
| 10 | % distance from 52-week high |

**Fundamental qualifying filters** (from Yahoo Finance financial statements):
Sales CAGR > 15%, Profit CAGR > 15%, ROCE > 18%, Debt/Equity < 0.5.

**Composite score** (0-100, ranked within each universe):
OBV 30% · Weekly MACD 20% · Daily MACD 15% · Trend 20% · Relative Strength 15%

**Output tabs** in your Google Sheet: full NSE500 scan, full S&P500 scan,
Top 20 NSE, Top 20 US, Elite Compounders (score ≥85 + fundamentally
qualified), Emerging Compounders (75-85 + qualified), Exit Candidates
(score <50, regardless of fundamentals — flags deteriorating technical
setups even in names you already hold), and a Run Log tab for auditability.

---
## One-time setup (about 15 minutes)

### 1. Create the Google Sheet
Create a new, blank Google Sheet. Copy its ID from the URL:
`https://docs.google.com/spreadsheets/d/THIS_PART_IS_THE_ID/edit`

### 2. Set up the Google Cloud service account
You said you already have one — just confirm it has:
- The **Google Sheets API** and **Google Drive API** enabled on its project
  (Cloud Console → APIs & Services → Library → enable both).
- Its JSON key downloaded.
- The target Sheet **shared with the service account's email** (the
  `client_email` field inside the JSON key, looks like
  `xxxx@yyyy.iam.gserviceaccount.com`) with **Editor** access — the script
  authenticates as this account, so without sharing it can't write to your
  sheet.

### 3. Push this code to a GitHub repo
Create a new repo (public or private — this job is light enough to comfortably
fit GitHub's free Actions minutes even on a private repo) and push everything
in this folder to it.

### 4. Add two repository secrets
Repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret name | Value |
|---|---|
| `GOOGLE_SHEET_ID` | The Sheet ID from step 1 |
| `GOOGLE_SERVICE_ACCOUNT_JSON_B64` | Base64 of your full service-account JSON key file |

To get the base64 value, run locally (do **not** paste the raw JSON into the
secret — base64 avoids newline/quoting issues GitHub secrets sometimes choke
on):
```bash
base64 -i service_account.json | tr -d '\n'   # macOS/Linux
# Windows (PowerShell): [Convert]::ToBase64String([IO.File]::ReadAllBytes("service_account.json"))
```
Paste the output as the secret value.

### 5. Done
The workflow (`.github/workflows/daily_scan.yml`) runs automatically on
weekdays. To run it immediately without waiting: GitHub repo → **Actions**
tab → **Daily Stock Scan** → **Run workflow**.

---
## Customizing

Everything tunable lives in **`config.py`**:
- Composite score weights (`WEIGHT_OBV`, etc. — must sum to 100)
- Fundamental thresholds (`MIN_SALES_CAGR`, `MIN_ROCE`, etc.)
- Category cutoffs (`ELITE_THRESHOLD`, `EXIT_THRESHOLD`, ...)
- Indicator parameters (Supertrend periods/multipliers, MACD periods, EMA period)
- Fundamentals refresh day (default Monday — fundamentals don't move daily,
  so they're cached and only refetched weekly to keep runs fast and avoid
  hammering Yahoo Finance)
- The daily run time: edit the `cron` line in `.github/workflows/daily_scan.yml`
  (times are UTC)

---
## Known limitations — read before relying on this

- **NSE fundamental coverage via Yahoo Finance is patchy.** Many Indian
  mid/small-caps have incomplete income statement / balance sheet data on
  Yahoo. Every row carries a `data_quality` flag (`ok` / `partial` /
  `missing`) in the full-scan tabs — a stock failing the fundamental filter
  because of *missing data* is different from failing it because it
  genuinely doesn't meet the threshold. Check this column before drawing
  conclusions, especially for smaller NSE names. If this matters a lot to
  you, a paid data provider (e.g., a Screener.in API plan, Tijori, or a
  Refinitiv/Capital IQ feed) would meaningfully improve this — happy to wire
  one in if you get access.
- **The live NSE500 list fetch** depends on NSE's archive endpoint, which
  occasionally changes format or blocks automated requests. If it fails
  twice, the script silently falls back to a 20-stock seed list so the
  pipeline doesn't crash — check the Actions log / Run Log tab for a warning
  if your NSE tab looks suspiciously short.
- **ROCE is derived, not reported.** Calculated as EBIT ÷ (Total Assets −
  Current Liabilities) from Yahoo's statements, which can differ slightly
  from what a company reports directly (e.g., due to how "Current
  Liabilities" is bucketed).
- **GitHub Actions' free scheduler can run a few minutes late** during
  platform load — fine for an end-of-day scan, not suitable if you need
  exact-time execution.
- The composite score is a relative, cross-sectional ranking within each
  day's universe — a score of "80" on a strong market day and a score of
  "80" on a weak one are *not* measuring the same absolute strength.

---
## Repo structure
```
config.py              All tunable parameters
universe.py             NSE500 / S&P500 constituent loading
data_fetch.py            Batched yfinance price history fetch
indicators.py             OBV, MACD, Supertrend, EMA, RS, 52w-high distance
fundamentals.py          CAGR / ROCE / D-E from Yahoo financial statements (cached weekly)
scoring.py               Cross-sectional percentile scoring + composite + categorization
sheets_export.py          Google Sheets writer
main.py                   Orchestrator — run this
test_dry_run.py            Mocked end-to-end test, no network needed (dev use only)
.github/workflows/daily_scan.yml   Automation
```
