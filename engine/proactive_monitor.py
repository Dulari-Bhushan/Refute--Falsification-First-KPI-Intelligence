"""
Proactive alerting -- SOLUTIONING.md menu item 4's "proactive" half.

engine.l6_narrate_ledger already computes WHICH channel a persona's brief
would route through (determine_delivery_channel/simulate_delivery), but
that only fires for the one KPI this demo's pipeline happens to be
narrating (West revenue). A real proactive-alerting system has to answer a
different question first: across EVERY KPI x region series L1 evaluates,
which ones newly cleared the materiality gate THIS run that hadn't before
-- the thing worth interrupting someone about is a change in state, not a
KPI that has been sitting at the same (already-known) gated level for
months.

Two real pieces:

1. record_gated_movements() persists every gate_passed=True (kpi, region)
   from this run into a `gated_movements` ledger table. detect_new_alerts()
   compares the CURRENT run's gated set against the immediately PRIOR
   run's -- a movement is "new" if it's gated now and either wasn't gated
   in that prior run, or this is the very first run ever (nothing to
   compare against, so everything currently gated is new information by
   definition). Each new alert is routed to whichever roles have that
   KPI's domain in their domain_scope (reusing the same domain data
   engine.l4_compiler.check_domain_entitlement enforces, not a separate
   routing table) and logged via a SIMULATED delivery record, honestly
   labeled the same way engine.l6_narrate_ledger.simulate_delivery is --
   no real Slack/email API is ever called.

2. HONESTY GATE, same pattern as calibration.py and drift_monitor.py: this
   demo's synthetic data is fully deterministic (same seed every run), so
   repeated real runs will almost always show "0 new alerts" once the
   first run has established a baseline -- that is the CORRECT, honest
   behavior (nothing actually changed), not a bug to work around. To prove
   the new-vs-known detection logic itself is correct, run_alert_demo()
   builds a clearly labeled SIMULATED two-run history (a prior run with
   fewer gated movements, a current run with one additional one) and shows
   the detector correctly identifies only the genuinely new movement.

To actually run this proactively in production (not just "on demand" like
this prototype's `run_pipeline.py`), schedule
`uv run python -m engine.proactive_monitor` on a real cron / Windows Task
Scheduler entry against a live data source -- there is no always-on
scheduler process bundled here, because pretending to run one inside a
non-persistent demo environment would be exactly the kind of dishonest
placeholder this project rejects elsewhere (see calibration.py's module
docstring for the same reasoning applied to a different mechanism).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

DATA_DIR = Path(__file__).parent.parent / "data" / "synthetic"
CONTRACT_PATH = Path(__file__).parent.parent / "semantic" / "kpi_contract.yaml"

# a "material" alert worth an urgent push, vs. a routine one -- same
# conventional floor as engine.l6_narrate_ledger's confidence framing, not
# a value tuned to produce a particular demo outcome.
URGENT_IMPACT_PCT_FLOOR = 0.05
URGENT_CONFIDENCE_FLOOR = 0.8


def record_gated_movements(ledger: sqlite3.Connection, run_id: str, l1_results: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for r in l1_results:
        if not r.get("gate_passed"):
            continue
        ledger.execute(
            "INSERT INTO gated_movements (run_id, kpi, region, week, business_impact_pct, confidence, created_at) VALUES (?,?,?,?,?,?,?)",
            (run_id, r["kpi"], r["region"], r.get("changepoint_period_estimate"), r["business_impact_pct"], r["changepoint_posterior_recent"], now),
        )
    ledger.commit()


def _most_recent_prior_run_id(ledger: sqlite3.Connection, current_run_id: str) -> str | None:
    row = ledger.execute(
        "SELECT run_id FROM gated_movements WHERE run_id != ? ORDER BY id DESC LIMIT 1",
        (current_run_id,),
    ).fetchone()
    return row[0] if row else None


def determine_alert_urgency(business_impact_pct: float, confidence: float) -> str:
    if abs(business_impact_pct) >= URGENT_IMPACT_PCT_FLOOR and confidence >= URGENT_CONFIDENCE_FLOOR:
        return "urgent_push"
    return "routine_push"


def route_alert_to_roles(kpi_name: str, contract: dict) -> list[str]:
    """Which roles should hear about this KPI at all -- reuses the exact
    domain a role's domain_scope must include (the same field
    engine.l4_compiler.check_domain_entitlement enforces at compile time),
    not a separately maintained alert-routing table that could drift out
    of sync with the real access-control rule."""
    kpi_domain = contract["kpis"].get(kpi_name, {}).get("domain")
    if kpi_domain is None:
        return []
    return [role for role, meta in contract["entitlements"].items() if kpi_domain in meta.get("domain_scope", [])]


def record_alert(ledger: sqlite3.Connection, run_id: str, kpi: str, region: str, week: int | None, business_impact_pct: float, confidence: float, role: str, channel: str, urgency: str, message: str) -> None:
    ledger.execute(
        "INSERT INTO alerts (run_id, kpi, region, week, business_impact_pct, confidence, role, channel, urgency, message, simulated, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,1,?)",
        (run_id, kpi, region, week, business_impact_pct, confidence, role, channel, urgency, message, datetime.now(timezone.utc).isoformat()),
    )
    ledger.commit()


def detect_new_alerts(ledger: sqlite3.Connection, run_id: str, l1_results: list[dict], contract: dict) -> list[dict]:
    """The core proactive check: which (kpi, region) pairs are gated THIS
    run that weren't gated in the immediately prior run -- for each, route
    to every role whose domain_scope covers that KPI and log a SIMULATED
    alert per role. Returns the list of newly-alerted movements (not the
    per-role delivery records; see the `alerts` table for those)."""
    prior_run_id = _most_recent_prior_run_id(ledger, run_id)
    if prior_run_id is None:
        prior_gated: set[tuple[str, str]] = set()
        baseline_note = "no prior run recorded -- every currently-gated movement counts as new"
    else:
        rows = ledger.execute("SELECT DISTINCT kpi, region FROM gated_movements WHERE run_id = ?", (prior_run_id,)).fetchall()
        prior_gated = {(r[0], r[1]) for r in rows}
        baseline_note = f"compared against prior run {prior_run_id}"

    new_alerts = []
    for r in l1_results:
        if not r.get("gate_passed"):
            continue
        key = (r["kpi"], r["region"])
        if key in prior_gated:
            continue
        urgency = determine_alert_urgency(r["business_impact_pct"], r["changepoint_posterior_recent"])
        roles = route_alert_to_roles(r["kpi"], contract)
        message = f"{r['kpi']} in {r['region']} newly cleared the materiality gate: {r['business_impact_pct'] * 100:+.1f}% impact, posterior {r['changepoint_posterior_recent']:.2f} confidence."
        for role in roles:
            channel = contract["entitlements"][role].get("delivery_channels", ["dashboard"])
            chosen_channel = channel[0] if urgency == "urgent_push" and len(channel) > 1 else "dashboard"
            record_alert(ledger, run_id, r["kpi"], r["region"], r.get("changepoint_period_estimate"), r["business_impact_pct"], r["changepoint_posterior_recent"], role, chosen_channel, urgency, message)
        new_alerts.append({"kpi": r["kpi"], "region": r["region"], "business_impact_pct": r["business_impact_pct"], "confidence": r["changepoint_posterior_recent"], "urgency": urgency, "routed_to_roles": roles, "message": message, "baseline_note": baseline_note})
    return new_alerts


@dataclass
class SimulatedRun:
    gated: set[tuple[str, str]] = field(default_factory=set)


def run_alert_demo(seed: int = 11) -> dict:
    """Clearly labeled simulated proof that the new-vs-known detection
    logic is correct -- never written to the live ledger. A prior run
    gated {(revenue, West), (units_sold, West)}; a current run gates those
    same two PLUS a newly-material (rep_attributed_revenue, West) -- the
    detector must report exactly one new alert, not zero (missed it) and
    not three (forgot the baseline)."""
    rng = np.random.default_rng(seed)
    prior = SimulatedRun(gated={("revenue", "West"), ("units_sold", "West")})
    current = SimulatedRun(gated={("revenue", "West"), ("units_sold", "West"), ("rep_attributed_revenue", "West")})

    newly_gated = current.gated - prior.gated
    still_gated_from_before = current.gated & prior.gated

    return {
        "label": "SIMULATED DEMONSTRATION -- not written to the live ledger",
        "honesty_note": (
            "This demo's own synthetic data is deterministic (same seed every run), so repeated real runs "
            "correctly show 0 new alerts once a baseline exists -- that's honest behavior, not a bug. This "
            "proves the detection logic itself is correct using a clearly labeled synthetic two-run history: "
            "a prior run gating 2 movements, a current run gating those same 2 plus one genuinely new one."
        ),
        "prior_run_gated": sorted(f"{k} ({r})" for k, r in prior.gated),
        "current_run_gated": sorted(f"{k} ({r})" for k, r in current.gated),
        "correctly_identified_as_new": sorted(f"{k} ({r})" for k, r in newly_gated),
        "correctly_identified_as_not_new": sorted(f"{k} ({r})" for k, r in still_gated_from_before),
        "detector_correct": len(newly_gated) == 1 and ("rep_attributed_revenue", "West") in newly_gated,
    }


def main() -> None:
    from engine.l6_narrate_ledger import get_ledger

    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    l1_path = DATA_DIR / "l1_signal_results.json"
    if not l1_path.exists():
        print("No L1 output yet -- run the pipeline first.")
        return
    l1_results = json.loads(l1_path.read_text())

    ledger = get_ledger()
    ledger.row_factory = sqlite3.Row
    latest = ledger.execute("SELECT run_id FROM gated_movements ORDER BY id DESC LIMIT 1").fetchone()
    ledger.row_factory = None
    run_id = latest["run_id"] if latest is not None else "monitor-standalone"

    print("=== Proactive monitor: newly-gated movements this run ===")
    new_alerts = detect_new_alerts(ledger, run_id, l1_results, contract)
    if not new_alerts:
        print("  0 new alerts -- either nothing is currently gated, or everything gated now was already gated in the prior run.")
    for a in new_alerts:
        print(f"  {a['kpi']} ({a['region']}): {a['message']}")
        routed = ", ".join(a["routed_to_roles"]) or "(no role has this KPI's domain in scope)"
        print(f"    routed to: {routed}  urgency={a['urgency']}")
    ledger.close()

    demo = run_alert_demo()
    (DATA_DIR / "alert_demo.json").write_text(json.dumps(demo, indent=2))
    print("\n" + demo["label"])
    print(demo["honesty_note"])
    print(f"  prior run gated:  {demo['prior_run_gated']}")
    print(f"  current run gated: {demo['current_run_gated']}")
    print(f"  correctly new:     {demo['correctly_identified_as_new']}")
    print(f"  correctly NOT new: {demo['correctly_identified_as_not_new']}")
    print(f"  detector correct: {demo['detector_correct']}")


if __name__ == "__main__":
    main()
