"""
Generates REFUTE's synthetic multi-source dataset.

No production or scraped data is used, by design (see README section 5 / the
Round 1 handoff doc): falsification can only be *validated* against known
ground truth, so every effect in this dataset is planted deliberately and
recorded in scenario_manifest.json. That manifest is the answer key later
layers (and tests) check their verdicts against.

Four sources at four different grains/cadences, matching the semantic
contract in semantic/kpi_contract.yaml:
  - pos_transactions   daily,   region x product_category x fulfillment_center
  - marketing_spend    weekly,  region x channel
  - crm_headcount      monthly, region x rep
  - finance_gl_extract monthly, region  (deliberately reports revenue on a
                                          slightly different definition than
                                          pos_transactions, for the
                                          reconciliation-layer demo)
  - support_tickets    continuous, free text

One true cause (rep attrition in Region West) and four decoys, each planted
to fail a different falsification archetype:
  1. shipping_delay     -> killed by the PLACEBO test
  2. competitor_launch   -> killed by the SPECIFICITY test
  3. billing_complaints  -> killed by the PRECEDENCE test (reverse-caused)
  4. accessories_pricing -> returns INCONCLUSIVE (genuinely underpowered)

Plus a sparse-history KPI (Outdoor category, launched week 34) and a
definition-drift pair (pos_transactions vs finance_gl_extract) for the
reconciliation layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
OUT_DIR = Path(__file__).parent / "synthetic"

WEEK1_START = date(2025, 1, 6)  # a Monday
TOTAL_WEEKS = 40
LAST_DATE = WEEK1_START + timedelta(weeks=TOTAL_WEEKS) - timedelta(days=1)

REGIONS = ["West", "East", "Central"]
HOME_DC = {"West": "WEST_DC", "East": "EAST_DC", "Central": "CENTRAL_DC"}
FULFILLMENT_CENTERS = ["WEST_DC", "EAST_DC", "CENTRAL_DC"]

# category: (base units/day/region, unit price usd, launch week)
CATEGORIES = {
    "Electronics": (40, 120.0, 1),
    "Home": (30, 45.0, 1),
    "Apparel": (35, 30.0, 1),
    "Accessories": (5, 15.0, 1),   # deliberately low-volume -> the underpowered decoy lives here
    "Outdoor": (15, 60.0, 34),      # launches week 34 -> sparse-history scenario
}

CHANNELS = ["Search", "Social", "Display", "Email"]

CAUSE_ONSET_WEEK = 31   # reps stop actively servicing accounts
KPI_ONSET_WEEK = 32     # the revenue drop becomes material / detected
DECOY_ONSET_WEEK = 32   # shipping/competitor/accessories decoys surface here
REVERSE_CAUSE_WEEK = 33  # billing complaints spike AFTER the kpi moved (reverse causation)

# West rep roster. Shares are of a "managed accounts" revenue channel that
# is itself a fraction of total region revenue (REP_CHANNEL_SHARE_OF_REGION
# below) -- most of a region's revenue is self-serve/untracked-by-rep, which
# is why these shares are small relative to total region revenue even
# though the four departed reps collectively cause 71% of the region's
# total revenue LOSS (see CAUSE_ONSET_WEEK effect and the share arithmetic
# in generate_crm_headcount: what matters for the "71% of the loss" figure
# is the departed reps' share of the *decline*, not of the *total*).
# Sized empirically so the four departed reps' collective loss works out to
# ~71% of the region's actual total revenue loss (Jul -> Aug/Sep avg, per
# engine/l2_localise.py's rep_contribution) -- not 71% of total revenue,
# and not a closed-form value, since the region's realized loss depends on
# the Poisson-noised pos_transactions draw as well as the analytic impact
# factor. Calibrated against SEED=42; see the canonical worked example.
REP_CHANNEL_SHARE_OF_REGION = 0.13
WEST_REPS = [
    # rep_id, name, share of the managed-accounts channel, departs?
    ("W1", "Alicia Nguyen", 0.22, True),
    ("W2", "Marcus Webb", 0.20, True),
    ("W3", "Priya Raman", 0.16, True),
    ("W4", "Devon Ortiz", 0.13, True),
    ("W5", "Sofia Reyes", 0.16, False),
    ("W6", "Tom Baxter", 0.13, False),
]
EAST_REPS = [("E1", "Grace Kim", 0.35, False), ("E2", "Liam Chen", 0.30, False), ("E3", "Nora Fields", 0.35, False)]
CENTRAL_REPS = [("C1", "Omar Haddad", 0.4, False), ("C2", "Ivy Larsson", 0.6, False)]
REPS_BY_REGION = {"West": WEST_REPS, "East": EAST_REPS, "Central": CENTRAL_REPS}


def week_start(week_n: int) -> date:
    return WEEK1_START + timedelta(weeks=week_n - 1)


def week_of(d: date) -> int:
    return (d - WEEK1_START).days // 7 + 1


def month_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


CAUSE_ONSET_DATE = week_start(CAUSE_ONSET_WEEK)
KPI_ONSET_DATE = week_start(KPI_ONSET_WEEK)
DECOY_ONSET_DATE = week_start(DECOY_ONSET_WEEK)
REVERSE_CAUSE_DATE = week_start(REVERSE_CAUSE_WEEK)


def west_revenue_impact_factor(d: date) -> float:
    """The TRUE cause's effect: broad-based across every category and every
    fulfillment center, because it's driven by rep attrition, not a
    shipping- or product-specific mechanism. Applying it uniformly (not
    sliced by category/DC) is what makes the placebo and specificity tests
    correctly kill the shipping-delay and competitor-launch decoys later."""
    w = week_of(d)
    if w < CAUSE_ONSET_WEEK:
        return 1.0
    if w == CAUSE_ONSET_WEEK:
        return 0.965  # partial ramp as accounts start going unmanaged
    return 0.92  # sustained ~8% decline from the kpi-onset week onward


def accessories_extra_factor(region: str, d: date) -> float:
    """A second, independent, genuinely small effect confined to West's
    lowest-volume category. Real, but the category's tiny weekly volume
    keeps statistical power low -- this is the planted underpowered decoy,
    not a bug in the effect size."""
    if region == "West" and week_of(d) >= DECOY_ONSET_WEEK:
        return 0.93
    return 1.0


def daily_seasonality(d: date) -> float:
    # weekends run lighter than weekdays
    return 0.75 if d.weekday() >= 5 else 1.0


def generate_pos_transactions(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    d = WEEK1_START
    while d <= LAST_DATE:
        for region in REGIONS:
            home_dc = HOME_DC[region]
            for category, (base_units, price, launch_week) in CATEGORIES.items():
                if week_of(d) < launch_week:
                    continue
                trend = 1.0 + 0.00006 * (d - WEEK1_START).days  # gentle organic growth -- kept small deliberately, so BOCPD's level-change model isn't fighting a trend it doesn't account for
                expected_units = base_units * trend * daily_seasonality(d)
                if region == "West":
                    expected_units *= west_revenue_impact_factor(d)
                    if category == "Accessories":
                        expected_units *= accessories_extra_factor(region, d)
                units_total = max(0, rng.poisson(max(expected_units, 0.5)))
                if units_total == 0:
                    continue
                # split across fulfillment centers: mostly the home DC, with
                # a realistic overflow slice to other DCs -- this is what
                # keeps fulfillment_center from being perfectly collinear
                # with region, which the placebo test needs.
                overflow_others = [fc for fc in FULFILLMENT_CENTERS if fc != home_dc]
                home_share = rng.uniform(0.72, 0.85)
                split = rng.multinomial(
                    units_total,
                    [home_share] + [(1 - home_share) / 2] * 2,
                )
                for fc, units in zip([home_dc] + overflow_others, split):
                    if units <= 0:
                        continue
                    unit_price = price * rng.normal(1.0, 0.03)
                    rows.append(
                        {
                            "date": d.isoformat(),
                            "region": region,
                            "product_category": category,
                            "fulfillment_center": fc,
                            "units": int(units),
                            "gross_revenue": round(float(units) * unit_price, 2),
                        }
                    )
        d += timedelta(days=1)
    return pd.DataFrame(rows)


def generate_marketing_spend(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for week_n in range(1, TOTAL_WEEKS + 1):
        wk_start = week_start(week_n)
        for region in REGIONS:
            for channel in CHANNELS:
                base_spend = {"Search": 4200, "Social": 2600, "Display": 1800, "Email": 400}[channel]
                spend = max(0.0, base_spend * rng.normal(1.0, 0.08))
                # marketing stays on-plan through the anomaly window --
                # deliberately NOT a driver, so it serves as a control
                # confirming the movement isn't a marketing-spend story.
                roas = rng.normal(2.3, 0.2)
                attributed_revenue = round(spend * max(roas, 0.5), 2)
                rows.append(
                    {
                        "week": week_n,
                        "week_start": wk_start.isoformat(),
                        "region": region,
                        "channel": channel,
                        "spend_usd": round(spend, 2),
                        "attributed_revenue_usd": attributed_revenue,
                    }
                )
    return pd.DataFrame(rows)


def complete_months() -> list[str]:
    """Monthly-cadence sources only ever publish a month once it's over --
    reporting a partial trailing month would itself be a freshness bug, not
    a feature. Returns the "YYYY-MM" strings for months fully contained in
    the generated date range."""
    months = []
    d = WEEK1_START
    while d <= LAST_DATE:
        months.append(month_of(d))
        d += timedelta(days=1)
    months = sorted(set(months))
    last_full_month = month_of(LAST_DATE)
    last_day_num = int(LAST_DATE.strftime("%d"))
    days_in_last_month = (date(LAST_DATE.year, LAST_DATE.month % 12 + 1, 1) - timedelta(days=1)).day if LAST_DATE.month < 12 else 31
    if last_day_num < days_in_last_month:
        months = [m for m in months if m != last_full_month]
    return months


def _avg_impact_factor_for_month(region: str, month: str) -> float:
    """Average of west_revenue_impact_factor over every day in a month, used
    to deflate an already-depressed actual region revenue back to a
    counterfactual (undepressed) baseline. 1.0 for any region/month the
    attrition effect doesn't touch."""
    if region != "West":
        return 1.0
    y, m = map(int, month.split("-"))
    d = date(y, m, 1)
    total, n = 0.0, 0
    while d.month == m:
        total += west_revenue_impact_factor(d)
        n += 1
        d += timedelta(days=1)
    return total / n if n else 1.0


def generate_crm_headcount(pos_df: pd.DataFrame) -> pd.DataFrame:
    """A rep's book is sized against a *counterfactual* (undepressed) region
    baseline, not the actual post-attrition total -- otherwise a rep who
    lost no accounts would still show a mechanical decline purely because
    the region's pie shrank due to *other* reps' departures, which is not
    what "71% of the loss sits in four departed reps' accounts" is supposed
    to mean. Only departed reps' own books collapse; everyone else tracks
    the region's normal growth."""
    pos_df = pos_df.copy()
    pos_df["month"] = pd.to_datetime(pos_df["date"]).dt.to_period("M").astype(str)
    monthly_region_revenue = pos_df.groupby(["month", "region"])["gross_revenue"].sum().to_dict()

    rows = []
    months = complete_months()
    for month in months:
        for region, reps in REPS_BY_REGION.items():
            actual_region_revenue = monthly_region_revenue.get((month, region), 0.0)
            factor = _avg_impact_factor_for_month(region, month)
            counterfactual_region_revenue = actual_region_revenue / factor if factor else actual_region_revenue
            channel_revenue = counterfactual_region_revenue * REP_CHANNEL_SHARE_OF_REGION
            month_date = date.fromisoformat(month + "-01")
            for rep_id, rep_name, share, departs in reps:
                attrition_date = CAUSE_ONSET_DATE.isoformat() if departs else None
                active = not (departs and month_date >= CAUSE_ONSET_DATE.replace(day=1))
                if departs and not active:
                    # accounts go largely unmanaged -- residual trailing revenue only
                    rep_revenue = channel_revenue * share * 0.05
                else:
                    rep_revenue = channel_revenue * share
                rows.append(
                    {
                        "month": month,
                        "region": region,
                        "rep_id": rep_id,
                        "rep_name": rep_name,
                        "active": active,
                        "attrition_date": attrition_date,
                        "assigned_accounts": int(rng_accounts(rep_id)),
                        "rep_attributed_revenue_usd": round(rep_revenue, 2),
                    }
                )
    return pd.DataFrame(rows)


def rng_accounts(rep_id: str) -> int:
    # deterministic small account-book size per rep, just for narrative color
    return 8 + (hash(rep_id) % 6)


def generate_finance_gl_extract(pos_df: pd.DataFrame) -> pd.DataFrame:
    """Independently produced monthly revenue figure that nets out a 2%
    returns/discount allowance pos_transactions does not apply -- the
    planted definition-drift pair for the reconciliation layer."""
    pos_df = pos_df.copy()
    pos_df["month"] = pd.to_datetime(pos_df["date"]).dt.to_period("M").astype(str)
    monthly = pos_df.groupby(["month", "region"])["gross_revenue"].sum().reset_index()
    monthly = monthly[monthly["month"].isin(complete_months())]
    monthly["gl_revenue_usd"] = round(monthly["gross_revenue"] * 0.98, 2)
    return monthly[["month", "region", "gl_revenue_usd"]]


TICKET_TEMPLATES = {
    "shipping_delay": [
        "Order shipped from WEST_DC is running 4 days late, no tracking update.",
        "Customer escalation: West region deliveries delayed again this week, carrier backlog cited.",
        "Multiple complaints about WEST_DC fulfillment center delays since last week.",
        "Delivery promised in 2 days, took 6 -- carrier says West hub is backed up.",
        "West warehouse shipping delay flagged by ops as ongoing.",
        "Customer asking why West orders are slower than usual lately.",
        "Carrier notified us of continued congestion at the West distribution center.",
        "Another late delivery ticket, West region, carrier delay cited again.",
        "Ops flagged recurring WEST_DC dispatch delays this week.",
        "Support queue seeing a cluster of 'where is my order' tickets tied to West fulfillment.",
    ],
    "competitor_launch": [
        "Customer mentioned a rival electronics brand just launched a cheaper competing model.",
        "Sales rep note: prospect referenced a new competitor product announced this month.",
        "Social listening flagged buzz around a competitor's new electronics line.",
        "Customer asked if we'd price-match the newly launched competitor product.",
        "Rep call notes: competitor's launch event came up twice this week.",
        "Marketing flagged competitor ad spend spike coinciding with their new launch.",
        "Customer churned to a competitor citing their new product launch.",
        "Support ticket: customer comparing us unfavorably to competitor's new release.",
    ],
    "billing_complaints": [
        "Customer says their account rep hasn't responded in two weeks.",
        "Escalation: West account complaining nobody from sales has followed up since reassignment.",
        "Customer confused about who their account manager even is now.",
        "Billing question went unanswered for 10 days, customer frustrated.",
        "Account flagged as unmanaged, customer requesting a new point of contact.",
        "Customer says service has gotten worse since their old rep stopped responding.",
        "Repeat complaint: no account coverage, invoices going unexplained.",
        "Customer threatening to churn over lack of account management responsiveness.",
    ],
    "accessories_pricing": [
        "Customer asked about a recent price change on accessories items.",
        "Small complaint about accessories pricing test being confusing at checkout.",
        "Rep note: a few accessories customers pushed back on new pricing.",
        "Accessories category pricing experiment flagged by a customer as inconsistent.",
    ],
    "baseline_noise": [
        "Product quality praised in a 5-star review this week.",
        "Customer asked a general question about return policy.",
        "Checkout UX feedback: customer liked the new one-click flow.",
        "Routine inquiry about loyalty points balance.",
        "Customer complimented packaging on a recent Home category order.",
        "General question about warranty coverage on Electronics purchase.",
        "Customer asked about upcoming holiday shipping cutoffs.",
        "Positive feedback on delivery speed in the Central region.",
        "Routine product sizing question, Apparel category.",
        "Customer requested an invoice copy for expense reporting.",
        "Feedback: customer loves the Outdoor category's new arrivals.",
        "General inquiry about gift card balance.",
        "Customer asked about bulk order discounts.",
        "Positive review of Home category product durability.",
        "Routine address-change request.",
        "Customer asked about restock timing for a popular Electronics SKU.",
        "General satisfaction survey response, no issues raised.",
        "Customer praised support responsiveness in East region.",
        "Routine question about app login issue, unrelated to orders.",
        "Customer asked about product care instructions, Apparel category.",
        "Feedback on packaging sustainability, generally positive.",
        "Routine question about order tracking link not loading.",
    ],
}


def generate_support_tickets(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    ticket_id = 1

    def add(topic: str, texts: list[str], center_week: int, spread_weeks: int, region: str | None):
        nonlocal ticket_id
        for text in texts:
            offset_days = int(rng.normal(0, spread_weeks * 7 / 2.5))
            created = week_start(center_week) + timedelta(days=max(0, offset_days if offset_days > -21 else -21))
            rows.append(
                {
                    "ticket_id": f"T{ticket_id:04d}",
                    "created_at": created.isoformat(),
                    "region": region or rng.choice(REGIONS),
                    "topic_seed": topic,
                    "text": text,
                }
            )
            ticket_id += 1

    add("shipping_delay", TICKET_TEMPLATES["shipping_delay"], DECOY_ONSET_WEEK - 1, 2, "West")
    add("competitor_launch", TICKET_TEMPLATES["competitor_launch"], DECOY_ONSET_WEEK, 2, "West")
    add("billing_complaints", TICKET_TEMPLATES["billing_complaints"], REVERSE_CAUSE_WEEK, 2, "West")
    add("accessories_pricing", TICKET_TEMPLATES["accessories_pricing"], DECOY_ONSET_WEEK, 2, "West")

    # baseline noise spread evenly across the whole 40-week window, no changepoint
    noise = TICKET_TEMPLATES["baseline_noise"]
    for i, text in enumerate(noise):
        week_n = 1 + (i * TOTAL_WEEKS // len(noise))
        add("baseline_noise", [text], week_n, 6, None)

    df = pd.DataFrame(rows).sort_values("created_at").reset_index(drop=True)
    return df


def build_scenario_manifest() -> dict:
    return {
        "analysis_target": {
            "kpi": "revenue",
            "region": "West",
            "kpi_onset_week": KPI_ONSET_WEEK,
            "kpi_onset_date": KPI_ONSET_DATE.isoformat(),
            "observed_movement_pct": -0.08,
            "description": "Region West revenue fell ~8% starting week 32.",
        },
        "true_cause": {
            "hypothesis_id": "h_rep_attrition",
            "mechanism": "Four of six West sales reps departed; their accounts went unmanaged, suppressing revenue broadly across all categories and fulfillment centers.",
            "cause_onset_date": CAUSE_ONSET_DATE.isoformat(),
            "departed_reps": [r[0] for r in WEST_REPS if r[3]],
            "expected_share_of_loss": 0.71,
            "expected_verdict": "SURVIVED",
        },
        "decoys": [
            {
                "hypothesis_id": "h_shipping_delay",
                "mechanism": "Carrier delays at WEST_DC suppressed West revenue.",
                "designed_to_fail": "placebo",
                "why_it_fails": "The revenue decline is broad-based across all fulfillment centers serving West (driven by rep attrition), not confined to WEST_DC-fulfilled orders -- the control group (non-WEST_DC-fulfilled West orders) shows the same drop.",
                "expected_verdict": "KILLED",
            },
            {
                "hypothesis_id": "h_competitor_launch",
                "mechanism": "A competitor's product launch drew Electronics customers away.",
                "designed_to_fail": "specificity",
                "why_it_fails": "The decline is uniform across every product category, not confined to Electronics where the competitor actually competes -- unrelated categories (Home, Apparel) fell just as hard.",
                "expected_verdict": "KILLED",
            },
            {
                "hypothesis_id": "h_billing_complaints",
                "mechanism": "A spike in billing/account-service complaints caused customers to reduce spend.",
                "designed_to_fail": "precedence",
                "why_it_fails": "The complaint-volume changepoint (week 33) comes AFTER the kpi's own changepoint (week 32) -- the complaints are a downstream symptom of unmanaged accounts, not the cause.",
                "reverse_cause_onset_week": REVERSE_CAUSE_WEEK,
                "expected_verdict": "KILLED",
            },
            {
                "hypothesis_id": "h_accessories_pricing",
                "mechanism": "A pricing change in the Accessories category reduced West revenue.",
                "designed_to_fail": "power",
                "why_it_fails": "The effect is real but Accessories is West's lowest-volume category -- too few weekly observations to reach 80% power, so the honest verdict is INCONCLUSIVE, not KILLED.",
                "expected_verdict": "INCONCLUSIVE",
            },
        ],
        "sparse_history_scenario": {
            "kpi": "revenue",
            "category": "Outdoor",
            "launch_week": CATEGORIES["Outdoor"][2],
            "weeks_of_history_at_analysis_end": TOTAL_WEEKS - CATEGORIES["Outdoor"][2] + 1,
            "description": "Outdoor category launched week 34 -- fewer than 8 weeks of history by the end of the analysis window. L1 should widen its prior and report low confidence rather than extrapolate confidently.",
        },
        "definition_drift_scenario": {
            "sources": ["pos_transactions", "finance_gl_extract"],
            "kpi": "revenue",
            "deviation": "finance_gl_extract reports revenue net of a 2% returns/discount allowance; pos_transactions reports gross. The reconciliation layer must detect and report this as a refuted 'sources agree' claim, not silently average the two.",
        },
        "entitlement_scenario": {
            "roles": ["ops_manager_west", "regional_vp"],
            "description": "ops_manager_west may see West rep-level attrition detail; regional_vp sees cross-region aggregates but is denied rep-level detail at compile time.",
        },
        "generation": {"seed": SEED, "week1_start": WEEK1_START.isoformat(), "total_weeks": TOTAL_WEEKS},
    }


def main() -> None:
    rng = np.random.default_rng(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    pos_df = generate_pos_transactions(rng)
    marketing_df = generate_marketing_spend(rng)
    crm_df = generate_crm_headcount(pos_df)
    finance_df = generate_finance_gl_extract(pos_df)
    tickets_df = generate_support_tickets(rng)
    manifest = build_scenario_manifest()

    pos_df.to_csv(OUT_DIR / "pos_transactions.csv", index=False)
    marketing_df.to_csv(OUT_DIR / "marketing_spend.csv", index=False)
    crm_df.to_csv(OUT_DIR / "crm_headcount.csv", index=False)
    finance_df.to_csv(OUT_DIR / "finance_gl_extract.csv", index=False)
    tickets_df.to_csv(OUT_DIR / "support_tickets.csv", index=False)
    (OUT_DIR / "scenario_manifest.json").write_text(json.dumps(manifest, indent=2))

    print(f"pos_transactions:   {len(pos_df):>6} rows")
    print(f"marketing_spend:    {len(marketing_df):>6} rows")
    print(f"crm_headcount:      {len(crm_df):>6} rows")
    print(f"finance_gl_extract: {len(finance_df):>6} rows")
    print(f"support_tickets:    {len(tickets_df):>6} rows")

    west_weekly = pos_df[pos_df.region == "West"].copy()
    west_weekly["week"] = pd.to_datetime(west_weekly["date"]).apply(lambda d: week_of(d.date()))
    by_week = west_weekly.groupby("week")["gross_revenue"].sum()
    wk30, wk32 = by_week.get(30), by_week.get(32)
    if wk30 and wk32:
        print(f"\nWest revenue, week 30 -> week 32: {wk30:,.0f} -> {wk32:,.0f} ({(wk32/wk30 - 1) * 100:+.1f}%)")


if __name__ == "__main__":
    main()
