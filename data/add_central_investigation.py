"""
One-off script: runs the SECOND investigation (Central . revenue, a
CENTRAL_DC fulfillment disruption -- see data/inject_central_anomaly.py for
the underlying data) through the REAL L5 adjudication pipeline (identical
compile -> panel -> DiD -> parallel-trends -> power-gate -> BH-correction
treatment as West's investigation gets), then merges the resulting verdicts
into data/synthetic/l5_verdicts.json and writes matching ledger telemetry.
Not part of run_pipeline.py -- hand-run once, like the data injection script
it depends on.
"""
import json
from pathlib import Path

from engine.investigations import CENTRAL_PREDICATES, CENTRAL_WINDOWS
from engine.l5_adjudicate import adjudicate_all
from engine.l6_narrate_ledger import get_ledger, telemetry_span, write_ledger_entries

DATA_DIR = Path(__file__).parent / "synthetic"


def main() -> None:
    outcomes = adjudicate_all(role="regional_vp", region="Central", predicates=CENTRAL_PREDICATES, windows=CENTRAL_WINDOWS)

    print(f"{'hypothesis':<28} {'verdict':<14} reason")
    for o in outcomes:
        print(f"{o.hypothesis_id:<28} {o.verdict:<14} {o.reason}")
        # tag with the investigation this verdict belongs to -- TestOutcome
        # has no region/kpi field (the whole schema assumed one investigation
        # existed, ever); attaching it post-hoc here, and retroactively on
        # the existing West entries below, is the smallest honest fix that
        # lets the frontend filter "which hypotheses belong to the
        # currently-selected investigation" instead of guessing.
        o.region = "Central"
        o.kpi = "revenue"

    existing = json.loads((DATA_DIR / "l5_verdicts.json").read_text())
    for entry in existing:
        entry.setdefault("region", "West")
        entry.setdefault("kpi", "revenue")

    merged = existing + [o.__dict__ for o in outcomes]
    (DATA_DIR / "l5_verdicts.json").write_text(json.dumps(merged, indent=2))
    print(f"\nWrote {len(merged)} total verdicts ({len(existing)} West + {len(outcomes)} Central) to l5_verdicts.json")

    ledger = get_ledger()
    run_id = "central-investigation-1"  # note: an earlier attempt with a bad pre-window (weeks 34-35, unusually noisy) was deleted from the ledger before this run
    for o in outcomes:
        with telemetry_span(ledger, run_id, f"L4_L5_{o.hypothesis_id}", is_llm_call=False, model=None, tokens_in=0, tokens_out=0, cost_usd=0.0):
            pass
    write_ledger_entries(ledger, run_id, outcomes)
    ledger.close()
    print("Wrote ledger telemetry + verdict entries.")


if __name__ == "__main__":
    main()
