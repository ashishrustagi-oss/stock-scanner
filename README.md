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

## NSE Small/Micro-cap tier — raw, deliberately unscored

A third, fully separate universe alongside NSE500 and S&P500: **Nifty
Smallcap 250 + Nifty Microcap 250 combined**, in a new
`NSE_SmallMicro_Full_Scan` tab.

**Why a third universe and not just a bigger NSE500.** By NSE's own index
rules, Smallcap 250 must already be a Nifty 500 member — including it on
its own would add ~zero genuinely new tickers, just a label on stocks
already in `NSE500_Full_Scan`. Microcap 250 is the opposite: stocks already
in (or entering) Nifty 500 are **compulsorily ineligible** for it — it's
built from the rank ~351–675 band sitting just beyond the Nifty 500 floor.
Combining both is the smallest fetch that gets genuinely new names without
going all the way to NSE's full ~2,000-name equity list (`EQUITY_L.csv`),
which was considered and deliberately rejected: that would roughly double
total universe size, push the MF/FII shareholding module's full-coverage
cycle from ~9 runs to ~25, and add a tier of names yfinance covers
noticeably worse than even the existing "patchy" NSE500 fundamentals
coverage. Smallcap+Microcap 250 keeps the new tier inside ~250 net-new,
bounded names.

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
~9 runs. Adding ~250 more names would stretch that to ~1 month for full
coverage. NSE500 keeps sole priority — the shareholding gate in
`main.py` checks `label == "NSE500"` (strict equality, deliberately, not a
prefix check) so the new tier is correctly excluded.

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

**What's in the tab:** ticker/name/sector, the full OBV/MACD/Supertrend/EMA/
RS indicator set (per-ticker, not ranked), 52-week-high and near-breakout
proximity, volatility compression (informational — remember the backtest
found this is NOT a reliable standalone signal even on NSE500/SP500, so
treat it with at least that much skepticism here, on an unbacktested
universe), fundamentals + `fundamentally_qualified`, and earnings
acceleration. No category, no elite_category, no flags that depend on a
cross-sectional rank.

**Treat everything in this tab as informational only, not yet a trading
signal.** None of it has been backtested — not the indicators' predictive
value on this liquidity tier, not the fundamentals coverage rate, nothing.
Before trusting anything here the way the NSE500/SP500 scores are
trusted, it needs its own walk-forward backtest run separately (same
methodology as `backtest.py`, see below) — smallcap/microcap stocks behave
differently across market cycles than the large/mid-cap names the existing
backtest evidence actually covers.

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

**Honest framing:** both of these came from reading 8-9 winning charts
visually, not from a statistical backtest. See the next section for the
actual rigorous validation tool.

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
indicators.py             OBV, MACD, Supertrend, EMA, RS, 52w-high distance
fundamentals.py          CAGR / ROCE / D-E from Yahoo financial statements (cached weekly)
scoring.py               Cross-sectional percentile scoring + composite + categorization
sheets_export.py          Google Sheets writer
main.py                   Orchestrator — run this
test_dry_run.py            Mocked end-to-end test, no network needed (dev use only)
diagnostics/               One-off checks (e.g. earnings-acceleration data-quality coverage), not part of the daily pipeline
.github/workflows/daily_scan.yml   Automation
```
