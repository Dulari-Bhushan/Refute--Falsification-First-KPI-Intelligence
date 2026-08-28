"""
L2 -- LOCALISE: where did an L1-flagged movement concentrate? Never why.

Two methods, both producing output tagged "kind": "localisation" -- a type
tag enforced at the data-structure level so nothing downstream can render
localisation as a causal claim (that's L3/L4's job, and only after a
hypothesis has survived falsification testing):

1. Exact price/volume/mix bridge (Revenue = Q x P): a standard FP&A
   three-term decomposition, exact by construction (the three terms sum to
   the observed delta with no residual error). Answers "was this a units
   story, a pricing story, or a category-composition story" -- structurally,
   before any hypothesis about *why* gets generated, which already rules out
   whole classes of candidate explanations (e.g. a pricing-driven story
   can't survive if the price effect term is ~0).

2. Monte-Carlo Shapley attribution across categories: which categories the
   decline concentrated in, with a bootstrap confidence interval (not a
   bare point estimate) reflecting the real sampling noise in daily
   transaction volume. The category "game" here is additive (categories
   don't meaningfully interact in how the underlying effect was generated),
   so Shapley reduces to each category's own contribution -- reported
   through the general permutation machinery anyway, since that's what
   correctly handles interaction effects when they do exist, not because
   this specific example needs them.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

DATA_DIR = Path(__file__).parent.parent / "data" / "synthetic"
CONTRACT_PATH = Path(__file__).parent.parent / "semantic" / "kpi_contract.yaml"

N_SHAPLEY_PERMUTATIONS = 2000
N_BOOTSTRAP = 500
RNG_SEED = 7


@dataclass
class PriceVolumeMixResult:
    kind: str  # always "localisation" -- never read as a causal claim downstream
    region: str
    pre_window_weeks: list[int]
    post_window_weeks: list[int]
    revenue_pre: float
    revenue_post: float
    delta: float
    volume_effect: float
    price_effect: float
    mix_interaction_effect: float


def price_volume_mix_bridge(pos_df: pd.DataFrame, region: str, pre_weeks: list[int], post_weeks: list[int]) -> PriceVolumeMixResult:
    """Exact three-term bridge: Volume effect + Price effect +
    Mix/interaction effect = observed delta, exactly (mix/interaction is a
    residual by construction, which is what makes the decomposition exact
    rather than approximate)."""
    df = pos_df[pos_df.region == region]
    pre = df[df.week.isin(pre_weeks)]
    post = df[df.week.isin(post_weeks)]

    q_pre, rev_pre = pre["units"].sum(), pre["gross_revenue"].sum()
    q_post, rev_post = post["units"].sum(), post["gross_revenue"].sum()
    avg_price_pre = rev_pre / q_pre if q_pre else 0.0
    avg_price_post = rev_post / q_post if q_post else 0.0

    volume_effect = (q_post - q_pre) * avg_price_pre
    price_effect = q_post * (avg_price_post - avg_price_pre)
    delta = rev_post - rev_pre
    mix_interaction_effect = delta - volume_effect - price_effect

    return PriceVolumeMixResult(
        kind="localisation",
        region=region,
        pre_window_weeks=pre_weeks,
        post_window_weeks=post_weeks,
        revenue_pre=round(float(rev_pre), 2),
        revenue_post=round(float(rev_post), 2),
        delta=round(float(delta), 2),
        volume_effect=round(float(volume_effect), 2),
        price_effect=round(float(price_effect), 2),
        mix_interaction_effect=round(float(mix_interaction_effect), 2),
    )


def shapley_values(players: list[str], value_fn, n_permutations: int = N_SHAPLEY_PERMUTATIONS, rng: np.random.Generator | None = None) -> dict[str, float]:
    """Generic Monte-Carlo Shapley value estimation via random permutation
    sampling (exact Shapley is O(2^n); this is O(m*n)). Works for any
    coalition value function, additive or not -- correctness for
    interacting players is the point of using this machinery at all, even
    though the specific value functions used below happen to be additive
    (see module docstring)."""
    rng = rng or np.random.default_rng(RNG_SEED)
    totals = {p: 0.0 for p in players}
    for _ in range(n_permutations):
        order = list(players)
        rng.shuffle(order)
        coalition: list[str] = []
        prev_value = value_fn(coalition)
        for p in order:
            coalition.append(p)
            new_value = value_fn(coalition)
            totals[p] += new_value - prev_value
            prev_value = new_value
    return {p: v / n_permutations for p, v in totals.items()}


def category_contribution_with_ci(pos_df: pd.DataFrame, region: str, pre_weeks: list[int], post_weeks: list[int]) -> list[dict]:
    """Shapley-attributes the region's revenue decline across product
    categories, then bootstraps the underlying daily transactions (with
    replacement, within each week) to get a genuine confidence interval on
    each category's contribution -- not a bare point estimate."""
    df = pos_df[pos_df.region == region].copy()
    categories = sorted(df["product_category"].unique())

    def point_estimate(frame: pd.DataFrame) -> dict[str, float]:
        pre = frame[frame.week.isin(pre_weeks)].groupby("product_category")["gross_revenue"].sum()
        post = frame[frame.week.isin(post_weeks)].groupby("product_category")["gross_revenue"].sum()
        deltas = {c: float(post.get(c, 0.0) - pre.get(c, 0.0)) for c in categories}

        def value_fn(coalition: list[str]) -> float:
            return sum(deltas[c] for c in coalition)

        return shapley_values(categories, value_fn, n_permutations=200)  # additive game -> permutation count barely matters, kept modest for speed

    point = point_estimate(df)

    rng = np.random.default_rng(RNG_SEED)
    boot_results: dict[str, list[float]] = {c: [] for c in categories}
    weeks_involved = df[df.week.isin(pre_weeks + post_weeks)]
    day_groups = {wk: g for wk, g in weeks_involved.groupby("week")}
    for _ in range(N_BOOTSTRAP):
        resampled_frames = []
        for wk, g in day_groups.items():
            idx = rng.integers(0, len(g), size=len(g))
            resampled_frames.append(g.iloc[idx])
        resampled = pd.concat(resampled_frames, ignore_index=True) if resampled_frames else weeks_involved
        sv = point_estimate(resampled)
        for c in categories:
            boot_results[c].append(sv[c])

    out = []
    for c in categories:
        arr = np.array(boot_results[c])
        lo, hi = np.percentile(arr, [5, 95])
        out.append(
            {
                "kind": "localisation",
                "dimension": "product_category",
                "value": c,
                "shapley_contribution_usd": round(point[c], 2),
                "ci90_low": round(float(lo), 2),
                "ci90_high": round(float(hi), 2),
            }
        )
    return sorted(out, key=lambda r: r["shapley_contribution_usd"])


def rep_contribution(crm_df: pd.DataFrame, region: str, pre_month: str, post_months: list[str], region_revenue_loss_usd: float) -> list[dict]:
    """Rep-level breakdown of the revenue decline -- the source of the
    canonical "71% of the loss sits in four departed reps' accounts" figure.
    Each rep's share is measured against the region's actual total revenue
    loss (region_revenue_loss_usd, computed independently from
    pos_transactions) -- not against the sum of CRM-only deltas, which
    would be circular now that staying reps correctly show ~0 movement of
    their own (a share of "total CRM movement" would trivially be ~100%
    once only departing reps are moving at all).

    No confidence interval is reported here: crm_headcount's rep-level
    allocation is generated as a deterministic share of a counterfactual
    baseline (see data/generate_synthetic_data.py) with no independent
    per-rep noise, so a bootstrap CI would be a false precision claim
    rather than a real one -- unlike the category breakdown above, which
    does have genuine underlying daily transaction noise to resample."""
    df = crm_df[crm_df.region == region]
    pre = df[df.month == pre_month].set_index("rep_id")["rep_attributed_revenue_usd"]
    post = df[df.month.isin(post_months)].groupby("rep_id")["rep_attributed_revenue_usd"].mean()
    reps = sorted(df["rep_id"].unique())
    deltas = {r: float(post.get(r, 0.0) - pre.get(r, 0.0)) for r in reps}
    out = []
    for r in reps:
        share_of_decline = (deltas[r] / region_revenue_loss_usd) if region_revenue_loss_usd < 0 else 0.0
        out.append(
            {
                "kind": "localisation",
                "dimension": "rep_id",
                "value": r,
                "contribution_usd": round(deltas[r], 2),
                "share_of_total_decline": round(share_of_decline, 4),
                "note": "no confidence interval -- see docstring: this figure is not bootstrap-resampled",
            }
        )
    return sorted(out, key=lambda row: row["contribution_usd"])


def main() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    pos_df = pd.read_csv(DATA_DIR / "pos_transactions.csv", parse_dates=["date"])
    crm_df = pd.read_csv(DATA_DIR / "crm_headcount.csv")

    week1_start = pd.Timestamp(contract["analysis_calendar"]["week1_start"])
    pos_df["week"] = ((pos_df["date"] - week1_start).dt.days // 7) + 1
    pos_df = pos_df[pos_df.product_category != "Outdoor"]  # excluded from core revenue per the maturity rule -- see reconciliation.py

    region = "West"
    pre_weeks = [28, 29, 30]
    post_weeks = [32, 33, 34]  # equal-length window to pre_weeks -- comparing raw totals over unequal windows would conflate "the trend kept accruing" with "the level shifted"

    bridge = price_volume_mix_bridge(pos_df, region, pre_weeks, post_weeks)
    category_contrib = category_contribution_with_ci(pos_df, region, pre_weeks, post_weeks)

    monthly_region_revenue = pos_df[pos_df.region == region].assign(month=pos_df["date"].dt.to_period("M").astype(str)).groupby("month")["gross_revenue"].sum()
    post_avg = float(monthly_region_revenue.reindex(["2025-08", "2025-09"]).mean())
    region_revenue_loss_usd = post_avg - float(monthly_region_revenue.get("2025-07", 0.0))
    rep_contrib = rep_contribution(crm_df, region, pre_month="2025-07", post_months=["2025-08", "2025-09"], region_revenue_loss_usd=region_revenue_loss_usd)

    output = {
        "price_volume_mix_bridge": bridge.__dict__,
        "category_contribution": category_contrib,
        "rep_contribution": rep_contrib,
        "region_revenue_loss_usd_jul_to_aug": round(region_revenue_loss_usd, 2),
    }
    (DATA_DIR / "l2_localisation_results.json").write_text(json.dumps(output, indent=2))

    print(f"Price / Volume / Mix bridge (West, week {pre_weeks[0]}-{pre_weeks[-1]} -> week {post_weeks[0]}-{post_weeks[-1]}):")
    print(f"  revenue: {bridge.revenue_pre:,.0f} -> {bridge.revenue_post:,.0f}  (delta {bridge.delta:+,.0f})")
    print(f"  volume effect:        {bridge.volume_effect:+,.0f}")
    print(f"  price effect:         {bridge.price_effect:+,.0f}")
    print(f"  mix/interaction:      {bridge.mix_interaction_effect:+,.0f}")

    print("\nCategory contribution to the decline (Shapley, 90% CI):")
    for row in category_contrib:
        print(f"  {row['value']:<14} {row['shapley_contribution_usd']:>+10,.0f}   [{row['ci90_low']:>+9,.0f}, {row['ci90_high']:>+9,.0f}]")

    print("\nRep contribution to the decline:")
    departed = {"W1", "W2", "W3", "W4"}
    departed_share = sum(r["share_of_total_decline"] for r in rep_contrib if r["value"] in departed)
    for row in rep_contrib:
        flag = " (departed)" if row["value"] in departed else ""
        print(f"  {row['value']:<6} {row['contribution_usd']:>+10,.0f}   share={row['share_of_total_decline']:>6.1%}{flag}")
    print(f"\n  -> {departed_share:.0%} of the decline sits in the four departed reps' accounts.")


if __name__ == "__main__":
    main()
