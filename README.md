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
## Elite Compounder Early Detection System

An additional, fully backward-compatible layer designed to flag institutional
accumulation and emerging leadership **before** standard trend-confirmation
tools (Supertrend, weekly MACD positive) catch up — the goal being stocks
that look like Bharat Forge (2020), BEL (2022), Tech Mahindra (2023), CAMS
(2024), MTAR (2025) did in their early accumulation phase, not after the
breakout was already obvious.

**New modules, each producing its own fields + score contribution:**

| Module | Key fields | Points |
|---|---|---|
| OBV Leadership | `obv_52w_high`, `obv_26w_high`, `obv_slope_13w`, `obv_slope_26w` | 20 |
| RS Leadership | `rs_nifty_52w_high`, `rs_sector_52w_high`, `rs_nifty_chg_13w/26w`, `rs_sector_chg_13w/26w` | 20 |
| Early MACD | `macd_early_bullish` (crossed above signal while still below zero) | 10 |
| Early EMA Structure | `early_ema_alignment` (EMA10>EMA20 + EMA20 sloping up) | 5 |
| Volatility Compression | `volatility_compression`, `atr_compression_ratio`, `range_compression_ratio` | 10 |
| (existing) Daily Supertrend | rescaled from the original system | 10 |
| (existing) Weekly MACD | rescaled from the original system | 10 |
| (existing) Price > EMA20 | — | 5 |
| (existing) Fundamentals | pass/fail on the same 4 filters as before | 10 |

These sum to **`EliteCompounderScore`** (0-100), with a parallel category field
(`elite_category`): Category A (>80), Category B (65-80), Category C (50-65).

**New tabs:**
- `Elite_Compounders_EarlyDetect` — the strict filter: only stocks where
  `OBV_52W_HIGH` AND `RS_NIFTY_52W_HIGH` AND `MACD_EARLY_BULLISH` are all
  true, sorted by `EliteCompounderScore` descending. This is the
  highest-conviction "catch it early" list.
- `Category_A_Elite_Compounders`, `Category_B_Emerging_Leaders`,
  `Category_C_Watchlist` — broader score-band views across the whole universe.

**Visual flags** (🟢 = true, blank = false/unknown) appear in every full-scan
and category tab: `flag_obv_leader`, `flag_rs_leader`, `flag_early_macd`,
`flag_compression`, `flag_ema_alignment`, `flag_near_breakout`.

### The RS-vs-Sector caveat

`rs_sector_*` fields compare each stock against its **sector** benchmark
(not just the broad Nifty/S&P 500). For US stocks this is rock-solid — the
11 GICS sectors map 1:1 to the SPDR sector ETFs (XLK, XLF, XLV, etc.).

For NSE stocks, the mapping (`SECTOR_INDEX_MAP_NSE` in `config.py`) uses
common NSE sector index tickers (Nifty Bank, Nifty IT, Nifty Auto, etc.) —
these are **best-effort**, not all verified to resolve via yfinance. If a
specific sector ticker fails to fetch, that stock's RS_SECTOR automatically
and silently-to-the-user-but-not-silently-in-the-data falls back to RS vs.
the broad Nifty 50 index instead. **Check the `sector_index_source` column**
after your first run with this update — it shows the actual ticker used per
sector, or `FALLBACK_BROAD_INDEX` if it had to fall back. If you see a lot of
fallbacks, send me the sector labels involved and I'll find working tickers
or alternative data sources for those.

### Original system is untouched

Nothing above changes the original composite score, categories (Elite/
Emerging/Exit), or tabs from before. The Elite Compounder Early Detection
System runs as an additional, independent scoring pass — you now have both
the lagging/confirmation view (original) and the leading/accumulation view
(new) side by side.



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
## MF / FII Shareholding Trend (NSE only — highest-risk module)

Tracks whether Mutual Fund and FII/FPI holding % is **increasing quarter-over-
quarter** — a classic "smart money" accumulation signal, separate from
everything else in this system.

**Why this is the riskiest part of the whole build:** every other data source
here (yfinance prices, sector ETFs, S&P500/NSE500 lists) has a clean,
well-documented format. Shareholding pattern data does not — it's a
quarterly SEBI regulatory filing, usually submitted as XBRL (structured XML)
with a PDF attachment, and the exact field names / table layout can vary by
which filing software the company used. This module:

1. Queries NSE's corporate-filings API for each NSE stock's shareholding
   pattern filings (`config.NSE_SHAREHOLDING_API_URL` — **fix this first**
   if nothing resolves at all).
2. Tries to parse the XBRL attachment using flexible keyword-based tag
   matching (looks for tags containing "mutualfund"/"foreignportfolio" +
   "percentage", not exact hardcoded paths).
3. Falls back to parsing the PDF attachment's summary table the same way —
   by keyword-matching row labels ("Mutual Funds", "Foreign Portfolio
   Investors") rather than assuming a fixed column position.
4. Caches each quarter found in `cache/shareholding_history.json`
   permanently — once 2+ quarters are on file for a stock, the
   increasing/decreasing flag becomes available. **Expect the first couple
   of quarters to just show missing/blank trend flags** until enough
   history accumulates; this is expected, not a bug.

**New columns** (NSE tabs only — blank on US tabs, this is an Indian
regulatory concept with no equivalent built here): `mf_holding_pct`,
`fii_holding_pct`, previous-quarter values, `mf_holding_increasing`,
`fii_holding_increasing`, visual flags, and `shareholding_data_quality`.

**This is informational only** — it does NOT feed into `composite_score` or
`EliteCompounderScore`, specifically so a shaky data source can't quietly
degrade either already-tuned scoring system. If the parsing logic turns out
to work well after a few live runs, ask and I can fold a weighted
contribution into the Elite score.

**Realistic expectation:** the XBRL/PDF parsing logic is tested against
synthetic data that mimics the standard SEBI format, but has not been tested
against a real NSE filing (no network access to verify the exact API/file
formats from where this was built). The first live run's
`shareholding_data_quality` column will tell us whether the approach works
at all — if most rows show `missing`, send me the Actions log and we'll fix
it the same way we fixed the sector mapping, likely needing 2-3 rounds given
the added complexity of PDF/XBRL parsing vs. the simple CSVs used elsewhere.

## My Portfolio — your actual Zerodha holdings, scored

A separate tab (`My_Portfolio_Scored`) that takes your real holdings and runs
them through the exact same scoring pipeline as the NSE500/S&P500 scan —
composite score, category, EliteCompounderScore, OBV/Supertrend/MACD
signals, plus your invested value, current value, and P&L%.

### One-time setup

1. In Zerodha Console, go to **Holdings → Export** and download the XLSX.
2. In your Google Sheet, go to **File → Import**.
3. Click **Upload**, select the downloaded XLSX.
4. Choose **Insert new sheet(s)**, then click **Import data**.
5. Rename the newly-created tab to exactly `My_Holdings` (right-click the
   tab → Rename).

That's it — the next scheduled run will pick it up automatically and create
`My_Portfolio_Scored`.

### Updating your holdings later

Whenever you trade, just repeat steps 1-4 above (File → Import → Replace
current sheet, selecting your existing `My_Holdings` tab as the target this
time). This script only ever **reads** `My_Holdings`, never writes to it, so
re-importing never conflicts with anything automated here.

### How scoring works for your holdings

- **Already in the NSE500 scan** (true for most large/mid-cap holdings):
  full treatment — composite_score, EliteCompounderScore, all the same
  signals as the main scan, zero extra cost since it's already computed.
- **Not in the top-500 scan universe** (smaller-cap holdings): technical
  indicators are still fetched and computed fresh (OBV, MACD, Supertrend,
  RS vs Nifty), but `composite_score` and `EliteCompounderScore` show
  "Outside scan universe" instead of a number — both scores are
  cross-sectional percentile rankings, which are statistically meaningless
  computed against a peer group of one or two stocks. Showing a fake number
  there would be misleading rather than informative.

### Live price

`live_price` and `live_day_change_pct` are `GOOGLEFINANCE()` formulas written
into the sheet — once written, they keep updating live in your browser
independent of the daily script run. Assumes NSE-listed holdings (`NSE:`
prefix); if you hold US stocks too, you'd want to adjust those two formulas
manually for those specific rows.

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
