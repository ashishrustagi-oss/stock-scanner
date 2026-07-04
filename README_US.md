# US Equities Strategy — Design & Backtest (IBKR)

Longer-term positional/trend-following strategy for US equities, extending
Ashish Capital's NSE scanner/trading system to an IBKR account. This is a
**separate strategy leg** in the same repo — shares core modules (Supertrend,
fundamentals fetching, backtest engine) with the NSE strategies, but has its
own scoring model, universe, and (eventually) execution script.

**Status: Phase 1 — design & backtest. No live execution yet.**

---
## Philosophy

Value investing + future growth (GARP — growth at a reasonable price), with
**growth leading and value as a guardrail**, not a hard gate. A stock with
strong growth at a fair-but-not-cheap valuation is preferred over a cheap
stock with mediocre growth.

Same backtest discipline as the NSE strategies:
- Never trust a signal without two independent backtests on genuinely
  different market regimes.
- Flag when evidence contradicts a hypothesis rather than retrofitting
  explanations.
- Keep validated vs. unvalidated signals clearly separated in code and docs.
- Watch for survivorship bias and look-ahead bias explicitly — see the
  Backtest Design section below.

---
## Strategy parameters (locked in)

| Parameter | Value |
|---|---|
| Universe | Full S&P 500 (point-in-time, not today's list — see below) |
| Hold period | Weeks to months (positional, not swing/day trading) |
| Rescan/rescore frequency | Weekly |
| Starting capital | $2,000 (adding gradually) |
| Position sizing | TBD by backtest — likely 2-4 concurrent, fractional shares |
| Broker | IBKR Pro, Fixed pricing ($0.005/share, $1 min, capped 1% of trade value) |
| Slippage assumption (backtest) | 5-10 bps per trade |

---
## Composite score

Unlike the NSE strategies (technical-signal-led, fundamentals as a secondary
gate), the US strategy is **fundamentals-led with technicals for entry
timing**, reflecting the GARP philosophy above.

| Component | Weight | Inputs |
|---|---|---|
| Growth | 45% | Revenue growth (YoY + 3yr), EPS growth, analyst estimate revisions* |
| Value | 25% | PEG ratio, EV/EBITDA vs. sector median |
| Quality | 15% | Free cash flow trend, Debt/EBITDA, margin trend |
| Technical timing | 15% | Weekly + Daily Supertrend alignment, RS rank vs. S&P 500/sector, OBV trend |

\* Estimate revisions are only included from 2015 onward — see Backtest
Design below for why.

No hard disqualifying gates (e.g. a PEG ceiling) initially — pure composite
score. Backtest results will show whether the growth weighting is letting
overpriced names through; a guardrail gets added only if evidence supports
it, not pre-emptively.

**Entry:** composite score clears a backtest-tuned threshold AND technical
component shows a recent bullish Supertrend alignment (the composite alone
doesn't trigger a trade — timing still matters).

**Exit:** Weekly Supertrend flip to bearish (primary trend exit), OR a sharp
fundamental deterioration between quarterly refreshes (earnings miss,
negative estimate revision cascade, margin collapse) overrides the technical
signal regardless of trend state. No fixed profit target — let winners run,
consistent with the NSE strategies' trend-following philosophy.

---
## Backtest design

### Two-era split (solves the free-data-for-2008 problem)

| Era | Range | Growth score includes estimate revisions? | Purpose |
|---|---|---|---|
| 1 | 2008–2015 | No (not reliably available for free this far back) | Tests the GFC + recovery; also tests whether the composite works *without* the revisions component |
| 2 | 2015–2026 | Yes | Full composite, covers 2018 selloff, 2020 COVID crash, 2022 bear market |

This split is a genuine feature, not just a workaround: running the same
composite with vs. without estimate revisions across two independent regimes
directly measures whether that component adds real predictive value, rather
than assuming it does.

### Point-in-time universe (survivorship bias)

`universe.get_sp500_universe()` returns **today's** S&P 500 — using it for a
2008 backtest would silently omit every stock removed from the index since
(bankruptcies, acquisitions, underperformers dropped), inflating backtest
returns. `sp500_point_in_time.py` (new module) solves this using a free,
community-maintained dataset (`github.com/fja05680/sp500`) of dated
add/remove events, reconstructed into a queryable timeline.

**Validated:** the reconstruction correctly drops Lehman Brothers (traded as
`LEHMQ` in its final days) from the index exactly on 2008-09-17, the day
after its bankruptcy filing — confirmed against the raw snapshot data.

**Known caveat:** the source maintainer notes Wikipedia's changes list
(one of his own inputs) is not fully complete on its own; his merged file is
cross-checked against Wikipedia's current list on each update but isn't a
perfect record of every historical change. Not a concern for 1996-2001 (the
maintainer's own flagged weak spot) since our range starts 2008.

### Price data coverage (open item)

Point-in-time membership tells us *which* tickers should be in the backtest;
it doesn't guarantee yfinance actually has usable price data for all of
them — delisted/bankrupt tickers are exactly the ones most likely to be
missing from a free source, and also the ones most important not to silently
drop (that's where survivorship bias hides). `diagnostics/sp500_coverage_probe.py`
checks this explicitly and writes a gap report to
`cache/sp500_coverage_report.json` — **run this before trusting any backtest
results**, and document whatever gaps it finds rather than treating the
pipeline as complete.

### Point-in-time fundamentals

Not yet built. `fundamentals.py` currently pulls from yfinance's
`income_stmt`/`balance_sheet`, which only exposes a few trailing years —
not enough for a 2008-2026 backtest. Needs either a SEC EDGAR XBRL-based
puller (free, but manual — same category of effort as the shareholding
pipeline rebuild) or acceptance of a shorter fundamentals-validated window
layered onto the longer price-only backtest. Decision pending.

---
## Build status

| Component | Status |
|---|---|
| `sp500_point_in_time.py` | Built, tested against live data |
| `diagnostics/sp500_coverage_probe.py` | Built, **not yet run** (needs real internet, won't run in a network-restricted sandbox) |
| `config.py` additions | Done (`SP500_HISTORICAL_URL`, `SP500_CHANGES_SINCE_URL`, `SP500_TIMELINE_CACHE_PATH`) |
| Extended `fundamentals.py` (PEG, EV/EBITDA, FCF, margin trend) | Not started |
| `scoring_us.py` (composite score) | Not started |
| Extended `backtest.py` (historical fundamental gate, two-era mode) | Not started |
| Point-in-time fundamentals (pre-2015) | Not started — open design question above |
| IBKR execution (`trade_ibkr.py`) | Not started — deliberately deferred to Phase 2 |

---
## Deferred to Phase 2 (automation) — not being worked on yet

IBKR requires a persistent `IB Gateway` process with `/tickle` keep-alive
pings roughly every minute, incompatible with GitHub Actions' ephemeral
runners. Candidate solution: Oracle Cloud's Always Free tier (Ampere A1,
reduced to 2 OCPU/12GB as of June 2026, still enough for this use case) as
an always-on host for the Gateway process only, keeping the scan/scoring
logic on GitHub Actions as usual — so a lost Oracle instance only breaks
execution, not signal generation. Not evaluated in depth; revisit once
backtesting proves the strategy out.
