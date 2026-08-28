"""
L4 -- LIVE LLM PREDICATE GENERATION (the step deferred until the templated
path was proven correct end to end -- see the Round 1 handoff doc's build
sequencing and README.md section 0).

Runs a small instruction-tuned model locally on GPU (Qwen2.5-3B-Instruct,
bfloat16, via transformers) and constrains its output to
engine.l4_compiler.Predicate's exact Pydantic schema using `outlines`
(token-level constrained decoding -- the model literally cannot emit a
token that would make the output fail schema validation, which is a
stronger guarantee than "ask nicely for JSON and retry on failure").

This does NOT replace the templated fixtures in engine/l4_compiler.py.
Those remain the proven, audited reference path. What this module adds is
exactly L3's narrow, spec-defined role extended one step further: "given a
topic cluster + a localisation slice, state a plausible causal mechanism in
natural language. It proposes; it does not adjudicate" -- except the
proposal is now a fully structured, testable predicate, not just prose,
because that's what L4's compiler actually needs to run a falsification
test on it.

Local, not hosted: no API key needed, and it makes the "LLM economics"
story concrete rather than aspirational -- the telemetry this module
writes shows genuine token counts and latency for a real model call, with
$0 marginal cost because the compute is local (a real, honestly-reported
number, not a placeholder), alongside a comparison to what an equivalent
hosted-API call would typically cost.

Schema-valid does not mean semantically valid: outlines guarantees the
JSON parses into a Predicate, but the model could still propose a
dimension value that doesn't exist in the data (e.g. a fulfillment center
that was never generated), or an unsupported archetype. validate_semantics()
below is the second gate -- semantic domain validation the schema itself
can't express. A predicate that fails either gate is rejected, not
silently coerced, exactly like the templated path's refutes_if check.

Model size note, empirically determined rather than assumed: Qwen2.5-1.5B-
Instruct was tried first and passes both validation gates reliably, but
its choice of WHICH dimension to test was weak -- it defaulted to
fulfillment_center for topics whose content (accessories pricing, a
competitor launch) more naturally maps to product_category, and it
initially mislabeled every predicate's archetype as "precedence"
regardless of structure (see SUPPORTED_ARCHETYPES below). Qwen2.5-3B-
Instruct fixes both: given the accessories/pricing ticket cluster, it
independently proposes treatment=Accessories vs control=[Electronics,
Home, Apparel] on product_category -- the exact structure of the
hand-written h_accessories_pricing fixture in engine/l4_compiler.py -- and
that independently-generated predicate reaches the identical INCONCLUSIVE
verdict through L5's adjudication. That convergence (not just "it produced
valid JSON") is the actual evidence this module works, not merely that it
runs.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

import outlines
import torch
import yaml
from transformers import AutoModelForCausalLM, AutoTokenizer

from engine.l3_hypothesise import TopicCandidate
from engine.l4_compiler import Predicate, PredicateRejected, validate_predicate

DATA_DIR = Path(__file__).parent.parent / "data" / "synthetic"
CONTRACT_PATH = Path(__file__).parent.parent / "semantic" / "kpi_contract.yaml"

MODEL_NAME = "Qwen/Qwen2.5-3B-Instruct"
MAX_NEW_TOKENS = 500
MAX_ATTEMPTS = 3

# What an equivalent call to a mid-tier hosted API would cost, for the
# telemetry comparison note -- illustrative, not this run's actual cost
# (which is $0, computed locally).
HOSTED_API_COST_PER_1K_INPUT_TOKENS = 0.003
HOSTED_API_COST_PER_1K_OUTPUT_TOKENS = 0.015

VALID_DIM_VALUES = {
    "fulfillment_center": ["WEST_DC", "EAST_DC", "CENTRAL_DC"],
    "product_category": ["Electronics", "Home", "Apparel", "Accessories"],
    "rep_id": ["W1", "W2", "W3", "W4", "W5", "W6"],
}
SUPPORTED_ARCHETYPES = {"placebo", "specificity"}
# dose_response is schema-legal but the SQL compiler doesn't implement it
# yet -- rejected at the semantic gate, not silently coerced.
# precedence is deliberately excluded from what THIS module generates: a
# precedence test compares a ticket topic's own BOCPD changepoint against
# the KPI's, which is already handled structurally by L3's generation-time
# filter and independently re-verified by l5_adjudicate.evaluate_precedence_test.
# This module only ever proposes data-DIMENSION-based mechanisms (a
# fulfillment center, a category, a rep), which placebo/specificity test
# correctly -- an earlier version of this prompt also offered "precedence"
# as a choice here and the model reliably mislabeled dimension-based
# predicates as "precedence" (a real finding about a 1.5B model's grasp of
# this specific taxonomy, not a bug in the compiler), which is a second
# reason to just not offer a choice that doesn't apply to what's being
# generated in the first place.

SYSTEM_PROMPT = """You are the hypothesis-generation step of REFUTE, a falsification-first root-cause engine for business KPIs.

Your only job is to propose ONE testable causal predicate -- a specific, falsifiable claim about what might have caused a KPI movement. You do NOT decide whether it's true; a separate deterministic statistical system tests it after you propose it.

Output a JSON object with exactly these fields:
- hypothesis_id: snake_case, prefixed with "h_", based on the mechanism (e.g. "h_shipping_delay")
- mechanism: one sentence, plain English, stating the proposed causal mechanism
- test_archetype: "placebo" if your mechanism claims the cause only reaches a specific slice of the business and unrelated slices should be unaffected (e.g. a mechanism specific to one fulfillment center, or specific to certain reps) -- "specificity" if your mechanism claims the cause should only affect certain products/categories and other categories should be unaffected. If you can't tell which fits, prefer "placebo".
- treatment: {"dim": one of "fulfillment_center"/"product_category"/"rep_id", "in": [list of values from the valid list you're given for that dimension]}
- control: {"dim": SAME dimension as treatment, "in": [different values, no overlap with treatment]}
- outcome: {"metric": "revenue", "expect": "decline"}
- temporal: {"cause_onset": a date string, "kpi_onset": a date string}
- refutes_if: {"condition": a concrete, checkable condition under which this hypothesis would be considered wrong, "rationale": why that condition would disprove the mechanism}

A hypothesis that cannot state a real refutation condition is worthless -- do not write a vague or trivially-true refutes_if.condition."""


@dataclass
class GenerationResult:
    predicate_dict: dict | None
    accepted: bool
    reason: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    raw_output: str


_generator = None
_tokenizer = None


def _load() -> tuple:
    global _generator, _tokenizer
    if _generator is not None:
        return _generator, _tokenizer
    device = "cuda" if torch.cuda.is_available() else "cpu"
    _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_NAME,
        dtype=torch.bfloat16 if device == "cuda" else torch.float32,
        device_map=device,
    )
    outlines_model = outlines.from_transformers(model, _tokenizer)
    _generator = outlines.Generator(outlines_model, Predicate)
    return _generator, _tokenizer


def build_user_prompt(topic: TopicCandidate, kpi_context: dict) -> str:
    return (
        f"KPI: {kpi_context['kpi']} in region {kpi_context['region']} moved {kpi_context['movement_pct']:+.1%} "
        f"starting week {kpi_context['kpi_onset_week']}.\n\n"
        f"A cluster of {topic.n_tickets} support tickets has its own independent changepoint at week "
        f"{topic.changepoint_week} (confidence {topic.changepoint_confidence:.2f}), which precedes the KPI's onset.\n"
        f'Representative ticket: "{topic.representative_text}"\n'
        f"Top terms in this cluster: {', '.join(topic.top_terms)}\n\n"
        "Valid dimensions and the ONLY values you may choose treatment/control from:\n"
        f"  fulfillment_center: {VALID_DIM_VALUES['fulfillment_center']}\n"
        f"  product_category: {VALID_DIM_VALUES['product_category']}\n"
        f"  rep_id: {VALID_DIM_VALUES['rep_id']}\n\n"
        "Propose ONE causal predicate that this ticket cluster's content plausibly suggests as a mechanism "
        "for the KPI movement."
    )


def validate_semantics(predicate: Predicate) -> None:
    """The second gate: schema-valid but not necessarily grounded in this
    dataset's actual values. Raises PredicateRejected, same as a schema
    failure -- both are "this predicate doesn't get tested," not "let's
    coerce it into something testable"."""
    valid_values = set(VALID_DIM_VALUES.get(predicate.treatment.dim, []))
    if not valid_values:
        raise PredicateRejected(f"Unknown dimension '{predicate.treatment.dim}'.")
    for v in predicate.treatment.in_ + predicate.control.in_:
        if v not in valid_values:
            raise PredicateRejected(f"'{v}' is not a valid value for dimension '{predicate.treatment.dim}' -- got {predicate.treatment.in_ + predicate.control.in_}, valid set is {sorted(valid_values)}.")
    if set(predicate.treatment.in_) & set(predicate.control.in_):
        raise PredicateRejected("Treatment and control groups overlap -- not a valid comparison.")
    if predicate.test_archetype not in SUPPORTED_ARCHETYPES:
        raise PredicateRejected(f"Archetype '{predicate.test_archetype}' is schema-legal but not implemented by the SQL compiler yet (supported: {sorted(SUPPORTED_ARCHETYPES)}).")


ADVERSARIAL_SYSTEM_PROMPT = """You are the adversarial-challenge step of REFUTE, a falsification-first root-cause engine for business KPIs.

A hypothesis has SURVIVED statistical testing and is about to be reported as the cause of a KPI movement. Your job is to argue against it: propose the STRONGEST plausible ALTERNATIVE causal predicate for the SAME KPI movement, using a DIFFERENT dimension or different treatment/control groups than the surviving hypothesis. You are not trying to be agreeable -- you are trying to find the best case that the surviving hypothesis has this wrong, so that case can be tested too before anyone trusts the original conclusion.

Output a JSON object with exactly these fields (same schema as any predicate):
- hypothesis_id: snake_case, prefixed with "h_adversarial_"
- mechanism: one sentence, plain English, stating your ALTERNATIVE causal mechanism -- it must be a genuinely different story than the surviving hypothesis, not a restatement of it
- test_archetype: "placebo" or "specificity" (see field definitions below)
- treatment: {"dim": one of "fulfillment_center"/"product_category"/"rep_id", "in": [values from the valid list]}
- control: {"dim": SAME dimension as treatment, "in": [different values, no overlap]}
- outcome: {"metric": "revenue", "expect": "decline"}
- temporal: {"cause_onset": a date string, "kpi_onset": a date string}
- refutes_if: {"condition": a concrete, checkable condition under which YOUR alternative would be considered wrong, "rationale": why that condition would disprove your mechanism}

test_archetype: "placebo" if your mechanism claims the cause only reaches a specific slice of the business (e.g. one fulfillment center, or certain reps) -- "specificity" if your mechanism claims only certain products/categories should be affected."""


def build_adversarial_prompt(survived_predicate: dict, kpi_context: dict) -> str:
    return (
        f"KPI: {kpi_context['kpi']} in region {kpi_context['region']} moved {kpi_context['movement_pct']:+.1%} "
        f"starting week {kpi_context['kpi_onset_week']}.\n\n"
        f"The hypothesis that survived testing: \"{survived_predicate['mechanism']}\" "
        f"(tested via {survived_predicate['treatment']['dim']}, treatment={survived_predicate['treatment']['in']} "
        f"vs control={survived_predicate['control']['in']}).\n\n"
        "Valid dimensions and the ONLY values you may choose treatment/control from:\n"
        f"  fulfillment_center: {VALID_DIM_VALUES['fulfillment_center']}\n"
        f"  product_category: {VALID_DIM_VALUES['product_category']}\n"
        f"  rep_id: {VALID_DIM_VALUES['rep_id']}\n\n"
        "Propose the strongest alternative explanation you can, using a DIFFERENT dimension than the one above "
        "if at all plausible, so it is a genuinely independent test, not a restatement of the same claim."
    )


def generate_adversarial_challenge(survived_predicate: dict, kpi_context: dict) -> GenerationResult:
    """Tier 3 stretch feature: before a SURVIVED verdict is trusted, ask the
    model to argue the other side -- propose the best counter-explanation
    it can construct, using a different dimension than the surviving
    predicate so it's a genuinely independent test. Then (see main()) run
    that challenge through the identical L4/L5 pipeline. If the challenge
    also survives, that's a real signal the original conclusion is more
    contested than a single surviving test suggests; if it's killed, that's
    evidence the original conclusion held up against the best case the
    model could make against it -- either way, this is one more test
    result feeding the ledger, not a debate settled by which side "sounds"
    more convincing."""
    return _generate_and_validate(ADVERSARIAL_SYSTEM_PROMPT, build_adversarial_prompt(survived_predicate, kpi_context))


def generate_predicate_for_topic(topic: TopicCandidate, kpi_context: dict) -> GenerationResult:
    return _generate_and_validate(SYSTEM_PROMPT, build_user_prompt(topic, kpi_context))


def _generate_and_validate(system_prompt: str, user_prompt: str) -> GenerationResult:
    generator, tokenizer = _load()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    prompt_tokens = len(tokenizer(prompt)["input_ids"])

    last_reason = "unknown failure"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        start = time.perf_counter()
        raw = generator(prompt, max_new_tokens=MAX_NEW_TOKENS)
        latency_ms = (time.perf_counter() - start) * 1000
        completion_tokens = len(tokenizer(raw)["input_ids"])

        try:
            raw_dict = json.loads(raw) if isinstance(raw, str) else raw
            predicate = validate_predicate(raw_dict)  # schema gate, incl. hard refutes_if check
            validate_semantics(predicate)  # domain gate
        except PredicateRejected as e:
            last_reason = f"attempt {attempt}: {e}"
            continue
        except Exception as e:  # noqa: BLE001 -- malformed output on this attempt, try again rather than crash the pipeline
            last_reason = f"attempt {attempt}: unparseable output ({e})"
            continue

        return GenerationResult(
            predicate_dict=raw_dict,
            accepted=True,
            reason=f"Accepted on attempt {attempt}.",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=latency_ms,
            raw_output=raw if isinstance(raw, str) else json.dumps(raw),
        )

    # graceful degradation: never fall back to guessing -- the caller falls
    # back to the templated fixtures, which is an accepted, audited outcome,
    # not a failure state.
    return GenerationResult(
        predicate_dict=None,
        accepted=False,
        reason=f"Rejected after {MAX_ATTEMPTS} attempts. Last reason: {last_reason}",
        prompt_tokens=prompt_tokens,
        completion_tokens=0,
        latency_ms=0.0,
        raw_output="",
    )


def estimate_hosted_cost(prompt_tokens: int, completion_tokens: int) -> float:
    return (prompt_tokens / 1000) * HOSTED_API_COST_PER_1K_INPUT_TOKENS + (completion_tokens / 1000) * HOSTED_API_COST_PER_1K_OUTPUT_TOKENS


def main() -> None:
    l1_results = json.loads((DATA_DIR / "l1_signal_results.json").read_text())
    west_revenue = next(r for r in l1_results if r["kpi"] == "revenue" and r["region"] == "West")
    kpi_context = {
        "kpi": "revenue",
        "region": "West",
        "kpi_onset_week": west_revenue["changepoint_period_estimate"],
        "movement_pct": west_revenue["business_impact_pct"],
    }

    l3_candidates_raw = json.loads((DATA_DIR / "l3_topic_candidates.json").read_text())
    candidates = [TopicCandidate(**c) for c in l3_candidates_raw if c["became_candidate"]]

    print(f"Loading {MODEL_NAME} on {'cuda' if torch.cuda.is_available() else 'cpu'}...")
    _load()  # warm up once, outside the per-topic timing loop
    print("Loaded.\n")

    results = []
    for topic in candidates:
        print(f"--- Generating predicate for topic cluster {topic.cluster_id} (top terms: {', '.join(topic.top_terms[:4])}) ---")
        result = generate_predicate_for_topic(topic, kpi_context)
        results.append((topic, result))
        if result.accepted:
            print(f"  ACCEPTED: {json.dumps(result.predicate_dict, indent=2)}")
        else:
            print(f"  REJECTED: {result.reason}")
        cost = estimate_hosted_cost(result.prompt_tokens, result.completion_tokens)
        print(f"  tokens: {result.prompt_tokens} in / {result.completion_tokens} out, {result.latency_ms:.0f}ms, $0.0000 actual (local GPU) / ~${cost:.4f} if this were a hosted API call\n")

    accepted = [r for _, r in results if r.accepted]
    print(f"\n{len(accepted)}/{len(results)} candidate topics produced an accepted, schema+semantically valid predicate.")
    (DATA_DIR / "l4_llm_generated_predicates.json").write_text(
        json.dumps(
            [
                {"cluster_id": t.cluster_id, "top_terms": t.top_terms, "accepted": r.accepted, "predicate": r.predicate_dict, "reason": r.reason, "prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens, "latency_ms": r.latency_ms}
                for t, r in results
            ],
            indent=2,
        )
    )

    # Run every accepted predicate through the exact same L5 adjudication
    # pipeline the templated fixtures use -- a live-LLM-generated predicate
    # is not treated as more trustworthy or less trustworthy than a
    # hand-written one once it passes the two acceptance gates above; it
    # gets the identical DiD / parallel-trends / power-gate / BH-correction
    # treatment. Also writes real telemetry (genuine token counts and
    # latency, is_llm_call=True) into the same ledger the templated path
    # uses, so the LLM-vs-non-LLM breakdown in the ledger is honest about
    # this run actually having made LLM calls.
    from engine.l5_adjudicate import adjudicate_all
    from engine.l6_narrate_ledger import get_ledger, telemetry_span, write_ledger_entries

    ledger = get_ledger()
    run_id = f"llm-{int(time.time())}"
    print("\n=== Adjudicating LLM-generated predicates through L5 (identical pipeline to the templated fixtures) ===")
    for topic, result in results:
        with telemetry_span(
            ledger,
            run_id,
            f"L4_llm_generate_{result.predicate_dict['hypothesis_id'] if result.accepted else f'cluster_{topic.cluster_id}_rejected'}",
            is_llm_call=True,
            model=MODEL_NAME,
            tokens_in=result.prompt_tokens,
            tokens_out=result.completion_tokens,
            cost_usd=0.0,  # local GPU compute -- genuinely $0, see estimate_hosted_cost() for the hosted-API comparison printed above
            override_latency_ms=result.latency_ms,  # generation already happened above; this span exists to log it, not to time it
        ):
            pass

        if not result.accepted:
            continue
        outcomes = adjudicate_all(predicates=[result.predicate_dict])
        if not outcomes:
            print(f"  {result.predicate_dict['hypothesis_id']}: could not be adjudicated (panel could not be built).")
            continue
        write_ledger_entries(ledger, run_id, outcomes)
        for o in outcomes:
            print(f"  {o.hypothesis_id:<32} [{o.verdict:<12}] {o.reason}")

    # --- Tier 3 stretch feature: adversarial challenge against the SURVIVED hypothesis ---
    from engine.l4_compiler import PREDICATE_FIXTURES

    survived_predicate = next(p for p in PREDICATE_FIXTURES if p["hypothesis_id"] == "h_rep_attrition")
    print(f"\n=== Adversarial challenge against '{survived_predicate['hypothesis_id']}' (the SURVIVED hypothesis) ===")
    challenge = generate_adversarial_challenge(survived_predicate, kpi_context)
    with telemetry_span(
        ledger,
        run_id,
        f"L4_adversarial_{challenge.predicate_dict['hypothesis_id'] if challenge.accepted else 'rejected'}",
        is_llm_call=True,
        model=MODEL_NAME,
        tokens_in=challenge.prompt_tokens,
        tokens_out=challenge.completion_tokens,
        cost_usd=0.0,
        override_latency_ms=challenge.latency_ms,
    ):
        pass

    if not challenge.accepted:
        print(f"  No valid adversarial challenge produced: {challenge.reason}")
    else:
        print(f"  Challenge: {json.dumps(challenge.predicate_dict, indent=2)}")
        challenge_outcomes = adjudicate_all(predicates=[challenge.predicate_dict])
        if challenge_outcomes:
            write_ledger_entries(ledger, run_id, challenge_outcomes)
            for o in challenge_outcomes:
                verdict_meaning = (
                    "the original conclusion is more contested than a single surviving test suggested -- review both."
                    if o.verdict == "SURVIVED"
                    else "the original conclusion held up against the strongest counter-case the model could construct."
                )
                print(f"  {o.hypothesis_id:<32} [{o.verdict:<12}] {o.reason}")
                print(f"    -> {verdict_meaning}")

    ledger.close()


if __name__ == "__main__":
    main()
