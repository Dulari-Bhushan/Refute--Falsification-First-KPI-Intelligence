"""
Runs as a step in run_pipeline.py (right after data/generate_synthetic_data.py)
and injects a SECOND, independent anomaly into the freshly-generated synthetic
dataset: a fulfillment disruption at CENTRAL_DC, affecting Central region
revenue from week 36 onward, plus the support-ticket cluster that narrates it
(so L3's clustering has something real to discover if a live LLM-generation
run ever targets Central -- see build_central_dc_tickets() below). Deliberately
does NOT touch West's or East's rows. Safe to run every pipeline invocation,
not just once: it's pure deterministic arithmetic/fixed content applied on top
of data/generate_synthetic_data.py's own seeded (SEED=42), reproducible output
-- chaining the two always reproduces the exact same calibrated numbers the
README/GAPS.md/UI narration quote, rather than only the first time someone
happened to hand-run both in order.
"""
import pandas as pd

WEEK1_START = pd.Timestamp("2025-01-06")
CUT_START_WEEK = 36
CUT_FACTOR = 0.85  # CENTRAL_DC units/revenue retained at 85% of what they'd otherwise be (a 15% cut)

pos = pd.read_csv("data/synthetic/pos_transactions.csv", parse_dates=["date"])
pos["week"] = ((pos["date"] - WEEK1_START).dt.days // 7) + 1

mask = (pos.region == "Central") & (pos.fulfillment_center == "CENTRAL_DC") & (pos.week >= CUT_START_WEEK)
print(f"Rows affected: {mask.sum()} (Central/CENTRAL_DC, week >= {CUT_START_WEEK})")

before_central_total = pos[pos.region == "Central"].groupby("week")["gross_revenue"].sum()

pos.loc[mask, "gross_revenue"] = (pos.loc[mask, "gross_revenue"] * CUT_FACTOR).round(2)
pos.loc[mask, "units"] = (pos.loc[mask, "units"] * CUT_FACTOR).round().astype(int)

after_central_total = pos[pos.region == "Central"].groupby("week")["gross_revenue"].sum()
print("\nCentral weekly revenue, before -> after (weeks 30-40):")
for w in range(30, 41):
    b, a = before_central_total.get(w, 0), after_central_total.get(w, 0)
    print(f"  week {w}: {b:>10,.0f} -> {a:>10,.0f}  ({(a - b) / b:+.1%})" if b else f"  week {w}: n/a")

pos = pos.drop(columns=["week"])
pos.to_csv("data/synthetic/pos_transactions.csv", index=False)
print("\nWrote data/synthetic/pos_transactions.csv")

# --- proportionally adjust the finance_gl_extract cross-check for the one
# month this touches, so the reconciliation report doesn't show a spurious
# NEW divergence exactly where the anomaly is (the whole point of this
# addition is a fulfillment-delay story, not a data-quality one) ---
gl = pd.read_csv("data/synthetic/finance_gl_extract.csv")
# weeks 36-39 fall in September 2025 (week 40 starts in October, which GL
# doesn't cover yet per the freshness table -- see printed week/date check below)
sept_weeks_affected = [w for w in range(CUT_START_WEEK, 40) if w <= 39]
print(f"\nSeptember weeks affected (<=39): {sept_weeks_affected}")
sept_before = before_central_total.reindex(sept_weeks_affected).sum()
sept_after = after_central_total.reindex(sept_weeks_affected).sum()
ratio = sept_after / sept_before if sept_before else 1.0
print(f"September retention ratio to apply to GL: {ratio:.4f}")

gl_mask = (gl.region == "Central") & (gl.month == "2025-09")
gl.loc[gl_mask, "gl_revenue_usd"] = (gl.loc[gl_mask, "gl_revenue_usd"] * ratio).round(2)
gl.to_csv("data/synthetic/finance_gl_extract.csv", index=False)
print("Wrote data/synthetic/finance_gl_extract.csv")

# --- sanity check: confirm week->date mapping for week 40 is indeed October ---
week40_start = WEEK1_START + pd.Timedelta(days=(40 - 1) * 7)
print(f"\nWeek 40 starts {week40_start.date()} (confirms it's outside GL's Sept coverage)")

# --- seed the CENTRAL_DC support-ticket cluster this anomaly's narrative
# depends on -- fixed content, not sampled, so no seed/RNG concerns; the
# dates (Aug 25 - Sep 1) independently precede the KPI's own week-37 onset,
# same precedence structure West's decoy/cause clusters rely on. ---
CENTRAL_DC_TICKETS = [
    ("T0061", "2025-08-25", "Central", "central_dc_delay", "CENTRAL_DC flagged a warehouse system migration causing order processing delays."),
    ("T0062", "2025-08-25", "Central", "central_dc_delay", "Customer escalation: Central region deliveries running late this week, ops cites a WMS transition at the DC."),
    ("T0063", "2025-08-25", "Central", "central_dc_delay", "Multiple complaints about CENTRAL_DC fulfillment slowdowns since the new system rollout."),
    ("T0064", "2025-08-26", "Central", "central_dc_delay", "Order shipped from CENTRAL_DC is running 5 days late, no tracking update."),
    ("T0065", "2025-08-27", "Central", "central_dc_delay", "Support queue seeing a cluster of 'where is my order' tickets tied to Central fulfillment."),
    ("T0066", "2025-08-28", "Central", "central_dc_delay", "Ops flagged recurring CENTRAL_DC dispatch delays tied to the warehouse system cutover."),
    ("T0067", "2025-09-01", "Central", "central_dc_delay", "Customer asking why Central orders are slower than usual since late August."),
    ("T0068", "2025-09-01", "Central", "central_dc_delay", "Carrier notified us of continued backlog at the Central distribution center post-migration."),
]

tickets = pd.read_csv("data/synthetic/support_tickets.csv")
if (tickets["ticket_id"] == CENTRAL_DC_TICKETS[0][0]).any():
    print(f"\n{CENTRAL_DC_TICKETS[0][0]} already present in support_tickets.csv -- skipping ticket injection (already run against this file).")
else:
    new_tickets = pd.DataFrame(CENTRAL_DC_TICKETS, columns=["ticket_id", "created_at", "region", "topic_seed", "text"])
    tickets = pd.concat([tickets, new_tickets], ignore_index=True)
    tickets.to_csv("data/synthetic/support_tickets.csv", index=False)
    print(f"\nAppended {len(new_tickets)} CENTRAL_DC ticket(s) to data/synthetic/support_tickets.csv")
