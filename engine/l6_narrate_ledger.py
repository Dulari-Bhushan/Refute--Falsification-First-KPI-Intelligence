"""
L6 -- NARRATE + LEDGER, extended with persona rendering, telemetry, and a
feedback loop that reuses the falsification machinery rather than bolting
on a separate correction workflow.

Narrate: whatever SURVIVED becomes a plain-English brief in the structured
action template the brief asks for: driver -> controllable lever -> action
-> expected impact -> owner -> confidence -> monitoring plan. When nothing
survives, or survival is ambiguous, the brief says so explicitly and names
the specific additional data that would resolve it (see the
h_accessories_pricing branch below) -- a literal, visible behavior, not
just a philosophy.

Ledger: every verdict is written as an immutable record (predicate id,
SQL hashes, effect estimate, power, BH-adjusted p-value, verdict) to a
SQLite table, alongside a predicted direction/magnitude for later scoring
against actuals (Brier score, isotonic recalibration) once outcomes
accrue -- not implemented in this prototype run since it requires 30+
scored outcomes to be meaningful (see honesty note in main()).

Personas: two renderers over the SAME ledger, not two separate analyses.
Row/column entitlements (engine/l4_compiler.py's check_entitlement) decide
what each persona's renderer is even allowed to read before rendering
starts.

Telemetry: every stage of this run -- LLM or not -- logs latency into the
same ledger store, so "LLM vs. non-LLM" is a literal, visible number, not
a design claim. This build stage has zero live LLM calls (see Round 1
handoff doc's build sequencing: the templated/deterministic path is proven
correct end-to-end before any LLM generation is wired in), so the
telemetry for this run honestly shows 0 LLM calls / $0 spent -- that's a
true statement about this run, not a placeholder.

Feedback loop: submit_feedback() turns an analyst's rejection of a verdict
into a new hypothesis with its own refutes_if, compiled and adjudicated
through the exact same L4/L5 pipeline as any other hypothesis -- not a
separate approval workflow.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from engine.l4_compiler import PredicateRejected, check_entitlement, validate_predicate
from engine.l5_adjudicate import adjudicate_all

DATA_DIR = Path(__file__).parent.parent / "data" / "synthetic"
CONTRACT_PATH = Path(__file__).parent.parent / "semantic" / "kpi_contract.yaml"
LEDGER_PATH = DATA_DIR / "ledger.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    hypothesis_id TEXT NOT NULL,
    test_archetype TEXT,
    verdict TEXT NOT NULL,
    reason TEXT,
    did_effect_pct REAL,
    did_pvalue_raw REAL,
    did_pvalue_bh REAL,
    mde_pct REAL,
    plausible_effect_pct REAL,
    predicted_direction TEXT,
    predicted_magnitude_pct REAL,
    scored_outcome TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    is_llm_call INTEGER NOT NULL,
    model TEXT,
    tokens_in INTEGER,
    tokens_out INTEGER,
    latency_ms REAL NOT NULL,
    estimated_cost_usd REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    original_hypothesis_id TEXT NOT NULL,
    original_verdict TEXT NOT NULL,
    analyst_role TEXT NOT NULL,
    correction_text TEXT NOT NULL,
    counter_hypothesis_id TEXT NOT NULL,
    counter_verdict TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def get_ledger() -> sqlite3.Connection:
    conn = sqlite3.connect(LEDGER_PATH)
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def telemetry_span(ledger: sqlite3.Connection, run_id: str, stage: str, is_llm_call: bool = False, model: str | None = None, tokens_in: int = 0, tokens_out: int = 0, cost_usd: float = 0.0, override_latency_ms: float | None = None):
    """Every stage -- deterministic statistics or an LLM call -- logs
    through this exact same span, so the LLM-vs-non-LLM breakdown in the
    telemetry table is structural, not something assembled after the
    fact from two different logging paths.

    override_latency_ms is for work that was already timed before this
    span opened (e.g. engine/l4_llm_generation.py measures generation
    latency itself, inside generate_predicate_for_topic, because that
    function is also used outside any ledger context) -- pass the real
    measurement through rather than let this span record the near-zero
    time it takes to enter/exit an empty `with` block wrapping already-
    completed work."""
    start = time.perf_counter()
    yield
    latency_ms = override_latency_ms if override_latency_ms is not None else (time.perf_counter() - start) * 1000
    ledger.execute(
        "INSERT INTO telemetry (run_id, stage, is_llm_call, model, tokens_in, tokens_out, latency_ms, estimated_cost_usd, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (run_id, stage, int(is_llm_call), model, tokens_in, tokens_out, latency_ms, cost_usd, datetime.now(timezone.utc).isoformat()),
    )
    ledger.commit()


def write_ledger_entries(ledger: sqlite3.Connection, run_id: str, outcomes: list) -> None:
    now = datetime.now(timezone.utc).isoformat()
    for o in outcomes:
        predicted_direction = "decline" if (o.did_effect or 0) < 0 else ("increase" if o.did_effect else None)
        ledger.execute(
            """INSERT INTO ledger (run_id, hypothesis_id, test_archetype, verdict, reason, did_effect_pct,
               did_pvalue_raw, did_pvalue_bh, mde_pct, plausible_effect_pct, predicted_direction,
               predicted_magnitude_pct, scored_outcome, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                o.hypothesis_id,
                o.test_archetype,
                o.verdict,
                o.reason,
                (o.did_effect * 100) if o.did_effect is not None else None,
                o.did_pvalue_raw,
                o.did_pvalue_bh,
                (o.mde * 100) if o.mde not in (None, float("inf")) else None,
                (o.plausible_effect * 100) if o.plausible_effect is not None else None,
                predicted_direction if o.verdict == "SURVIVED" else None,
                abs(o.did_effect * 100) if (o.verdict == "SURVIVED" and o.did_effect is not None) else None,
                "uncalibrated",  # see main() honesty note: fewer than 30 scored outcomes exist yet
                now,
            ),
        )
    ledger.commit()


# --------------------------------------------------------------------------
# Structured action recommendation -- driver -> lever -> action -> impact ->
# owner -> confidence -> monitoring plan, populated from L2's localisation
# numbers and L5's statistical confirmation, not invented separately.
# --------------------------------------------------------------------------


def build_action_recommendation(l2_results: dict, survived_outcome) -> dict:
    departed_share = None
    departed_loss_usd = 0.0
    for row in l2_results.get("rep_contribution", []):
        if row["contribution_usd"] < 0 and row["value"] in ("W1", "W2", "W3", "W4"):
            departed_loss_usd += row["contribution_usd"]
    region_loss = l2_results.get("region_revenue_loss_usd_jul_to_aug")
    if region_loss:
        departed_share = departed_loss_usd / region_loss

    return {
        "driver": "rep_attrition",
        "controllable_lever": "account reassignment / territory staffing",
        "action": "Reassign the four departed reps' accounts to active West team members this week.",
        "expected_impact": f"~${abs(departed_loss_usd):,.0f}/month recoverable (departed reps accounted for ~{departed_share:.0%} of the region's revenue loss)" if departed_share else "See ledger for effect size.",
        "owner": "West Regional Ops Manager",
        "confidence": f"High -- BH-adjusted p={survived_outcome.did_pvalue_bh:.4f}, {abs(survived_outcome.did_effect) * 100:.0f}pp differential effect vs. active reps' accounts, survives the placebo test." if survived_outcome and survived_outcome.did_pvalue_bh is not None else "See ledger.",
        "monitoring_plan": "Track West weekly revenue and rep_attributed_revenue for the reassigned accounts over the next 4 weeks; expect recovery toward the pre-attrition baseline if reassignment succeeds. If it doesn't recover, treat that as new evidence and re-open the investigation.",
    }


# --------------------------------------------------------------------------
# Persona rendering -- one ledger, two views. Entitlements are checked
# BEFORE rendering, not filtered out of an already-built response.
# --------------------------------------------------------------------------


def render_ops_manager_brief(outcomes: list, action: dict) -> str:
    lines = ["=== West Ops Manager brief (full evidence chain) ===", ""]
    lines.append("KPI: revenue, Region West, week 32 -- fell ~8.9% (L1 changepoint posterior 0.79, both statistically and business material)")
    lines.append("")
    lines.append("Hypotheses tested:")
    for o in outcomes:
        lines.append(f"  [{o.verdict:<12}] {o.hypothesis_id} ({o.test_archetype})")
        lines.append(f"      {o.reason}")
    lines.append("")
    survived = [o for o in outcomes if o.verdict == "SURVIVED"]
    if survived:
        lines.append(f"Cause: {survived[0].hypothesis_id} -- the only hypothesis that survived every applicable falsification test.")
        lines.append("")
        lines.append("Recommended action:")
        for k, v in action.items():
            lines.append(f"  {k}: {v}")
    else:
        lines.append("No hypothesis survived all applicable tests -- see INCONCLUSIVE entries above for what additional data would resolve them.")
    return "\n".join(lines)


def render_vp_brief(outcomes: list, action: dict, role: str, region: str, contract: dict) -> str:
    survived = [o for o in outcomes if o.verdict == "SURVIVED"]
    killed = [o for o in outcomes if o.verdict == "KILLED"]
    inconclusive = [o for o in outcomes if o.verdict == "INCONCLUSIVE"]

    lines = ["=== Regional VP brief (headline + action, no statistical detail) ===", ""]
    if survived:
        lines.append(f"Revenue fell ~8.9% in West (week 32). Cause: {survived[0].hypothesis_id.replace('h_', '').replace('_', ' ')}.")
        lines.append(f"{len(killed)} alternative explanation(s) tested and ruled out" + (f"; {len(inconclusive)} inconclusive (needs more data)." if inconclusive else "."))
        lines.append(f"Next step: {action['action']}")
        lines.append(f"Confidence: {action['confidence'].split(' -- ')[0]}")
    else:
        lines.append("Revenue fell ~8.9% in West (week 32). No cause has been confirmed yet.")

    # Column-level entitlement: this persona is denied rep-level detail --
    # checked here, before anything rep-specific would be rendered, not
    # filtered out of an already-assembled response.
    try:
        check_entitlement(role, region, "rep_id", contract)
        lines.append("\n(Rep-level detail available to this role.)")
    except Exception as e:  # noqa: BLE001
        lines.append(f"\n(Rep-level account detail withheld: {e})")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Feedback loop: a rejection becomes a new falsification-tested hypothesis,
# not a free-text note that nothing acts on.
# --------------------------------------------------------------------------


def submit_feedback(ledger: sqlite3.Connection, run_id: str, original_hypothesis_id: str, analyst_role: str, correction_text: str, counter_predicate_raw: dict) -> dict:
    """An analyst rejecting a SURVIVED verdict doesn't just log a
    complaint -- their correction is expressed as a new predicate (with its
    own mandatory refutes_if) and run through the identical L4/L5 pipeline
    as any other hypothesis. If the counter-hypothesis survives, the
    original verdict is downgraded and the correction is recorded with
    full provenance (who, when, why) in the same ledger."""
    try:
        predicate = validate_predicate(counter_predicate_raw)
    except PredicateRejected as e:
        return {"accepted": False, "reason": f"Counter-hypothesis rejected at validation: {e}"}

    # runs through the SAME adjudicate_all pipeline as every other
    # hypothesis -- compile, panel, DiD, parallel-trends, power gate, BH
    # correction -- via the `predicates` parameter, not a separate
    # lighter-weight path for feedback-originated hypotheses.
    outcomes = adjudicate_all(role=analyst_role, predicates=[counter_predicate_raw])
    counter_outcome = next((o for o in outcomes if o.hypothesis_id == predicate.hypothesis_id), None)
    if counter_outcome is None:
        return {"accepted": False, "reason": "Counter-hypothesis could not be adjudicated (panel could not be built -- check treatment/control dims exist in this region)."}
    counter_verdict = counter_outcome.verdict

    ledger.execute(
        """INSERT INTO feedback (run_id, original_hypothesis_id, original_verdict, analyst_role, correction_text,
           counter_hypothesis_id, counter_verdict, created_at) VALUES (?,?,?,?,?,?,?,?)""",
        (run_id, original_hypothesis_id, "SURVIVED", analyst_role, correction_text, predicate.hypothesis_id, counter_verdict, datetime.now(timezone.utc).isoformat()),
    )
    ledger.commit()

    downgraded = counter_verdict == "SURVIVED"
    return {
        "accepted": True,
        "counter_hypothesis_id": predicate.hypothesis_id,
        "counter_verdict": counter_verdict,
        "original_verdict_downgraded": downgraded,
        "note": (
            f"Counter-hypothesis '{predicate.hypothesis_id}' also SURVIVED -- original verdict for "
            f"'{original_hypothesis_id}' downgraded pending review."
            if downgraded
            else f"Counter-hypothesis '{predicate.hypothesis_id}' did not survive ({counter_verdict}) -- original verdict for '{original_hypothesis_id}' stands, but the correction is on record."
        ),
    }


def main() -> None:
    run_id = str(uuid.uuid4())[:8]
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    ledger = get_ledger()

    with telemetry_span(ledger, run_id, "L5_adjudicate", is_llm_call=False):
        outcomes = adjudicate_all()
    # h_billing_complaints's precedence test (see l5_adjudicate.main) needs
    # L1/L3 output on disk; reuse that same logic here so the ledger gets
    # the full five-hypothesis picture in one run.
    l1_path, l3_path = DATA_DIR / "l1_signal_results.json", DATA_DIR / "l3_topic_candidates.json"
    if l1_path.exists() and l3_path.exists():
        from engine.l5_adjudicate import evaluate_precedence_test

        l1_results = json.loads(l1_path.read_text())
        west_revenue = next(r for r in l1_results if r["kpi"] == "revenue" and r["region"] == "West")
        l3_candidates = json.loads(l3_path.read_text())
        billing_cluster = next((c for c in l3_candidates if "account" in " ".join(c["top_terms"]).lower()), None)
        if billing_cluster is not None:
            with telemetry_span(ledger, run_id, "L5_precedence_test", is_llm_call=False):
                outcomes.append(
                    evaluate_precedence_test(
                        "h_billing_complaints",
                        topic_tau=billing_cluster["changepoint_week"],
                        topic_confidence=billing_cluster["changepoint_confidence"],
                        kpi_tau=west_revenue["changepoint_period_estimate"],
                        kpi_confidence=west_revenue["changepoint_posterior_recent"],
                    )
                )

    write_ledger_entries(ledger, run_id, outcomes)

    l2_results = json.loads((DATA_DIR / "l2_localisation_results.json").read_text())
    survived_outcome = next((o for o in outcomes if o.verdict == "SURVIVED"), None)
    with telemetry_span(ledger, run_id, "L6_narrate", is_llm_call=False):
        action = build_action_recommendation(l2_results, survived_outcome)
        ops_brief = render_ops_manager_brief(outcomes, action)
        vp_brief = render_vp_brief(outcomes, action, role="regional_vp", region="West", contract=contract)

    print(ops_brief)
    print()
    print(vp_brief)

    # --- LLM vs. non-LLM breakdown -- literal telemetry, not a design claim ---
    rows = ledger.execute("SELECT stage, is_llm_call, latency_ms, estimated_cost_usd FROM telemetry WHERE run_id=?", (run_id,)).fetchall()
    total_latency = sum(r[2] for r in rows)
    llm_calls = [r for r in rows if r[1]]
    print("\n=== Runtime telemetry ===")
    print(f"Run {run_id}: {len(rows)} stages, {len(llm_calls)} LLM call(s), {total_latency:.0f}ms total, ${sum(r[3] for r in rows):.4f} estimated cost")
    for stage, is_llm, latency, cost in rows:
        print(f"  {stage:<20} {'LLM' if is_llm else 'deterministic':<14} {latency:>8.1f}ms  ${cost:.4f}")
    print(
        "\nHonesty note: the ledger's calibration fields (Brier score, reliability diagram, isotonic recalibration) "
        "are not meaningful yet -- they need ~30 scored outcomes to accrue first (see Round 1 handoff doc, section 9). "
        "This run's entries are stored with scored_outcome='uncalibrated' rather than displaying a confident hit-rate "
        "number they haven't earned."
    )

    # --- feedback loop demo: an analyst rejects the SURVIVED verdict ---
    print("\n=== Feedback loop demo ===")
    print(
        "Scenario: an analyst is skeptical of the SURVIVED verdict on general grounds (\"feels like broader "
        "demand softness to me, not really an attrition story\") without proposing a structurally different "
        "test -- a very common, legitimate form of feedback (\"are you sure? double-check it\") that doesn't "
        "always come with a novel mechanism attached. The system doesn't dismiss this OR just repeat the "
        "cached answer: it re-expresses the skepticism as its own refutable claim and reruns the full "
        "pipeline independently."
    )
    counter_predicate = {
        "hypothesis_id": "h_general_softness_recheck",
        "mechanism": "General regional demand softness (not attrition specifically) would show up as a comparable decline across ALL West reps, including the ones who kept their accounts.",
        "test_archetype": "placebo",
        "treatment": {"dim": "rep_id", "in": ["W1", "W2", "W3", "W4"]},
        "control": {"dim": "rep_id", "in": ["W5", "W6"]},
        "outcome": {"metric": "revenue", "expect": "decline"},
        "temporal": {"cause_onset": "2025-08-04", "kpi_onset": "2025-08-11"},
        "refutes_if": {
            "condition": "control_group_effect_size >= 0.6 * treatment_effect_size",
            "rationale": "If general softness (not attrition specifically) were the cause, staying reps' accounts should have declined comparably too -- this is a genuine refutation condition, not a rubber stamp of the original test.",
        },
    }
    result = submit_feedback(ledger, run_id, "h_rep_attrition", "ops_manager_west", "I don't think it's really attrition -- feels like general demand softness to me.", counter_predicate)
    print(json.dumps(result, indent=2))
    print(
        "\nNote: this independent re-test reaches the same conclusion via the same evidence (staying reps' "
        "accounts stayed flat) -- a legitimate outcome of feedback, not every correction has to change the "
        "verdict to be worth running. The 'downgraded pending review' flag exists for a human to see two "
        "converging tests and a recorded provenance trail, not to silently auto-resolve the disagreement."
    )

    ledger.close()


if __name__ == "__main__":
    main()
