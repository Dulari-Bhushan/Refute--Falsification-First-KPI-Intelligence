"""
L6 extension -- investigation-aware, LLM-synthesized action recommendations.

Where this sits relative to what already existed: engine/l6_narrate_ledger.py's
build_action_recommendation() was written for exactly one case -- West's
rep-attrition cause -- with the rep IDs, the L2 field names, and the
capacity check all hardcoded to it. It was never wired to accept a region,
and the two API endpoints that called it (`/api/action-recommendation`,
`/api/delivery-channel`) called adjudicate_all() with zero arguments, whose
defaults are region="West" -- so no matter which investigation was on
screen, the backend was always checking West's verdicts.

This module replaces that single-case function with a generic pipeline that
works for ANY investigation registered in engine/investigations.py:
  1. Pull every SURVIVED verdict for that investigation's (region, kpi) out
     of l5_verdicts.json -- hand-authored AND live-LLM-generated alike, the
     same file /api/hypotheses already serves (see engine/l4_llm_generation.py's
     _persist_llm_verdicts).
  2. Pull that investigation's L1 signal-detection numbers (business impact,
     changepoint week/confidence) for full context.
  3. Look up REAL operational-capacity data for whichever dimension the
     surviving hypothesis was tested on: rep_id -> crm_headcount.csv (via
     l6_narrate_ledger.check_capacity_constraint, already real), or
     fulfillment_center -> the new fulfillment_center_ops.csv (see
     data/generate_fulfillment_ops_data.py) via check_fulfillment_capacity_constraint
     below. Other dimensions (e.g. product_category) currently have no
     operational dataset -- that's surfaced honestly, not hidden.
  4. Hand ALL of that (mechanism, effect size, p-values, L1 context,
     operational feasibility numbers or their explicit absence) to an LLM
     and ask for a synthesized root-cause-to-action writeup, with an
     explicit instruction: ground every number in what's given, and when
     operational data is missing, still propose the most plausible action
     from the mechanism/statistics alone but SAY SO (inferred_without_operational_data=true)
     and cap confidence at Medium -- consistent with this project's whole
     ethos (see engine/l4_llm_generation.py's module docstring): an LLM
     output gets exactly as much trust as what backs it, never more because
     it sounds confident.
  5. backend="none" (or an LLM call failure) never breaks the panel -- it
     falls back to a deterministic composition using the exact same real
     evidence and operational numbers, just without LLM-synthesized prose,
     honestly labeled as such. Same "graceful degradation, not a failure
     state" pattern l4_llm_generation.py already uses for predicate generation.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd
import yaml
from pydantic import BaseModel

DATA_DIR = Path(__file__).parent.parent / "data" / "synthetic"
CONTRACT_PATH = Path(__file__).parent.parent / "semantic" / "kpi_contract.yaml"


class ActionRecommendationSchema(BaseModel):
    root_cause_analysis: str
    driver: str
    controllable_lever: str
    action: str
    expected_impact: str
    owner: str
    confidence: str
    monitoring_plan: str
    data_sources_used: list[str]
    inferred_without_operational_data: bool


# --------------------------------------------------------------------------
# Operational feasibility checks -- one per dimension a surviving hypothesis
# can be tested on. rep_id reuses the existing (already-real) West check;
# fulfillment_center is the new one this module adds data for.
# --------------------------------------------------------------------------


def check_fulfillment_capacity_constraint(fulfillment_center: str, contract: dict) -> dict | None:
    """The fulfillment_center-dimension equivalent of l6_narrate_ledger.py's
    check_capacity_constraint(): is the obvious lever (surge/overtime
    capacity) actually enough to clear this center's backlog within the
    contract's target window? Reads data/synthetic/fulfillment_center_ops.csv
    (see data/generate_fulfillment_ops_data.py for how it was built) --
    real numbers, not invented for this call."""
    ops_path = DATA_DIR / "fulfillment_center_ops.csv"
    if not ops_path.exists():
        return None
    ops = pd.read_csv(ops_path)
    rows = ops[ops.fulfillment_center == fulfillment_center]
    if rows.empty:
        return None
    latest = rows.sort_values("month").iloc[-1]

    incoming = float(latest["daily_incoming_orders"])
    capacity = float(latest["daily_processing_capacity_orders"])
    backlog = float(latest["orders_backlog"])
    boost_pct = contract["operational_constraints"]["max_overtime_capacity_boost_pct"]["value"]
    target_days = contract["operational_constraints"]["backlog_clear_target_days"]["value"]

    net_clear_rate_current = capacity - incoming
    boosted_capacity = capacity * (1 + boost_pct / 100)
    net_clear_rate_boosted = boosted_capacity - incoming

    days_current = round(backlog / net_clear_rate_current, 1) if net_clear_rate_current > 0 else None
    days_boosted = round(backlog / net_clear_rate_boosted, 1) if net_clear_rate_boosted > 0 else None
    fits = days_boosted is not None and days_boosted <= target_days

    return {
        "type": "fulfillment_capacity",
        "fulfillment_center": fulfillment_center,
        "month": str(latest["month"]),
        "wms_migration_status": str(latest["wms_migration_status"]),
        "orders_backlog": backlog,
        "daily_incoming_orders": incoming,
        "daily_processing_capacity_orders": capacity,
        "staff_headcount": int(latest["staff_headcount"]),
        "overtime_boost_pct": boost_pct,
        "boosted_capacity_orders": round(boosted_capacity, 1),
        "days_to_clear_backlog_at_current_capacity": days_current,
        "days_to_clear_backlog_with_max_overtime": days_boosted,
        "backlog_clear_target_days": target_days,
        "fits_within_target_window": fits,
    }


def _lookup_treatment_values(hypothesis_id: str, region: str) -> list[str] | None:
    """The surviving TestOutcome only stores `dim`, not the actual treatment
    values tested (n_treatment_units is a count, not the values themselves)
    -- those live on the ORIGINAL predicate. Checked against
    engine.investigations first (hand-authored fixtures), then against
    data/synthetic/l4_llm_generated_predicates.json (live-generated ones).
    Returns None if not found (e.g. a collision-renamed live predicate,
    whose stored id no longer matches the original) -- callers treat that
    as "no operational data available," not an error."""
    from engine.investigations import INVESTIGATIONS

    inv = INVESTIGATIONS.get(region, {})
    for p in inv.get("predicates", []):
        if p["hypothesis_id"] == hypothesis_id:
            return p["treatment"]["in"]

    llm_path = DATA_DIR / "l4_llm_generated_predicates.json"
    if llm_path.exists():
        for g in json.loads(llm_path.read_text()):
            pred = g.get("predicate")
            if pred and pred.get("hypothesis_id") == hypothesis_id:
                return pred["treatment"]["in"]
    return None


def gather_operational_context(primary_verdict: dict, region: str, contract: dict) -> dict | None:
    """Dispatches to the right feasibility check for whichever dimension the
    surviving hypothesis was tested on. Returns None (not an error) when no
    operational dataset exists for that dimension yet -- e.g. product_category
    has no equivalent table -- so the caller can be honest about the gap
    rather than fabricate a number."""
    from engine.l6_narrate_ledger import check_capacity_constraint

    dim = primary_verdict.get("dim")
    if dim == "rep_id":
        return {"type": "rep_capacity", **check_capacity_constraint(contract, region)}
    if dim == "fulfillment_center":
        values = _lookup_treatment_values(primary_verdict["hypothesis_id"], region)
        if not values:
            return None
        return check_fulfillment_capacity_constraint(values[0], contract)
    return None


# --------------------------------------------------------------------------
# Evidence gathering -- every SURVIVED verdict + L1 context for one investigation.
# --------------------------------------------------------------------------


def gather_hypothesis_evidence(region: str) -> dict:
    from engine.investigations import INVESTIGATIONS

    if region not in INVESTIGATIONS:
        raise ValueError(f"Unknown investigation region '{region}' -- must be one of {sorted(INVESTIGATIONS)}.")
    kpi = INVESTIGATIONS[region]["kpi"]

    verdicts = json.loads((DATA_DIR / "l5_verdicts.json").read_text())
    survived = [v for v in verdicts if v.get("region", "West") == region and v.get("kpi", "revenue") == kpi and v["verdict"] == "SURVIVED"]

    l1_results = json.loads((DATA_DIR / "l1_signal_results.json").read_text())
    l1 = next((r for r in l1_results if r["kpi"] == kpi and r["region"] == region), None)

    return {"region": region, "kpi": kpi, "l1": l1, "survived": survived}


# --------------------------------------------------------------------------
# LLM synthesis
# --------------------------------------------------------------------------

ACTION_SYSTEM_PROMPT = """You are the action-recommendation step of REFUTE, a falsification-first root-cause engine for business KPIs.

A hypothesis has SURVIVED real statistical falsification testing (difference-in-differences, a parallel-trends pre-check, a statistical power gate, and Benjamini-Hochberg correction for multiple comparisons) and is being reported as the confirmed cause of a KPI movement. Your job is to turn that confirmed cause into a concrete, actionable recommendation.

Ground every concrete number in the evidence given to you -- never invent a statistic, date, headcount, capacity figure, or DOLLAR AMOUNT that was not provided. The only dollar figure you are given is the L1 signal's business-impact estimate (the whole KPI movement, not a per-cause breakdown) -- if you want to express expected_impact in dollars, use that figure or a clearly-labeled fraction of it, and otherwise express it in the percentage/effect-size terms the evidence actually gives you (e.g. "23pp differential effect (BH-adjusted p=0.0063)") rather than fabricating a specific dollar estimate that was never computed. When OPERATIONAL CAPACITY DATA is given, use it to check whether the obvious action is actually FEASIBLE: if it doesn't fit within the stated target window, say what the shortfall is and propose the phased/escalated alternative instead of promising something the team cannot execute (do not silently ignore an infeasibility the data shows you). When NO operational data is available for this hypothesis's dimension, you may still propose the most plausible action implied by the mechanism itself, but you MUST say so explicitly (set inferred_without_operational_data=true) and set confidence no higher than "Medium" -- a recommendation with no feasibility check behind it is an informed guess, not a confirmed plan, and this system never lets an LLM-authored claim carry more trust than what actually backs it.

Output a JSON object with exactly these fields:
- root_cause_analysis: 2-3 sentences synthesizing WHY this happened, citing the mechanism and effect-size evidence given
- driver: short id/slug for the confirmed cause
- controllable_lever: the business lever this action pulls (e.g. "account reassignment", "fulfillment surge staffing", "WMS migration timeline")
- action: one concrete, specific action statement -- if operational data made the naive version infeasible, this must already be the phased/escalated version
- expected_impact: a STRING (not a bare number) quantifying the expectation using ONLY the effect-size/impact numbers given in the evidence, e.g. "~$14,000/month recoverable" or "23pp differential effect"
- owner: the role/team who should own executing this
- confidence: "High", "Medium", or "Low" -- High only if grounded in real operational feasibility data AND a statistically strong verdict
- monitoring_plan: what metric to watch and over what window to know if this worked
- data_sources_used: list of which provided evidence sections you actually used, from ["survived_hypothesis", "l1_signal", "operational_capacity"]
- inferred_without_operational_data: true if no operational_capacity data was provided and the action is inferred from mechanism/statistics alone"""


def build_action_user_prompt(evidence: dict, operational_context: dict | None) -> str:
    lines = [f"INVESTIGATION: {evidence['region']} region, {evidence['kpi']} KPI.", ""]
    if evidence["l1"]:
        l1 = evidence["l1"]
        lines.append(
            f"L1 SIGNAL DETECTION: business impact {l1['business_impact_pct']:+.1%} "
            f"(${l1['business_impact_abs_usd']:,.0f}), changepoint week {l1['changepoint_period_estimate']}, "
            f"posterior confidence {l1['changepoint_posterior_recent']:.2f}."
        )
    lines.append("")
    lines.append("SURVIVED HYPOTHESIS/ES (passed DiD + parallel-trends + power-gate + BH-correction):")
    for h in evidence["survived"]:
        bh = h.get("did_pvalue_bh")
        effect = h.get("did_effect")
        lines.append(
            f"- {h['hypothesis_id']} (source={h.get('source', 'hand-authored')}): \"{h.get('mechanism') or '(no mechanism text)'}\" "
            f"-- test_archetype={h['test_archetype']}, dim={h['dim']}, "
            f"effect={f'{effect:.3f}' if effect is not None else 'n/a'}, "
            f"BH-adjusted p={f'{bh:.4f}' if bh is not None else 'n/a'}, "
            f"n_treatment_units={h.get('n_treatment_units')}, n_control_units={h.get('n_control_units')}, "
            f"reason=\"{h.get('reason', '')}\""
        )
    lines.append("")
    if operational_context:
        lines.append(f"OPERATIONAL CAPACITY DATA (real, available -- use it to check feasibility):\n{json.dumps(operational_context, indent=2)}")
    else:
        lines.append(
            "OPERATIONAL CAPACITY DATA: none available for this hypothesis's dimension. "
            "Infer the action from the mechanism and statistical evidence alone, and set inferred_without_operational_data=true."
        )
    return "\n".join(lines)


def _fallback_action(evidence: dict, primary: dict, operational_context: dict | None, note: str | None = None) -> dict:
    """No LLM call made (backend="none", or a live call failed) -- compose
    the recommendation directly from the same real evidence and operational
    numbers, honestly labeled as raw-evidence rather than LLM-synthesized.
    Never a failure state, matching l4_llm_generation.py's own graceful-
    degradation pattern for predicate generation."""
    mechanism = primary.get("mechanism") or "(no mechanism text recorded)"
    effect = primary.get("did_effect")
    p = primary.get("did_pvalue_bh")
    impact_txt = f"{abs(effect) * 100:.0f}pp differential effect (BH-adjusted p={p:.4f})" if effect is not None and p is not None else "see ledger for effect size"

    action_text = f"Address the confirmed cause: {mechanism}"
    if operational_context and operational_context.get("type") == "rep_capacity":
        c = operational_context
        if not c["fits_within_capacity"]:
            action_text = (
                f"Reassign as many of the {c['accounts_needing_reassignment']} affected accounts as fit within existing capacity now "
                f"({c['staying_rep_headroom']} accounts of headroom under the {c['max_accounts_per_rep_ceiling']}-account ceiling); "
                f"the remaining {c['shortfall']} need a phased plan (temporary coverage or a hire)."
            )
        else:
            action_text = f"Reassign the {c['accounts_needing_reassignment']} affected accounts to existing staff -- fits within current headroom ({c['staying_rep_headroom']})."
    elif operational_context and operational_context.get("type") == "fulfillment_capacity":
        c = operational_context
        if c["fits_within_target_window"]:
            action_text = f"Apply up to {c['overtime_boost_pct']}% surge capacity at {c['fulfillment_center']} -- the backlog clears within the {c['backlog_clear_target_days']}-day target (est. {c['days_to_clear_backlog_with_max_overtime']} days)."
        else:
            eta = c["days_to_clear_backlog_with_max_overtime"]
            eta_txt = f"est. {eta} days" if eta is not None else "capacity still below intake even with max overtime -- backlog would never clear on staffing alone"
            action_text = (
                f"Escalate {c['fulfillment_center']}'s backlog beyond a staffing fix: even at the maximum sanctioned {c['overtime_boost_pct']}% "
                f"surge capacity, the {c['orders_backlog']:.0f}-order backlog would not clear within {c['backlog_clear_target_days']} days ({eta_txt}) -- "
                "this needs a WMS-migration-timeline decision (accelerate cutover or roll back), not more staff."
            )

    return {
        "root_cause_analysis": f"{(note + ' ') if note else 'No live LLM backend selected -- '}Raw evidence only: {mechanism}",
        "driver": primary["hypothesis_id"],
        "controllable_lever": "see action" if operational_context else "unknown -- no operational data exists yet for this dimension",
        "action": action_text,
        "expected_impact": impact_txt,
        "owner": f"{evidence['region']} regional ops",
        "confidence": "Medium" if operational_context else "Low",
        "monitoring_plan": f"Track {evidence['kpi']} in {evidence['region']} over the next 4 weeks for recovery toward the pre-onset baseline.",
        "data_sources_used": [k for k, v in {"survived_hypothesis": True, "l1_signal": evidence["l1"] is not None, "operational_capacity": operational_context is not None}.items() if v],
        "inferred_without_operational_data": operational_context is None,
    }


# --------------------------------------------------------------------------
# Local-backend (Qwen) support -- separate generator/tokenizer cache from
# engine.l4_llm_generation's, since outlines binds a Generator to one
# Pydantic schema (Predicate there, ActionRecommendationSchema here) at
# load time and the two calls can't share a cached instance.
# --------------------------------------------------------------------------

_action_generator = None
_action_tokenizer = None


def _load_action_model():
    global _action_generator, _action_tokenizer
    if _action_generator is not None:
        return _action_generator, _action_tokenizer
    import outlines
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    from engine.l4_llm_generation import MODEL_NAME

    device = "cuda" if torch.cuda.is_available() else "cpu"
    _action_tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForCausalLM.from_pretrained(MODEL_NAME, dtype=torch.bfloat16 if device == "cuda" else torch.float32, device_map=device)
    outlines_model = outlines.from_transformers(model, _action_tokenizer)
    _action_generator = outlines.Generator(outlines_model, ActionRecommendationSchema)
    return _action_generator, _action_tokenizer


def _call_local_action(messages: list[dict]) -> tuple[str, int, int, float]:
    generator, tokenizer = _load_action_model()
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    prompt_tokens = len(tokenizer(prompt)["input_ids"])
    start = time.perf_counter()
    raw = generator(prompt, max_new_tokens=700)
    latency_ms = (time.perf_counter() - start) * 1000
    completion_tokens = len(tokenizer(raw)["input_ids"])
    return raw, prompt_tokens, completion_tokens, latency_ms


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def generate_action_recommendation(region: str) -> dict:
    """The generic replacement for l6_narrate_ledger.build_action_recommendation():
    works for whichever investigation `region` names (anything registered in
    engine.investigations.INVESTIGATIONS), not just West."""
    from engine.l4_llm_generation import _call_openrouter, estimate_hosted_cost
    from engine.l6_narrate_ledger import get_ledger, telemetry_span
    from engine.llm_config import get_llm_config

    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    evidence = gather_hypothesis_evidence(region)

    if not evidence["survived"]:
        return {"has_action": False, "region": region, "kpi": evidence["kpi"]}

    # normally exactly one hypothesis survives per investigation; if more
    # than one genuinely does (real contradictory evidence), the strongest
    # (lowest BH-adjusted p) grounds the operational check, but every
    # survivor is still passed to the LLM as context.
    primary = min(evidence["survived"], key=lambda h: h["did_pvalue_bh"] if h.get("did_pvalue_bh") is not None else 1.0)
    operational_context = gather_operational_context(primary, region, contract)

    config = get_llm_config()
    backend = config["backend"]
    user_prompt = build_action_user_prompt(evidence, operational_context)
    meta = {"backend": backend, "model": None, "prompt_tokens": 0, "completion_tokens": 0, "latency_ms": 0.0, "cost_usd": 0.0}

    if backend == "none":
        parsed = _fallback_action(evidence, primary, operational_context)
    else:
        messages = [{"role": "system", "content": ACTION_SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}]
        try:
            if backend == "openrouter":
                if not config.get("api_key"):
                    raise RuntimeError("OpenRouter backend selected but no API key configured.")
                raw, pt, ct, latency = _call_openrouter(messages, config["api_key"], config["openrouter_model"])
                # captured immediately after the call succeeds, before schema
                # validation -- a real call was made (and, on OpenRouter,
                # really billed) even if the model's JSON shape then fails
                # validation below; telemetry must reflect that, not silently
                # report it as a $0/0-token no-op just because parsing failed.
                meta.update(model=config["openrouter_model"], prompt_tokens=pt, completion_tokens=ct, latency_ms=latency, cost_usd=estimate_hosted_cost(pt, ct))
                validated = ActionRecommendationSchema(**json.loads(raw))
                parsed = validated.model_dump()
            else:  # local
                from engine.l4_llm_generation import MODEL_NAME

                raw, pt, ct, latency = _call_local_action(messages)
                meta.update(model=MODEL_NAME, prompt_tokens=pt, completion_tokens=ct, latency_ms=latency, cost_usd=0.0)
                validated = raw if isinstance(raw, ActionRecommendationSchema) else ActionRecommendationSchema(**json.loads(raw))
                parsed = validated.model_dump()
        except Exception as e:  # noqa: BLE001 -- an LLM call/parse failure degrades to the honest fallback, never a 500
            parsed = _fallback_action(evidence, primary, operational_context, note=f"Live LLM call failed ({e}); showing raw-evidence composition instead.")

    ledger = get_ledger()
    run_id = f"action-{region}-{int(time.time())}"
    with telemetry_span(
        ledger, run_id, f"L6_action_recommendation_{region}",
        is_llm_call=backend != "none" and meta["model"] is not None,
        model=meta["model"], tokens_in=meta["prompt_tokens"], tokens_out=meta["completion_tokens"],
        cost_usd=meta["cost_usd"], override_latency_ms=meta["latency_ms"],
    ):
        pass
    ledger.close()

    return {
        "has_action": True,
        "region": region,
        "kpi": evidence["kpi"],
        "hypothesis_id": primary["hypothesis_id"],
        "action": parsed,
        "operational_context": operational_context,
        "backend": backend,
        "llm_meta": meta,
    }
