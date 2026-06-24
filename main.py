"""
Orchestrates a full daily scan:
  1. Load NSE500 + S&P500 universes
  2. Fetch price history (+ benchmark index + sector benchmark history) for each
  3. Compute all technical indicators per ticker, including the Elite Compounder
     Early Detection modules (RS leadership, OBV leadership, early MACD,
     volatility compression, early EMA structure, near-breakout)
  4. Fetch/merge fundamentals, apply qualifying filter
  5. Compute the original composite score AND the new EliteCompounderScore
  6. Build output tabs (original + new) and push to Google Sheets

Run with: python main.py
"""

import datetime
import logging
import sys

import numpy as np
import pandas as pd

import config
import data_fetch
import fundamentals as fnd
import indicators as ind
import metrics_builder
import portfolio
import scoring as sc
import sector_data
import shareholding
import sheets_export
import universe

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("main")


def process_universe(label: str, universe_df: pd.DataFrame, index_ticker: str, skip_scoring: bool = False) -> pd.DataFrame:
    """
    skip_scoring=True computes per-ticker indicators and fundamentals as
    normal, but skips composite_score, EliteCompounderScore, and every
    NSE500/SP500 cross-sectional/percentile-rank module (rs_rank,
    sector_rank, obv_leadership_rank, institutional_accumulation_score).
    Those formulas were tuned/backtested against NSE500+SP500 liquidity and
    data-quality patterns; running them on a thinner, less-liquid universe
    would silently distort the percentiles for the universe they WERE
    validated on if ever combined, and would themselves be meaningless
    without separate backtesting.

    Instead, this branch computes its OWN separate scoring system —
    SmallMicroScore (scoring.py: compute_liquidity_gate +
    compute_smallmicro_score) — designed specifically for this tier rather
    than adapted from the other two. See scoring.py and config.py for the
    full rationale; treat its output as an unvalidated starting point, not
    a trusted signal, until backtested separately. Used for the
    NSE_SmallMicro tier — see README.
    """
    logger.info("=== Processing %s universe (%d tickers) ===", label, len(universe_df))

    yf_tickers = universe_df["yf_ticker"].tolist()
    price_data = data_fetch.fetch_price_history(yf_tickers)
    index_df = data_fetch.fetch_index_history(index_ticker)
    index_close = index_df["Close"]

    # Resolve each unique sector to a benchmark series ONCE per universe
    # (RS Leadership module needs RS_SECTOR = stock / sector benchmark).
    ticker_sector_map = dict(zip(universe_df["yf_ticker"], universe_df.get("sector", pd.Series(dtype=object))))
    unique_sectors = list(pd.Series(list(ticker_sector_map.values())).dropna().unique())
    sector_close_map = sector_data.get_sector_close_map(label, unique_sectors, index_close)

    rows = []
    for yf_ticker, df in price_data.items():
        try:
            sector_label = ticker_sector_map.get(yf_ticker)
            sector_close, sector_source = sector_close_map.get(sector_label, (index_close, "NO_SECTOR_LABEL"))
            rows.append(metrics_builder.build_metrics_row(yf_ticker, df, index_close, sector_close, sector_source))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Indicator computation failed for %s: %s", yf_ticker, exc)

    metrics_df = pd.DataFrame(rows)
    if metrics_df.empty:
        logger.error("No tickers produced usable metrics for %s — aborting this universe", label)
        return metrics_df

    metrics_df = metrics_df.merge(universe_df, on="yf_ticker", how="left")

    fundamentals_map = fnd.get_fundamentals(metrics_df["yf_ticker"].tolist())
    fund_df = pd.DataFrame(fundamentals_map.values())
    metrics_df = metrics_df.merge(fund_df, left_on="yf_ticker", right_on="ticker", how="left", suffixes=("", "_fund"))
    metrics_df["fundamentally_qualified"] = metrics_df.apply(
        lambda r: fnd.passes_fundamental_filter(r.to_dict()), axis=1
    )

    if skip_scoring:
        # Raw-indicators tier, PLUS its own separate scoring system
        # (SmallMicroScore — see scoring.py and config.py for full
        # rationale). Still gets NONE of NSE500/SP500's backtested scoring:
        # no composite_score, no EliteCompounderScore, no
        # obv_leadership_rank/rs_rank/sector_rank/institutional_accumulation
        # (those are tuned/backtested specifically for NSE500+SP500 and
        # would be meaningless — or worse, falsely authoritative-looking —
        # if computed here). No shareholding either (see README).
        metrics_df["RS_vs_Broad_Index_pct"] = metrics_df["rs_score"]
        metrics_df = sc.compute_earnings_acceleration_score(metrics_df)  # per-ticker, not a rank — safe to reuse as-is
        metrics_df = sc.compute_liquidity_gate(metrics_df)               # must run before compute_smallmicro_score
        metrics_df = sc.compute_smallmicro_score(metrics_df)              # SEPARATE system, NOT composite_score reweighted
        metrics_df = sc.compute_smallmicro_strict_checklist(metrics_df)   # SEPARATE pass/fail flag, not a pre-filter on the score above
        metrics_df["universe"] = label
        metrics_df = metrics_df.sort_values("smallmicro_score", ascending=False, na_position="last").reset_index(drop=True)
        logger.info(
            "%s done (separate SmallMicroScore, not NSE500/SP500's backtested system): "
            "%d total, %d liquidity-qualified, %d scored 'Strong', %d pass the strict checklist",
            label, len(metrics_df),
            int((metrics_df["liquidity_qualified"] == True).sum()),  # noqa: E712
            int((metrics_df["smallmicro_category"] == "Strong").sum()),
            int((metrics_df["smallmicro_strict_pass"] == True).sum()),  # noqa: E712
        )
        return metrics_df

    # Original composite scoring system — unchanged
    metrics_df = sc.compute_composite(metrics_df)
    metrics_df["category"] = metrics_df.apply(
        lambda r: sc.categorize(r["composite_score"], r["fundamentally_qualified"]), axis=1
    )

    # Elite Compounder Early Detection scoring — additive, runs after the above
    metrics_df = sc.compute_elite_compounder_score(metrics_df)

    # Single, clearly-labeled RS column: each stock vs ITS OWN home broad
    # index only (Nifty 50 for NSE, S&P 500 for US) — distinct from the
    # sector-specific RS fields above. This is the same underlying
    # outperformance figure as `rs_score` (blended over ~1m/3m/6m), just
    # surfaced under an unambiguous name for quick reading in the sheet.
    metrics_df["RS_vs_Broad_Index_pct"] = metrics_df["rs_score"]

    # Phase 1 of the Elite Compounder Discovery System v2.0 upgrade — RS rank,
    # Trend Birth detection, Monthly trend confirmation, Sector leadership.
    # Entirely additive: built only from columns already computed above.
    metrics_df = sc.compute_phase1_additions(metrics_df)

    # Chart study additions: Trend Death (Distribution Detection) + OBV-price
    # divergence — see README for the rationale behind both.
    metrics_df = sc.compute_trend_death(metrics_df)
    metrics_df = sc.compute_obv_divergence_flag(metrics_df)
    metrics_df = sc.compute_obv_leadership_rank(metrics_df)

    # MF/FII shareholding trend — NSE-specific (SEBI quarterly filing concept,
    # no equivalent free data source built for US institutional holdings
    # here; US uses a different framework — SEC 13F — out of scope).
    # Informational only: does NOT feed into composite_score or
    # EliteCompounderScore, so it can't disrupt either already-tuned system.
    # Strict equality (not a prefix check) is deliberate here: the new
    # NSE_SmallMicro tier should NOT get shareholding — NSE500 keeps sole
    # priority on the 60-tickers/run rate-limit budget (see README).
    if label == "NSE500":
        share_trends = shareholding.get_shareholding_trends(metrics_df["ticker"].tolist())
        share_df = pd.DataFrame(
            [{"ticker": t, **v} for t, v in share_trends.items()]
        ).rename(columns={
            "mf_pct": "mf_holding_pct", "fii_pct": "fii_holding_pct",
            "mf_pct_prev": "mf_holding_pct_prev_qtr", "fii_pct_prev": "fii_holding_pct_prev_qtr",
            "quarter_end": "shareholding_quarter_end", "data_quality": "shareholding_data_quality",
        })
        metrics_df = metrics_df.merge(share_df, on="ticker", how="left")
        metrics_df["flag_mf_increasing"] = metrics_df["mf_holding_increasing"].apply(
            lambda v: "🟢" if v is True else ""
        )
        metrics_df["flag_fii_increasing"] = metrics_df["fii_holding_increasing"].apply(
            lambda v: "🟢" if v is True else ""
        )
    else:
        for col in [
            "mf_holding_pct", "fii_holding_pct", "mf_holding_pct_prev_qtr", "fii_holding_pct_prev_qtr",
            "mf_holding_increasing", "fii_holding_increasing", "shareholding_quarter_end",
            "shareholding_data_quality", "flag_mf_increasing", "flag_fii_increasing",
            "mf_holding_change_qoq", "fii_holding_change_qoq",
            "mf_increasing_2q_streak", "fii_increasing_2q_streak",
        ]:
            metrics_df[col] = None

    # Phase 2 (Module 2 extension): institutional accumulation scoring.
    # Handles the US branch gracefully too (all-None columns above just
    # produce a NaN score / blank flag, not an error).
    metrics_df = sc.compute_institutional_accumulation_score(metrics_df)

    # Phase 3 (Module 1): earnings acceleration — applies to BOTH NSE and US
    # (yfinance quarterly statements are available for both, unlike MF/FII).
    metrics_df = sc.compute_earnings_acceleration_score(metrics_df)

    metrics_df["universe"] = label
    metrics_df = metrics_df.sort_values("composite_score", ascending=False).reset_index(drop=True)

    logger.info(
        "%s done: %d scored, %d fundamentally qualified, %d Category A elite compounders",
        label, len(metrics_df), int(metrics_df["fundamentally_qualified"].sum()),
        int((metrics_df["elite_category"] == "Category A: Elite Compounder").sum()),
    )
    return metrics_df


DISPLAY_COLUMNS = [
    "ticker", "name", "sector", "universe", "close",
    "composite_score", "category", "fundamentally_qualified",
    # ── Elite Compounder Early Detection — headline fields ──
    "EliteCompounderScore", "elite_category",
    "RS_vs_Broad_Index_pct",   # single clear RS-vs-home-index column
    "flag_obv_leader", "flag_rs_leader", "flag_early_macd",
    "flag_compression", "flag_ema_alignment", "flag_near_breakout",
    # Phase 1 (Elite Compounder Discovery v2.0) — additional headline flags
    "flag_rs_top_decile", "flag_trend_birth", "flag_monthly_bullish", "flag_sector_leader",
    "flag_trend_death", "flag_bullish_obv_divergence", "flag_obv_leadership_top_decile",
    "flag_institutional_accumulation", "flag_earnings_accelerating",
    # Original sub-scores
    "score_obv", "score_macd_weekly", "score_macd_daily", "score_trend", "score_rs",
    # Elite Compounder sub-scores
    "elite_score_obv", "elite_score_rs", "elite_score_macd_early",
    "elite_score_ema_alignment", "elite_score_compression",
    "elite_score_supertrend", "elite_score_weekly_macd",
    "elite_score_above_ema20", "elite_score_fundamentals",
    # OBV (original + Leadership module)
    "obv_slope_20d", "obv_slope_50d", "obv_52w_range_pct",
    "obv_52w_high", "obv_26w_high", "obv_slope_13w", "obv_slope_26w",
    # Daily MACD (original + Early module)
    "daily_macd", "daily_signal", "daily_hist", "macd_early_bullish",
    # Weekly MACD
    "weekly_macd", "weekly_signal", "weekly_hist", "weekly_macd_positive",
    # Supertrend (daily + weekly)
    "supertrend_10_3_dir", "supertrend_2_1_dir", "supertrend_weekly_dir",
    # EMA structure (original + Early Structure module)
    "ema10", "ema20", "early_ema_alignment",
    # 52-week high / breakout
    "pct_from_52w_high", "near_52w_high", "near_breakout_15pct",
    # Relative Strength (original + Leadership module)
    "rs_score",
    "rs_nifty_52w_high", "rs_nifty_chg_13w", "rs_nifty_chg_26w",
    "rs_sector_52w_high", "rs_sector_chg_13w", "rs_sector_chg_26w",
    "sector_index_source",
    # Volatility Compression module
    "atr_compression_ratio", "atr_compression_percentile",
    "range_compression_ratio", "volatility_compression",
    # Fundamentals
    "sales_cagr", "profit_cagr", "roce", "debt_equity", "data_quality",
    # Earnings Acceleration (Phase 3, Module 1) — QoQ-based, see README for the YoY-vs-QoQ trade-off
    "eps_growth_latest_qtr", "eps_growth_prev_qtr", "eps_acceleration",
    "revenue_growth_latest_qtr", "revenue_growth_prev_qtr", "revenue_acceleration",
    "earnings_acceleration_score", "earnings_data_quality",
    # MF/FII shareholding trend (NSE-only, informational — see shareholding.py)
    "mf_holding_pct", "mf_holding_pct_prev_qtr", "mf_holding_increasing", "flag_mf_increasing",
    "mf_holding_change_qoq", "mf_increasing_2q_streak",
    "fii_holding_pct", "fii_holding_pct_prev_qtr", "fii_holding_increasing", "flag_fii_increasing",
    "fii_holding_change_qoq", "fii_increasing_2q_streak",
    "shareholding_quarter_end", "shareholding_data_quality",
    "institutional_accumulation_score",
    # Phase 1 (Elite Compounder Discovery v2.0) — detail columns behind the headline flags above
    "rs_rank", "rs_rank_score",
    "trend_birth_flag", "trend_birth_score",
    "monthly_macd", "monthly_signal", "monthly_hist", "monthly_ema20", "monthly_ema50",
    "monthly_bullish", "monthly_trend_score",
    "sector_rank", "sector_leader_score",
    # Chart study additions
    "macd_early_bearish", "trend_death_flag", "trend_death_score",
    "obv_price_divergence", "obv_leadership_rank",
]

# Raw + SmallMicroScore tier — NSE_SmallMicro. Deliberately excludes
# composite_score, EliteCompounderScore, category, elite_category, every
# NSE500/SP500-specific cross-sectional flag/rank (rs_rank, sector_rank,
# obv_leadership_rank, trend_birth, trend_death, institutional_accumulation),
# and all shareholding columns — none of those are computed for this tier
# (skip_scoring=True). DOES include smallmicro_score and its supporting
# columns — see scoring.py for why that's a separate, purpose-built system
# rather than a reweighted copy of the other two. Keeping a separate list
# (rather than filtering DISPLAY_COLUMNS down) makes it obvious at a glance
# which columns this tier is meant to have, instead of relying on "whatever
# happens to exist in the dataframe."
RAW_DISPLAY_COLUMNS = [
    "ticker", "name", "sector", "universe", "close",
    # SmallMicroScore — leads the column list since it's the main thing to
    # look at; smallmicro_score_basis explains every NaN/category at a
    # glance without cross-referencing config.py or scoring.py. Built from
    # OBV Leadership 40 / RS 25 / Near-52w-High 15 / Earnings Accel 10 /
    # Liquidity 10 — see config.SMALLMICRO_SCORE_WEIGHTS.
    "smallmicro_score", "smallmicro_category", "smallmicro_score_basis", "smallmicro_score_coverage_pct",
    # Strict pass/fail checklist — a SEPARATE flag from the score above,
    # NOT a pre-filter on it. smallmicro_strict_fail_reasons makes a
    # near-miss immediately diagnosable without cross-referencing config.py.
    "smallmicro_strict_pass", "smallmicro_strict_fail_reasons",
    "liquidity_qualified", "avg_daily_traded_value",
    "RS_vs_Broad_Index_pct", "rs_score",
    # OBV — obv_52w_range_pct feeds the score directly (40 pts); the rest
    # are shown for context only.
    "obv_slope_20d", "obv_slope_50d", "obv_52w_range_pct",
    "obv_52w_high", "obv_26w_high", "obv_price_divergence",
    # 52w-high / breakout proximity — pct_from_52w_high feeds the score
    # (15 pts, inverted+ranked); near_breakout_15pct feeds the strict
    # checklist directly; near_52w_high (10% threshold) shown for context only.
    "pct_from_52w_high", "near_breakout_15pct", "near_52w_high",
    # MACD and Trend structure — NO LONGER feed smallmicro_score (dropped
    # in the 2nd revision in favor of weighting OBV/RS higher and adding
    # Near-52w-High/Liquidity as scored components). Kept in the tab purely
    # as supporting context for your own manual read of a stock, same as
    # any other indicator column here.
    "daily_macd", "daily_signal", "daily_hist", "macd_early_bullish", "macd_early_bearish",
    "weekly_macd", "weekly_signal", "weekly_hist", "weekly_macd_positive",
    "monthly_macd", "monthly_signal", "monthly_hist", "monthly_bullish",
    "supertrend_10_3_dir", "supertrend_weekly_dir", "ema10", "ema20", "early_ema_alignment",
    # Volatility compression (informational only — backtest showed this is
    # NOT a reliable standalone signal even on NSE500/SP500; doubly so on a
    # thinner, less-liquid universe that's never been backtested at all)
    "atr_compression_percentile", "volatility_compression",
    # Fundamentals
    "sales_cagr", "profit_cagr", "roce", "debt_equity", "fundamentally_qualified", "data_quality",
    # Earnings acceleration (per-ticker, not a cross-sectional rank — safe to
    # carry over as-is; same QoQ seasonality caveat applies, see README).
    # earnings_acceleration_score feeds the score directly (10 pts).
    "eps_acceleration", "revenue_acceleration", "earnings_acceleration_score",
    "flag_earnings_accelerating", "earnings_data_quality",
]


def main():
    run_started = datetime.datetime.utcnow()
    logger.info("Scan started at %s UTC", run_started.isoformat())

    nse_universe = universe.get_nse500_universe()
    sp500_universe = universe.get_sp500_universe()
    smallmicro_universe = universe.get_nse_smallmicro_universe()

    nse_df = process_universe("NSE500", nse_universe, config.INDEX_TICKER_NSE)
    us_df = process_universe("S&P500", sp500_universe, config.INDEX_TICKER_US)
    # Deliberately NOT included in `combined` below — see README "NSE
    # Small/Micro-cap tier" section. Raw indicators + fundamentals only,
    # no composite_score / EliteCompounderScore, no shareholding. Kept out
    # of every percentile-ranked tab (Top20, Elite/Emerging/Exit, Sector
    # Leaders, OBV Leaders, etc.) so it can't distort rankings that were
    # tuned/backtested against NSE500+SP500 liquidity and data patterns.
    smallmicro_df = process_universe("NSE_SmallMicro", smallmicro_universe, config.INDEX_TICKER_NSE, skip_scoring=True)

    combined = pd.concat([nse_df, us_df], ignore_index=True) if not nse_df.empty and not us_df.empty else (
        nse_df if not nse_df.empty else us_df
    )

    def view(df, cols=DISPLAY_COLUMNS):
        existing = [c for c in cols if c in df.columns]
        return df[existing]

    # Raw, unscored tier — own column set (RAW_DISPLAY_COLUMNS), no
    # composite_score / EliteCompounderScore / cross-sectional ranks exist
    # on this dataframe at all (skip_scoring=True upstream).
    smallmicro_tab = view(smallmicro_df, cols=RAW_DISPLAY_COLUMNS) if not smallmicro_df.empty else pd.DataFrame()

    # ── Original tabs — unchanged logic ──
    top20_nse = view(nse_df.head(config.TOP_N)) if not nse_df.empty else pd.DataFrame()
    top20_us = view(us_df.head(config.TOP_N)) if not us_df.empty else pd.DataFrame()
    elite = view(combined[combined["category"] == "Elite Compounder"]) if not combined.empty else pd.DataFrame()
    emerging = view(combined[combined["category"] == "Emerging Compounder"]) if not combined.empty else pd.DataFrame()
    exit_candidates = view(combined[combined["category"] == "Exit Candidate"]) if not combined.empty else pd.DataFrame()

    # ── New: Elite Compounder Early Detection tabs ──
    if not combined.empty:
        strict_filter = (
            (combined["obv_52w_high"] == 1.0)
            & (combined["rs_nifty_52w_high"] == 1.0)
            & (combined["macd_early_bullish"] == 1.0)
        )
        elite_early_detect = view(
            combined[strict_filter].sort_values("EliteCompounderScore", ascending=False)
        )
        category_a = view(
            combined[combined["elite_category"] == "Category A: Elite Compounder"]
            .sort_values("EliteCompounderScore", ascending=False)
        )
        category_b = view(
            combined[combined["elite_category"] == "Category B: Emerging Leader"]
            .sort_values("EliteCompounderScore", ascending=False)
        )
        category_c = view(
            combined[combined["elite_category"] == "Category C: Watchlist"]
            .sort_values("EliteCompounderScore", ascending=False)
        )
    else:
        elite_early_detect = category_a = category_b = category_c = pd.DataFrame()

    # ── Phase 1 (Elite Compounder Discovery v2.0) dashboards ──
    if not combined.empty:
        trend_birth_tab = view(
            combined[combined["trend_birth_flag"] == 1.0]
            .sort_values("trend_birth_score", ascending=False)
        )
        # Top N per (universe, sector) — never mixes NSE and US stocks within
        # a sector group, since sector_rank itself was computed per-universe.
        sector_leaders_tab = view(
            combined[combined["sector_rank"] <= config.SECTOR_LEADER_TOP_N_FOR_TAB]
            .sort_values(["universe", "sector", "sector_rank"])
        )
        trend_death_tab = view(
            combined[combined["trend_death_flag"] == 1.0]
            .sort_values("trend_death_score", ascending=False)
        )
        obv_leaders_tab = view(
            combined.sort_values("obv_leadership_rank", ascending=False)
            .head(config.OBV_LEADERS_TAB_TOP_N)
        )
        # Phase 3 (Module 1): Earnings Acceleration tab — filters to the flagged
        # subset (score > threshold), then ranks by score. Mixes NSE and US
        # rows deliberately: unlike Sector Leaders this isn't a per-universe
        # comparison, just "who's accelerating fastest, full stop." The
        # QoQ seasonality caveat (see config.py / fundamentals.py) means a
        # name here in a holiday-heavy quarter deserves a second look before
        # acting on it, not an automatic buy signal.
        earnings_accelerating_tab = view(
            combined[combined["flag_earnings_accelerating"] == "🟢"]
            .sort_values("earnings_acceleration_score", ascending=False)
            .head(config.EARNINGS_ACCELERATING_TAB_TOP_N)
        )
    else:
        trend_birth_tab = sector_leaders_tab = trend_death_tab = obv_leaders_tab = pd.DataFrame()
        earnings_accelerating_tab = pd.DataFrame()

    run_log = pd.DataFrame([{
        "run_timestamp_utc": run_started.isoformat(),
        "nse_tickers_scored": len(nse_df),
        "us_tickers_scored": len(us_df),
        "nse_qualified": int(nse_df["fundamentally_qualified"].sum()) if not nse_df.empty else 0,
        "us_qualified": int(us_df["fundamentally_qualified"].sum()) if not us_df.empty else 0,
        "elite_count": len(elite),
        "emerging_count": len(emerging),
        "exit_count": len(exit_candidates),
        # New counts
        "elite_early_detect_count": len(elite_early_detect),
        "category_a_count": len(category_a),
        "category_b_count": len(category_b),
        "category_c_count": len(category_c),
        # Phase 1 counts
        "trend_birth_count": len(trend_birth_tab),
        "sector_leaders_count": len(sector_leaders_tab),
        "trend_death_count": len(trend_death_tab),
        "earnings_accelerating_count": len(earnings_accelerating_tab),
        # NSE Small/Micro-cap tier (raw, unscored — see README)
        "nse_smallmicro_tickers_scanned": len(smallmicro_df),
    }])

    # ── My Portfolio (manually-imported Zerodha holdings) — additive, never
    # blocks the rest of the pipeline if the holdings tab doesn't exist yet ──
    try:
        nifty_index_close = data_fetch.fetch_index_history(config.INDEX_TICKER_NSE)["Close"]
        my_portfolio = portfolio.build_my_portfolio_tab(nse_df, nifty_index_close)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Portfolio enrichment failed, skipping: %s", exc)
        my_portfolio = pd.DataFrame()

    tabs = {
        # Original tabs — unchanged
        config.SHEET_TABS["nse_full"]: view(nse_df),
        config.SHEET_TABS["us_full"]: view(us_df),
        config.SHEET_TABS["top20_nse"]: top20_nse,
        config.SHEET_TABS["top20_us"]: top20_us,
        config.SHEET_TABS["elite"]: elite,
        config.SHEET_TABS["emerging"]: emerging,
        config.SHEET_TABS["exit"]: exit_candidates,
        config.SHEET_TABS["run_log"]: run_log,
        # New: Elite Compounder Early Detection tabs
        config.SHEET_TABS["elite_early_detect"]: elite_early_detect,
        config.SHEET_TABS["category_a"]: category_a,
        config.SHEET_TABS["category_b"]: category_b,
        config.SHEET_TABS["category_c"]: category_c,
        # Phase 1 (Elite Compounder Discovery v2.0) dashboards
        config.SHEET_TABS["trend_birth"]: trend_birth_tab,
        config.SHEET_TABS["sector_leaders"]: sector_leaders_tab,
        config.SHEET_TABS["trend_death"]: trend_death_tab,
        config.SHEET_TABS["obv_leaders"]: obv_leaders_tab,
        config.SHEET_TABS["earnings_accelerating"]: earnings_accelerating_tab,
        # NSE Small/Micro-cap tier (raw, unscored)
        config.SHEET_TABS["nse_smallmicro_full"]: smallmicro_tab,
    }

    # Only add the portfolio tab if there was something to show — avoids
    # creating an empty/confusing tab before the user has set up My_Holdings.
    if not my_portfolio.empty:
        tabs[config.SHEET_TABS["my_portfolio"]] = my_portfolio

    sheets_export.export_to_sheets(tabs)
    logger.info("Scan complete and exported to Google Sheets.")


if __name__ == "__main__":
    main()
