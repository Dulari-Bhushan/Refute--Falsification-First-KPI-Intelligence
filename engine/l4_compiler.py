"""
L4 -- FALSIFICATION COMPILER: the core engineering contribution.

The LLM (or, at this stage of the build, a human) never writes SQL. It
produces a typed, schema-validated "causal predicate" -- a claim about a
mechanism, structured as a treatment group, a control group, an outcome,
a timing claim, and a mandatory refutes_if condition. A separate,
deterministic compiler turns that predicate into parameterised, read-only
SQL against a whitelisted schema. Nothing here ever interpolates a
predicate's field values into a SQL string; table/column *identifiers* are
matched against a fixed registry (the only thing that legitimately can't
go through a bind parameter), and every *value* (region, category names,
week ranges) is passed as a sqlite bind parameter.

Per the build sequencing: this module is built and proven correct against
hand-written predicate fixtures first (see PREDICATE_FIXTURES below) --
covering the worked example's three hypotheses plus the two additional
decoys needed for the fuller evaluation (see data/generate_synthetic_data.py
scenario_manifest.json) -- before any live LLM generation is wired in.
That LLM step comes later in the build and falls back to these same
templated fixtures if it fails to produce a valid predicate; "refutes_if"
missing or empty is a hard rejection, not a warning, for either path.

Four of the archetypes compile to SQL (placebo, specificity, dose-response
run a treatment/control DiD panel query); precedence does not -- it
compares BOCPD changepoint estimates (reusing L1's machinery) between the
KPI series and a candidate cause's own time series, which is a timing
comparison, not a database query. Granger causality is named in the
original spec as a secondary corroborating check for precedence; it is not
implemented here (documented limitation, not a silent gap) because with
only a handful of weekly observations on either side of the window, a
Granger test's p-values would be unstable enough to be misleading rather
than informative -- the BOCPD tau comparison is the test this prototype
actually stands behind.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path
from typing import Literal

import pandas as pd
import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

DATA_DIR = Path(__file__).parent.parent / "data" / "synthetic"
CONTRACT_PATH = Path(__file__).parent.parent / "semantic" / "kpi_contract.yaml"

# --------------------------------------------------------------------------
# Predicate schema. hypothesis_id/mechanism/test_archetype/treatment/control/
# outcome/temporal/refutes_if are exactly the fields specified in the
# handoff doc's predicate JSON example.
# --------------------------------------------------------------------------


class DimFilter(BaseModel):
    dim: Literal["fulfillment_center", "product_category", "rep_id"]
    in_: list[str] = Field(alias="in", min_length=1)

    model_config = {"populate_by_name": True}


class Outcome(BaseModel):
    metric: Literal["revenue"]
    expect: Literal["decline", "increase"]


class Temporal(BaseModel):
    cause_onset: date
    kpi_onset: date


class RefutesIf(BaseModel):
    """Mandatory. A predicate that cannot state its own refutation
    condition is rejected before it is ever compiled or tested -- Popper's
    demarcation criterion enforced as a schema constraint."""

    condition: str = Field(min_length=1)
    rationale: str = Field(min_length=1)

    @field_validator("condition", "rationale")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("refutes_if fields cannot be blank")
        return v


class Predicate(BaseModel):
    hypothesis_id: str
    mechanism: str
    test_archetype: Literal["placebo", "dose_response", "precedence", "specificity"]
    treatment: DimFilter
    control: DimFilter
    outcome: Outcome
    temporal: Temporal
    refutes_if: RefutesIf

    @model_validator(mode="after")
    def treatment_control_same_dim(self) -> "Predicate":
        if self.test_archetype != "precedence" and self.treatment.dim != self.control.dim:
            raise ValueError("treatment and control must filter the same dimension for a DiD comparison")
        return self


class PredicateRejected(Exception):
    pass


def validate_predicate(raw: dict) -> Predicate:
    """The hard gate: schema validation IS the refutes_if enforcement.
    Pydantic raising here (missing/empty refutes_if, wrong archetype,
    mismatched dims) is what "reject before testing" means in code, not a
    prompt instruction hoping the LLM remembers to include a field."""
    try:
        return Predicate.model_validate(raw)
    except Exception as e:  # noqa: BLE001 -- deliberately broad: any schema failure is a rejection
        raise PredicateRejected(str(e)) from e


# --------------------------------------------------------------------------
# Whitelisted schema. Only these tables/columns can ever appear in a
# generated query -- identifiers are matched against this registry, never
# taken from predicate content directly.
# --------------------------------------------------------------------------

DIM_REGISTRY = {
    "fulfillment_center": {"table": "pos_transactions", "value_col": "gross_revenue", "time_col": "week", "grain_col": "fulfillment_center", "region_col": "region"},
    "product_category": {"table": "pos_transactions", "value_col": "gross_revenue", "time_col": "week", "grain_col": "product_category", "region_col": "region"},
    "rep_id": {
        "table": "crm_headcount",
        "value_col": "rep_attributed_revenue_usd",
        "time_col": "month",
        "grain_col": "rep_id",
        "region_col": "region",
        "requires_entitlement": "rep_detail_restricted",
    },
}
WHITELISTED_TABLES = {"pos_transactions", "crm_headcount"}


class EntitlementDenied(Exception):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def check_entitlement(role: str, region: str, dim: str, contract: dict) -> None:
    """Deny-by-default: raises EntitlementDenied unless the role's
    row_scope covers the requested region AND (if the dim needs it) the
    role's column_scope explicitly allows it. This runs at compile time --
    before any SQL is built -- so a denial can never be bypassed by asking
    for the evidence panel instead of the summary view."""
    entitlements = contract["entitlements"].get(role)
    if entitlements is None:
        raise EntitlementDenied(f"Unknown role '{role}'.")
    if region not in entitlements["row_scope"]["region"]:
        raise EntitlementDenied(f"Role '{role}' has no row-level access to region '{region}'.")
    dim_meta = DIM_REGISTRY[dim]
    required_scope = dim_meta.get("requires_entitlement")
    if required_scope and entitlements["column_scope"].get(required_scope) != "allow":
        raise EntitlementDenied(f"Role '{role}' is denied column-level access to '{required_scope}' (dimension '{dim}').")


# --------------------------------------------------------------------------
# Database + compiler
# --------------------------------------------------------------------------


def load_database() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    pos = pd.read_csv(DATA_DIR / "pos_transactions.csv", parse_dates=["date"])
    week1_start = pd.Timestamp(yaml.safe_load(CONTRACT_PATH.read_text())["analysis_calendar"]["week1_start"])
    pos["week"] = ((pos["date"] - week1_start).dt.days // 7) + 1
    # Outdoor is excluded from every core-revenue query for the same reason
    # it's excluded from the reconciled KPI panel (see reconciliation.py's
    # maturity rule): its week-34 launch would otherwise leak into a
    # fulfillment_center-sliced query (which isn't category-filtered) as a
    # spurious late-window revenue jump, unrelated to whatever hypothesis
    # is actually being tested.
    pos = pos[pos["product_category"] != "Outdoor"]
    pos.to_sql("pos_transactions", conn, index=False)

    crm = pd.read_csv(DATA_DIR / "crm_headcount.csv")
    crm.to_sql("crm_headcount", conn, index=False)
    return conn


def compile_query(dim: str, region: str, group_values: list[str], time_lo: int | str, time_hi: int | str) -> tuple[str, list]:
    """Builds parameterised, read-only SQL for one side (treatment or
    control) of a DiD comparison. `dim`, its table, and its columns come
    only from DIM_REGISTRY (never from predicate text); `region`,
    `group_values`, and the time range are bound as '?' parameters, never
    interpolated into the SQL string."""
    meta = DIM_REGISTRY[dim]
    table = meta["table"]
    if table not in WHITELISTED_TABLES:
        raise PredicateRejected(f"Table '{table}' is not whitelisted.")

    placeholders = ",".join("?" for _ in group_values)
    sql = (
        f"SELECT {meta['time_col']} AS period, SUM({meta['value_col']}) AS value "
        f"FROM {table} "
        f"WHERE {meta['region_col']} = ? AND {meta['grain_col']} IN ({placeholders}) "
        f"AND {meta['time_col']} BETWEEN ? AND ? "
        f"GROUP BY {meta['time_col']} ORDER BY {meta['time_col']}"
    )
    params = [region, *group_values, time_lo, time_hi]
    return sql, params


def compile_unit_query(dim: str, region: str, group_values: list[str], time_lo, time_hi) -> tuple[str, list]:
    """Same whitelisting/parameterisation discipline as compile_query, but
    grouped by (unit, period) rather than summed across the whole group --
    L5's clustered-SE DiD needs individual units (e.g. each fulfillment
    center, not "WEST_DC" pre-summed against "EAST_DC+CENTRAL_DC") to
    cluster standard errors at the treatment-unit level, per the spec."""
    meta = DIM_REGISTRY[dim]
    table = meta["table"]
    if table not in WHITELISTED_TABLES:
        raise PredicateRejected(f"Table '{table}' is not whitelisted.")
    placeholders = ",".join("?" for _ in group_values)
    sql = (
        f"SELECT {meta['grain_col']} AS unit, {meta['time_col']} AS period, SUM({meta['value_col']}) AS value "
        f"FROM {table} "
        f"WHERE {meta['region_col']} = ? AND {meta['grain_col']} IN ({placeholders}) "
        f"AND {meta['time_col']} BETWEEN ? AND ? "
        f"GROUP BY {meta['grain_col']}, {meta['time_col']} ORDER BY {meta['grain_col']}, {meta['time_col']}"
    )
    params = [region, *group_values, time_lo, time_hi]
    return sql, params


def fetch_unit_panel(conn: sqlite3.Connection, predicate: "Predicate", region: str, role: str, contract: dict, windows: dict[str, tuple]) -> pd.DataFrame:
    """Unit-level panel for L5: one row per (unit, period), tagged treat
    (0/1) and post (0/1) -- the shape a DiD regression needs, as opposed to
    compile_predicate/run_compiled_test's pre-aggregated group totals
    (which are what the quick treatment-vs-control printout in main() uses
    for a fast eyeball check)."""
    check_entitlement(role, region, predicate.treatment.dim, contract)
    time_col = DIM_REGISTRY[predicate.treatment.dim]["time_col"]
    pre_window, post_window = windows[time_col]
    time_lo, time_hi = pre_window[0], post_window[1]

    t_sql, t_params = compile_unit_query(predicate.treatment.dim, region, predicate.treatment.in_, time_lo, time_hi)
    c_sql, c_params = compile_unit_query(predicate.control.dim, region, predicate.control.in_, time_lo, time_hi)
    treatment = pd.read_sql_query(t_sql, conn, params=t_params)
    treatment["treat"] = 1
    control = pd.read_sql_query(c_sql, conn, params=c_params)
    control["treat"] = 0

    # traceability: every verdict downstream needs to be traceable back to
    # the EXACT query that produced its evidence, not just "we ran some
    # SQL somewhere" -- same predicate -> same query -> same hash, every
    # time, so a human (or the Engineer persona view) can recompute this
    # hash from the predicate alone and confirm nothing was tampered with.
    panel_attrs_sql = {
        "treatment_sql": t_sql,
        "treatment_params": t_params,
        "treatment_sql_hash": sql_hash(t_sql, t_params),
        "control_sql": c_sql,
        "control_params": c_params,
        "control_sql_hash": sql_hash(c_sql, c_params),
    }

    panel = pd.concat([treatment, control], ignore_index=True)
    if time_col == "week":
        pre_values = set(range(pre_window[0], pre_window[1] + 1))
        post_values = set(range(post_window[0], post_window[1] + 1))
    else:
        pre_values = set(_month_range(*pre_window))
        post_values = set(_month_range(*post_window))
    # periods strictly between the two windows (e.g. the ramp week) are
    # neither a clean pre- nor post-observation and are dropped, not
    # defaulted into "pre" -- including a partially-affected period on the
    # pre side would contaminate exactly the parallel-trends check that
    # period is meant to help validate.
    panel = panel[panel["period"].isin(pre_values | post_values)].copy()
    panel["post"] = panel["period"].isin(post_values).astype(int)
    panel.attrs["pre_window"] = pre_window
    panel.attrs["post_window"] = post_window
    panel.attrs["time_col"] = time_col
    panel.attrs.update(panel_attrs_sql)
    return panel


def _month_range(lo: str, hi: str) -> list[str]:
    y0, m0 = map(int, lo.split("-"))
    y1, m1 = map(int, hi.split("-"))
    out = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


def sql_hash(sql: str, params: list) -> str:
    """Same predicate -> same query -> same hash, every time -- what makes
    ledger entries reproducible and auditable (a human can diff the stated
    predicate against the generated query, or recompute this hash to
    confirm nothing was tampered with after the fact)."""
    payload = json.dumps({"sql": sql, "params": params}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


class CompiledTest(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    hypothesis_id: str
    test_archetype: str
    dim: str
    treatment_sql: str
    treatment_params: list
    control_sql: str
    control_params: list
    treatment_sql_hash: str
    control_sql_hash: str
    pre_window: tuple
    post_window: tuple


def compile_predicate(
    predicate: Predicate,
    region: str,
    role: str,
    contract: dict,
    windows: dict[str, tuple],
) -> CompiledTest:
    """The end-to-end compile step for SQL-backed archetypes (placebo,
    specificity, dose_response). Raises EntitlementDenied or
    PredicateRejected rather than silently degrading -- a denial or a
    rejection is itself part of the auditable record, not a code path that
    falls through to guessing.

    `windows` maps each possible time grain ("week", "month") to its own
    (pre_window, post_window) pair -- a placebo test on fulfillment_center
    runs against weekly pos_transactions, while one on rep_id runs against
    monthly crm_headcount, and those two grains' period numbers/labels are
    not interchangeable."""
    if predicate.test_archetype == "precedence":
        raise PredicateRejected("precedence predicates are not SQL-compiled -- see evaluate_precedence()")

    check_entitlement(role, region, predicate.treatment.dim, contract)

    time_col = DIM_REGISTRY[predicate.treatment.dim]["time_col"]
    pre_window, post_window = windows[time_col]
    time_lo, time_hi = pre_window[0], post_window[1]
    t_sql, t_params = compile_query(predicate.treatment.dim, region, predicate.treatment.in_, time_lo, time_hi)
    c_sql, c_params = compile_query(predicate.control.dim, region, predicate.control.in_, time_lo, time_hi)

    return CompiledTest(
        hypothesis_id=predicate.hypothesis_id,
        test_archetype=predicate.test_archetype,
        dim=predicate.treatment.dim,
        treatment_sql=t_sql,
        treatment_params=t_params,
        control_sql=c_sql,
        control_params=c_params,
        treatment_sql_hash=sql_hash(t_sql, t_params),
        control_sql_hash=sql_hash(c_sql, c_params),
        pre_window=pre_window,
        post_window=post_window,
    )


def run_compiled_test(conn: sqlite3.Connection, compiled: CompiledTest) -> dict:
    treatment = pd.read_sql_query(compiled.treatment_sql, conn, params=compiled.treatment_params)
    control = pd.read_sql_query(compiled.control_sql, conn, params=compiled.control_params)
    return {
        "hypothesis_id": compiled.hypothesis_id,
        "test_archetype": compiled.test_archetype,
        "dim": compiled.dim,
        "treatment_series": treatment,
        "control_series": control,
        "pre_window": compiled.pre_window,
        "post_window": compiled.post_window,
        "treatment_sql_hash": compiled.treatment_sql_hash,
        "control_sql_hash": compiled.control_sql_hash,
    }


# --------------------------------------------------------------------------
# Hand-written predicate fixtures -- the templated/deterministic path,
# built and proven correct before any live LLM generation (see module
# docstring). These are the same five hypotheses from
# data/generate_synthetic_data.py's scenario_manifest.json, expressed as
# validated predicates.
# --------------------------------------------------------------------------

PREDICATE_FIXTURES: list[dict] = [
    {
        "hypothesis_id": "h_shipping_delay",
        "mechanism": "Carrier delays at WEST_DC suppressed West revenue for orders fulfilled through that center.",
        "test_archetype": "placebo",
        "treatment": {"dim": "fulfillment_center", "in": ["WEST_DC"]},
        "control": {"dim": "fulfillment_center", "in": ["EAST_DC", "CENTRAL_DC"]},
        "outcome": {"metric": "revenue", "expect": "decline"},
        "temporal": {"cause_onset": "2025-08-04", "kpi_onset": "2025-08-11"},
        "refutes_if": {
            "condition": "control_group_effect_size >= 0.6 * treatment_effect_size",
            "rationale": "If West orders NOT fulfilled through WEST_DC fell just as hard, the WEST_DC-specific delay isn't doing the work.",
        },
    },
    {
        "hypothesis_id": "h_competitor_launch",
        "mechanism": "A competitor's product launch drew Electronics customers away in West.",
        "test_archetype": "specificity",
        "treatment": {"dim": "product_category", "in": ["Electronics"]},
        "control": {"dim": "product_category", "in": ["Home", "Apparel", "Accessories"]},
        "outcome": {"metric": "revenue", "expect": "decline"},
        "temporal": {"cause_onset": "2025-08-11", "kpi_onset": "2025-08-11"},
        "refutes_if": {
            "condition": "control_group_effect_size >= 0.6 * treatment_effect_size",
            "rationale": "If categories the competitor doesn't compete in fell just as hard, the decline isn't specific to competitive pressure on Electronics.",
        },
    },
    {
        "hypothesis_id": "h_accessories_pricing",
        "mechanism": "A pricing change in the Accessories category reduced West revenue.",
        "test_archetype": "specificity",
        "treatment": {"dim": "product_category", "in": ["Accessories"]},
        "control": {"dim": "product_category", "in": ["Electronics", "Home", "Apparel"]},
        "outcome": {"metric": "revenue", "expect": "decline"},
        "temporal": {"cause_onset": "2025-08-11", "kpi_onset": "2025-08-11"},
        "refutes_if": {
            "condition": "control_group_effect_size >= 0.6 * treatment_effect_size",
            "rationale": "If the broader business fell just as hard, this isn't an Accessories-specific pricing effect.",
        },
    },
    {
        "hypothesis_id": "h_rep_attrition",
        "mechanism": "Four departed West reps' accounts went unmanaged, suppressing the revenue those accounts generated.",
        "test_archetype": "placebo",
        "treatment": {"dim": "rep_id", "in": ["W1", "W2", "W3", "W4"]},
        "control": {"dim": "rep_id", "in": ["W5", "W6"]},
        "outcome": {"metric": "revenue", "expect": "decline"},
        "temporal": {"cause_onset": "2025-08-04", "kpi_onset": "2025-08-11"},
        "refutes_if": {
            "condition": "control_group_effect_size >= 0.6 * treatment_effect_size",
            "rationale": "If the reps who kept their accounts saw a comparable decline, attrition isn't the mechanism -- something region-wide is.",
        },
    },
]

# billing_complaints (h_billing_complaints) is a precedence predicate --
# evaluated by evaluate_precedence() against ticket data, not compiled to
# SQL. Its fixture lives in engine/l3_hypothesise.py alongside the ticket
# topic series it depends on.


def main() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    conn = load_database()
    region = "West"
    windows = {
        "week": ((28, 30), (32, 34)),
        "month": (("2025-07", "2025-07"), ("2025-08", "2025-09")),
    }

    print(f"{'hypothesis':<22} {'archetype':<12} {'entitlement':<10}  treatment_sql_hash  control_sql_hash")
    for raw in PREDICATE_FIXTURES:
        predicate = validate_predicate(raw)  # raises PredicateRejected if refutes_if is missing/empty
        try:
            compiled = compile_predicate(predicate, region, role="ops_manager_west", contract=contract, windows=windows)
            result = run_compiled_test(conn, compiled)
            print(f"{predicate.hypothesis_id:<22} {predicate.test_archetype:<12} {'ALLOWED':<10}  {compiled.treatment_sql_hash}    {compiled.control_sql_hash}")
            print(f"  treatment: {result['treatment_series']['value'].tolist()}")
            print(f"  control:   {result['control_series']['value'].tolist()}")
        except EntitlementDenied as e:
            print(f"{predicate.hypothesis_id:<22} {predicate.test_archetype:<12} {'DENIED':<10}  {e.reason}")

    print("\nEntitlement scenario: regional_vp requesting rep-level detail")
    try:
        rep_predicate = validate_predicate(PREDICATE_FIXTURES[3])
        compile_predicate(rep_predicate, region, role="regional_vp", contract=contract, windows=windows)
        print("  ALLOWED (unexpected)")
    except EntitlementDenied as e:
        print(f"  DENIED: {e.reason}")

    print("\nRejection check: a predicate with an empty refutes_if")
    bad = dict(PREDICATE_FIXTURES[0])
    bad["refutes_if"] = {"condition": "", "rationale": ""}
    try:
        validate_predicate(bad)
        print("  ACCEPTED (unexpected)")
    except PredicateRejected as e:
        print(f"  REJECTED: {e}")


if __name__ == "__main__":
    main()
