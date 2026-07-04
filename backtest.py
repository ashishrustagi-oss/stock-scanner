"""
Walk-forward backtest of the scanner's signals — measures whether stocks
flagged by a given signal (Trend Birth, Elite Compounder Score thresholds,
OBV 52w-high, etc.) actually went on to outperform afterward, across a real
universe and many historical dates. This is the rigorous counterpart to
eyeballing 8 winning charts: every signal gets tested across hundreds of
stocks and dozens of historical snapshots, with losing/flat stocks included
automatically (not just survivors).

CRITICAL DESIGN PRINCIPLE — no lookahead bias:
At each historical "as-of" date, every indicator is computed using ONLY
price data up to and including that date (df.loc[:asof_date]) — exactly
what would have been known at the time. Forward returns are then measured
using data AFTER that date, which is correct: that's the outcome being
tested, not an input to the signal itself.

SIMPLIFICATION — fundamentals are not historically reconstructed:
`fundamentally_qualified` is set True for every row in this backtest. This
means EliteCompounderScore and composite_score here measure the TECHNICAL
signal's predictive power in isolation, not combined with the fundamental
gate (point-in-time historical fundamentals are a much harder data problem
than this backtest needs to solve to be useful). Keep this in mind when
reading the results — real-world categorization also requires passing the
fundamental filter, which this backtest doesn't test.

PERFORMANCE: this is much more expensive than a daily scan, since every
indicator gets recomputed at every snapshot date. Defaults are deliberately
conservative (see config.BACKTEST_* settings) — widen them only once you've
confirmed a smaller run completes in a reasonable time. Run via the separate
`backtest_workflow.yml` (manual trigger only), not as part of the daily scan.
"""

import datetime
import functools
import logging
import sys

import numpy as np
import pandas as pd

import config
import data_fetch
import metrics_builder
import scoring as sc
import sheets_export
import sp500_point_in_time as spt
import universe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("backtest")


# ----------------------------------------------------------------------------
# Forward return measurement (point-in-time safe — uses only FUTURE data,
# which is correct here: this is the outcome, not the signal input)
# ----------------------------------------------------------------------------
def forward_return(price_series: pd.Series, asof_date, horizon_days: int) -> float:
    if asof_date not in price_series.index:
        return np.nan
    future = price_series.loc[price_series.index > asof_date]
    if len(future) < horizon_days:
        return np.nan  # not enough future history yet (e.g. too close to today)
    asof_price = price_series.loc[asof_date]
    target_price = future.iloc[horizon_days - 1]
    if pd.isna(asof_price) or asof_price == 0 or pd.isna(target_price):
        return np.nan
    return float((target_price - asof_price) / asof_price * 100)


# ----------------------------------------------------------------------------
# Per-snapshot signal computation — reuses the SAME functions the live daily
# scan uses, just called on a point-in-time-sliced DataFrame instead of the
# full history, so every computed indicator is faithful to what would have
# actually been visible on that historical date.
# ----------------------------------------------------------------------------
def compute_signals_for_snapshot(
    price_data: dict[str, pd.DataFrame], index_close_full: pd.Series, asof_date,
) -> pd.DataFrame:
    rows = []
    index_close_asof = index_close_full.loc[:asof_date]
    if len(index_close_asof) < 100:
        return pd.DataFrame()

    for ticker, df_full in price_data.items():
        df_asof = df_full.loc[:asof_date]
        if len(df_asof) < config.BACKTEST_MIN_HISTORY_DAYS:
            continue
        try:
            row = metrics_builder.build_metrics_row(
                ticker, df_asof, index_close_asof, sector_close=index_close_asof,
                sector_source="BACKTEST_NO_SECTOR",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Signal computation failed for %s @ %s: %s", ticker, asof_date, exc)
            continue
        row["ticker"] = ticker
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    metrics_df = pd.DataFrame(rows)
    metrics_df["fundamentally_qualified"] = True  # see module docstring — simplification
    metrics_df["sector"] = "BACKTEST"  # sector leadership isn't meaningfully testable without
                                        # historical sector labels; placeholder so compute_phase1_additions
                                        # doesn't error. Sector-leadership-specific results aren't tested here.
    metrics_df = sc.compute_composite(metrics_df)
    metrics_df = sc.compute_elite_compounder_score(metrics_df)
    metrics_df["RS_vs_Broad_Index_pct"] = metrics_df["rs_score"]
    metrics_df = sc.compute_phase1_additions(metrics_df)
    metrics_df = sc.compute_trend_death(metrics_df)
    return metrics_df


def compute_us_signals_for_snapshot_pit(
    price_data: dict[str, pd.DataFrame], index_close_full: pd.Series, asof_date,
    timeline: list[dict] | None = None,
) -> pd.DataFrame:
    """
    Point-in-time-correct variant of compute_signals_for_snapshot, for the
    US GARP strategy's backtest. Same technical-indicator computation
    (build_metrics_row, compute_composite, compute_elite_compounder_score —
    reused unchanged), but additionally restricts each snapshot to only the
    tickers that were ACTUALLY S&P 500 members on asof_date, via
    sp500_point_in_time.get_sp500_members_asof(). Without this filter, a
    stock added to the index in 2020 would incorrectly appear "eligible" in
    a 2010 snapshot just because its price history happens to exist that
    far back — a look-ahead-inclusion bias in the OPPOSITE direction from
    the survivorship bias sp500_point_in_time.py was built to fix, but
    just as capable of inflating backtest results if left unguarded.

    SCOPE — READ BEFORE INTERPRETING RESULTS: this tests ONLY the technical
    signals already in SIGNAL_DEFINITIONS (composite_score, EliteCompounder
    Score, obv_52w_high, trend_birth, etc.) against the REAL point-in-time
    S&P 500 membership and price history. It does NOT test the US GARP
    composite from scoring_us.py (Growth/Value/Quality/Technical). That
    composite depends on fundamentals.py's GARP fields (peg_ratio, ev_ebitda,
    fcf_trend_pct, margin trends), which — exactly like the existing
    "fundamentals are not historically reconstructed" simplification
    documented at the top of this module — can only be fetched as CURRENT
    trailing data from yfinance, not point-in-time historical data. Wiring
    scoring_us.compute_us_composite() into a 2008-2015 backtest using
    today's fundamentals would silently use 2026-known information to
    "predict" 2010 returns — a direct violation of this module's own
    no-lookahead-bias principle stated at the top of this file. DO NOT do
    this as a shortcut. The GARP composite backtest stays blocked until a
    genuine point-in-time fundamentals source (e.g. SEC EDGAR XBRL, see
    README_US.md open item) is built. This function exists to validate the
    TECHNICAL half of the strategy now, honestly, rather than fake-validate
    the whole thing.

    "sector" here is a best-effort join from CURRENT constituent labels
    (same caveat as get_point_in_time_sp500_universe's name/sector join) —
    fine for the Technical-only signals tested here (none of which use
    sector), but NOT sufficiently reliable to backtest the GARP Value
    bucket's within-sector EV/EBITDA ranking, which is another reason that
    composite isn't wired in here yet.
    """
    if timeline is None:
        timeline = spt.get_timeline()

    valid_tickers = set(spt.get_sp500_members_asof(asof_date, timeline=timeline))
    filtered_price_data = {t: df for t, df in price_data.items() if t in valid_tickers}

    return compute_signals_for_snapshot(filtered_price_data, index_close_full, asof_date)


def compute_smallmicro_signals_for_snapshot(
    price_data: dict[str, pd.DataFrame], index_close_full: pd.Series, asof_date,
) -> pd.DataFrame:
    """
    SmallMicroScore variant of compute_signals_for_snapshot above — same
    point-in-time-safe construction (build_metrics_row on df_asof =
    df_full.loc[:asof_date] only), but runs compute_liquidity_gate +
    compute_smallmicro_score + compute_smallmicro_strict_checklist instead
    of composite_score/EliteCompounderScore.

    SIMPLIFICATION — earnings acceleration is not historically reconstructed:
    same spirit as the module-level "fundamentals are not historically
    reconstructed" simplification above. eps_acceleration/revenue_acceleration
    require point-in-time-correct historical quarterly statements, which
    this backtest doesn't attempt to solve. compute_earnings_acceleration_score
    already handles a missing eps_acceleration column gracefully (returns
    NaN for every row, doesn't error), and compute_smallmicro_score's
    renormalization correctly redistributes that 10-point weight across the
    other 4 components rather than nulling the whole score — so this
    backtest faithfully tests OBV/RS/Near-52w-High/Liquidity, but NOT the
    Earnings Acceleration component's real-world predictive power. Keep
    this in mind reading results: a live score also has a chance to be
    pulled up or down by real earnings data this backtest can't replicate.

    SURVIVORSHIP CAVEAT — unlike NSE500/SP500, this universe is fetched
    fresh from TODAY's Smallcap 250 + Microcap 250 list (see universe.py;
    no historical reconstruction of index membership exists as a free data
    source). Testing today's list against years-old price history silently
    assumes these same ~250-500 names were already at this size tier back
    then, which isn't strictly true — NSE rebalances this list twice a
    year, so some names may have since grown into NSE500, and some may not
    have existed at this tier yet at earlier snapshot dates. This is a real
    limitation, not just a disclaimer — results here are more likely to be
    optimistic than a true historical small/microcap backtest would be,
    since today's list is itself a survivor of whatever happened since.
    """
    rows = []
    index_close_asof = index_close_full.loc[:asof_date]
    if len(index_close_asof) < 100:
        return pd.DataFrame()

    for ticker, df_full in price_data.items():
        df_asof = df_full.loc[:asof_date]
        if len(df_asof) < config.BACKTEST_MIN_HISTORY_DAYS:
            continue
        try:
            row = metrics_builder.build_metrics_row(
                ticker, df_asof, index_close_asof, sector_close=index_close_asof,
                sector_source="BACKTEST_NO_SECTOR",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Signal computation failed for %s @ %s: %s", ticker, asof_date, exc)
            continue
        row["ticker"] = ticker
        rows.append(row)

    if not rows:
        return pd.DataFrame()

    metrics_df = pd.DataFrame(rows)
    # No eps_acceleration/revenue_acceleration columns here — see docstring.
    metrics_df = sc.compute_earnings_acceleration_score(metrics_df)  # gracefully returns NaN for all rows
    metrics_df = sc.compute_liquidity_gate(metrics_df)
    metrics_df = sc.compute_smallmicro_score(metrics_df)
    metrics_df = sc.compute_smallmicro_strict_checklist(metrics_df)
    return metrics_df


# ----------------------------------------------------------------------------
# Signal definitions to test — (name, boolean mask function)
# ----------------------------------------------------------------------------
SIGNAL_DEFINITIONS = {
    "trend_birth": lambda df: df["trend_birth_flag"] == 1.0,
    "trend_death": lambda df: df["trend_death_flag"] == 1.0,
    "near_breakout_15pct": lambda df: df["near_breakout_15pct"] == 1.0,
    "volatility_compression": lambda df: df["volatility_compression"] == 1.0,
    "obv_52w_high": lambda df: df["obv_52w_high"] == 1.0,
    "macd_early_bullish": lambda df: df["macd_early_bullish"] == 1.0,
    "elite_score_above_80": lambda df: df["EliteCompounderScore"] > 80,
    "elite_score_above_65": lambda df: df["EliteCompounderScore"] > 65,
    "composite_score_above_85": lambda df: df["composite_score"] > 85,
    "composite_score_below_50": lambda df: df["composite_score"] < 50,
    "baseline_all_stocks": lambda df: pd.Series(True, index=df.index),
    # OBV Acceleration / Quiet Base (25-06-2026, chart-study) — EARLY-ENTRY
    # signal. REDESIGNED 26-06-2026: `qualifies` now depends ONLY on
    # acceleration — the quiet-price gate was dropped after backtest
    # evidence (two runs) showed it hurt performance (compound signal
    # underperformed obv_accel_subcondition_only alone both times). As a
    # direct result, "obv_acceleration_quiet_base" and
    # "obv_accel_subcondition_only" below are now EXACTLY THE SAME signal
    # — kept both rather than removing the duplicate, since seeing two
    # identical rows in a backtest run is itself a quick visual
    # confirmation that the redesign took effect correctly.
    "obv_acceleration_quiet_base": lambda df: df["obv_acceleration_quiet_base"] == "🟢",
    # obv_quiet_subcondition_only is STILL useful as standalone diagnostic
    # context (what would "price was quiet" alone have predicted?), even
    # though it's no longer a constraint on the compound flag above.
    "obv_accel_subcondition_only": lambda df: df["obv_acceleration_basis"].isin(
        ["accelerating_quiet_base", "accelerating_but_price_moved"]
    ),
    "obv_quiet_subcondition_only": lambda df: df["obv_acceleration_basis"].isin(
        ["accelerating_quiet_base", "quiet_but_not_accelerating"]
    ),
    # OBV Calm Continuation (RELABELED 26-06-2026, was "obv_divergence_decaying"
    # — a caution flag). Two independent backtest runs (different ticker
    # counts/lookback years, not a re-run of the same data) both showed
    # this predicting STRONG POSITIVE excess return — the opposite of the
    # original caution hypothesis, confirmed rather than guessed. Real-data
    # mechanism check (diagnostics/divergence_decaying_mechanism_check.py)
    # found flagged stocks run calmer AND already have stronger RS than
    # average — but ALSO found a real sector-concentration risk (Healthcare
    # ~3.5x overrepresented in one live check). Read this signal's results
    # the SAME direction as the other bullish signals in this file now
    # (positive excess return is the hoped-for/expected result) — but treat
    # any strength here with the sector caveat in mind, especially if a
    # given backtest run's universe happens to be Healthcare-heavy.
    "obv_calm_continuation": lambda df: df["obv_calm_continuation"] == "🟢",
    # Only ONE sub-condition is cleanly extractable from the basis string —
    # "price still rising" (calm_continuation + obv_still_strong both have
    # price rising; price_not_rising and no_obv_signal don't). "OBV
    # sustained-decayed regardless of price" can't cleanly be isolated this
    # way: obv_calm_continuation()'s branching collapses BOTH "OBV decayed,
    # price not rising" and "OBV didn't decay, price not rising" into the
    # same "price_not_rising" basis value once price isn't rising — the
    # function doesn't preserve which case applies in that branch. Testing
    # only the cleanly-isolable half rather than guessing at the other.
    "obv_calm_price_rising_subcondition_only": lambda df: df["obv_calm_continuation_basis"].isin(
        ["calm_continuation", "obv_still_strong"]
    ),
    # RESOLVED (26-06-2026): a 2nd confirming run (full ~500-ticker NSE500
    # universe, 5y lookback — genuinely different from the 1st run's
    # 300-ticker/3y, not a re-run of the same data) showed the SAME strong
    # positive result (+33.78pp at 12m, n=270, vs. the 1st run's +33.08pp,
    # n=104) — close enough across two independent samples to treat as a
    # real, confirmed finding rather than a fluke, clearing this project's
    # own two-run standard. Relabeled accordingly above. A 3rd run
    # (29-06-2026) held again (+34.18pp, n=271) — three consecutive runs
    # within a 1.1pp band of each other. The sector-concentration caveat
    # (not yet controlled for) remains the main open question, not
    # whether the positive result itself is real.

    # Range Position Divergence (29-06-2026, see README) — a genuinely
    # different metric from obv_price_divergence/obv_calm_continuation
    # above: a snapshot comparison of price_52w_range_pct vs
    # obv_52w_range_pct, no peak-date anchor at all. Percentile-ranked
    # WITHIN each snapshot (same pattern as the SmallMicroScore component
    # signals below) rather than a fixed magnitude threshold, since
    # there's no existing evidence yet for what a "meaningful" raw value
    # of this metric looks like across market regimes.
    #
    # RESULT (29-06-2026, NSE500, full universe, 5y lookback): direction
    # was correct (obv_ahead_top_decile +20.20pp 12m excess > price_ahead
    # _bottom_decile +13.92pp, a real 6.28pp gap — the basic "OBV leading
    # is better" intuition held, unlike obv_calm_continuation's complete
    # reversal above) BUT both buckets underperformed baseline (+22.39pp)
    # — likely because this is a single-day snapshot with no persistence
    # requirement, unlike obv_calm_continuation's 15-of-20-day sustained
    # check. Deliberately left as a snapshot rather than redesigned to
    # require sustained persistence — this column's job is to be a quick
    # eyeball reference, not a backtested trading signal, so it doesn't
    # need to clear that bar. Revisit with the sustained-pattern treatment
    # later if you ever want to push it toward being a real standalone signal.
    "range_position_divergence_obv_ahead_top_decile": lambda df: sc._pct_rank(df["range_position_divergence"]) >= config.SMALLMICRO_STRICT_TOP_DECILE_THRESHOLD,
    "range_position_divergence_price_ahead_bottom_decile": lambda df: sc._pct_rank(df["range_position_divergence"]) <= (100 - config.SMALLMICRO_STRICT_TOP_DECILE_THRESHOLD),
}

# SmallMicroScore signals — separate dict, used only when
# config.BACKTEST_UNIVERSE == "NSE_SmallMicro". Component-level signals
# isolate each piece of the formula (the same way the original backtest
# discovered OBV was trustworthy and volatility compression wasn't — you
# can't learn that from the composite alone). Top-decile thresholds use
# config.SMALLMICRO_STRICT_TOP_DECILE_THRESHOLD for consistency with the
# live strict checklist, even for components the live score doesn't gate
# on at that bar (e.g. liquidity is only WEIGHTED live, never gated at the
# 90th percentile — tested here anyway, for a fair side-by-side against
# the components that ARE gated that way).
SMALLMICRO_SIGNAL_DEFINITIONS = {
    # Component-level — isolate each piece of the formula
    "smallmicro_obv_top_decile": lambda df: df["obv_52w_range_pct"] >= config.SMALLMICRO_STRICT_TOP_DECILE_THRESHOLD,
    "smallmicro_rs_top_decile": lambda df: sc._pct_rank(df["rs_score"]) >= config.SMALLMICRO_STRICT_TOP_DECILE_THRESHOLD,
    "smallmicro_near_52w_high": lambda df: df["near_breakout_15pct"] == 1.0,
    "smallmicro_earnings_accelerating": lambda df: df["flag_earnings_accelerating"] == "🟢",
    "smallmicro_high_liquidity": lambda df: sc._pct_rank(df["avg_daily_traded_value"]) >= config.SMALLMICRO_STRICT_TOP_DECILE_THRESHOLD,
    # Composite-level — the actual outputs you'd act on
    "smallmicro_strict_pass": lambda df: df["smallmicro_strict_pass"] == True,  # noqa: E712
    "smallmicro_score_above_70": lambda df: df["smallmicro_score"] > 70,
    "smallmicro_score_above_50": lambda df: df["smallmicro_score"] > 50,
    "baseline_all_smallmicro": lambda df: pd.Series(True, index=df.index),
    # OBV Acceleration / Quiet Base + OBV Divergence Decaying (25-06-2026,
    # chart-study, unvalidated) — same signals as SIGNAL_DEFINITIONS above,
    # tested here too since both are computed by build_metrics_row() for
    # every universe, including NSE_SmallMicro. obv_calm_continuation was
    # RELABELED 26-06-2026 (was a caution flag, "obv_divergence_decaying")
    # after two confirming NSE500 backtest runs both showed it predicting
    # POSITIVE excess return — see README for the full evidence trail and
    # the sector-concentration caveat that wasn't yet tested specifically
    # on the SmallMicro universe.
    "smallmicro_obv_acceleration_quiet_base": lambda df: df["obv_acceleration_quiet_base"] == "🟢",
    "smallmicro_obv_calm_continuation": lambda df: df["obv_calm_continuation"] == "🟢",
    # Range Position Divergence (29-06-2026, see README) — same signals as
    # SIGNAL_DEFINITIONS above. First NSE500 run: direction correct (OBV-
    # ahead beat price-ahead by ~6.3pp) but both buckets underperformed
    # baseline — kept as a snapshot eyeball column, not redesigned for
    # sustained persistence. Still untested specifically on NSE_SmallMicro.
    "smallmicro_range_position_divergence_obv_ahead_top_decile": lambda df: sc._pct_rank(df["range_position_divergence"]) >= config.SMALLMICRO_STRICT_TOP_DECILE_THRESHOLD,
    "smallmicro_range_position_divergence_price_ahead_bottom_decile": lambda df: sc._pct_rank(df["range_position_divergence"]) <= (100 - config.SMALLMICRO_STRICT_TOP_DECILE_THRESHOLD),
}


def run_backtest(
    tickers: list[str], price_data: dict[str, pd.DataFrame], index_close: pd.Series,
    snapshot_dates: list, horizons_days: dict[str, int],
    snapshot_fn=compute_signals_for_snapshot, signal_definitions: dict = None,
) -> pd.DataFrame:
    """
    Returns a long-format DataFrame: one row per (ticker, snapshot_date)
    with every signal's boolean value and every horizon's forward return.

    snapshot_fn and signal_definitions default to the original NSE500/SP500
    behavior (compute_signals_for_snapshot + SIGNAL_DEFINITIONS) so existing
    backtests are unaffected. Pass compute_smallmicro_signals_for_snapshot +
    SMALLMICRO_SIGNAL_DEFINITIONS for the NSE_SmallMicro tier.
    """
    if signal_definitions is None:
        signal_definitions = SIGNAL_DEFINITIONS

    records = []
    for i, asof_date in enumerate(snapshot_dates):
        logger.info("Snapshot %d/%d: %s", i + 1, len(snapshot_dates), asof_date.date())
        metrics_df = snapshot_fn(price_data, index_close, asof_date)
        if metrics_df.empty:
            continue

        signal_flags = {name: fn(metrics_df) for name, fn in signal_definitions.items()}

        for idx, row in metrics_df.iterrows():
            ticker = row["ticker"]
            price_series = price_data[ticker]["Close"]
            rec = {"ticker": ticker, "asof_date": asof_date}
            for h_name, h_days in horizons_days.items():
                rec[f"fwd_return_{h_name}"] = forward_return(price_series, asof_date, h_days)
            for sig_name, mask in signal_flags.items():
                rec[sig_name] = bool(mask.loc[idx])
            records.append(rec)

    # Benchmark (index) forward return on the same snapshot dates, for context
    bench_records = []
    for asof_date in snapshot_dates:
        rec = {"asof_date": asof_date}
        for h_name, h_days in horizons_days.items():
            rec[f"fwd_return_{h_name}"] = forward_return(index_close, asof_date, h_days)
        bench_records.append(rec)

    long_df = pd.DataFrame(records)
    bench_df = pd.DataFrame(bench_records)
    return long_df, bench_df


def aggregate_results(long_df: pd.DataFrame, bench_df: pd.DataFrame, horizons_days: dict[str, int], signal_definitions: dict = None) -> pd.DataFrame:
    """Per-signal summary: sample size, mean/median/hit-rate per horizon, vs benchmark excess return."""
    if signal_definitions is None:
        signal_definitions = SIGNAL_DEFINITIONS
    if long_df.empty:
        return pd.DataFrame()

    summary_rows = []
    for sig_name in signal_definitions:
        subset = long_df[long_df[sig_name] == True]  # noqa: E712
        row = {"signal": sig_name, "sample_size": len(subset)}
        for h_name in horizons_days:
            col = f"fwd_return_{h_name}"
            vals = subset[col].dropna()
            bench_vals = bench_df[col].dropna()
            row[f"{h_name}_mean_return_pct"] = round(vals.mean(), 2) if len(vals) else np.nan
            row[f"{h_name}_median_return_pct"] = round(vals.median(), 2) if len(vals) else np.nan
            row[f"{h_name}_hit_rate_pct"] = round((vals > 0).mean() * 100, 1) if len(vals) else np.nan
            row[f"{h_name}_benchmark_mean_pct"] = round(bench_vals.mean(), 2) if len(bench_vals) else np.nan
            row[f"{h_name}_excess_vs_benchmark_pct"] = (
                round(row[f"{h_name}_mean_return_pct"] - row[f"{h_name}_benchmark_mean_pct"], 2)
                if len(vals) and len(bench_vals) else np.nan
            )
        summary_rows.append(row)

    return pd.DataFrame(summary_rows)


def main():
    universe_label = config.BACKTEST_UNIVERSE
    if config.BACKTEST_DATE_RANGE_MODE:
        logger.info(
            "Backtest starting — universe=%s, DATE-RANGE MODE: %s to %s",
            universe_label, config.BACKTEST_DATE_RANGE_START, config.BACKTEST_DATE_RANGE_END,
        )
    else:
        logger.info("Backtest starting — universe=%s, lookback=%dy", universe_label, config.BACKTEST_LOOKBACK_YEARS)

    if universe_label == "NSE500":
        uni_df = universe.get_nse500_universe()
        index_ticker = config.INDEX_TICKER_NSE
        snapshot_fn = compute_signals_for_snapshot
        signal_definitions = SIGNAL_DEFINITIONS
        results_tab_name = config.BACKTEST_RESULTS_TAB_NAME
    elif universe_label == "NSE_SmallMicro":
        uni_df = universe.get_nse_smallmicro_universe()
        index_ticker = config.INDEX_TICKER_NSE
        snapshot_fn = compute_smallmicro_signals_for_snapshot
        signal_definitions = SMALLMICRO_SIGNAL_DEFINITIONS
        results_tab_name = config.BACKTEST_SMALLMICRO_RESULTS_TAB_NAME
        logger.warning(
            "NSE_SmallMicro backtest: survivorship caveat applies — this universe is "
            "fetched fresh from TODAY's Smallcap 250 + Microcap 250 list, not "
            "reconstructed historically. See compute_smallmicro_signals_for_snapshot's "
            "docstring before trusting these results. Earnings Acceleration component "
            "also isn't tested here (no point-in-time historical quarterly data) — "
            "see the same docstring."
        )
    elif universe_label == "SP500_PointInTime":
        if not config.BACKTEST_DATE_RANGE_MODE:
            logger.error(
                "SP500_PointInTime requires BACKTEST_DATE_RANGE_MODE=True with "
                "BACKTEST_DATE_RANGE_START/END set (e.g. the two-era GARP backtest "
                "windows from README_US.md) — point-in-time membership needs an "
                "explicit window to know which tickers to fetch. Falling back to "
                "today's constituent list (same as plain 'SP500') for this run."
            )
            uni_df = universe.get_sp500_universe()
            snapshot_fn = compute_signals_for_snapshot
        else:
            timeline = spt.get_timeline()
            membership = spt.get_all_members_in_window(
                config.BACKTEST_DATE_RANGE_START, config.BACKTEST_DATE_RANGE_END, timeline=timeline,
            )
            uni_df = pd.DataFrame({"yf_ticker": list(membership.keys())})
            snapshot_fn = functools.partial(compute_us_signals_for_snapshot_pit, timeline=timeline)
            logger.info(
                "SP500_PointInTime: %d unique tickers were ever S&P 500 members "
                "during %s to %s (fetching all of them; each snapshot date will "
                "then be restricted to only the tickers actually in the index on "
                "that specific date — see compute_us_signals_for_snapshot_pit).",
                len(membership), config.BACKTEST_DATE_RANGE_START, config.BACKTEST_DATE_RANGE_END,
            )
        index_ticker = config.INDEX_TICKER_US
        signal_definitions = SIGNAL_DEFINITIONS
        results_tab_name = f"{config.BACKTEST_RESULTS_TAB_NAME}_PointInTime"
        logger.warning(
            "SP500_PointInTime tests TECHNICAL signals only (composite_score, "
            "EliteCompounderScore, obv_52w_high, etc.) against real historical "
            "index membership. It does NOT test the US GARP composite "
            "(scoring_us.py) — that needs point-in-time fundamentals, which "
            "don't exist yet. See compute_us_signals_for_snapshot_pit's "
            "docstring before interpreting these results as validating the "
            "full US strategy."
        )
    else:
        uni_df = universe.get_sp500_universe()
        index_ticker = config.INDEX_TICKER_US
        snapshot_fn = compute_signals_for_snapshot
        signal_definitions = SIGNAL_DEFINITIONS
        results_tab_name = config.BACKTEST_RESULTS_TAB_NAME
        if universe_label == "SP500":
            logger.warning(
                "SP500 backtest (NOT point-in-time): this universe is fetched fresh "
                "from TODAY's constituent list, same survivorship-bias caveat as "
                "NSE_SmallMicro below. Use BACKTEST_UNIVERSE='SP500_PointInTime' "
                "instead for a survivorship-bias-corrected run — see "
                "sp500_point_in_time.py and README_US.md."
            )

    tickers = uni_df["yf_ticker"].tolist()
    if config.BACKTEST_MAX_TICKERS:
        tickers = tickers[: config.BACKTEST_MAX_TICKERS]
    logger.info("Backtest universe size: %d tickers", len(tickers))

    if config.BACKTEST_DATE_RANGE_MODE:
        # Fetch from (start - warmup buffer) through end, so indicators have
        # real history before the FIRST snapshot date, not just within the
        # window itself — same purpose as the "+2y buffer" in the other
        # branch below, just date-based instead of relative.
        warmup_start = (
            pd.Timestamp(config.BACKTEST_DATE_RANGE_START)
            - pd.DateOffset(years=int(config.BACKTEST_DATE_RANGE_WARMUP_YEARS), months=int((config.BACKTEST_DATE_RANGE_WARMUP_YEARS % 1) * 12))
        ).strftime("%Y-%m-%d")
        logger.info(
            "Date-range backtest mode: window %s to %s (fetching from %s for warmup)",
            config.BACKTEST_DATE_RANGE_START, config.BACKTEST_DATE_RANGE_END, warmup_start,
        )
        price_data = data_fetch.fetch_price_history_range(tickers, warmup_start, config.BACKTEST_DATE_RANGE_END)
        index_df = data_fetch.fetch_index_history_range(index_ticker, warmup_start, config.BACKTEST_DATE_RANGE_END)
        index_close = index_df["Close"]

        all_dates = index_close.index
        end_buffer_days = max(config.BACKTEST_HORIZONS_DAYS.values())
        window_end = pd.Timestamp(config.BACKTEST_DATE_RANGE_END)
        # Snapshot dates must stay within [start, end] AND leave room before
        # the absolute end of FETCHED data for the longest forward-return
        # horizon to resolve — if the window's own end is close to the last
        # fetched date, some snapshots near the window's end may not get a
        # full 12m forward return and will show up as fewer non-NaN rows in
        # the aggregated results, which is expected and not a bug.
        usable_dates = all_dates[all_dates <= window_end]
        usable_dates = usable_dates[: -end_buffer_days] if len(usable_dates) > end_buffer_days else usable_dates
        snapshot_dates = pd.date_range(
            start=config.BACKTEST_DATE_RANGE_START, end=config.BACKTEST_DATE_RANGE_END, freq=config.BACKTEST_SNAPSHOT_FREQ,
        )
        snapshot_dates = [d for d in snapshot_dates if d in all_dates] or \
            [all_dates[all_dates.searchsorted(d)] for d in snapshot_dates if all_dates.searchsorted(d) < len(all_dates)]
        # Drop any snapshot dates that fall in the warmup buffer (shouldn't
        # happen given date_range's own start=, but defensive) or past the
        # forward-return-safe cutoff computed above.
        snapshot_dates = [d for d in snapshot_dates if d >= pd.Timestamp(config.BACKTEST_DATE_RANGE_START) and (len(usable_dates) == 0 or d <= usable_dates[-1])]
    else:
        # Fetch with extended history for backtest purposes
        original_period = config.PRICE_HISTORY_PERIOD
        config.PRICE_HISTORY_PERIOD = f"{config.BACKTEST_LOOKBACK_YEARS + 2}y"  # +2y buffer for indicator warmup
        price_data = data_fetch.fetch_price_history(tickers)
        index_df = data_fetch.fetch_index_history(index_ticker)
        config.PRICE_HISTORY_PERIOD = original_period
        index_close = index_df["Close"]

        # Monthly snapshot dates over the configured lookback, leaving enough
        # room at the end for the longest forward-return horizon to resolve
        all_dates = index_close.index
        end_buffer_days = max(config.BACKTEST_HORIZONS_DAYS.values())
        usable_dates = all_dates[: -end_buffer_days] if len(all_dates) > end_buffer_days else all_dates
        start_date = usable_dates[-1] - pd.DateOffset(years=config.BACKTEST_LOOKBACK_YEARS)
        snapshot_dates = pd.date_range(start=start_date, end=usable_dates[-1], freq=config.BACKTEST_SNAPSHOT_FREQ)
        snapshot_dates = [d for d in snapshot_dates if d in all_dates] or \
            [all_dates[all_dates.searchsorted(d)] for d in snapshot_dates if all_dates.searchsorted(d) < len(all_dates)]

    if not snapshot_dates:
        logger.error(
            "No usable snapshot dates — in date-range mode this usually means the "
            "window is too close to BACKTEST_DATE_RANGE_END for any forward-return "
            "horizon to resolve, or too close to the fetch start for indicator warmup. "
            "Widen the window or increase BACKTEST_DATE_RANGE_WARMUP_YEARS."
        )
        return
    logger.info("Running %d snapshot dates from %s to %s", len(snapshot_dates), snapshot_dates[0].date(), snapshot_dates[-1].date())

    long_df, bench_df = run_backtest(
        tickers, price_data, index_close, snapshot_dates, config.BACKTEST_HORIZONS_DAYS,
        snapshot_fn=snapshot_fn, signal_definitions=signal_definitions,
    )
    summary_df = aggregate_results(long_df, bench_df, config.BACKTEST_HORIZONS_DAYS, signal_definitions=signal_definitions)

    logger.info("Backtest complete. Signal summary:\n%s", summary_df.to_string())

    if config.BACKTEST_DATE_RANGE_MODE:
        # Date-stamped, distinct from the normal-mode filenames/tab — running
        # a date-range backtest should never silently overwrite a normal
        # "last N years" run, or a different date-range run on a different window.
        range_tag = f"{config.BACKTEST_DATE_RANGE_START}_to_{config.BACKTEST_DATE_RANGE_END}"
        out_path = f"backtest_results_{universe_label}_{range_tag}.csv"
        results_tab_name = f"{results_tab_name}_{range_tag}"
    else:
        out_path = "backtest_results.csv" if universe_label != "NSE_SmallMicro" else "backtest_results_smallmicro.csv"

    summary_df.to_csv(out_path, index=False)
    logger.info("Saved summary to %s", out_path)

    if config.GOOGLE_SHEET_ID:
        try:
            sheets_export.export_to_sheets({results_tab_name: summary_df})
            logger.info("Exported summary to Google Sheets tab '%s'", results_tab_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not export to Google Sheets (CSV was still saved): %s", exc)


if __name__ == "__main__":
    main()
