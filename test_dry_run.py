"""
Dry-run test: monkeypatches data_fetch / fundamentals / sheets_export with
synthetic data so the full main.py pipeline can be validated without live
network access. Not part of the deployed package — delete or ignore in prod.
"""
import numpy as np
import pandas as pd

import config
import data_fetch
import fundamentals as fnd
import sheets_export
import universe

np.random.seed(7)


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


def fake_get_fundamentals(yf_tickers, force_refresh=False):
    rng = np.random.RandomState(123)
    out = {}
    for t in yf_tickers:
        roll = rng.rand()
        if roll < 0.1:
            out[t] = {"ticker": t, "sales_cagr": np.nan, "profit_cagr": np.nan, "roce": np.nan,
                       "debt_equity": np.nan, "data_quality": "missing"}
        else:
            out[t] = {
                "ticker": t,
                "sales_cagr": rng.normal(15, 10),
                "profit_cagr": rng.normal(15, 12),
                "roce": rng.normal(18, 8),
                "debt_equity": abs(rng.normal(0.4, 0.3)),
                "data_quality": "ok",
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
        "sector": ["TestSector"] * 40,
    })


def fake_sp500_universe():
    tickers = [f"USFAKE{i}" for i in range(40)]
    return pd.DataFrame({
        "ticker": tickers,
        "yf_ticker": tickers,
        "name": [f"US Fake Co {i}" for i in range(40)],
        "sector": ["TestSector"] * 40,
    })


# Monkeypatch
data_fetch.fetch_price_history = fake_fetch_price_history
data_fetch.fetch_index_history = fake_fetch_index_history
fnd.get_fundamentals = fake_get_fundamentals
sheets_export.export_to_sheets = fake_export_to_sheets
universe.get_nse500_universe = fake_nse_universe
universe.get_sp500_universe = fake_sp500_universe

import main  # noqa: E402  (import after monkeypatch so main uses patched modules)

main.main()

print("\n=== Sample of NSE500_Full_Scan ===")
print(captured_tabs[config.SHEET_TABS["nse_full"]][
    ["ticker", "composite_score", "category", "fundamentally_qualified", "data_quality"]
].head(10).to_string())

print("\n=== Elite Compounders ===")
print(captured_tabs[config.SHEET_TABS["elite"]][["ticker", "universe", "composite_score"]].to_string())

print("\n=== Run log ===")
print(captured_tabs[config.SHEET_TABS["run_log"]].to_string())

print("\nDRY RUN PASSED - no exceptions")
