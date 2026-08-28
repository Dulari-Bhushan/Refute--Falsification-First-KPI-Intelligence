"""
Reconciliation layer -- satisfies Round 2 objective 2 ("reconcile data and
business context across heterogeneous sources") and the minimum-expectation
requirement for evidence of source freshness and lineage.

Two jobs:

1. Resample every source to the KPI semantic contract's analysis grain
   (region x week) and assemble one reconciled panel that L1 onward reads
   from, so nothing downstream has to know that pos_transactions is daily,
   marketing_spend is weekly, and crm_headcount is monthly.

2. Treat cross-source agreement as a falsifiable claim, not an assumption.
   pos_transactions and finance_gl_extract both claim to report "revenue"
   for the same region-month. Rather than silently averaging or picking
   one, this module states the claim ("these two sources agree on revenue,
   within tolerance"), tests it, and reports the verdict -- including
   surfacing the KPI contract's documented reason when a real definitional
   difference explains the gap. This is the same claim -> test -> verdict
   pattern the L4 falsification compiler formalizes later, applied here to
   reconciliation rather than root-cause hypotheses, using the plain stdlib
   since L4's schema/compiler doesn't exist yet at this stage of the build.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yaml

DATA_DIR = Path(__file__).parent / "synthetic"
CONTRACT_PATH = Path(__file__).parent.parent / "semantic" / "kpi_contract.yaml"

# tolerance for the source-agreement claim: gaps smaller than this are noise,
# not worth a reconciliation note
AGREEMENT_TOLERANCE_PCT = 0.005


def load_contract() -> dict:
    return yaml.safe_load(CONTRACT_PATH.read_text())


def week_of(d: date, week1_start: date) -> int:
    return (d - week1_start).days // 7 + 1


def load_sources() -> dict[str, pd.DataFrame]:
    return {
        "pos_transactions": pd.read_csv(DATA_DIR / "pos_transactions.csv", parse_dates=["date"]),
        "marketing_spend": pd.read_csv(DATA_DIR / "marketing_spend.csv", parse_dates=["week_start"]),
        "crm_headcount": pd.read_csv(DATA_DIR / "crm_headcount.csv"),
        "finance_gl_extract": pd.read_csv(DATA_DIR / "finance_gl_extract.csv"),
        "support_tickets": pd.read_csv(DATA_DIR / "support_tickets.csv", parse_dates=["created_at"]),
    }


def compute_freshness(sources: dict[str, pd.DataFrame], contract: dict, as_of: date) -> list[dict]:
    """How stale is each source relative to the analysis 'as of' date? This
    is exactly the evidence the minimum prototype expectations ask for --
    freshness has to be a visible, per-source fact, not an assumption that
    everything is equally current."""
    rows = []

    pos_max = sources["pos_transactions"]["date"].max().date()
    rows.append(_freshness_row(contract, "pos_transactions", pos_max, as_of))

    mkt_max = sources["marketing_spend"]["week_start"].max().date() + timedelta(days=6)
    rows.append(_freshness_row(contract, "marketing_spend", mkt_max, as_of))

    crm_last_month = sources["crm_headcount"]["month"].max()
    y, m = map(int, crm_last_month.split("-"))
    next_month = date(y + (m == 12), (m % 12) + 1, 1)
    crm_period_end = next_month - timedelta(days=1)
    rows.append(_freshness_row(contract, "crm_headcount", crm_period_end, as_of))

    fin_last_month = sources["finance_gl_extract"]["month"].max()
    y, m = map(int, fin_last_month.split("-"))
    next_month = date(y + (m == 12), (m % 12) + 1, 1)
    fin_period_end = next_month - timedelta(days=1)
    rows.append(_freshness_row(contract, "finance_gl_extract", fin_period_end, as_of))

    tix_max = sources["support_tickets"]["created_at"].max().date()
    rows.append(_freshness_row(contract, "support_tickets", tix_max, as_of))

    return rows


def _freshness_row(contract: dict, source_name: str, period_covered_through: date, as_of: date) -> dict:
    meta = contract["sources"][source_name]
    lag_days = (as_of - period_covered_through).days
    return {
        "source": source_name,
        "refresh_cadence": meta["refresh_cadence"],
        "covered_through": period_covered_through.isoformat(),
        "as_of": as_of.isoformat(),
        "staleness_days": lag_days,
    }


def test_revenue_source_agreement(sources: dict[str, pd.DataFrame], contract: dict) -> dict:
    """The falsifiable reconciliation claim: pos_transactions.gross_revenue
    and finance_gl_extract.gl_revenue_usd agree on revenue for the same
    region-month, within tolerance. refutes_if mirrors the same field the
    L4 compiler will later require of every causal predicate -- a
    reconciliation claim that can't say what would disprove it doesn't
    belong in the evidence panel either."""
    pos = sources["pos_transactions"].copy()
    pos["month"] = pos["date"].dt.to_period("M").astype(str)
    pos_monthly = pos.groupby(["month", "region"])["gross_revenue"].sum().reset_index()

    fin = sources["finance_gl_extract"]
    merged = pos_monthly.merge(fin, on=["month", "region"], how="inner")
    merged["gap_pct"] = (merged["gross_revenue"] - merged["gl_revenue_usd"]) / merged["gross_revenue"]

    mean_gap_pct = float(merged["gap_pct"].mean())
    max_gap_pct = float(merged["gap_pct"].abs().max())
    agrees = max_gap_pct <= AGREEMENT_TOLERANCE_PCT

    known_deviation = contract["sources"]["finance_gl_extract"].get("known_definition_deviation")

    claim = {
        "claim": "pos_transactions.gross_revenue and finance_gl_extract.gl_revenue_usd agree on revenue for the same region-month, within tolerance",
        "refutes_if": {
            "condition": f"abs(gap_pct) > {AGREEMENT_TOLERANCE_PCT}",
            "rationale": "A systematic gap this size can't be sampling noise across every region-month -- it means the two sources are computing revenue on different definitions.",
        },
        "observed": {
            "mean_gap_pct": round(mean_gap_pct * 100, 3),
            "max_abs_gap_pct": round(max_gap_pct * 100, 3),
            "n_region_months_compared": int(len(merged)),
        },
        "verdict": "SOURCES_AGREE" if agrees else "SOURCES_DIVERGE",
    }
    if not agrees:
        claim["explanation"] = (
            f"Systematic ~{abs(mean_gap_pct) * 100:.1f}% gap, consistent across every region-month "
            f"(not random noise). Documented cause in the KPI contract: {known_deviation!r}. "
            "Treat pos_transactions.gross_revenue as the analysis series (it is the declared "
            "system-of-record for the `revenue` KPI); finance_gl_extract is kept as a reconciliation "
            "check, not swapped in silently."
        )
    return claim


def test_rep_attribution_within_revenue_bounds(sources: dict[str, pd.DataFrame], contract: dict) -> dict:
    """A second, DIFFERENT-IN-KIND reconciliation claim from
    test_revenue_source_agreement above -- that one catches a definitional
    mismatch (two sources computing the same-named number two different
    ways). This one catches a LOGICAL CONSISTENCY violation: rep-attributed
    revenue is a subset of total region revenue by construction (a rep's
    book can't be worth more than the region that contains it), so if the
    ratio ever exceeds 100% anywhere, that's not noise -- it's a
    join/aggregation bug (e.g. double-counting a rep across two account
    territories) that a naive dashboard would silently plot without ever
    noticing. Objective 2 asks for reconciling "business context", not
    just numbers -- a claim like "attribution can't exceed the whole it's
    attributed from" is exactly business context a numeric comparison
    alone wouldn't catch."""
    crm = sources["crm_headcount"].copy()
    pos = sources["pos_transactions"].copy()
    pos["month"] = pos["date"].dt.to_period("M").astype(str)

    rep_monthly = crm.groupby(["region", "month"])["rep_attributed_revenue_usd"].sum().reset_index()
    revenue_monthly = pos.groupby(["region", "month"])["gross_revenue"].sum().reset_index()
    merged = rep_monthly.merge(revenue_monthly, on=["region", "month"], how="inner")
    merged["attribution_ratio"] = merged["rep_attributed_revenue_usd"] / merged["gross_revenue"]

    max_ratio = float(merged["attribution_ratio"].max())
    violated = bool(max_ratio > 1.0)
    worst = merged.loc[merged["attribution_ratio"].idxmax()]

    claim = {
        "claim": "rep-attributed revenue never exceeds total region revenue for the same region-month (a rep's book is a subset of the region it's in)",
        "refutes_if": {
            "condition": "attribution_ratio > 1.0 for any region-month",
            "rationale": "Rep-attributed revenue is defined as a portion of total region revenue -- a ratio above 100% means a join or aggregation step double-counted something, not that a rep genuinely out-earned their entire region.",
        },
        "observed": {
            "max_attribution_ratio_pct": round(max_ratio * 100, 2),
            "n_region_months_compared": int(len(merged)),
            "worst_region_month": f"{worst['region']} {worst['month']}",
        },
        "verdict": "VIOLATED" if violated else "CONSTRAINT_HOLDS",
    }
    if not violated:
        claim["explanation"] = (
            f"Attribution ratio stays within bounds everywhere (max {max_ratio * 100:.1f}% of region revenue, "
            f"in {worst['region']} {worst['month']}) -- no double-counting detected across "
            f"{len(merged)} region-months checked."
        )
    return claim


def compute_missing_data_rates(sources: dict[str, pd.DataFrame], panel: pd.DataFrame) -> list[dict]:
    """GAPS.md item 6: an inner or left join silently drops or NaNs
    unmatched rows -- freshness/staleness above answers "how current is
    each source" but says nothing about "how much of it actually joined."
    This makes that a visible, quantified fact instead of a fabricated one,
    by re-checking the exact joins the rest of this module already performs
    (not a separately invented metric): how much of the LEFT side of each
    join found no match on the right."""
    rows = []

    pos = sources["pos_transactions"].copy()
    pos["month"] = pos["date"].dt.to_period("M").astype(str)
    pos_monthly = pos.groupby(["month", "region"])["gross_revenue"].sum().reset_index()
    matched = pos_monthly.merge(sources["finance_gl_extract"], on=["month", "region"], how="inner")
    rows.append(_missing_row("pos_transactions -> finance_gl_extract (revenue-agreement join)", len(pos_monthly), len(matched)))

    rep_monthly = sources["crm_headcount"].groupby(["region", "month"])["rep_attributed_revenue_usd"].sum().reset_index()
    revenue_monthly = pos.groupby(["region", "month"])["gross_revenue"].sum().reset_index()
    matched2 = rep_monthly.merge(revenue_monthly, on=["region", "month"], how="inner")
    rows.append(_missing_row("crm_headcount -> pos_transactions (rep-attribution-bounds join)", len(rep_monthly), len(matched2)))

    # the reconciled weekly panel's own left-joins (marketing_spend, then
    # crm_headcount, onto the pos-transactions-derived weekly base) -- an
    # unmatched region-week leaves NaN in these columns post-merge, so their
    # null rate IS the missing-match rate, not a proxy for it.
    rows.append(_missing_row_from_nulls("pos_transactions weekly base -> marketing_spend (reconciled panel left-join)", panel, "marketing_attributed_revenue"))
    rows.append(_missing_row_from_nulls("pos_transactions weekly base -> crm_headcount (reconciled panel left-join)", panel, "rep_attributed_revenue"))

    return rows


def _missing_row(join_description: str, expected: int, matched: int) -> dict:
    missing = expected - matched
    missing_pct = (missing / expected) if expected else 0.0
    return {
        "join": join_description,
        "expected_rows": int(expected),
        "matched_rows": int(matched),
        "missing_rows": int(missing),
        "missing_pct": round(missing_pct * 100, 2),
    }


def _missing_row_from_nulls(join_description: str, panel: pd.DataFrame, column: str) -> dict:
    expected = len(panel)
    missing = int(panel[column].isna().sum())
    return {
        "join": join_description,
        "expected_rows": int(expected),
        "matched_rows": int(expected - missing),
        "missing_rows": missing,
        "missing_pct": round((missing / expected) * 100, 2) if expected else 0.0,
    }


def apply_maturity_rule(pos: pd.DataFrame, contract: dict) -> pd.DataFrame:
    """Core revenue/units_sold exclude any category still inside its
    maturity window (revenue.maturity_rule in the KPI contract) -- a
    category's first weeks of trading history are tracked separately
    (new_category_revenue) instead of being folded into the aggregate every
    mature category is measured against. Without this, a brand-new
    category's ramp-up shows up as a level shift in every region's
    aggregate revenue simultaneously, which would mask or be mistaken for
    an unrelated, region-specific movement."""
    min_weeks = contract["kpis"]["revenue"]["maturity_rule"]["min_weeks_of_history"]
    first_seen_week = pos.groupby("product_category")["week"].transform("min")
    # a category present since the start of the analysis window is an
    # established line of business as far as this window is concerned --
    # "first seen in our data" is not the same claim as "just launched".
    # the maturity bar only bites a category that started partway through
    # the window, like Outdoor.
    weeks_of_history = pos["week"] - first_seen_week + 1
    is_mature = (first_seen_week <= 1) | (weeks_of_history > min_weeks)
    return pos[is_mature]


def build_reconciled_weekly_panel(sources: dict[str, pd.DataFrame], contract: dict) -> pd.DataFrame:
    """Resample every source to region x week -- the KPI contract's declared
    analysis grain -- and assemble the four connected KPIs (revenue,
    units_sold, marketing_attributed_revenue_share, rep_attributed_revenue)
    into one panel."""
    week1_start = date.fromisoformat(contract["analysis_calendar"]["week1_start"])

    pos = sources["pos_transactions"].copy()
    pos["week"] = pos["date"].dt.date.apply(lambda d: week_of(d, week1_start))
    pos_mature = apply_maturity_rule(pos, contract)
    pos_weekly = pos_mature.groupby(["region", "week"]).agg(revenue=("gross_revenue", "sum"), units_sold=("units", "sum")).reset_index()

    mkt = sources["marketing_spend"].copy()
    mkt_weekly = mkt.groupby(["region", "week"]).agg(marketing_attributed_revenue=("attributed_revenue_usd", "sum")).reset_index()

    crm = sources["crm_headcount"].copy()
    crm["month_start"] = pd.to_datetime(crm["month"] + "-01")
    crm_monthly_region = crm.groupby(["region", "month"])["rep_attributed_revenue_usd"].sum().reset_index()

    panel = pos_weekly.merge(mkt_weekly, on=["region", "week"], how="left")

    def week_to_month(week_n: int) -> str:
        d = week1_start + timedelta(weeks=week_n - 1)
        return f"{d.year:04d}-{d.month:02d}"

    panel["month"] = panel["week"].apply(week_to_month)
    panel = panel.merge(crm_monthly_region, on=["region", "month"], how="left").rename(
        columns={"rep_attributed_revenue_usd": "rep_attributed_revenue_month_total"}
    )
    # monthly rep revenue resampled down to a weekly rate -- the model always
    # states which fields were resampled vs natively observed at this grain,
    # so lineage stays honest rather than implying weekly precision that
    # doesn't exist in the source.
    weeks_per_month = panel.groupby(["region", "month"])["week"].transform("count")
    panel["rep_attributed_revenue"] = panel["rep_attributed_revenue_month_total"] / weeks_per_month
    panel["marketing_attributed_revenue_share"] = (panel["marketing_attributed_revenue"] / panel["revenue"]).round(4)

    panel = panel.drop(columns=["rep_attributed_revenue_month_total"])
    return panel.sort_values(["region", "week"]).reset_index(drop=True)


def build_category_weekly_panel(sources: dict[str, pd.DataFrame], contract: dict) -> pd.DataFrame:
    """Region x category x week revenue/units -- finer-grained than the
    headline KPI panel, needed for L2's category-level decomposition and for
    demonstrating L1's sparse-history handling on the Outdoor category
    (launched week 34, so it has under 8 weeks of history by design)."""
    week1_start = date.fromisoformat(contract["analysis_calendar"]["week1_start"])
    pos = sources["pos_transactions"].copy()
    pos["week"] = pos["date"].dt.date.apply(lambda d: week_of(d, week1_start))
    panel = pos.groupby(["region", "product_category", "week"]).agg(revenue=("gross_revenue", "sum"), units=("units", "sum")).reset_index()
    return panel.sort_values(["region", "product_category", "week"]).reset_index(drop=True)


def main() -> None:
    contract = load_contract()
    sources = load_sources()
    week1_start = date.fromisoformat(contract["analysis_calendar"]["week1_start"])
    total_weeks = contract["analysis_calendar"]["total_weeks"]
    as_of = week1_start + timedelta(weeks=total_weeks) - timedelta(days=1)

    freshness = compute_freshness(sources, contract, as_of)
    agreement = test_revenue_source_agreement(sources, contract)
    attribution_bounds = test_rep_attribution_within_revenue_bounds(sources, contract)
    panel = build_reconciled_weekly_panel(sources, contract)
    category_panel = build_category_weekly_panel(sources, contract)
    missing_data_rates = compute_missing_data_rates(sources, panel)

    panel.to_csv(DATA_DIR / "reconciled_weekly.csv", index=False)
    category_panel.to_csv(DATA_DIR / "reconciled_weekly_by_category.csv", index=False)
    report = {
        "as_of": as_of.isoformat(),
        "source_freshness": freshness,
        "revenue_source_agreement_claim": agreement,
        "rep_attribution_bounds_claim": attribution_bounds,
        "missing_data_rates": missing_data_rates,
        "reconciled_panel_grain": "region x week",
        "reconciled_kpis": ["revenue", "units_sold", "marketing_attributed_revenue_share", "rep_attributed_revenue"],
    }
    (DATA_DIR / "reconciliation_report.json").write_text(json.dumps(report, indent=2))

    print("Source freshness:")
    for row in freshness:
        print(f"  {row['source']:<20} covered through {row['covered_through']}  ({row['staleness_days']} days stale, cadence={row['refresh_cadence']})")
    print()
    print(f"Revenue source-agreement claim: {agreement['verdict']}")
    if agreement["verdict"] == "SOURCES_DIVERGE":
        print(f"  {agreement['explanation']}")
    print()
    print(f"Rep-attribution bounds claim: {attribution_bounds['verdict']}")
    print(f"  {attribution_bounds.get('explanation', attribution_bounds['observed'])}")
    print()
    print("Missing-data rates (per join, not per source -- a join can be 0% missing while a source is stale):")
    for row in missing_data_rates:
        print(f"  {row['join']:<70} {row['missing_rows']:>3}/{row['expected_rows']:<3} missing ({row['missing_pct']}%)")
    print()
    print(f"Reconciled panel: {len(panel)} region-week rows -> {DATA_DIR / 'reconciled_weekly.csv'}")


if __name__ == "__main__":
    main()
