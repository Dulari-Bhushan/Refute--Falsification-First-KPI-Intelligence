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

from engine.l4_compiler import EntitlementDenied, check_entitlement
from engine.l5_adjudicate import adjudicate_all
from engine.l6_narrate_ledger import build_action_recommendation, build_counterfactual_projection, submit_feedback
from engine.methods_registry import REGISTRY, assert_llm_not_quantitative_source

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


@app.get("/api/kpi-series")
def kpi_series(region: str = "West", kpi: str = "revenue"):
    panel = pd.read_csv(DATA_DIR / "reconciled_weekly.csv")
    if kpi not in panel.columns:
        raise HTTPException(400, f"Unknown kpi '{kpi}'.")
    rows = panel[panel.region == region][["week", kpi]].sort_values("week")

    l1 = _read_json("l1_signal_results.json")
    l1_entry = next((r for r in l1 if r["kpi"] == kpi and r["region"] == region), None)

    return {
        "region": region,
        "kpi": kpi,
        "series": [{"week": int(w), "value": float(v)} for w, v in zip(rows["week"], rows[kpi])],
        "changepoint_week": l1_entry["changepoint_period_estimate"] if l1_entry else None,
        "gate_passed": l1_entry["gate_passed"] if l1_entry else None,
        "business_impact_pct": l1_entry["business_impact_pct"] if l1_entry else None,
        "narrative": l1_entry["narrative"] if l1_entry else None,
    }


@app.get("/api/l1-summary")
def l1_summary():
    """Every KPI x region L1 was run against -- including the ones that
    stayed noise (no LLM call made) and the sparse-history Outdoor demo."""
    return _read_json("l1_signal_results.json")


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
def action_recommendation():
    l2 = _read_json("l2_localisation_results.json")
    outcomes = adjudicate_all()
    survived = next((o for o in outcomes if o.verdict == "SURVIVED"), None)
    if survived is None:
        return {"has_action": False}
    return {"has_action": True, "hypothesis_id": survived.hypothesis_id, "action": build_action_recommendation(l2, survived)}


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
    contract = _contract()
    try:
        check_entitlement(role, region, dim, contract)
        return {"allowed": True, "reason": None}
    except EntitlementDenied as e:
        return {"allowed": False, "reason": str(e)}
    except Exception as e:  # noqa: BLE001 -- unknown role/dim is a client error, surface it plainly
        raise HTTPException(400, str(e)) from e


@app.get("/api/entitlements")
def entitlements():
    return _contract()["entitlements"]


@app.post("/api/feedback")
def feedback(original_hypothesis_id: str, analyst_role: str, correction_text: str, counter_predicate: dict):
    ledger_path = DATA_DIR / "ledger.sqlite"
    conn = sqlite3.connect(ledger_path)
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, original_hypothesis_id TEXT, "
        "original_verdict TEXT, analyst_role TEXT, correction_text TEXT, counter_hypothesis_id TEXT, counter_verdict TEXT, created_at TEXT)"
    )
    result = submit_feedback(conn, "ui-session", original_hypothesis_id, analyst_role, correction_text, counter_predicate)
    conn.close()
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


if UI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")
