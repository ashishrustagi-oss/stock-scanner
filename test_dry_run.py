"""
Dry-run test: monkeypatches data_fetch / fundamentals / sector_data / sheets_export
with synthetic data so the full main.py pipeline can be validated without live
network access. Not part of the deployed package — delete or ignore in prod.
"""
import numpy as np
import pandas as pd

import config
import data_fetch
import fundamentals as fnd
import sector_data
import shareholding
import sheets_export
import universe

np.random.seed(7)

SECTORS = ["Banking", "Information Technology", "Pharmaceuticals", "Automobiles", "Energy"]


def fake_price_df(n=400, seed_offset=0):
    rng = np.random.RandomState(seed_offset)
    dates = pd.bdate_range("2023-01-01", periods=n)
    close = 100 + np.cumsum(rng.normal(0.05, 1.5, n))
    high = close + rng.uniform(0.1, 1.5, n)
    low = close - rng.uniform(0.1, 1.5, n)
    openp = close + rng.normal(0, 0.5, n)
    vol = rng.randint(100000, 900000, n)
    return pd.DataFrame({"Open": openp, "High": high, "Low": low, "Close": close, "Volume": vol}, index=dates)


def fake_fetch_price_history(yf_tickers):
    return {t: fake_price_df(seed_offset=i + 1) for i, t in enumerate(yf_tickers)}


def fake_fetch_index_history(index_ticker):
    return fake_price_df(seed_offset=999)


def fake_get_sector_close_map(universe_label, sector_labels, fallback_close):
    """Simulates real sector benchmarks for most sectors, with one deliberate fallback."""
    result = {}
    for i, s in enumerate(sector_labels):
        if s == "Energy":  # simulate one sector ticker failing to fetch
            result[s] = (fallback_close, "FALLBACK_BROAD_INDEX")
        else:
            result[s] = (fake_price_df(seed_offset=5000 + i)["Close"], f"FAKE_SECTOR_{i}")
    return result


def fake_get_fundamentals(yf_tickers, force_refresh=False):
    rng = np.random.RandomState(123)
    out = {}
    for t in yf_tickers:
        roll = rng.rand()
        if roll < 0.1:
            out[t] = {"ticker": t, "sales_cagr": np.nan, "profit_cagr": np.nan, "roce": np.nan,
                       "debt_equity": np.nan, "data_quality": "missing",
                       "eps_growth_latest_qtr": np.nan, "eps_growth_prev_qtr": np.nan, "eps_acceleration": np.nan,
                       "revenue_growth_latest_qtr": np.nan, "revenue_growth_prev_qtr": np.nan,
                       "revenue_acceleration": np.nan, "earnings_data_quality": "missing"}
        else:
            eps_lat, eps_prev = rng.normal(15, 12), rng.normal(15, 12)
            rev_lat, rev_prev = rng.normal(10, 8), rng.normal(10, 8)
            out[t] = {
                "ticker": t,
                "sales_cagr": rng.normal(15, 10),
                "profit_cagr": rng.normal(15, 12),
                "roce": rng.normal(18, 8),
                "debt_equity": abs(rng.normal(0.4, 0.3)),
                "data_quality": "ok",
                "eps_growth_latest_qtr": eps_lat, "eps_growth_prev_qtr": eps_prev,
                "eps_acceleration": eps_lat - eps_prev,
                "revenue_growth_latest_qtr": rev_lat, "revenue_growth_prev_qtr": rev_prev,
                "revenue_acceleration": rev_lat - rev_prev,
                "earnings_data_quality": "ok",
            }
    return out


def fake_get_shareholding_trends(nse_symbols):
    rng = np.random.RandomState(456)
    out = {}
    for t in nse_symbols:
        roll = rng.rand()
        if roll < 0.15:
            out[t] = {"mf_pct": None, "fii_pct": None, "mf_pct_prev": None, "fii_pct_prev": None,
                      "mf_holding_increasing": None, "fii_holding_increasing": None,
                      "mf_holding_change_qoq": None, "fii_holding_change_qoq": None,
                      "mf_increasing_2q_streak": None, "fii_increasing_2q_streak": None,
                      "quarter_end": None, "data_quality": "missing"}
        else:
            mf, mf_prev = rng.uniform(2, 15), rng.uniform(2, 15)
            fii, fii_prev = rng.uniform(5, 25), rng.uniform(5, 25)
            mf_inc, fii_inc = mf > mf_prev, fii > fii_prev
            # Roughly a third of "increasing" cases also get a 2-quarter streak (need 3rd quarter on file)
            mf_streak = mf_inc and rng.rand() < 0.3
            fii_streak = fii_inc and rng.rand() < 0.3
            out[t] = {
                "mf_pct": mf, "fii_pct": fii, "mf_pct_prev": mf_prev, "fii_pct_prev": fii_prev,
                "mf_holding_increasing": mf_inc, "fii_holding_increasing": fii_inc,
                "mf_holding_change_qoq": mf - mf_prev, "fii_holding_change_qoq": fii - fii_prev,
                "mf_increasing_2q_streak": mf_streak, "fii_increasing_2q_streak": fii_streak,
                "quarter_end": "2025-12-31", "data_quality": "ok",
            }
    return out


captured_tabs = {}


def fake_export_to_sheets(tabs):
    captured_tabs.update(tabs)
    print("\n=== EXPORT CALLED (mocked) ===")
    for name, df in tabs.items():
        print(f"  tab='{name}': {df.shape[0]} rows x {df.shape[1]} cols")


def fake_nse_universe():
    tickers = [f"FAKE{i}" for i in range(40)]
    return pd.DataFrame({
        "ticker": tickers,
        "yf_ticker": [t + ".NS" for t in tickers],
        "name": [f"Fake Co {i}" for i in range(40)],
        "sector": [SECTORS[i % len(SECTORS)] for i in range(40)],
    })


def fake_sp500_universe():
    tickers = [f"USFAKE{i}" for i in range(40)]
    return pd.DataFrame({
        "ticker": tickers,
        "yf_ticker": tickers,
        "name": [f"US Fake Co {i}" for i in range(40)],
        "sector": [SECTORS[i % len(SECTORS)] for i in range(40)],
    })


def fake_smallmicro_universe():
    tickers = [f"SMALLCAP{i}" for i in range(20)]
    return pd.DataFrame({
        "ticker": tickers,
        "yf_ticker": [t + ".NS" for t in tickers],
        "name": [f"Small/Micro Fake Co {i}" for i in range(20)],
        "sector": [SECTORS[i % len(SECTORS)] for i in range(20)],
    })


# Monkeypatch
data_fetch.fetch_price_history = fake_fetch_price_history
data_fetch.fetch_index_history = fake_fetch_index_history
fnd.get_fundamentals = fake_get_fundamentals
sector_data.get_sector_close_map = fake_get_sector_close_map
shareholding.get_shareholding_trends = fake_get_shareholding_trends
sheets_export.export_to_sheets = fake_export_to_sheets
universe.get_nse500_universe = fake_nse_universe
universe.get_sp500_universe = fake_sp500_universe
universe.get_nse_smallmicro_universe = fake_smallmicro_universe

import main  # noqa: E402  (import after monkeypatch so main uses patched modules)

main.main()

print("\n=== Sample of NSE500_Full_Scan (key Elite columns) ===")
print(captured_tabs[config.SHEET_TABS["nse_full"]][
    ["ticker", "composite_score", "EliteCompounderScore", "elite_category",
     "flag_obv_leader", "flag_rs_leader", "flag_early_macd",
     "sector_index_source", "category"]
].head(10).to_string())

print("\n=== Elite Compounders (original system) ===")
print(captured_tabs[config.SHEET_TABS["elite"]][["ticker", "universe", "composite_score"]].to_string())

print("\n=== Elite_Compounders_EarlyDetect (NEW strict filter) ===")
edt = captured_tabs[config.SHEET_TABS["elite_early_detect"]]
if not edt.empty:
    print(edt[["ticker", "universe", "EliteCompounderScore", "elite_category"]].to_string())
else:
    print("(empty — expected with random synthetic data, strict AND filter is hard to satisfy by chance)")

print("\n=== Category A/B/C counts ===")
for key in ["category_a", "category_b", "category_c"]:
    df = captured_tabs[config.SHEET_TABS[key]]
    print(f"{key}: {len(df)} rows")

print("\n=== Run log ===")
print(captured_tabs[config.SHEET_TABS["run_log"]].to_string())

print("\n=== NSE_SmallMicro_Full_Scan (raw, unscored tier) ===")
smc = captured_tabs[config.SHEET_TABS["nse_smallmicro_full"]]
print(f"Shape: {smc.shape}")
print(f"Columns: {list(smc.columns)}")
forbidden_cols = {"composite_score", "EliteCompounderScore", "category", "elite_category",
                   "rs_rank", "sector_rank", "obv_leadership_rank", "institutional_accumulation_score",
                   "mf_holding_pct", "fii_holding_pct"}
leaked = forbidden_cols & set(smc.columns)
assert not leaked, f"Scoring/shareholding columns leaked into the unscored tier: {leaked}"
print("Confirmed: no scoring or shareholding columns present in NSE_SmallMicro_Full_Scan.")
print(smc[["ticker", "sector", "RS_vs_Broad_Index_pct", "data_quality"]].head(10).to_string())

print("\n=== SmallMicroScore detail ===")
print(smc[["ticker", "smallmicro_score", "smallmicro_category", "smallmicro_score_basis",
           "liquidity_qualified", "avg_daily_traded_value"]].sort_values(
    "smallmicro_score", ascending=False, na_position="last"
).to_string())
print("\nCategory counts:")
print(smc["smallmicro_category"].value_counts())
print("\nBasis counts:")
print(smc["smallmicro_score_basis"].value_counts())
assert smc["smallmicro_score"].dropna().between(0, 100).all(), "smallmicro_score out of 0-100 range!"
print("\nConfirmed: all non-NaN smallmicro_score values are within 0-100.")

print("\n=== Strict checklist detail ===")
print(smc[["ticker", "smallmicro_score", "smallmicro_strict_pass", "smallmicro_strict_fail_reasons"]].to_string())
print("\nStrict pass count:", int((smc["smallmicro_strict_pass"] == True).sum()))  # noqa: E712
# A failing row must always have a non-empty reasons string, and a passing
# row must always have an empty one — if either breaks, the two columns
# have gone out of sync with each other.
mismatched = smc[(smc["smallmicro_strict_pass"]) & (smc["smallmicro_strict_fail_reasons"] != "")]
assert mismatched.empty, f"Rows marked pass=True but have fail reasons: {mismatched['ticker'].tolist()}"
mismatched2 = smc[(~smc["smallmicro_strict_pass"]) & (smc["smallmicro_strict_fail_reasons"] == "")]
assert mismatched2.empty, f"Rows marked pass=False but have NO fail reasons: {mismatched2['ticker'].tolist()}"
print("Confirmed: smallmicro_strict_pass and smallmicro_strict_fail_reasons are consistent for every row.")

print("\nDRY RUN PASSED - no exceptions")

