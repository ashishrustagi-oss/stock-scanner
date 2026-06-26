"""
Standalone test for the main (NSE500/SP500-style) backtest path — NOT
part of the daily pipeline or backtest_workflow.yml. Mirrors
smallmicro_backtest_smoketest.py's approach but for compute_signals_for_snapshot
+ SIGNAL_DEFINITIONS rather than the SmallMicro variants, since that path
had never had a synthetic-data smoketest before (the original NSE500
backtest only ever ran against real, live data).

Added specifically to verify the two new chart-study signals
(obv_acceleration_quiet_base, obv_divergence_decaying, added 25-06-2026)
wire correctly into the main backtest path — both are computed inside
metrics_builder.build_metrics_row(), so no changes to
compute_signals_for_snapshot() itself were needed, but that assumption is
exactly the kind of thing worth verifying mechanically rather than trusting.

Run locally or in CI with: python diagnostics/main_backtest_smoketest.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import backtest

np.random.seed(42)

N_TICKERS = 15
N_DAYS = 900  # ~3.5 years of trading days, enough for a few snapshot dates + warmup + forward-return horizons


def fake_ohlcv(n_days: int, seed_offset: int) -> pd.DataFrame:
    rng = np.random.RandomState(seed_offset)
    dates = pd.bdate_range("2022-01-03", periods=n_days)
    drift = rng.uniform(-0.0003, 0.0008)
    returns = rng.normal(drift, 0.02, n_days)
    close = 100 * np.cumprod(1 + returns)
    high = close * (1 + np.abs(rng.normal(0, 0.01, n_days)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, n_days)))
    open_ = close * (1 + rng.normal(0, 0.005, n_days))
    volume = rng.randint(50_000, 800_000, n_days).astype(float)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=dates)


price_data = {f"FAKE{i}.NS": fake_ohlcv(N_DAYS, seed_offset=i) for i in range(N_TICKERS)}
index_close = fake_ohlcv(N_DAYS, seed_offset=999)["Close"]
index_close.name = "Close"

# Snapshot dates well clear of both ends, so every snapshot has enough
# trailing history (the OBV pattern signals need ~150+ trading days of
# lookback for obv_slope_series) and enough room for forward returns to resolve
all_dates = index_close.index
horizons = {"1m": 21, "3m": 63}
end_buffer = max(horizons.values())
usable_dates = all_dates[200:-end_buffer]
snapshot_dates = list(usable_dates[:: len(usable_dates) // 5])[:5]

print(f"Testing {N_TICKERS} synthetic tickers across {len(snapshot_dates)} snapshot dates")
print(f"Snapshot dates: {[d.date() for d in snapshot_dates]}")

# ── Test 1: compute_signals_for_snapshot runs cleanly and produces every
# expected column, including the two new chart-study signals ──
sample_df = backtest.compute_signals_for_snapshot(price_data, index_close, snapshot_dates[-1])
assert not sample_df.empty, "compute_signals_for_snapshot returned empty on a normal snapshot"
required_cols = {
    "composite_score", "EliteCompounderScore", "trend_birth_flag", "trend_death_flag",
    "obv_52w_high", "macd_early_bullish",
    "obv_acceleration_quiet_base", "obv_acceleration_basis",
    "obv_divergence_decaying", "obv_divergence_decay_basis",
    "obv_slope_42d", "obv_slope_42d_recent_high",
}
missing = required_cols - set(sample_df.columns)
assert not missing, f"Missing expected columns from a single snapshot: {missing}"
print(f"\nSingle-snapshot test passed: {len(sample_df)} tickers, all {len(required_cols)} required columns present.")

# ── Test 2: full run_backtest + aggregate_results pipeline, including the
# new signals and their sub-condition isolations ──
long_df, bench_df = backtest.run_backtest(
    list(price_data.keys()), price_data, index_close, snapshot_dates, horizons,
    snapshot_fn=backtest.compute_signals_for_snapshot,
    signal_definitions=backtest.SIGNAL_DEFINITIONS,
)
assert not long_df.empty, "run_backtest produced an empty long_df"
for sig_name in backtest.SIGNAL_DEFINITIONS:
    assert sig_name in long_df.columns, f"Signal column missing from long_df: {sig_name}"
print(f"\nrun_backtest passed: long_df shape {long_df.shape}, all {len(backtest.SIGNAL_DEFINITIONS)} signal columns present.")

summary_df = backtest.aggregate_results(long_df, bench_df, horizons, signal_definitions=backtest.SIGNAL_DEFINITIONS)
assert not summary_df.empty, "aggregate_results produced an empty summary"
assert set(summary_df["signal"]) == set(backtest.SIGNAL_DEFINITIONS.keys()), "Signal set mismatch in summary"
print(f"\n=== Summary ===\n{summary_df[['signal', 'sample_size', '1m_mean_return_pct', '1m_excess_vs_benchmark_pct']].to_string()}")

# Sanity: baseline_all_stocks should have the largest or equal sample size
baseline_n = summary_df.loc[summary_df["signal"] == "baseline_all_stocks", "sample_size"].iloc[0]
other_max = summary_df.loc[summary_df["signal"] != "baseline_all_stocks", "sample_size"].max()
assert baseline_n >= other_max, "baseline_all_stocks should have the largest or equal sample size"
print(f"\nConfirmed: baseline sample size ({baseline_n}) >= every other signal's sample size ({other_max}).")

# Sub-condition consistency: the compound flag's sample size should never
# exceed either of its own sub-conditions' sample sizes (a stock flagged
# by the compound condition must also satisfy each half individually)
n_compound = summary_df.loc[summary_df["signal"] == "obv_acceleration_quiet_base", "sample_size"].iloc[0]
n_accel_sub = summary_df.loc[summary_df["signal"] == "obv_accel_subcondition_only", "sample_size"].iloc[0]
n_quiet_sub = summary_df.loc[summary_df["signal"] == "obv_quiet_subcondition_only", "sample_size"].iloc[0]
# REDESIGN (26-06-2026): obv_acceleration_quiet_base's `qualifies` now
# depends ONLY on the acceleration condition (the quiet-price gate was
# dropped after backtest evidence showed it hurt performance — see
# README). So the invariant changed too: the compound flag must now equal
# obv_accel_subcondition_only EXACTLY (same underlying condition, same
# basis-string mapping for "is_accelerating"), not be a strict subset of
# both sub-conditions the way it was before this redesign. It's no longer
# expected to relate to the quiet sub-condition at all.
assert n_compound == n_accel_sub, (
    f"Compound flag ({n_compound}) should now EXACTLY match the acceleration "
    f"sub-condition ({n_accel_sub}) post-redesign — qualifies no longer depends on quiet-price."
)
print(f"\nConfirmed: obv_acceleration_quiet_base ({n_compound}) == obv_accel_subcondition_only "
      f"({n_accel_sub}) exactly — correct post-redesign behavior (quiet-price gate dropped, "
      f"quiet sub-condition sample size {n_quiet_sub} is no longer a constraint on the compound flag).")

n_decay_compound = summary_df.loc[summary_df["signal"] == "obv_divergence_decaying", "sample_size"].iloc[0]
n_decay_sub = summary_df.loc[summary_df["signal"] == "obv_decay_price_rising_subcondition_only", "sample_size"].iloc[0]
assert n_decay_compound <= n_decay_sub, f"Compound decay flag ({n_decay_compound}) exceeds its price-rising sub-condition ({n_decay_sub})"
print(f"Confirmed: obv_divergence_decaying ({n_decay_compound}) <= its price-rising sub-condition ({n_decay_sub}).")

print("\nSMOKETEST PASSED - no exceptions")
