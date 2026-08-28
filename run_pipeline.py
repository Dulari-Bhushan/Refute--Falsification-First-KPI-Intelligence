"""
Runs the full REFUTE pipeline end to end, in build order:

  data generation -> reconciliation -> L1 (signal) -> L2 (localise) ->
  L3 (hypothesise) -> L5 (adjudicate, which compiles via L4 internally) ->
  L6 (narrate + ledger + personas + telemetry + feedback demo)
  -> [optional] L4 live LLM predicate generation

By default this is the templated/deterministic path (see README and the
handoff doc): no live LLM calls are made. Every hypothesis tested comes
from data/generate_synthetic_data.py's hand-written fixtures, and L3's
candidate-generation step still runs for real (embeddings + BOCPD), it
just isn't the thing that PROPOSES the predicates L4/L5 test in this mode.

    uv run python run_pipeline.py            # templated path only
    uv run python run_pipeline.py --with-llm  # + live local-GPU LLM predicate generation

--with-llm requires a CUDA GPU (or falls back to slow CPU inference) and
downloads Qwen2.5-3B-Instruct on first run (~6GB) -- see
engine/l4_llm_generation.py for what it does and why a local model, not a
hosted API.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

STEPS = [
    ("Generating synthetic dataset", ["data/generate_synthetic_data.py"]),
    ("Reconciling multi-source data", ["data/reconciliation.py"]),
    ("L1 -- signal detection", ["-m", "engine.l1_signal"]),
    ("L2 -- localisation", ["-m", "engine.l2_localise"]),
    ("L3 -- hypothesis generation", ["-m", "engine.l3_hypothesise"]),
    ("L5 -- falsification + adjudication (compiles via L4 internally)", ["-m", "engine.l5_adjudicate"]),
    ("L6 -- narration, ledger, personas, telemetry, feedback", ["-m", "engine.l6_narrate_ledger"]),
]
LLM_STEP = ("L4 -- live LLM predicate generation (local GPU)", ["-m", "engine.l4_llm_generation"])


def main() -> None:
    root = Path(__file__).parent
    steps = list(STEPS)
    if "--with-llm" in sys.argv:
        steps.append(LLM_STEP)

    for label, args in steps:
        print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
        result = subprocess.run([sys.executable, *args], cwd=root)
        if result.returncode != 0:
            print(f"\nPipeline stopped: '{label}' exited with code {result.returncode}.")
            sys.exit(result.returncode)
    print(f"\n{'=' * 70}\nDone. See data/synthetic/ for intermediate JSON outputs and ledger.sqlite for the immutable run record.\n{'=' * 70}")


if __name__ == "__main__":
    main()
