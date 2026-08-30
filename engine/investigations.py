"""
Registry of every real, independently root-caused investigation this run --
single source of truth for the region/kpi/timing-window/predicate-set
metadata that engine/l3_hypothesise.py, engine/l4_llm_generation.py, and
data/add_central_investigation.py all need to agree on. Mirrors ui/app.js's
INVESTIGATIONS list. Adding a third investigation later means adding one
entry here, not hunting down every place "West" or its week numbers were
hardcoded.
"""

from __future__ import annotations

from engine.l4_compiler import MARKETING_DOSE_RESPONSE_FIXTURE, PREDICATE_FIXTURES

CENTRAL_PREDICATES: list[dict] = [
    {
        "hypothesis_id": "h_central_dc_delay",
        "mechanism": "A warehouse-management-system migration at CENTRAL_DC caused a fulfillment backlog, suppressing Central revenue for orders routed through that center.",
        "test_archetype": "placebo",
        "treatment": {"dim": "fulfillment_center", "in": ["CENTRAL_DC"]},
        "control": {"dim": "fulfillment_center", "in": ["WEST_DC", "EAST_DC"]},
        "outcome": {"metric": "revenue", "expect": "decline"},
        "temporal": {"cause_onset": "2025-08-25", "kpi_onset": "2025-09-15"},
        "refutes_if": {
            "condition": "control_group_effect_size >= 0.6 * treatment_effect_size",
            "rationale": "If Central orders NOT fulfilled through CENTRAL_DC fell just as hard, the CENTRAL_DC-specific migration isn't doing the work.",
        },
    },
    {
        "hypothesis_id": "h_central_competitor_launch",
        "mechanism": "A competitor launch drew Electronics customers away in Central, the same shape as West's competitor-launch decoy.",
        "test_archetype": "specificity",
        "treatment": {"dim": "product_category", "in": ["Electronics"]},
        "control": {"dim": "product_category", "in": ["Home", "Apparel", "Accessories"]},
        "outcome": {"metric": "revenue", "expect": "decline"},
        "temporal": {"cause_onset": "2025-09-15", "kpi_onset": "2025-09-15"},
        "refutes_if": {
            "condition": "control_group_effect_size >= 0.6 * treatment_effect_size",
            "rationale": "If categories the competitor doesn't compete in fell just as hard, the decline isn't specific to competitive pressure on Electronics.",
        },
    },
]

CENTRAL_WINDOWS = {
    # weeks 34-35 deliberately excluded from the pre-window: CENTRAL_DC runs
    # unusually high those two weeks (ordinary noise, not the injected cut,
    # which only starts week 36) -- including them makes the pre-period look
    # like it was already trending up relative to the control group, which
    # is exactly the false pre-trend the parallel-trends check exists to
    # catch. See data/add_central_investigation.py for the diagnosis.
    "week": ((27, 32), (36, 39)),
    "month": (("2025-08", "2025-08"), ("2025-09", "2025-09")),  # unused (no rep_id-dim predicate here) but required by fetch_unit_panel's windows[time_col] lookup shape
}

INVESTIGATIONS: dict[str, dict] = {
    "West": {
        "kpi": "revenue",
        "predicates": PREDICATE_FIXTURES + [MARKETING_DOSE_RESPONSE_FIXTURE],
        "survived_hypothesis_id": "h_rep_attrition",
        "role": "ops_manager_west",
        "windows": None,  # None -> engine.l5_adjudicate's module-level WINDOWS default
    },
    "Central": {
        "kpi": "revenue",
        "predicates": CENTRAL_PREDICATES,
        "survived_hypothesis_id": "h_central_dc_delay",
        "role": "regional_vp",  # ops_manager_west's row_scope is West-only; regional_vp covers all three regions
        "windows": CENTRAL_WINDOWS,
    },
}
