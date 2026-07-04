"""
US GARP (Growth At a Reasonable Price) composite score — separate from the
NSE/base composite in scoring.py, but reuses its cross-sectional ranking
helper and, for the technical bucket, its existing indicator columns
(Supertrend direction flags, RS score, OBV slope) so no duplicate indicator
computation is needed.

Composite = 0.45*Growth + 0.25*Value + 0.15*Quality + 0.15*Technical

See README_US.md for the full design rationale, including why growth leads
and why there are deliberately NO hard disqualifying gates (e.g. a PEG
ceiling) yet — that's a backtest question, not an assumption to bake in
upfront.

INPUT CONTRACT: expects a DataFrame with one row per US stock, already
containing:
  - The fundamentals.py GARP fields: sales_cagr, profit_cagr, peg_ratio,
    ev_ebitda, debt_equity, fcf_trend_pct, gross_margin_trend,
    operating_margin_trend
  - A "sector" column (from universe.py / sp500_point_in_time.py)
  - The existing technical indicator columns already used by scoring.py:
    supertrend_10_3_dir, supertrend_weekly_dir, rs_score, obv_slope_20d,
    obv_slope_50d

This module does NOT fetch data or compute indicators itself — it only
scores columns that are expected to already be present, exactly like
scoring.py's compute_composite().
"""

import numpy as np
import pandas as pd

import config
import scoring  # reuses scoring._pct_rank and, for Technical, the existing indicator columns


def _pct_rank_within_sector(series: pd.Series, sector: pd.Series) -> pd.Series:
    """
    Percentile rank WITHIN each sector, not against the whole universe.
    Used for EV/EBITDA specifically — "cheap" means something different in
    software vs. utilities, so ranking a software stock's EV/EBITDA against
    a utility's would be comparing unlike things. Falls back to NaN for any
    sector with fewer than 3 members (too small a group for a percentile
    rank to mean much).
    """
    df = pd.DataFrame({"value": series, "sector": sector})
    counts = df.groupby("sector")["value"].transform("count")
    ranked = df.groupby("sector")["value"].transform(lambda s: s.rank(pct=True, na_option="keep") * 100)
    ranked[counts < 3] = np.nan
    return ranked


def _weighted_blend(components: list[tuple[pd.Series, float]]) -> pd.Series:
    """
    Combines (series, weight) pairs into one weighted score, PER ROW
    redistributing weight across only the components that have data for
    that row — rather than letting a single missing component turn the
    whole bucket into NaN via ordinary weighted-sum arithmetic.

    A row with only 1 of 2 components available gets a score based on that
    1 component alone (not penalized for the other being missing); a row
    where ALL components are missing correctly comes back NaN (the bucket
    genuinely has no data — see compute_us_composite's bucket-availability
    tracking, which is where that NaN actually gets accounted for, not
    silently zeroed here).
    """
    values = pd.concat([c for c, _ in components], axis=1)
    weights = np.array([w for _, w in components])

    mask = values.notna().to_numpy()
    weighted_vals = values.fillna(0).to_numpy() * weights
    row_weight_sums = (mask * weights).sum(axis=1)

    with np.errstate(invalid="ignore", divide="ignore"):
        result = np.where(row_weight_sums > 0, weighted_vals.sum(axis=1) / row_weight_sums, np.nan)

    return pd.Series(result, index=values.index)


def score_growth_us(df: pd.DataFrame) -> pd.Series:
    """
    Growth bucket (45% of composite). Blends sales CAGR + profit CAGR +
    estimate revisions (currently inactive — see config.py comment on
    US_GROWTH_SUBWEIGHT_REVISIONS). While revisions data doesn't exist,
    its weight is redistributed across sales/profit CAGR for every stock
    (via _weighted_blend simply never seeing that component) — NOT scored
    as if revisions were "missing data" for that stock, since no stock has
    this data source yet; treating it as a per-stock data gap would be
    misleading. Per-row, a stock missing just ONE of sales/profit CAGR
    still gets a growth score based on whichever it has.
    """
    sales_rank = scoring._pct_rank(df["sales_cagr"])
    profit_rank = scoring._pct_rank(df["profit_cagr"])

    revisions_col = "estimate_revisions_score"  # not built yet; see module docstring
    if revisions_col in df.columns and df[revisions_col].notna().any():
        revisions_rank = scoring._pct_rank(df[revisions_col])
        return _weighted_blend([
            (sales_rank, config.US_GROWTH_SUBWEIGHT_SALES_CAGR),
            (profit_rank, config.US_GROWTH_SUBWEIGHT_PROFIT_CAGR),
            (revisions_rank, config.US_GROWTH_SUBWEIGHT_REVISIONS),
        ])

    return _weighted_blend([
        (sales_rank, config.US_GROWTH_SUBWEIGHT_SALES_CAGR),
        (profit_rank, config.US_GROWTH_SUBWEIGHT_PROFIT_CAGR),
    ])


def score_value_us(df: pd.DataFrame) -> pd.Series:
    """
    Value bucket (25% of composite). Both PEG and EV/EBITDA are
    "lower is better" — inverted percentile rank (100 - rank) so a LOW
    PEG/EV-EBITDA scores HIGH. EV/EBITDA is ranked within-sector (see
    _pct_rank_within_sector); PEG is ranked against the whole universe
    since it already normalizes for growth, making cross-sector comparison
    more reasonable than raw EV/EBITDA would be. A stock missing just ONE
    of the two (e.g. PEG unavailable because trailing P/E didn't load —
    see fundamentals.py's isolated try/except around that specific field)
    still gets a value score based on whichever it has.
    """
    peg_rank = 100 - scoring._pct_rank(df["peg_ratio"])
    ev_ebitda_rank = 100 - _pct_rank_within_sector(df["ev_ebitda"], df["sector"])

    return _weighted_blend([
        (peg_rank, config.US_VALUE_SUBWEIGHT_PEG),
        (ev_ebitda_rank, config.US_VALUE_SUBWEIGHT_EV_EBITDA),
    ])


def score_quality_us(df: pd.DataFrame) -> pd.Series:
    """
    Quality bucket (15% of composite). FCF trend and margin trends are
    "higher is better" (improving); debt/equity is "lower is better"
    (inverted rank), reusing the same field the NSE strategies already
    fetch — no new data needed for that component. Gross and operating
    margin trend are blended 50/50 into one "margin trend" input before
    joining FCF and debt/equity, so a stock missing just one margin type
    still contributes a margin signal rather than being treated as having
    none.
    """
    fcf_rank = scoring._pct_rank(df["fcf_trend_pct"])
    debt_equity_rank = 100 - scoring._pct_rank(df["debt_equity"])

    gross_margin_rank = scoring._pct_rank(df["gross_margin_trend"])
    operating_margin_rank = scoring._pct_rank(df["operating_margin_trend"])
    margin_rank = _weighted_blend([(gross_margin_rank, 0.5), (operating_margin_rank, 0.5)])

    return _weighted_blend([
        (fcf_rank, config.US_QUALITY_SUBWEIGHT_FCF_TREND),
        (debt_equity_rank, config.US_QUALITY_SUBWEIGHT_DEBT_EQUITY),
        (margin_rank, config.US_QUALITY_SUBWEIGHT_MARGIN_TREND),
    ])


def score_technical_us(df: pd.DataFrame) -> pd.Series:
    """
    Technical timing bucket (15% of composite) — deliberately simpler than
    the NSE Trend bucket (no fast Supertrend, no EMA20 check): this is a
    weeks-to-months positional hold, not a swing setup, so only
    higher-timeframe trend + leadership signals apply. Reuses the SAME
    indicator columns scoring.py's score_trend()/score_relative_strength()/
    score_obv() already consume — no new indicator computation needed here,
    only a different blend appropriate for this hold horizon.
    """
    st_weekly = (df["supertrend_weekly_dir"] + 1) / 2 * 100   # -1/1 -> 0/100
    st_daily = (df["supertrend_10_3_dir"] + 1) / 2 * 100
    rs_rank = scoring._pct_rank(df["rs_score"])
    obv_rank = scoring._pct_rank(df["obv_slope_20d"].fillna(0) + df["obv_slope_50d"].fillna(0))

    return _weighted_blend([
        (st_weekly, config.US_TECHNICAL_SUBWEIGHT_SUPERTREND_WEEKLY),
        (st_daily, config.US_TECHNICAL_SUBWEIGHT_SUPERTREND_DAILY),
        (rs_rank, config.US_TECHNICAL_SUBWEIGHT_RS),
        (obv_rank, config.US_TECHNICAL_SUBWEIGHT_OBV),
    ])


def compute_us_composite(df: pd.DataFrame) -> pd.DataFrame:
    """
    Appends growth/value/quality/technical sub-scores and the final
    us_composite_score (0-100) to the DataFrame. Rows missing a whole
    bucket's worth of data get NaN for that bucket (propagated, not
    silently zeroed) so a stock's composite score visibly reflects data
    completeness rather than looking artificially low.
    """
    df = df.copy()

    df["us_score_growth"] = score_growth_us(df)
    df["us_score_value"] = score_value_us(df)
    df["us_score_quality"] = score_quality_us(df)
    df["us_score_technical"] = score_technical_us(df)

    df["us_composite_score"] = (
        config.US_WEIGHT_GROWTH * df["us_score_growth"].fillna(0)
        + config.US_WEIGHT_VALUE * df["us_score_value"].fillna(0)
        + config.US_WEIGHT_QUALITY * df["us_score_quality"].fillna(0)
        + config.US_WEIGHT_TECHNICAL * df["us_score_technical"].fillna(0)
    ) / 100

    # Visibility: how many of the 4 buckets actually had data for this row —
    # a composite score built from 1 of 4 buckets should not be trusted the
    # same as one built from 4 of 4. Downstream (entry logic) should
    # threshold on this alongside the composite score itself.
    df["us_score_buckets_available"] = (
        df["us_score_growth"].notna().astype(int)
        + df["us_score_value"].notna().astype(int)
        + df["us_score_quality"].notna().astype(int)
        + df["us_score_technical"].notna().astype(int)
    )

    return df
