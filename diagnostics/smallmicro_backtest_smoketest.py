"""
Standalone test for the NSE_SmallMicro backtest path — NOT part of the
daily pipeline or the real backtest_workflow.yml. Exercises
compute_smallmicro_signals_for_snapshot, run_backtest, and
aggregate_results end-to-end with synthetic multi-year price data, since
this sandbox can't reach live NSE/Yahoo data (see other diagnostics/
scripts for the same constraint).

Run locally or in CI with: python diagnostics/smallmicro_backtest_smoketest.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

import backtest
import config

np.random.seed(42)

N_TICKERS = 15
N_DAYS = 900  # ~3.5 years of trading days, enough for a few snapshot dates + warmup + forward-return horizons


def fake_ohlcv(n_days: int, seed_offset: int) -> pd.DataFrame:
    rng = np.random.RandomState(seed_offset)
    dates = pd.bdate_range("2022-01-03", periods=n_days)
    drift = rng.uniform(-0.0003, 0.0008)  # some tickers trend up, some down/flat
    returns = rng.normal(drift, 0.025, n_days)
    close = 100 * np.cumprod(1 + returns)
    high = close * (1 + np.abs(rng.normal(0, 0.01, n_days)))
    low = close * (1 - np.abs(rng.normal(0, 0.01, n_days)))
    open_ = close * (1 + rng.normal(0, 0.005, n_days))
    volume = rng.randint(50_000, 800_000, n_days).astype(float)
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=dates)


price_data = {f"SMALLCAP{i}.NS": fake_ohlcv(N_DAYS, seed_offset=i) for i in range(N_TICKERS)}
index_close = fake_ohlcv(N_DAYS, seed_offset=999)["Close"]
index_close.name = "Close"

# A handful of snapshot dates, well clear of both ends for warmup + the
# longest forward-return horizon to resolve
all_dates = index_close.index
horizons = {"1m": 21, "3m": 63}  # shorter horizons than the real default, this is just a smoketest
end_buffer = max(horizons.values())
usable_dates = all_dates[config.BACKTEST_MIN_HISTORY_DAYS: -end_buffer]
snapshot_dates = list(usable_dates[:: len(usable_dates) // 5])[:5]

print(f"Testing {N_TICKERS} synthetic tickers across {len(snapshot_dates)} snapshot dates")
print(f"Snapshot dates: {[d.date() for d in snapshot_dates]}")

# ── Test 1: compute_smallmicro_signals_for_snapshot runs cleanly and
# produces all expected columns ──
sample_df = backtest.compute_smallmicro_signals_for_snapshot(price_data, index_close, snapshot_dates[0])
assert not sample_df.empty, "compute_smallmicro_signals_for_snapshot returned empty on a normal snapshot"
required_cols = {
    "smallmicro_score", "smallmicro_category", "smallmicro_score_basis",
    "smallmicro_strict_pass", "smallmicro_strict_fail_reasons",
    "obv_52w_range_pct", "rs_score", "near_breakout_15pct",
    "avg_daily_traded_value", "flag_earnings_accelerating",
}
missing = required_cols - set(sample_df.columns)
assert not missing, f"Missing expected columns from a single snapshot: {missing}"
print(f"\nSingle-snapshot test passed: {len(sample_df)} tickers, all {len(required_cols)} required columns present.")

# Confirm the earnings-acceleration simplification is behaving as documented
# (no eps_acceleration column fed in -> NaN score for every row, not an error)
assert sample_df["earnings_acceleration_score"].isna().all(), (
    "Expected earnings_acceleration_score to be all-NaN in the backtest "
    "(no historical fundamentals reconstructed) — got real values, "
    "something upstream changed unexpectedly."
)
print("Confirmed: earnings_acceleration_score is all-NaN as documented (no historical fundamentals).")

# ── Test 2: full run_backtest + aggregate_results pipeline ──
long_df, bench_df = backtest.run_backtest(
    list(price_data.keys()), price_data, index_close, snapshot_dates, horizons,
    snapshot_fn=backtest.compute_smallmicro_signals_for_snapshot,
    signal_definitions=backtest.SMALLMICRO_SIGNAL_DEFINITIONS,
)
assert not long_df.empty, "run_backtest produced an empty long_df"
for sig_name in backtest.SMALLMICRO_SIGNAL_DEFINITIONS:
    assert sig_name in long_df.columns, f"Signal column missing from long_df: {sig_name}"
print(f"\nrun_backtest passed: long_df shape {long_df.shape}, all 9 signal columns present.")

summary_df = backtest.aggregate_results(long_df, bench_df, horizons, signal_definitions=backtest.SMALLMICRO_SIGNAL_DEFINITIONS)
assert not summary_df.empty, "aggregate_results produced an empty summary"
assert set(summary_df["signal"]) == set(backtest.SMALLMICRO_SIGNAL_DEFINITIONS.keys()), "Signal set mismatch in summary"
print(f"\n=== Summary ===\n{summary_df.to_string()}")

# Sanity: baseline_all_smallmicro's sample_size should be the largest (every
# ticker at every snapshot date counts toward it)
baseline_n = summary_df.loc[summary_df["signal"] == "baseline_all_smallmicro", "sample_size"].iloc[0]
other_max = summary_df.loc[summary_df["signal"] != "baseline_all_smallmicro", "sample_size"].max()
assert baseline_n >= other_max, "baseline_all_smallmicro should have the largest or equal sample size"
print(f"\nConfirmed: baseline sample size ({baseline_n}) >= every other signal's sample size ({other_max}).")

# smallmicro_strict_pass should be a SUBSET in sample size of score_above_70,
# which should be a subset of score_above_50, on any reasonably-sized sample
# (not a hard mathematical guarantee row-by-row, since strict_pass requires
# conditions outside the score formula too, but worth a glance)
n_strict = summary_df.loc[summary_df["signal"] == "smallmicro_strict_pass", "sample_size"].iloc[0]
n_70 = summary_df.loc[summary_df["signal"] == "smallmicro_score_above_70", "sample_size"].iloc[0]
n_50 = summary_df.loc[summary_df["signal"] == "smallmicro_score_above_50", "sample_size"].iloc[0]
print(f"\nSample sizes — strict_pass: {n_strict}, score>70: {n_70}, score>50: {n_50} (informational, not asserted)")

print("\nSMOKETEST PASSED - no exceptions")
