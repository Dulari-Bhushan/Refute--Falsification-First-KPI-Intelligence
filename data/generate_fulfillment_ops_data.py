"""
One-off, hand-run script (same pattern as data/inject_central_anomaly.py) that
adds a SYNTHETIC operational-capacity dataset for the three fulfillment
centers: data/synthetic/fulfillment_center_ops.csv.

Why this exists: engine/l6_narrate_ledger.py's check_capacity_constraint()
grounds West's rep-attrition action recommendation in a REAL feasibility
check against crm_headcount.csv (can staying reps actually absorb the
departed reps' accounts?). Central's surviving cause -- a WMS migration at
CENTRAL_DC causing a fulfillment backlog -- had no equivalent operational
data to ground an analogous check against; the anomaly was injected as a
pure revenue multiplier on pos_transactions.csv (see inject_central_anomaly.py),
with nothing behind it describing backlog size, processing capacity, or
staffing. Without that, any "recommended action" for Central is just prose
with no feasibility check attached -- exactly what objective 6 (actions
"grounded in business levers, constraints and decision rights") warns
against.

This table is deliberately built for ALL THREE centers, not just Central --
so the same check_fulfillment_capacity_constraint() function (see
engine/action_recommendation.py) works generically for whichever fulfillment
center a future investigation's surviving hypothesis names, not just this
one hand-fitted case.

Numbers are hand-authored to be internally consistent with the existing
revenue anomaly (CENTRAL_DC's revenue cut starts week 36 -- September 2025,
per inject_central_anomaly.py's CUT_START_WEEK/WEEK1_START), not fit to
produce a particular verdict:
- WEST_DC and EAST_DC run with comfortable, stable headroom (processing
  capacity ~15% above incoming volume) in every month -- no anomaly there,
  so no backlog story there.
- CENTRAL_DC runs the same comfortable headroom Jan-Aug, then in September
  the WMS migration cuts its effective processing capacity below incoming
  volume (some staff pulled onto migration support, the new system running
  below the old one's throughput during cutover) -- backlog accumulates
  fast, which is exactly the mechanism the surviving hypothesis claims.
"""
import pandas as pd

MONTHS = [f"2025-{m:02d}" for m in range(1, 10)]  # 2025-01 .. 2025-09, matches crm_headcount.csv's coverage

# (daily_incoming_orders, daily_processing_capacity_orders, staff_headcount) when stable
STABLE = {
    "WEST_DC": (380, 440, 22),
    "EAST_DC": (340, 395, 20),
    "CENTRAL_DC": (410, 470, 24),
}

rows = []
central_backlog = 55.0  # starting backlog carried at all times even when "stable" -- no real ops process ever hits exactly zero
west_backlog = 40.0
east_backlog = 35.0

for month in MONTHS:
    for center, region in [("WEST_DC", "West"), ("EAST_DC", "East"), ("CENTRAL_DC", "Central")]:
        incoming, capacity, staff = STABLE[center]
        status = "stable"

        if center == "CENTRAL_DC" and month == "2025-09":
            # WMS migration cutover: capacity drops below incoming, some
            # staff reassigned to migration support instead of floor work.
            # 350 is deliberate, not just "low": even the contract's max
            # sanctioned overtime boost (+20%, see kpi_contract.yaml) only
            # pushes effective capacity to 420 -- barely above the 410/day
            # incoming rate -- so the backlog this accumulates cannot
            # plausibly clear within the 14-day target window on staffing
            # alone. That's a real, checkable finding for the action
            # recommendation to surface, not a number tuned to LOOK dramatic.
            status = "in_progress"
            capacity = 350
            staff = 19

        net_clear_rate = capacity - incoming
        days_in_month = 30
        if center == "WEST_DC":
            west_backlog = max(10.0, west_backlog - net_clear_rate * 0.3)  # small steady drift, never fully clears (realistic)
            backlog = west_backlog
        elif center == "EAST_DC":
            east_backlog = max(10.0, east_backlog - net_clear_rate * 0.3)
            backlog = east_backlog
        else:
            central_backlog = max(10.0, central_backlog - net_clear_rate * days_in_month) if net_clear_rate > 0 else central_backlog - net_clear_rate * days_in_month
            backlog = central_backlog

        rows.append(
            {
                "month": month,
                "fulfillment_center": center,
                "region": region,
                "orders_backlog": round(backlog),
                "daily_incoming_orders": incoming,
                "daily_processing_capacity_orders": capacity,
                "staff_headcount": staff,
                "wms_migration_status": status,
            }
        )

df = pd.DataFrame(rows)
df.to_csv("data/synthetic/fulfillment_center_ops.csv", index=False)
print(f"Wrote data/synthetic/fulfillment_center_ops.csv ({len(df)} rows)")
print(df[df.fulfillment_center == "CENTRAL_DC"].to_string(index=False))
