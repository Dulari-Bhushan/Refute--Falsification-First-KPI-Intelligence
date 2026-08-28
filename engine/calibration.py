"""
Objective 7's "mechanism to learn from feedback" has two parts, and this
module is honest about which one it actually demonstrates:

1. CAPTURE + RE-TEST (built and running in production terms already):
   engine/l6_narrate_ledger.py's submit_feedback() turns a correction into
   a new predicate and re-adjudicates it through the identical L4/L5
   pipeline. That's real, live, and exercised in this session's own runs.

2. CALIBRATION (the actual "the system gets more trustworthy with use"
   mechanism -- Brier score, reliability diagram, isotonic recalibration):
   the machinery is implemented HERE and genuinely works, but it needs
   real scored outcomes (a prediction, and later, what actually happened)
   to run on -- and this prototype's live ledger has zero of those, because
   nothing has had time to play out yet. Faking that with invented "history"
   would be exactly the kind of dishonest placeholder number the Round 1
   handoff doc explicitly warned against ("the '31 of 38' figure should be
   replaced with a real computed number from your synthetic evaluation
   run, or clearly kept as an illustrative placeholder").

   So: this module generates a CLEARLY LABELED SIMULATED BACKTEST (not real
   production history, never written to the live `ledger` table) with a
   deliberately-imperfect confidence-accuracy relationship, and runs the
   real calibration functions against it. This proves the mechanism works
   when it has data, without pretending the live system has accumulated
   30 real outcomes it hasn't.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from sklearn.isotonic import IsotonicRegression

DATA_DIR = Path(__file__).parent.parent / "data" / "synthetic"
MIN_OUTCOMES_FOR_REAL_CALIBRATION = 30


def compute_brier_score(predictions: list[tuple[float, int]]) -> float:
    """BS = (1/N) * sum((forecast_i - outcome_i)^2) -- standard proper
    scoring rule for probabilistic binary predictions. 0 is perfect, 0.25
    is what a coin-flip forecaster gets on a balanced outcome set."""
    if not predictions:
        return float("nan")
    return float(np.mean([(p - o) ** 2 for p, o in predictions]))


def build_reliability_diagram(predictions: list[tuple[float, int]], n_bins: int = 5) -> list[dict]:
    """Buckets by stated confidence, compares to observed frequency in each
    bucket -- a well-calibrated system's points should sit near the
    diagonal (stated 70% confidence -> actually right ~70% of the time).
    A system that's systematically overconfident sits below the diagonal."""
    if not predictions:
        return []
    edges = np.linspace(0, 1, n_bins + 1)
    bins: list[dict] = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        in_bin = [(p, o) for p, o in predictions if lo <= p < hi or (hi == 1.0 and p == 1.0)]
        if not in_bin:
            continue
        mean_predicted = float(np.mean([p for p, _ in in_bin]))
        observed_freq = float(np.mean([o for _, o in in_bin]))
        bins.append(
            {
                "bucket_lo": round(float(lo), 2),
                "bucket_hi": round(float(hi), 2),
                "n": len(in_bin),
                "mean_predicted_confidence": round(mean_predicted, 3),
                "observed_frequency": round(observed_freq, 3),
                "calibration_gap": round(observed_freq - mean_predicted, 3),
            }
        )
    return bins


def fit_isotonic_recalibration(predictions: list[tuple[float, int]]) -> dict:
    """Refits the confidence -> accuracy map on accumulated outcomes
    (sklearn.isotonic.IsotonicRegression, as the original architecture doc
    specifies) -- monotonic by construction, so it corrects systematic
    over/under-confidence without inventing a fake non-monotonic
    relationship. Returns the fitted mapping at a handful of query points
    so it can be rendered as a curve, not just a fitted object."""
    if len(predictions) < 3:
        return {"fitted": False, "reason": "Too few points to fit a monotonic curve meaningfully."}
    x = np.array([p for p, _ in predictions])
    y = np.array([o for _, o in predictions])
    iso = IsotonicRegression(out_of_bounds="clip")
    iso.fit(x, y)
    query_points = np.linspace(0, 1, 11)
    calibrated = iso.predict(query_points)
    return {
        "fitted": True,
        "curve": [{"raw_confidence": round(float(q), 2), "recalibrated_confidence": round(float(c), 3)} for q, c in zip(query_points, calibrated)],
    }


@dataclass
class SimulatedOutcome:
    hypothesis_kind: str  # "obvious_decoy" | "underpowered" | "true_cause"
    stated_confidence: float
    correct: int  # 1 if the directional call matched reality, 0 otherwise


def generate_simulated_backtest(n: int = 40, seed: int = 99) -> list[SimulatedOutcome]:
    """Simulates n past verdicts with a DELIBERATELY IMPERFECT confidence-
    accuracy relationship (mild overconfidence at the high end, which is a
    common, realistic calibration failure mode) so the isotonic
    recalibration step has something genuine to correct -- a perfectly
    calibrated synthetic set would prove nothing about whether the
    recalibration machinery actually does anything. Mirrors the three
    kinds of verdicts this system actually produces (see the worked
    example): obvious decoys the system is very confident about and is
    usually right, underpowered cases where it's appropriately less
    confident, and true-cause calls with high stated confidence."""
    rng = np.random.default_rng(seed)
    outcomes = []
    for _ in range(n):
        kind = rng.choice(["obvious_decoy", "underpowered", "true_cause"], p=[0.4, 0.25, 0.35])
        if kind == "obvious_decoy":
            stated = rng.uniform(0.85, 0.98)
            true_prob_correct = stated - 0.05  # mild overconfidence
        elif kind == "underpowered":
            stated = rng.uniform(0.5, 0.7)
            true_prob_correct = stated - 0.02
        else:
            stated = rng.uniform(0.75, 0.95)
            true_prob_correct = stated - 0.08  # true-cause calls are the most overconfident bucket here -- a realistic failure mode worth a recalibration curve actually correcting
        correct = int(rng.uniform(0, 1) < np.clip(true_prob_correct, 0, 1))
        outcomes.append(SimulatedOutcome(kind, round(float(stated), 3), correct))
    return outcomes


def run_calibration_demo(n: int = 40, seed: int = 99) -> dict:
    simulated = generate_simulated_backtest(n, seed)
    predictions = [(o.stated_confidence, o.correct) for o in simulated]

    brier = compute_brier_score(predictions)
    reliability = build_reliability_diagram(predictions)
    isotonic = fit_isotonic_recalibration(predictions)

    return {
        "label": "SIMULATED BACKTEST -- NOT real production history",
        "honesty_note": (
            f"This is {n} synthetically generated scored outcomes with a deliberately imperfect confidence-accuracy "
            "relationship, used to prove the calibration MECHANISM works. The live ledger (data/synthetic/ledger.sqlite) "
            f"currently has real verdicts but they are all marked scored_outcome='uncalibrated' because nothing has had "
            f"time to play out yet -- real calibration needs >= {MIN_OUTCOMES_FOR_REAL_CALIBRATION} real scored outcomes "
            "before it would run on production data, per the honesty constraint stated from the original design."
        ),
        "n_simulated_outcomes": n,
        "brier_score": round(brier, 4),
        "reliability_diagram": reliability,
        "isotonic_recalibration": isotonic,
        "hit_rate_by_kind": {
            kind: round(float(np.mean([o.correct for o in simulated if o.hypothesis_kind == kind])), 3)
            for kind in ("obvious_decoy", "underpowered", "true_cause")
            if any(o.hypothesis_kind == kind for o in simulated)
        },
    }


def main() -> None:
    report = run_calibration_demo()
    (DATA_DIR / "calibration_demo.json").write_text(json.dumps(report, indent=2))

    print(report["label"])
    print(report["honesty_note"])
    print(f"\nBrier score: {report['brier_score']} (0 = perfect, 0.25 = coin-flip on a balanced set)")
    print("\nReliability diagram (stated confidence vs. observed frequency):")
    for b in report["reliability_diagram"]:
        print(f"  [{b['bucket_lo']:.1f}-{b['bucket_hi']:.1f}) n={b['n']:>3}  stated={b['mean_predicted_confidence']:.2f}  observed={b['observed_frequency']:.2f}  gap={b['calibration_gap']:+.2f}")
    print("\nIsotonic recalibration curve (raw stated confidence -> recalibrated):")
    if report["isotonic_recalibration"]["fitted"]:
        for pt in report["isotonic_recalibration"]["curve"]:
            print(f"  {pt['raw_confidence']:.1f} -> {pt['recalibrated_confidence']:.3f}")
    print(f"\nHit rate by hypothesis kind: {report['hit_rate_by_kind']}")


if __name__ == "__main__":
    main()
