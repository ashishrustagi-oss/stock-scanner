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

## Phase 1 — Elite Compounder Discovery System v2.0

Four modules, all built entirely from data already being fetched — no new
external data sources, no changes to `composite_score` or
`EliteCompounderScore`.

### Module 3: RS Percentile Rank
`rs_rank` — where this stock's `RS_vs_Broad_Index_pct` ranks (0-100) within
its own universe. One column rather than the originally-discussed
`rs_rank_nse500`/`rs_rank_sp500` pair, since a stock only ever belongs to one
universe — a single column carries the same information without an
always-blank twin. `rs_rank_score` (0/5/10/15) and `flag_rs_top_decile`
(🟢 above rank 90) ride alongside it. Currently informational only — not
folded into any existing score.

### Module 4: Trend Birth Detection
`trend_birth_flag` — fires when price just reclaimed EMA20, MACD just turned
bullish while still below zero, OBV has been rising for 13 weeks, and the
stock isn't more than 25% off its highs. Meant to catch the "just starting
to turn" moment, distinct from the already-confirmed-trend signals
elsewhere. New tab: **`TREND_BIRTH`**, sorted by `trend_birth_score`.

### Module 5: Monthly Trend Confirmation
Adds a third timeframe (daily → weekly → **monthly**) using the same
12/26/9 MACD convention and 20/50-period EMA cross, computed on
calendar-month candles. `monthly_bullish` requires both monthly MACD>signal
AND monthly EMA20>EMA50.

**Trade-off made to support this:** `PRICE_HISTORY_PERIOD` was bumped from
3 years to 5 years (`config.py`) so the monthly EMA50 has ~60 monthly bars
to work with instead of ~36 — still less converged than a multi-decade
history would give, so treat monthly EMA50 as directionally useful, not
perfectly precise.

### Module 6: Sector Leadership Engine
Ranks stocks within their own **(universe, sector)** group — NSE and US
stocks are never mixed even if a sector label looks similar on both sides.
**Ranking basis: `EliteCompounderScore`** — chosen because it's already a
normalized 0-100 score safe to compare directly within a small group, and
it's the system built specifically for leadership/early detection. If you'd
rather rank by `composite_score` or pure RS-vs-sector instead, that's a
one-line change in `scoring.py`'s `compute_sector_leadership()`. Top 3 get
points (15/10/5) and a 🟢 flag; new tab **`SECTOR_LEADERS`** shows the top 5
per sector group.

### A note on the column layout

Adding 4 new headline flags pushed the "headline vs. detail" boundary in
every wide tab from **column R to column V** — everything up through V is
still flat/visible; the collapsible detail group now starts at V instead of R.

## Phase 2 — Institutional Accumulation Scoring (Module 2 extension)

Builds on the MF/FII shareholding trend (NSE-only, same scope as before) by
adding quarter-over-quarter magnitude and 2-quarter streak detection.

**New columns:** `mf_holding_change_qoq` / `fii_holding_change_qoq` (the
actual percentage-point change, not just the up/down boolean),
`mf_increasing_2q_streak` / `fii_increasing_2q_streak` (was it increasing
the quarter before that too?), `institutional_accumulation_score` (0-20),
`flag_institutional_accumulation`.

**Scoring (resolves an ambiguity in the original spec):** the spec listed
"MF increasing: +5" and "MF increasing 2 quarters: +10" as separate line
items, but summing all four literally would max out at 30, not the stated
"Maximum = 20." The two tiers **don't stack** — a 2-quarter streak already
implies the latest quarter was increasing too, so it replaces the
single-quarter tier rather than adding to it:

| MF/FII state | Points (each side, max 10) |
|---|---|
| Increasing 2 quarters in a row | 10 |
| Increasing just the latest quarter (or streak broke) | 5 |
| Not increasing / no data | 0 |

MF max 10 + FII max 10 = 20 total, matching the spec's stated maximum.
`flag_institutional_accumulation` fires when the combined score exceeds 10
— meaning at least one side needs a real 2-quarter streak; two stocks each
just nudging up one quarter (5+5=10) doesn't clear the bar on its own.

**Important timing expectation:** the 2-quarter streak fields need **3
quarters of real history** on file for a stock before they can resolve to
`TRUE`/`FALSE` instead of blank. Since the shareholding cache only started
accumulating recently (and only gets ~60 new tickers per run due to NSE's
rate limiting), most stocks will show blank streak fields for several
months yet — falling back to the single-quarter `+5` tier in the meantime.
This isn't a bug; quarterly data just takes real calendar quarters to build
up. Not informational only this time, though it's still completely separate
from `composite_score` and `EliteCompounderScore` — neither existing system
is touched.

## Phase 3 — Earnings Acceleration Engine (Module 1, highest-risk phase)

Asks: is this stock's quarterly growth rate itself speeding up or slowing
down — not just "is it growing," but "is growth accelerating."

**New columns:** `eps_growth_latest_qtr` / `eps_growth_prev_qtr` /
`eps_acceleration`, the same three for revenue, plus
`earnings_acceleration_score` (0-20) and `flag_earnings_accelerating`.
Applies to **both** NSE and US (unlike MF/FII — yfinance exposes quarterly
statements for both universes).

### The key design decision: quarter-over-quarter, not year-over-year

True earnings acceleration in professional research usually compares this
quarter's YoY growth rate to last quarter's YoY growth rate — which needs
**6 quarters** of history (current + prior, each compared to its own
same-quarter-last-year). Yahoo Finance's quarterly statements via yfinance
typically only expose ~4-5 trailing quarters, which usually isn't enough
for that.

So this uses **quarter-over-quarter (QoQ)** growth instead — comparing each
quarter only to the one immediately before it, needing just 3 quarters of
history (much more likely to actually be available). The trade-off:
**QoQ is sensitive to seasonality.** A retailer's Q4-vs-Q3 will look
artificially strong every single year purely because of the holiday
quarter, regardless of whether the underlying business is actually
improving. This is a genuine, known limitation — not a hidden bug — and
worth keeping in mind especially for seasonal businesses (retail, certain
consumer names). If you get access to a data source with deeper quarterly
history later, switching to true YoY-based acceleration would be a
meaningful upgrade — ask and I can wire it in.

### Scoring

EPS acceleration and revenue acceleration are independent signals and **do
stack** here (unlike Module 2's MF/FII tiers, which don't) — max 10 + max
10 = 20, matching the original spec directly with no ambiguity to resolve.

| Signal | Threshold | Points |
|---|---|---|
| EPS acceleration | >20 percentage points | +10 |
| EPS acceleration | 10-20 points | +5 |
| Revenue acceleration | >15 percentage points | +10 |
| Revenue acceleration | 5-15 points | +5 |

`flag_earnings_accelerating` fires when the combined score exceeds 10.

### Live coverage check (22-06-2026)

Ran `diagnostics/earnings_accel_coverage_check.py` against a 19-ticker mixed
sample (NSE large-cap, NSE mid-cap, US large-cap, and deliberately seasonal
US retail names) before deploying this to production: **79% "ok", 21%
"partial", 0% "missing"** — comfortably good enough to build a dedicated
tab around, no field-name fallback issues found. NSE mid-caps (CAMS, MTAR,
Bharat Forge, Persistent, Polycab) actually came back slightly *better*
covered than the large-cap sample.

The seasonality caveat above isn't theoretical — it showed up immediately:
the seasonal retail sample (TGT, BBY, DECK, TPR) all produced large
*negative* acceleration scores, consistent with a strong holiday quarter
rolling off into a normal one, not a deteriorating business. Don't read a
red flag on `EARNINGS_ACCELERATING` for a seasonal name without checking
which quarter is being compared.

**New tab: `EARNINGS_ACCELERATING`** — top
`config.EARNINGS_ACCELERATING_TAB_TOP_N` (default 30) stocks where
`flag_earnings_accelerating` fired, ranked by `earnings_acceleration_score`
descending. Mixes NSE and US rows deliberately (this is "who's
accelerating fastest, full stop," not a per-universe comparison like
Sector Leaders).

## NSE Small/Micro-cap tier — raw indicators + a separate scoring system

A third, fully separate universe alongside NSE500 and S&P500: **Nifty
Smallcap 250** (Microcap 250 designed-in but currently disabled — see
"Current status" below), in a new `NSE_SmallMicro_Full_Scan` tab.

**Current status (22-06-2026): Smallcap 250 only, live.** Microcap 250's
only known free source — niftyindices.com's backend, reached via its raw
Azure App Service hostname (`nseindex-prod-app.azurewebsites.net`, found by
capturing the URL the site's own Download button hits; it's not on
`nsearchives.nseindia.com` or the usual `niftyindices.com/IndexConstituent/`
path the way every other Nifty index list is) — returns `403 Ip Forbidden`
specifically for GitHub Actions runner IPs. Confirmed the URL itself is
correct (works from an ordinary browser); this is an IP-range block, not a
header/cookie/URL problem, so it can't be fixed in code from here.
`config.NSE_MICROCAP_ENABLED = False` skips the fetch attempt entirely
(rather than retrying 3x against a known-blocked URL on every run) and the
pipeline runs on Smallcap 250 alone, which fetches cleanly with zero
issues. To revisit: either a self-hosted GitHub Actions runner (not on
Azure/AWS/GCP IP ranges) would likely get through, or a periodic manual
download committed as a static seed file (NSE rebalances this list only
twice a year, end of Jan/July, so it wouldn't go stale fast) — flip
`NSE_MICROCAP_ENABLED` back to `True` once either exists; no other code
changes needed, `get_nse_smallmicro_universe()` already handles both states.

**Why a third universe and not just a bigger NSE500.** By NSE's own index
rules, Smallcap 250 must already be a Nifty 500 member — including it on
its own would add ~zero genuinely new tickers, just a label on stocks
already in `NSE500_Full_Scan`. Microcap 250 is the opposite: stocks already
in (or entering) Nifty 500 are **compulsorily ineligible** for it — it's
built from the rank ~351–675 band sitting just beyond the Nifty 500 floor,
and would be where the genuinely new tickers come from once re-enabled.
Smallcap+Microcap 250 (vs. going all the way to NSE's full ~2,000-name
equity list, `EQUITY_L.csv`, which was considered and deliberately
rejected — see chat history) keeps the new tier inside ~250-500 net-new,
bounded names rather than roughly doubling total universe size.

**Why it's NOT merged into `combined` / NSE500.** `composite_score` and
`EliteCompounderScore` are percentile-ranked and were tuned/backtested
specifically against NSE500+SP500 liquidity and data-quality patterns (see
"Backtest Framework" below). Mixing in a thinner, less liquid, more
data-sparse tier would distort those percentiles for the universe they
*were* validated on — exactly the kind of silent contamination this
project has otherwise been careful to avoid. So this tier gets its own
tab, its own (much shorter) column list, and runs through
`process_universe(..., skip_scoring=True)` — a new parameter that computes
per-ticker indicators and fundamentals exactly as normal, but skips
`composite_score`, `EliteCompounderScore`, and every cross-sectional/
percentile-rank module: `rs_rank`, `sector_rank`, `trend_birth`/
`trend_death`, `obv_leadership_rank`, `institutional_accumulation_score`.
`flag_earnings_accelerating` / `earnings_acceleration_score` ARE still
computed — that module is per-ticker (QoQ vs. the same ticker's own prior
quarter), not a cross-sectional rank, so it isn't subject to the same
contamination risk.

**No shareholding for this tier.** The MF/FII module is already capped at
60 tickers/run by NSE's rate limit, and full NSE500 coverage alone takes
~9 runs. Adding ~250 more names (more once Microcap 250 is re-enabled)
would stretch that further. NSE500 keeps sole priority — the shareholding
gate in `main.py` checks `label == "NSE500"` (strict equality, deliberately,
not a prefix check) so the new tier is correctly excluded.

**One real bug caught and fixed while wiring this in:** `sector_data.py`'s
sector-benchmark mapping used to check `universe_label == "NSE500"` to
decide whether to use the NSE or US/GICS sector-ticker mapping. With the
new `"NSE_SmallMicro"` label, that strict check would have silently
misrouted every small/microcap stock into the US mapping — which would
never match NSE sector names like "Information Technology" or "Financial
Services," and would have quietly fallen back to broad-index RS for every
single sector without raising an error. Changed to
`universe_label.startswith("NSE")` so any current or future NSE-side
universe label routes correctly.

**What's in the tab:** ticker/name/sector, `smallmicro_score` and its
supporting columns (see below), the full OBV/MACD/Supertrend/EMA/RS
indicator set (per-ticker, not ranked), 52-week-high and near-breakout
proximity, volatility compression (informational — remember the backtest
found this is NOT a reliable standalone signal even on NSE500/SP500, so
treat it with at least that much skepticism here, on an unbacktested
universe), fundamentals + `fundamentally_qualified`, and earnings
acceleration. No `composite_score`, no `EliteCompounderScore`, no
`elite_category`, no flags that depend on an NSE500/SP500-specific
cross-sectional rank.

### SmallMicroScore — a separate scoring system, not a reweighted copy

`composite_score` and `EliteCompounderScore` were tuned/backtested
specifically against NSE500+SP500 liquidity and data-quality patterns —
running either one unchanged on this thinner, less-liquid, less-covered
universe would be misleading, not just incomplete. So this tier gets its
own purpose-built system instead, designed fresh rather than adapted from
either existing one, for three reasons:

1. **Liquidity is a real risk here that NSE500/SP500 never had to gate
   for.** A microcap can show a great-looking technical signal on a
   handful of thinly-traded days that mean nothing tradeable. A new
   `liquidity_qualified` gate runs FIRST, before any score is computed —
   see `compute_liquidity_gate()` in `scoring.py`. It's based on
   `avg_daily_traded_value` (price × volume, averaged over
   `config.LIQUIDITY_LOOKBACK_DAYS`, default 20 trading days), gated
   against `config.MIN_AVG_DAILY_TRADED_VALUE_INR` (₹50 lakh/day —
   **no precise, citable "correct" threshold for this exists**; checked
   before picking a number, this is a deliberately conservative starting
   default to tune after seeing real output, same status as
   `MIN_SALES_CAGR`/`MIN_ROCE` elsewhere in `config.py`). A stock that
   fails the gate gets no score at all, not a penalized one — see
   `smallmicro_score_basis` below for exactly why any given row is blank.
   This is deliberately a MINIMAL floor, separate from and much lower than
   the strict checklist's own ₹2 crore/day bar below — a stock between the
   two still gets a full score, it just won't pass the strict checklist.
2. **No MF/FII shareholding data exists for this tier** (see above) — that
   weight is folded into Earnings Acceleration and Liquidity instead,
   since both are real, computed, and otherwise unused here.
3. **Fundamentals coverage will be patchier than even NSE500's "patchy"
   coverage.** Handled as a separate qualifier, not folded into the 0-100
   score — a stock scored on technicals alone is always labeled as such,
   never silently presented the same as one with confirmed fundamentals.

**Components** (`config.SMALLMICRO_SCORE_WEIGHTS` — see revision history
below; still UNVALIDATED in the full sense, since no signal here has yet
hit the two-confirming-runs standard OBV itself had to meet on NSE500/SP500
before being trusted there):

| Component | Weight | What it reuses |
|---|---|---|
| Relative Strength | 40 | `rs_score` vs Nifty 50, percentile-ranked within this universe only — **strongest single component in the first backtest** (+38.27pp excess at 12m, n=410) |
| OBV Leadership | 25 | `obv_52w_range_pct` — your most-trusted signal on NSE500/SP500, but outperformed by RS here (+26.68pp excess at 12m, n=2,626) in the first backtest; demoted but still meaningfully weighted pending a 2nd confirming run either way |
| Near 52-Week High | 20 | `pct_from_52w_high`, inverted (closer to the high scores higher) and percentile-ranked — 2nd-strongest in the first backtest (+25.97pp excess at 12m, n=2,325, clean monotonic hit-rate climb across horizons) |
| Earnings Acceleration | 10 | `earnings_acceleration_score`, rescaled from its native 0-20 scale — untested in the backtest (n=0; earnings acceleration isn't historically reconstructed, see "Backtesting SmallMicroScore" below) |
| Liquidity | 5 | `avg_daily_traded_value`, percentile-ranked — showed ~zero predictive value as a SCORED component in the first backtest (+1.14pp excess at 12m, 48.7% hit rate, *below* 50%); cut from 10 but not removed, since one run isn't yet the two-run standard. Distinct from the pass/fail `liquidity_qualified` gate and the strict checklist's turnover bar, BOTH unaffected by this weight change |

**Revision history:**
- **1st** (initial build): OBV 30 / RS 20 / MACD 10 / Trend 20 / Earnings 20 — all five components untested
- **2nd** (your post-analysis call, before any backtest existed): MACD and Trend dropped entirely, OBV 40 / RS 25 / Near-52w-High 15 (promoted from a binary flag) / Earnings 10 / Liquidity 10 (promoted from gate-only to also scored)
- **3rd** (24-06-2026, driven by the first real backtest on this tier — see below): OBV 40→25, RS 25→40 (swapped, based on which one actually backtested better), Near-52w-High 15→20, Liquidity 10→5, Earnings unchanged at 10

**Real bug caught and fixed during testing (still applies to this
revision):** the first version combined components with a plain weighted
sum. A single missing component — most commonly Earnings Acceleration,
exactly when fundamentals data is `"missing"` — would NaN out the *entire*
score via standard arithmetic, even when the other components had
perfectly good data. That silently contradicted the actual design intent
(score on technicals alone when fundamentals are missing). Fixed: the
score is a weighted average across only the components present for that
row, **renormalized** so the weights of available components sum to 100.
Only NaN if literally every component is missing. A
`smallmicro_score_coverage_pct` column records how much of the 100-point
weight was actually available for that row (e.g. 90 = only Earnings
Acceleration was missing), so "Strong on full coverage" is distinguishable
from "Strong on partial coverage" without inspecting individual columns.

**`smallmicro_score_basis` values** — every blank or scored cell is
self-explanatory without cross-referencing `config.py`:
- `technicals_and_fundamentals` — fundamentals were available (ok/partial)
- `technicals_only` — fundamentals `data_quality` was `"missing"`, but enough else was present to score
- `not_scored_illiquid` — failed the liquidity gate
- `not_scored_insufficient_liquidity_data` — too few real trading days to judge liquidity at all
- `not_scored_no_usable_data` — liquidity-qualified, but every single scoring component was missing (a true data desert)

**Categories** (`smallmicro_category`, thresholds in
`config.SMALLMICRO_STRONG_THRESHOLD` / `SMALLMICRO_WATCH_THRESHOLD`):
deliberately different NAMES from `composite_score`'s (`Elite Compounder`/
`Emerging`/`Exit`/`Watch`), not just different numbers, so a `Strong` here
is never mistaken for the backtested `Elite Compounder` label on
NSE500/SP500. `Strong` (≥70) / `Watch` (50-70) / `Weak` (<50) /
`Insufficient Data` (no score at all).

### Strict checklist — a separate pass/fail flag, not a pre-filter

`smallmicro_strict_pass` (`compute_smallmicro_strict_checklist()` in
`scoring.py`) is a SEPARATE four-condition checklist, deliberately NOT a
pre-filter on `smallmicro_score` — every liquidity-qualified stock still
gets a full score regardless of whether it passes this. The point is to
see both "how strong is this on balance" and "does it clear my strict
bar" independently, rather than losing visibility into near-misses.

All four must be `True`:
1. OBV percentile (`obv_52w_range_pct`) in the top decile (≥90th, `config.SMALLMICRO_STRICT_TOP_DECILE_THRESHOLD`)
2. RS percentile (`rs_score`, ranked within this universe) in the top decile (≥90th, same threshold)
3. `near_breakout_15pct` is `True` — within 15% of the 52-week high (reuses the existing column, same 15% threshold already used elsewhere)
4. `avg_daily_traded_value` ≥ `config.SMALLMICRO_STRICT_MIN_TURNOVER_INR` (₹2 crore/day — deliberately much stricter than the ₹50 lakh/day scoring-eligibility floor above; a stock can clear that floor, get scored, and still fail this)

Any missing input fails that specific condition (never passes on
"unknown" — a strict "must be true" checklist can't be satisfied by data
you don't have). `smallmicro_strict_fail_reasons` lists exactly which
condition(s) failed, comma-joined, blank when `smallmicro_strict_pass` is
`True` — so a near-miss (e.g. a high-scoring stock that just barely misses
the OBV top-decile bar) is immediately diagnosable straight from the
sheet, without digging into the underlying percentiles yourself. Given how
tight a 90th-percentile bar is on two independent dimensions at once,
expect this checklist to pass only a small handful of names even when the
overall score looks healthy across the board — that's the design working
as intended, not a bug.

### Backtesting SmallMicroScore

`backtest.py` extends the existing walk-forward methodology (same
no-lookahead-bias construction: every indicator computed from
`df.loc[:asof_date]` only, forward returns measured strictly after that
date) to this tier. Set `config.BACKTEST_UNIVERSE = "NSE_SmallMicro"` and
run via `backtest_workflow.yml` exactly like the NSE500/SP500 backtest —
results land in a separate Google Sheets tab
(`config.BACKTEST_SMALLMICRO_RESULTS_TAB_NAME`, default
`Backtest_Results_SmallMicro`) so they never overwrite the NSE500/SP500
results if you switch `BACKTEST_UNIVERSE` back and forth.

**Signals tested** (`backtest.SMALLMICRO_SIGNAL_DEFINITIONS`) — both
component-level and composite-level, deliberately broken apart the same
way the original backtest discovered OBV was trustworthy and volatility
compression wasn't (you can't learn that from a composite score alone):

- `smallmicro_obv_top_decile`, `smallmicro_rs_top_decile`,
  `smallmicro_near_52w_high`, `smallmicro_earnings_accelerating`,
  `smallmicro_high_liquidity` — each component in isolation (liquidity
  tested at the same 90th-percentile bar as the others here, even though
  the live score only *weights* it, never gates on a top-decile basis —
  for a fair side-by-side against the components that genuinely are gated
  that way in the strict checklist)
- `smallmicro_strict_pass`, `smallmicro_score_above_70`,
  `smallmicro_score_above_50`, `baseline_all_smallmicro` — the actual
  composite outputs you'd act on

**Two real limitations specific to this backtest, beyond the existing
"fundamentals aren't historically reconstructed" simplification:**

1. **Earnings Acceleration isn't tested either, for the same reason.**
   `eps_acceleration`/`revenue_acceleration` require point-in-time-correct
   historical quarterly statements, which this backtest doesn't attempt to
   solve. `compute_earnings_acceleration_score` gracefully returns `NaN`
   for every row when fed no `eps_acceleration` column (rather than
   erroring), and the score's renormalization correctly redistributes that
   10-point weight across the other 4 components — so the backtest
   faithfully tests OBV/RS/Near-52w-High/Liquidity, but the
   `smallmicro_earnings_accelerating` signal will always show
   `sample_size: 0` (confirmed in testing). A live score still gets a
   chance to be pulled up or down by real earnings data this backtest
   can't replicate.
2. **Survivorship bias risk, unlike NSE500/SP500.** This universe is
   fetched fresh from TODAY's Smallcap 250 + Microcap 250 list (see
   `universe.py`) — there's no free historical reconstruction of index
   membership at this size tier. Testing today's list against years-old
   price history silently assumes these same ~250-500 names were already
   at this size tier back then, which isn't strictly true: NSE rebalances
   this list twice a year, so some names may have since grown into
   NSE500, and some may not have existed at this tier yet at earlier
   snapshot dates. Results here are more likely to be optimistic than a
   true historical small/microcap backtest would be, since today's list
   is itself a survivor of whatever happened since. Keep this in mind
   before treating any result here with the same confidence as the
   NSE500/SP500 backtest evidence (e.g. `elite_score_above_65`'s
   +3.48pp-excess, n=532 result) — it isn't an apples-to-apples comparison.

A standalone smoketest (`diagnostics/smallmicro_backtest_smoketest.py`,
not part of the daily pipeline or `backtest_workflow.yml`) exercises this
whole path end-to-end against synthetic multi-year price data, since live
NSE/Yahoo data isn't reachable from every environment — useful to re-run
after any future change to `compute_smallmicro_score` or the strict
checklist, to catch a wiring break before running the real (much slower,
quota-costing) backtest.

**First real run (24-06-2026)** — the result that drove the 3rd weight
revision above:

| Signal | n | 12m excess vs benchmark | 12m hit rate |
|---|---|---|---|
| `smallmicro_rs_top_decile` | 410 | **+38.27pp** | 69.5% |
| `smallmicro_strict_pass` | 351 | +38.04pp | 69.2% |
| `smallmicro_obv_top_decile` | 2,626 | +26.68pp | 66.3% |
| `smallmicro_near_52w_high` | 2,325 | +25.97pp | 69.1% |
| `smallmicro_score_above_70` | 1,726 | +25.85pp | 63.6% |
| `smallmicro_score_above_50` | 3,564 | +25.27pp | 65.9% |
| `baseline_all_smallmicro` | 3,999 | +23.61pp | 65.8% |
| `smallmicro_high_liquidity` | 410 | +1.14pp | 48.7% |
| `smallmicro_earnings_accelerating` | 0 | — (untested, see above) | — |

Every signal except `smallmicro_high_liquidity` clearly beat the
do-nothing baseline — the system as a whole works directionally. The two
things that stood out enough to act on immediately: RS beat OBV outright
(not just "also worked"), and liquidity as a *scored* component showed
essentially no edge — a 48.7% hit rate is *worse* than coin-flip, the only
signal in the table to fall below 50%. Both fed directly into the 3rd
revision weights above. `smallmicro_strict_pass`'s strong result is
probably substantially carried by its RS-top-decile requirement (one of
its four conditions) rather than equal contribution from all four — worth
keeping in mind when reading it as a single number.

**This is one run, not two.** OBV's NSE500/SP500 trust was earned by
holding up across *two* separate backtests with more data the second time
(+3.2pp→+4.1pp at 3mo). Nothing here has cleared that bar yet — these
weights reflect the best available evidence today, not a settled
conclusion. Re-run this backtest periodically (e.g. after `BACKTEST_UNIVERSE`
changes, or on a longer lookback once one's available) and compare against
this table before trusting the ranking further, the same way OBV's
NSE500/SP500 result was cross-checked a second time before being relied on.

**Treat `smallmicro_score` as a research starting point, not yet a fully
trusted trading signal**, even with real backtest evidence now behind
some of it. The weights above reflect one confirming run each, not the
two-run standard the rest of this system holds itself to; the liquidity
threshold (`config.MIN_AVG_DAILY_TRADED_VALUE_INR`) and strict-checklist
turnover bar (`config.SMALLMICRO_STRICT_MIN_TURNOVER_INR`) both remain
entirely unvalidated guesses; and Earnings Acceleration's real-world
predictive value on this tier is still completely untested either way.

## Chart Study Additions — Trend Death + OBV-Price Divergence

Built from studying real charts of BEL, Bharat Forge, CAMS, MTAR Tech, CDSL,
ADANIPORTS, IDFCFIRSTB, JYOTICNC, and Persistent — see the conversation for
the full visual read. Two new, standalone modules (neither folds into
composite_score or EliteCompounderScore):

### Trend Death / Distribution Detection
The mirror image of Trend Birth, for the "exit losers" side of the system:
fires when price just broke below EMA20, MACD just turned bearish *while
still above zero* (the topping equivalent of "early bullish below zero"),
OBV has been falling for 13 weeks, AND the stock is still within 15% of its
52-week high — deliberately tighter than Trend Birth's -25% floor, since
this is meant to catch the START of a top while still close to highs, not
stocks that have already broken down hard. New tab: **TREND_DEATH**.
Visual flag uses 🔴 (not 🟢) to make it visually distinct as a warning.

### OBV-Price Divergence
Directly inspired by the CAMS chart, which pulled back ~35% from its highs
in 2025 without OBV meaningfully declining — buyers didn't actually leave
even though price dropped. `obv_price_divergence` finds the most recent
price peak, then compares how much OBV has fallen since that peak to how
much price has fallen. Positive = bullish (OBV held up better than price).
`flag_bullish_obv_divergence` only fires if there was a real pullback of at
least 5% (a stock that hasn't pulled back has nothing to diverge from) and
the divergence exceeds 10 percentage points.

### OBV Acceleration / Quiet Base (25-06-2026)
From reviewing Redington, RR Kabel, and HDFC AMC charts: in each case, OBV
was quietly, steadily climbing for an extended stretch while price chopped
sideways or drifted down — and the tell that price was about to catch up
wasn't just "OBV is rising," it was a further **acceleration** in that
already-rising OBV slope, arriving before price moved. The underlying
motivation: by the time `composite_score`/`smallmicro_score` are at their
highest, this move has usually already happened — those scores are
backward-looking confirmations of strength already accumulated, not
predictions of strength about to begin. This signal is deliberately built
to be earlier instead, at the cost of being unvalidated and presumably
lower hit-rate / worse risk-reward than the validated scores — that's the
explicit tradeoff being made, not an oversight.

Two conditions, **both** required (`indicators.obv_acceleration_quiet_base()`):
1. **Acceleration** — the ~13-week (`obv_slope_13w`) OBV slope is at least
   `config.OBV_ACCELERATION_RATIO_THRESHOLD` (default 2.0x) the ~26-week
   (`obv_slope_26w`) baseline slope. Sign-aware: a short slope that's
   *more negative* than the long baseline (selling accelerating) is
   explicitly excluded — verified directly, not just assumed, since a
   naive ratio on two negative numbers could otherwise flag accelerating
   selling as if it were the bullish pattern.
2. **Quiet base** — price hasn't moved much yet: `price_chg_13w` (raw %
   change over the same ~13-week window) is within
   `config.OBV_ACCELERATION_PRICE_FLAT_BAND_PCT` (default ±8%) of flat.
   Without this, the signal would just be confirming a move that's
   already visible to everyone, not catching it early — that's the whole
   point of the "quiet base" requirement.

New columns: `price_chg_13w`, `obv_acceleration_quiet_base` (🟢 flag),
`obv_acceleration_basis` — the basis column is diagnostic, same pattern as
`smallmicro_strict_fail_reasons`: `"accelerating_quiet_base"` (both
conditions met), `"accelerating_but_price_moved"` (accelerating, but price
already ran — the move you'd have wanted to catch earlier),
`"quiet_but_not_accelerating"` (price is quiet, but OBV isn't speeding up),
`"neither"`, or `"insufficient_data"`.

Both windows (`obv_slope_13w`/`obv_slope_26w`) were already computed for
every stock by the OBV Leadership module — no new lookback windows were
added, this signal is built entirely from existing infrastructure.

### OBV Divergence Decaying (25-06-2026)
The mirror-image **caution** flag to OBV Acceleration / Quiet Base above.
From the same chart-study session, describing the sequence precisely: OBV's
own rate of accumulation **peaks first**; price then catches up and makes
its own peak, often rising sharply from there; but underneath that visible
price strength, OBV's slope is **already declining** from its earlier
high — the engine that drove the move is fading while the move is still
happening on the chart. Same motivation as the acceleration signal above,
inverted: this is meant to catch exhaustion *before* price itself rolls
over, not after.

**Design history worth knowing before changing this further:** the first
attempt at this signal checked whether `obv_price_divergence` (the
existing peak-anchored metric) had been positive recently and was now
fading. Built, then tested directly against constructed synthetic data
before being trusted — and found NOT to work: a divergence measured
against a single, increasingly-distant 52-week peak is dominated by the
*cumulative* effect since that peak and barely responds to genuinely
recent dynamics. In testing, it returned "a cushion existed recently" as
`True` for almost any stock sitting below its 52-week high with
generally-rising OBV — which describes a huge fraction of real stocks,
so it wasn't actually discriminating anything. Replaced with the approach
below, which compares OBV slope to **its own recent trajectory** instead —
verified directly against a constructed OBV-peaks-then-decelerates-while-
price-still-rises scenario (slope traced a clean 0.40 → 0.02 decline while
price's rolling % change stayed solidly positive throughout) before being
trusted.

Two conditions, **both** required (`indicators.obv_divergence_decaying()`):
1. **OBV slope has decayed from its own recent high** — `obv_slope_42d`
   (current ~2-month slope) divided by `obv_slope_42d_recent_high` (the
   highest that same 42-day slope reached at any point in the trailing
   `config.OBV_DIVERGENCE_DECAY_LOOKBACK_DAYS`, default 150 days, via the
   new `indicators.obv_slope_series()` rolling helper) is at or below
   `config.OBV_DIVERGENCE_DECAY_SLOPE_RATIO_THRESHOLD` (default 0.5 — slope
   has fallen to half or less of its own recent peak). Only counted when
   that recent high itself cleared `config.OBV_DIVERGENCE_DECAY_MIN_RECENT_HIGH_PCT`
   (default 0.3%) — a stock whose OBV slope was never strongly positive
   has nothing real to have decayed *from*.
2. **Price is still rising** — `price_chg_42d` (raw % change over the same
   ~2-month window) is at or above
   `config.OBV_DIVERGENCE_DECAY_PRICE_RISING_THRESHOLD_PCT` (default 3.0%).
   This is specifically for stocks that *look* fine (price still
   climbing) while the underlying volume support has already started
   fading — not for stocks that have already turned down, which is a
   different, more obvious problem this signal isn't trying to catch early.

New columns: `obv_slope_42d`, `obv_slope_42d_recent_high`, `price_chg_42d`,
`obv_divergence_decaying` (🔴 flag — not 🟢, matching the same convention
Trend Death uses to stay visually distinct as a warning rather than an
opportunity), `obv_divergence_decay_basis` — same diagnostic-transparency
pattern as the rest of this system: `"divergence_decaying"` (both
conditions met), `"obv_still_strong"` (price rising, but OBV slope hasn't
actually decayed from its own recent high yet), `"price_not_rising"` (OBV
has decayed, but price isn't rising — not the pattern this flag is for),
`"no_peak_to_decay_from"` (OBV's own recent high was never meaningfully
positive — nothing to fade from), or `"insufficient_data"`.

**Honest framing:** all four of these chart-study additions came from
reading a handful of winning charts visually, not from a statistical
backtest. See the next section for the actual rigorous validation tool —
and note that `obv_acceleration_quiet_base` and `obv_divergence_decaying`
in particular have not been backtested even once yet (unlike
`obv_52w_high`/OBV leadership, which has two confirming runs behind it).
Treat a flag here as a research lead worth a closer manual look, not a
trading signal, until it's been through `backtest.py`'s walk-forward
methodology the same way every other trusted signal in this system was.

## Backtest Framework — rigorous signal validation, not chart-reading

`backtest.py` is a genuinely separate tool from the daily scan: a
walk-forward simulation that tests whether any given signal (Trend Birth,
EliteCompounderScore thresholds, OBV 52w-high, etc.) actually preceded
real outperformance, across a real universe and many historical dates —
not just the handful of winning charts that prompted building it.

### How it avoids the trap the chart study couldn't avoid

The chart study only looked at survivors — stocks that already became
compounders — with no way to tell how many *other* stocks showed the same
early pattern and went nowhere. This backtest fixes that by testing every
signal against the **entire universe** (winners, losers, and everything
in between) across dozens of historical snapshot dates automatically.

### No-lookahead-bias guarantee (the most important property of any backtest)

At each historical "as-of" date, every indicator is computed using ONLY
price data up to and including that date — exactly what would have been
knowable at the time. I verified this directly: computed signals on a
synthetic stock with the full future price history sitting in memory, then
again with that future data physically removed before computation — the
two runs produced bit-for-bit identical results. If there were a lookahead
leak anywhere in the indicator chain, those two scenarios would differ.

### What it measures

For each signal: sample size, mean/median forward return at 1/3/6/12
months, hit rate (% of instances with a positive forward return), and the
**excess return vs. the benchmark index over the same dates** — the number
that actually matters, since a signal that just rides a generally rising
market isn't adding anything.

### Key simplification

`fundamentally_qualified` is set `True` for every historical row — point-in-
time historical fundamentals are a much harder data problem than this tool
needs to solve to be useful. This means the backtest measures the
**technical signals' predictive power in isolation**, not combined with
the fundamental gate real-world categorization also requires.

### Running it

This is **far more compute-intensive** than the daily scan (every indicator
recomputed at every snapshot date) and is **not** part of the daily
schedule — it has its own workflow (`.github/workflows/backtest_workflow.yml`),
manual-trigger only. Start small: the defaults in `config.py`
(`BACKTEST_MAX_TICKERS = 100`, monthly snapshots, 3-year lookback) are
deliberately conservative. Results land in a new `Backtest_Results` Sheet
tab and as a downloadable CSV artifact on the GitHub Actions run page.
Widen `BACKTEST_MAX_TICKERS` / `BACKTEST_LOOKBACK_YEARS` / switch
`BACKTEST_SNAPSHOT_FREQ` to weekly only after confirming a smaller run
finishes in a reasonable time — each added ticker and each added snapshot
multiplies the runtime.

### What I tested vs. what only a real run can tell you

I validated the **mechanics** thoroughly with synthetic data: forward-return
math is correct, there's no lookahead leakage, and the full pipeline runs
end-to-end across multiple tickers and dates without errors. What I could
not test from here (no live data access) is whether any signal actually
shows real predictive edge on real NSE/US history — that's exactly what
running this for real will tell you, and it might show some signals here
don't hold up as well as the chart study suggested. That's the point.

## OBV Leadership Rank — backtest-driven, not chart-driven

Added after running the actual backtest, not from reading charts: across
both the 100-ticker and 300-ticker runs, OBV proved to be the single most
consistently predictive signal in this whole system (`obv_52w_high` held
+3.2pp excess vs. random stock-picking at 3 months in the smaller run, then
**strengthened** to +4.1pp with 3x more data — most signals weakened or
reversed with more data; this one got stronger).

`obv_leadership_rank` smooths the binary `obv_52w_high` flag into a
continuous 0-100 percentile rank: blends `obv_slope_13w` and
`obv_slope_26w`, then ranks that blend within the universe. A rank of 98
means this stock's OBV momentum is stronger than 98% of the universe right
now — separating genuine accumulation from a stock that just barely
technically qualifies for the binary flag. `flag_obv_leadership_top_decile`
fires above rank 90. New tab: **OBV_LEADERS**, the top 30 stocks by this
rank across both universes.

Purely additive — doesn't change `composite_score` or
`EliteCompounderScore`. `obv_52w_high` and everything that already used it
(the early-detection strict filter, Trend Birth, the original Elite
Compounder OBV sub-score) are completely unchanged.

**Real bug found and fixed in `obv_slope()` (24-06-2026), which affects
this rank directly** — `obv_leadership_rank` blends `obv_slope_13w` and
`obv_slope_26w`, both built on the same `obv_slope()` function used
elsewhere (`obv_slope_20d`, `obv_slope_50d`). The function normalizes its
regression slope by `mean(abs(OBV))` over the window — found to break down
specifically when OBV crosses from negative to positive (or vice versa)
within the lookback window, since values near the crossing point pull the
denominator toward zero while the slope itself can still be genuinely
steep, inflating the result. Confirmed on real data via a Pine Script
side-by-side: IDEA's 50-day OBV window crossed from -4.48B to +4.19B,
producing 7.11% from the original formula when a manual check of the same
real values indicated the true magnitude was closer to 1-2%. Its 20-day
window over the same period never crossed zero and was correctly
unaffected (1.47%, matched expectations exactly) — confirming the bug was
specific to the zero-crossing case, not a wholesale formula error.

Fix: `obv_slope()` now checks whether the window's OBV values span across
zero (`min < 0 < max`). If so, it falls back to normalizing by the
window's own range (`max - min`) instead of mean-abs — range stays
meaningful even when values cross zero. Deliberately a fallback, not a
replacement: every non-crossing window (the overwhelming majority of
cases for most stocks) continues using the original mean-abs normalizer
unchanged, verified to produce byte-identical results to before the fix.
Since `obv_leadership_rank` is a percentile RANK, not a raw value, a
handful of zero-crossing stocks having a corrected, smaller slope mainly
just means they won't be artificially inflated relative to their peers —
the backtest evidence above (`obv_52w_high` and the original
`obv_slope_13w`/`26w` blend) was overwhelmingly built on stocks that
didn't hit this edge case, so the headline finding stands, but if you
re-run the backtest, expect `obv_leadership_rank`'s exact percentile
boundaries to shift slightly for whichever stocks happen to cross zero
during any given window.

### A finding worth remembering from the backtest before building further

Trend Birth's apparent edge (+2.06pp excess at 3m with 38 samples)
**reversed to -2.64pp** once tested on 137 samples, and got worse at longer
horizons (-14pp at 12m). `composite_score_above_85` similarly weakened from
+1.90pp to essentially flat. Meanwhile `elite_score_above_65` (the
most-sampled threshold-based signal, n=170→532 across the two runs) stayed
remarkably stable at roughly +3.5pp both times — that consistency under a
3x larger sample is exactly what separates a real signal from one that
just looked good by chance in a smaller sample. Treat any newly-added
signal here with the same skepticism until it's been tested the same way.

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
universe.py             NSE500 / S&P500 / NSE Small+Microcap 250 constituent loading
data_fetch.py            Batched yfinance price history fetch
indicators.py             OBV, MACD, Supertrend, EMA, RS, 52w-high distance, liquidity (avg traded value)
fundamentals.py          CAGR / ROCE / D-E from Yahoo financial statements (cached weekly)
scoring.py               Cross-sectional percentile scoring + composite + categorization + SmallMicroScore (separate system, NSE Small/Micro-cap tier only)
sheets_export.py          Google Sheets writer
main.py                   Orchestrator — run this
test_dry_run.py            Mocked end-to-end test, no network needed (dev use only)
diagnostics/               One-off checks (e.g. earnings-acceleration data-quality coverage), not part of the daily pipeline
.github/workflows/daily_scan.yml   Automation
```
