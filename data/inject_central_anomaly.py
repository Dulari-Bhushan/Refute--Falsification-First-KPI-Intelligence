"""
One-off, hand-run script (not part of run_pipeline.py) that injects a SECOND,
independent anomaly into the already-generated synthetic dataset: a
fulfillment disruption at CENTRAL_DC, affecting Central region revenue from
week 36 onward. Deliberately does NOT touch West's or East's rows, and does
NOT re-run data/generate_synthetic_data.py (which would re-roll the RNG and
risk shifting every number the existing README/GAPS.md/UI narration already
quotes). Run once; the resulting CSVs become the new checked-in fixtures.
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
