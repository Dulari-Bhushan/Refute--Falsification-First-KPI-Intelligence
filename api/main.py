"""
REFUTE demo API -- a thin read/action layer over what the pipeline already
produces (data/synthetic/*.json, reconciled_weekly.csv, ledger.sqlite). No
new analysis happens here: every number the UI shows is computed by L1-L6,
this just serves it.

Persona rendering is deliberately NOT pre-baked into a server-side string
here (unlike engine/l6_narrate_ledger.py's console output, which does
render text for the CLI demo). The frontend builds all three persona views
(leader / manager / engineer) from the SAME /api/hypotheses + /api/summary
data, filtered by /api/entitlement-check -- "one ledger, many renderers"
means the renderer can be the browser, not just the ledger module; the
entitlement check itself still goes through the real
engine.l4_compiler.check_entitlement function, not a client-side guess at
what should be hidden.

Run: uv run uvicorn api.main:app --reload
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from engine.action_recommendation import generate_action_recommendation
from engine.l4_compiler import EntitlementDenied, check_domain_entitlement, check_entitlement
from engine.l6_narrate_ledger import build_counterfactual_projection, detect_contradictory_verdicts, determine_delivery_channel, get_ledger, record_entitlement_check, submit_feedback
from engine.methods_registry import REGISTRY, assert_llm_not_quantitative_source
from engine.calibration import run_calibration_demo
from engine.drift_monitor import assess_drift, run_drift_demo
from engine.knowledge_graph import load_graph
from engine.proactive_monitor import run_alert_demo
from engine.llm_config import VALID_BACKENDS, public_llm_config, set_llm_config

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data" / "synthetic"
CONTRACT_PATH = ROOT / "semantic" / "kpi_contract.yaml"
UI_DIR = ROOT / "ui"

app = FastAPI(title="REFUTE demo API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _read_json(name: str) -> dict | list:
    path = DATA_DIR / name
    if not path.exists():
        raise HTTPException(404, f"{name} not found -- run `uv run python run_pipeline.py` first.")
    return json.loads(path.read_text())


def _contract() -> dict:
    return yaml.safe_load(CONTRACT_PATH.read_text())


@app.get("/api/priorities")
def priorities():
    """Objective 1's second half: which of the currently-material movements
    should an analyst look at first, and which are probably the same
    underlying event as another one on the list."""
    return _read_json("l1_priorities.json")


@app.get("/api/contradictions")
def contradictions():
    """Objective 5's second clause: currently-SURVIVED verdicts, checked
    for whether any two are contradictory (independent evidence pointing
    different directions) vs. just a re-test of the same evidence."""
    ledger_path = DATA_DIR / "ledger.sqlite"
    if not ledger_path.exists():
        return {"contradiction": None}
    conn = get_ledger()
    result = detect_contradictory_verdicts(conn)
    conn.close()
    return {"contradiction": result}


@app.get("/api/kpi-series")
def kpi_series(region: str = "West", kpi: str = "revenue"):
    """rep_attributed_revenue is served at its own native MONTHLY grain
    (9 points), not the flat-repeated weekly resample that lives in
    reconciled_weekly.csv -- charting that column directly would be
    misleading twice over: it manufactures fake weekly step-artifacts out
    of a single monthly total (exactly what engine/l1_signal.py's own
    docstring warns against for analysis), and l1_signal_results.json's
    changepoint_period_estimate for this KPI is a 1-indexed MONTH number,
    which would land a "changepoint" marker at the wrong x-position if
    plotted against week-indexed data. Every other KPI stays weekly,
    unchanged."""
    l1 = _read_json("l1_signal_results.json")
    l1_entry = next((r for r in l1 if r["kpi"] == kpi and r["region"] == region), None)

    if kpi == "rep_attributed_revenue":
        # crm_headcount's own "month" column is a calendar string ("2025-01"),
        # but changepoint_period_estimate (from engine/l1_signal.py) is the
        # 1-indexed POSITION within this same sorted series, not a parsed
        # calendar month number -- L1 never looks at the string's value, only
        # its sort order. Indexing this series the same way (enumerate after
        # sort, not int() on the string) is what makes changepoint_period
        # actually line up with the right point; the calendar string is kept
        # alongside purely as a nicer axis label.
        crm = pd.read_csv(DATA_DIR / "crm_headcount.csv")
        rep_monthly = crm.groupby(["region", "month"])["rep_attributed_revenue_usd"].sum().reset_index()
        rows = rep_monthly[rep_monthly.region == region].sort_values("month").reset_index(drop=True)
        period_unit = "month"
        series = [
            {"period": i + 1, "value": float(v), "label": str(m)}
            for i, (m, v) in enumerate(zip(rows["month"], rows["rep_attributed_revenue_usd"]))
        ]
    else:
        panel = pd.read_csv(DATA_DIR / "reconciled_weekly.csv")
        if kpi not in panel.columns:
            raise HTTPException(400, f"Unknown kpi '{kpi}'.")
        rows = panel[panel.region == region][["week", kpi]].sort_values("week")
        period_unit = "week"
        series = [{"period": int(w), "value": float(v)} for w, v in zip(rows["week"], rows[kpi])]

    return {
        "region": region,
        "kpi": kpi,
        "period_unit": period_unit,
        "series": series,
        "changepoint_period": l1_entry["changepoint_period_estimate"] if l1_entry else None,
        "gate_passed": l1_entry["gate_passed"] if l1_entry else None,
        "business_impact_pct": l1_entry["business_impact_pct"] if l1_entry else None,
        "narrative": l1_entry["narrative"] if l1_entry else None,
    }


@app.get("/api/l1-summary")
def l1_summary(role: str | None = None):
    """Every KPI x region L1 was run against -- including the ones that
    stayed noise (no LLM call made) and the sparse-history Outdoor demo.
    With `role` given, rows are filtered by REAL domain-level entitlement
    (engine.l4_compiler.check_domain_entitlement), not a client-side
    guess: a role denied the 'hr' domain (e.g. regional_vp) never sees
    rep_attributed_revenue's row at all, aggregate or not -- this is the
    row/column scope can't catch (see GAPS.md item 2)."""
    rows = _read_json("l1_signal_results.json")
    if role is None:
        return rows
    contract = _contract()
    visible = []
    for r in rows:
        kpi_name = r["kpi"].split(" ")[0]  # "new_category_revenue (Outdoor)" -> "new_category_revenue"
        try:
            check_domain_entitlement(role, kpi_name, contract)
            visible.append(r)
        except EntitlementDenied:
            continue
    return visible


@app.get("/api/hypotheses")
def hypotheses():
    """The full verdict table -- templated-fixture results plus, if the
    LLM step has been run, the LLM-generated predicates' own verdicts."""
    verdicts = _read_json("l5_verdicts.json")
    llm_path = DATA_DIR / "l4_llm_generated_predicates.json"
    llm_generated = json.loads(llm_path.read_text()) if llm_path.exists() else []
    return {"templated": verdicts, "llm_generated": llm_generated}


@app.get("/api/evidence")
def evidence():
    """Freshness/lineage (reconciliation), localisation (L2), and topic
    candidates (L3) -- the evidence panel's source data, per the minimum
    prototype expectation of showing freshness/method/contribution/
    confidence/lineage together."""
    return {
        "reconciliation": _read_json("reconciliation_report.json"),
        "localisation": _read_json("l2_localisation_results.json"),
        "topic_candidates": _read_json("l3_topic_candidates.json"),
        "scenario_manifest": _read_json("scenario_manifest.json"),
    }


@app.get("/api/action-recommendation")
def action_recommendation(region: str = "West"):
    """Investigation-aware: builds the recommendation from WHICHEVER
    region's investigation is passed, not hardcoded to West -- see
    engine.action_recommendation.generate_action_recommendation for the
    real evidence-gathering + LLM-synthesis (or honest fallback) pipeline."""
    from engine.investigations import INVESTIGATIONS

    if region not in INVESTIGATIONS:
        raise HTTPException(400, f"Unknown investigation region '{region}', must be one of {sorted(INVESTIGATIONS)}.")
    return generate_action_recommendation(region)


@app.get("/api/telemetry")
def telemetry():
    ledger_path = DATA_DIR / "ledger.sqlite"
    if not ledger_path.exists():
        raise HTTPException(404, "ledger.sqlite not found -- run the pipeline first.")
    conn = sqlite3.connect(ledger_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM telemetry ORDER BY id DESC LIMIT 200").fetchall()]
    conn.close()
    total_llm = sum(1 for r in rows if r["is_llm_call"])
    return {
        "rows": rows,
        "summary": {
            "total_calls": len(rows),
            "llm_calls": total_llm,
            "deterministic_calls": len(rows) - total_llm,
            "total_latency_ms": sum(r["latency_ms"] for r in rows),
            "total_cost_usd": sum(r["estimated_cost_usd"] for r in rows),
        },
    }


@app.get("/api/entitlement-check")
def entitlement_check(role: str, dim: str, region: str = "West"):
    """GAPS.md item 8 (auditability): every check made through this
    endpoint -- every persona-tab switch in the UI -- is persisted to the
    ledger's entitlement_checks table via record_entitlement_check(), not
    just returned in the HTTP response. run_id=None since an interactive
    UI check isn't tied to any one pipeline run."""
    contract = _contract()
    conn = get_ledger()
    try:
        check_entitlement(role, region, dim, contract)
        record_entitlement_check(conn, None, "row_column", role, dim, region, True, None)
        return {"allowed": True, "reason": None}
    except EntitlementDenied as e:
        record_entitlement_check(conn, None, "row_column", role, dim, region, False, str(e))
        return {"allowed": False, "reason": str(e)}
    except Exception as e:  # noqa: BLE001 -- unknown role/dim is a client error, surface it plainly
        raise HTTPException(400, str(e)) from e
    finally:
        conn.close()


@app.get("/api/domain-check")
def domain_check(role: str, kpi: str = "revenue"):
    """The domain-level counterpart to /api/entitlement-check -- a real
    access-control decision (engine.l4_compiler.check_domain_entitlement),
    not a client-side guess. Distinct mechanism from row/column scope: this
    can deny a whole KPI outright (e.g. marketing_analyst on 'revenue',
    regional_vp on 'rep_attributed_revenue') regardless of region or
    dimension requested."""
    contract = _contract()
    conn = get_ledger()
    try:
        check_domain_entitlement(role, kpi, contract)
        record_entitlement_check(conn, None, "domain", role, kpi, None, True, None)
        return {"allowed": True, "reason": None}
    except EntitlementDenied as e:
        record_entitlement_check(conn, None, "domain", role, kpi, None, False, str(e))
        return {"allowed": False, "reason": str(e)}
    finally:
        conn.close()


@app.get("/api/entitlement-log")
def entitlement_log(limit: int = 50):
    """The persisted audit trail GAPS.md item 8 asked for: every row/column
    and domain-level entitlement decision made anywhere (a real pipeline
    run's compile-time checks, or an interactive /api/entitlement-check /
    /api/domain-check call), newest first. Previously this only ever
    printed to a console or returned in one HTTP response -- 'what
    evidence supports a verdict' was auditable, 'who tried to access what
    and was denied' was not."""
    ledger_path = DATA_DIR / "ledger.sqlite"
    if not ledger_path.exists():
        return {"rows": []}
    conn = sqlite3.connect(ledger_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM entitlement_checks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    conn.close()
    return {"rows": rows}


@app.get("/api/delivery-channel")
def delivery_channel(role: str, region: str = "West"):
    """GAPS.md item 7: which channel this role's brief would actually route
    through right now, computed from the real current action-recommendation
    state for WHICHEVER investigation `region` names (see
    engine.action_recommendation.generate_action_recommendation) -- not a
    fixed lookup, an urgency-gated decision. See /api/delivery-log for the
    persisted SIMULATED delivery records a real pipeline run writes."""
    from engine.investigations import INVESTIGATIONS

    if region not in INVESTIGATIONS:
        raise HTTPException(400, f"Unknown investigation region '{region}', must be one of {sorted(INVESTIGATIONS)}.")
    action_wrapped = generate_action_recommendation(region)
    return determine_delivery_channel(role, action_wrapped, _contract())


@app.get("/api/delivery-log")
def delivery_log(limit: int = 20):
    """Persisted SIMULATED delivery records -- simulated=1 always, since no
    real Slack/email API is ever called (see engine.l6_narrate_ledger.
    simulate_delivery's docstring for why pretending otherwise would be
    dishonest). What's real is the routing decision behind each record."""
    ledger_path = DATA_DIR / "ledger.sqlite"
    if not ledger_path.exists():
        return {"rows": []}
    conn = sqlite3.connect(ledger_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM delivery_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    conn.close()
    return {"rows": rows}


@app.get("/api/entitlements")
def entitlements():
    return _contract()["entitlements"]


@app.post("/api/feedback")
def feedback(original_hypothesis_id: str, analyst_role: str, correction_text: str, counter_predicate: dict):
    # get_ledger() (not a hand-rolled CREATE TABLE) so this endpoint gets
    # the full schema + migrations every other write path gets -- a
    # feedback submission through the UI shouldn't risk landing in a
    # ledger table that's missing columns another path already relies on.
    conn = get_ledger()
    result = submit_feedback(conn, "ui-session", original_hypothesis_id, analyst_role, correction_text, counter_predicate)
    contradiction = detect_contradictory_verdicts(conn, hypothesis_ids=[original_hypothesis_id, result.get("counter_hypothesis_id")])
    conn.close()
    result["contradiction"] = contradiction
    return result


@app.get("/api/semantic-contract")
def semantic_contract():
    return _contract()


@app.get("/api/methods-breakdown")
def methods_breakdown():
    """The explicit answer to the brief's own requirement: which method
    category (deterministic logic / SQL / business rules / statistics /
    traditional ML / causal inference / retrieval / LLM) each pipeline
    stage uses, and why -- imported directly from
    engine/methods_registry.py, not restated here, so the UI can never
    drift from what the code actually declares. assert_llm_not_quantitative_
    source() is re-checked on every request, not just at import time."""
    assert_llm_not_quantitative_source()
    return {
        "entries": [asdict(e) for e in REGISTRY],
        "categories": sorted({e.method_category for e in REGISTRY}),
        "llm_stages_are_never_quantitative": True,
    }


@app.get("/api/counterfactual")
def counterfactual(region: str = "West", weeks_ahead: int = 4):
    return build_counterfactual_projection(region=region, weeks_ahead=weeks_ahead)


@app.get("/api/calibration-demo")
def calibration_demo_endpoint():
    """Objective 7: the calibration MECHANISM (Brier score, reliability
    diagram, isotonic recalibration), proven against a clearly-labeled
    simulated backtest -- see engine/calibration.py's module docstring for
    why this is honest and the live ledger's real entries aren't faked
    into a fake history instead."""
    return run_calibration_demo()


@app.get("/api/drift")
def drift_endpoint():
    """GAPS.md item 1: model/data drift monitoring. Real assessment
    (engine.drift_monitor.assess_drift) against this ledger's own run
    history when >=5 prior run snapshots exist; otherwise an honest
    insufficient_history status plus a clearly labeled simulated
    demonstration (run_drift_demo) proving the PSI mechanism itself works
    -- same honesty pattern as /api/calibration-demo."""
    ledger_path = DATA_DIR / "ledger.sqlite"
    demo = run_drift_demo()
    if not ledger_path.exists():
        return {"real": {"status": "insufficient_history", "n_baseline_runs": 0, "runs_needed": 5, "explanation": "No ledger yet -- run the pipeline first."}, "demo": demo}
    conn = sqlite3.connect(ledger_path)
    conn.row_factory = sqlite3.Row
    latest = conn.execute("SELECT run_id FROM run_snapshots ORDER BY id DESC LIMIT 1").fetchone()
    conn.row_factory = None
    real = assess_drift(conn, latest["run_id"]) if latest is not None else {"status": "insufficient_history", "n_baseline_runs": 0, "runs_needed": 5, "explanation": "No run snapshots recorded yet -- run the pipeline first."}
    conn.close()
    return {"real": real, "demo": demo}


@app.get("/api/knowledge-graph")
def knowledge_graph_endpoint():
    """SOLUTIONING.md item 2: a real, queryable structure over the
    semantic contract's own declared relationships (KPI->source, KPI->
    domain, role->domain, dimension->table) plus this run's actual
    verdicts (hypothesis->dimension, hypothesis->verdict) -- rebuilt fresh
    from the live contract + l5_verdicts.json every request, same
    read-the-live-source discipline as every other endpoint here."""
    return load_graph().export()


@app.get("/api/knowledge-graph/related")
def knowledge_graph_related(node_id: str):
    """'What else touches this node' -- direct neighbors, either
    direction, with the relation connecting them."""
    return {"node_id": node_id, "related": load_graph().related(node_id)}


@app.get("/api/knowledge-graph/blast-radius")
def knowledge_graph_blast_radius(node_id: str, max_depth: int = 3):
    """'What depends on this, transitively' -- most meaningful from a
    source node (what breaks if pos_transactions goes stale or wrong?),
    but works from any node."""
    return {"node_id": node_id, "max_depth": max_depth, "blast_radius": load_graph().blast_radius(node_id, max_depth)}


@app.get("/api/knowledge-graph/shared-mechanism")
def knowledge_graph_shared_mechanism(hypothesis_id: str):
    """'What else looks like this hypothesis, structurally' -- other
    hypotheses sharing a tested dimension or the same test_archetype."""
    return {"hypothesis_id": hypothesis_id, "shared_mechanism": load_graph().shared_mechanism(f"hyp:{hypothesis_id}")}


@app.get("/api/alerts")
def alerts_endpoint(limit: int = 20):
    """SOLUTIONING.md item 4's proactive-alerting half. `recent` is the
    real, persisted alert log (engine.proactive_monitor.detect_new_alerts,
    routed through the same domain_scope data check_domain_entitlement
    enforces) -- empty until a pipeline run has recorded at least one gated
    movement twice. `demo` proves the new-vs-known detection logic is
    correct against a clearly labeled simulated two-run history, same
    honesty pattern as /api/drift."""
    ledger_path = DATA_DIR / "ledger.sqlite"
    recent = []
    if ledger_path.exists():
        conn = sqlite3.connect(ledger_path)
        conn.row_factory = sqlite3.Row
        recent = [dict(r) for r in conn.execute("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
        conn.close()
    return {"recent": recent, "demo": run_alert_demo()}


@app.get("/api/adversarial-challenges")
def adversarial_challenges():
    """Verdicts for any h_adversarial_* predicates written by
    engine/l4_llm_generation.py's generate_adversarial_challenge() --
    empty if that step (run_pipeline.py --with-llm) hasn't been run yet."""
    ledger_path = DATA_DIR / "ledger.sqlite"
    if not ledger_path.exists():
        return {"challenges": []}
    conn = sqlite3.connect(ledger_path)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM ledger WHERE hypothesis_id LIKE 'h_adversarial_%' ORDER BY id DESC LIMIT 10").fetchall()]
    conn.close()
    return {"challenges": rows}


class LLMConfigUpdate(BaseModel):
    backend: str | None = None
    api_key: str | None = None
    openrouter_model: str | None = None


@app.get("/api/llm-config")
def llm_config_get():
    """Which backend generates predicates when the live-LLM step runs:
    local GPU (default, $0, no key) or a hosted model via OpenRouter (real
    per-call cost, needs a key). Never returns the key itself -- only
    whether one is set (engine.llm_config.public_llm_config)."""
    return public_llm_config()


@app.post("/api/llm-config")
def llm_config_set(update: LLMConfigUpdate):
    if update.backend is not None and update.backend not in VALID_BACKENDS:
        raise HTTPException(400, f"Unknown backend '{update.backend}', must be one of {sorted(VALID_BACKENDS)}.")
    set_llm_config(backend=update.backend, api_key=update.api_key, openrouter_model=update.openrouter_model)
    return public_llm_config()


@app.post("/api/llm-generate/run")
def llm_generate_run(region: str = "West"):
    """Runs engine.l4_llm_generation.main(region) with whichever backend is
    currently configured: generates a predicate for every L3 candidate
    topic IN THIS REGION, adjudicates each through the real L5 pipeline
    (identical treatment to the templated fixtures, using that
    investigation's own timing windows), and runs the adversarial challenge
    against that investigation's surviving hypothesis. Requires the
    templated pipeline (run_pipeline.py) to have already produced L1/L3
    output."""
    from engine.investigations import INVESTIGATIONS

    if region not in INVESTIGATIONS:
        raise HTTPException(400, f"Unknown investigation region '{region}', must be one of {sorted(INVESTIGATIONS)}.")
    if not (DATA_DIR / "l3_topic_candidates.json").exists():
        raise HTTPException(400, "Run the pipeline first (`uv run python run_pipeline.py`) -- no L3 topic candidates found yet.")
    cfg = public_llm_config()
    if cfg["backend"] == "openrouter" and not cfg["has_api_key"]:
        raise HTTPException(400, "OpenRouter backend selected but no API key is configured. Set one in LLM Settings first.")

    import engine.l4_llm_generation as l4llm

    try:
        l4llm.main(region=region)
    except Exception as e:  # noqa: BLE001 -- surface whatever went wrong (model load failure, API error, etc.) to the dashboard rather than a bare 500
        raise HTTPException(500, f"LLM generation run failed: {e}") from e

    predicates_path = DATA_DIR / "l4_llm_generated_predicates.json"
    generated = json.loads(predicates_path.read_text()) if predicates_path.exists() else []
    return {
        "backend": cfg["backend"],
        "region": region,
        "n_candidates": len(generated),
        "n_accepted": sum(1 for g in generated if g["accepted"]),
        "generated": generated,
    }


@app.post("/api/llm-generate/clear-cache")
def llm_generate_clear_cache():
    """Wipes the llm_predicate_cache table so the next 'Run live LLM
    generation now' click makes genuinely fresh calls instead of replaying
    a prior run's cached predicates -- self-service (no terminal/script
    access needed), for exactly the moment before a live demo where a real
    network round-trip matters more than saving a few cents."""
    ledger_path = DATA_DIR / "ledger.sqlite"
    if not ledger_path.exists():
        return {"cleared": 0}
    conn = sqlite3.connect(ledger_path)
    cur = conn.execute("DELETE FROM llm_predicate_cache")
    conn.commit()
    n = cur.rowcount
    conn.close()
    return {"cleared": n}


if UI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")
