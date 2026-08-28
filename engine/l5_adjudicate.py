"""
L5 -- ADJUDICATE: run the compiled test, return a three-valued verdict.

Difference-in-differences on the unit-level panel L4 compiles
(value ~ treat * post), standard errors clustered at the treatment-unit
level (statsmodels' cluster-robust OLS covariance -- the spec names
linearmodels' PanelOLS for this; statsmodels' formula-API OLS with
cov_type="cluster" implements the identical DiD specification and clustered
covariance and was simpler to get working reliably for this prototype's
very small cluster counts, which is a real, honestly-stated limitation:
with as few as 2-4 treatment-side clusters, cluster-robust inference is
already operating well outside where its asymptotics are trustworthy. That
is exactly why the power gate below, not the raw p-value, is what's allowed
to produce a KILLED verdict).

The power gate is the single most important piece of this module: a
non-significant DiD estimate is only evidence of absence if the test had
enough power to detect a plausible effect. Compute the minimum detectable
effect (MDE) at 80% power; if the observed (non-significant) test couldn't
have detected a business-meaningful effect even if one existed, the verdict
is INCONCLUSIVE, not KILLED -- and the output says what sample size would
resolve it.

Benjamini-Hochberg FDR control (q=0.10) is applied across every raw p-value
this run produces, before any verdict is assigned -- not Bonferroni, since
the tests share underlying data and are positively dependent, and
Bonferroni's power loss would push almost everything to INCONCLUSIVE.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
import yaml
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.power import TTestIndPower

from engine.l4_compiler import (
    PREDICATE_FIXTURES,
    fetch_unit_panel,
    load_database,
    validate_predicate,
)

CONTRACT_PATH = Path(__file__).parent.parent / "semantic" / "kpi_contract.yaml"
DATA_DIR = Path(__file__).parent.parent / "data" / "synthetic"

ALPHA = 0.05
POWER_TARGET = 0.8
FDR_Q = 0.10
PARALLEL_TRENDS_ALPHA = 0.10
PLAUSIBLE_EFFECT_FRACTION = 0.10  # a "business-meaningful" effect is defined as >=10% of the treatment group's own pre-period mean -- see compute_power_gate docstring

WINDOWS = {
    "week": ((26, 30), (32, 36)),
    "month": (("2025-06", "2025-07"), ("2025-08", "2025-09")),
}


@dataclass
class TestOutcome:
    hypothesis_id: str
    test_archetype: str
    dim: str
    n_treatment_units: int
    n_control_units: int
    did_effect: float | None
    did_se: float | None
    did_pvalue_raw: float | None
    did_pvalue_bh: float | None = None
    parallel_trends_pvalue: float | None = None
    parallel_trends_ok: bool | None = None
    mde: float | None = None
    plausible_effect: float | None = None
    verdict: str = "PENDING"
    reason: str = ""
    notes: list[str] = field(default_factory=list)


MIN_CLUSTERS_PER_SIDE = 4


def has_enough_clusters(panel: pd.DataFrame) -> bool:
    n_treat_units = panel[panel.treat == 1]["unit"].nunique()
    n_control_units = panel[panel.treat == 0]["unit"].nunique()
    return n_treat_units >= MIN_CLUSTERS_PER_SIDE and n_control_units >= MIN_CLUSTERS_PER_SIDE


def did_estimate(panel: pd.DataFrame) -> tuple[float, float, float, float] | None:
    """DiD on log(value), not raw dollar levels. Treatment and control
    groups here routinely differ hugely in absolute scale (e.g. a home
    fulfillment center vs. two much smaller overflow centers, or four
    reps' books vs. two) -- an identical *percentage* decline in both
    groups would still show up as a much larger *dollar* decline in the
    bigger group under a levels regression, which a naive placebo/
    specificity test would misread as a real differential effect. The log
    specification makes the treat:post coefficient an (approximate)
    percentage effect, which is what "did treatment decline more than
    control, proportionally" actually means."""
    if panel["unit"].nunique() < 2 or panel["treat"].nunique() < 2 or panel["post"].nunique() < 2:
        return None
    panel = panel.copy()
    panel["log_value"] = np.log(panel["value"].clip(lower=1))
    # unit fixed effects (C(unit)), not a standalone "treat" term: this is
    # the entity-effects DiD specification the spec calls for (PanelOLS
    # with entity effects), and it matters here specifically because it
    # absorbs each unit's own baseline level before estimating the
    # interaction -- without it, a residual-variance calculation (used by
    # the power gate below) would conflate genuine week-to-week noise with
    # the fact that, say, WEST_DC's level just sits far above EAST_DC's.
    formula = "log_value ~ post + treat:post + C(unit)"
    try:
        if has_enough_clusters(panel):
            model = smf.ols(formula, data=panel).fit(cov_type="cluster", cov_kwds={"groups": panel["unit"]})
        else:
            # too few natural clusters (fulfillment centers/categories/reps
            # -- this dataset only ever has a handful) for cluster-robust
            # inference to be meaningful at all; the literature's own
            # guidance (Bertrand/Duflo/Mullainathan among others) is that
            # clustering with very few clusters is worse than not
            # clustering, not better -- so this falls back to
            # heteroskedasticity-robust (HC1) SEs at the observation level,
            # which is the standard fallback, not a shortcut taken to
            # inflate significance.
            model = smf.ols(formula, data=panel).fit(cov_type="HC1")
    except Exception:  # noqa: BLE001 -- a regression that can't be estimated on this panel is a data fact, not a code bug
        return None
    key = "treat:post"
    if key not in model.params.index:
        return None
    return float(model.params[key]), float(model.bse[key]), float(model.pvalues[key]), float(np.sqrt(model.mse_resid))


def parallel_trends_check(panel: pd.DataFrame) -> tuple[float | None, bool | None, str]:
    """Regresses the PRE-period only on treat x time-trend; a significant
    interaction means treatment and control were already diverging before
    the window even opened, which invalidates the DiD identifying
    assumption -- the test result becomes INCONCLUSIVE regardless of what
    the post-period shows, full stop."""
    pre = panel[panel.post == 0].copy()
    periods = sorted(pre["period"].unique())
    if len(periods) < 2:
        return None, None, f"Only {len(periods)} pre-period observation(s) per unit -- not enough to test for a pre-trend at all; treat this identification check as unresolved, not passed."
    period_index = {p: i for i, p in enumerate(periods)}
    pre["t_num"] = pre["period"].map(period_index)
    # log-transformed outcome: the treatment and control groups here can
    # differ hugely in raw dollar scale (e.g. a home fulfillment center vs.
    # two much smaller overflow centers), and a levels regression would
    # read that scale difference itself as "diverging trends" even when
    # the underlying percentage-wise week-to-week noise is identical.
    pre["log_value"] = np.log(pre["value"].clip(lower=1))
    try:
        if has_enough_clusters(pre):
            model = smf.ols("log_value ~ treat * t_num", data=pre).fit(cov_type="cluster", cov_kwds={"groups": pre["unit"]})
        else:
            model = smf.ols("log_value ~ treat * t_num", data=pre).fit(cov_type="HC1")
    except Exception:  # noqa: BLE001
        return None, None, "Pre-trend regression could not be estimated on this panel (likely too few clusters)."
    key = "treat:t_num"
    if key not in model.pvalues.index:
        return None, None, "Pre-trend interaction term not identified."
    p = float(model.pvalues[key])
    ok = p >= PARALLEL_TRENDS_ALPHA
    return p, ok, ("Pre-trends look parallel." if ok else f"Pre-trends diverge significantly (p={p:.3f}) -- DiD identifying assumption fails.")


def compute_power_gate(panel: pd.DataFrame) -> tuple[float, float]:
    """MDE at 80% power via a two-sample t-test power calculation, using
    the TREATMENT group's own within-unit noise (log-value demeaned by
    unit, restricted to treatment rows) -- not the whole panel's pooled
    residual SD. The question the power gate answers is "could this test
    have detected a real effect *in the series we're actually testing*",
    and pooling the treatment group's noise together with a much larger,
    much less noisy control group (e.g. a tiny low-volume category being
    tested against three large stable ones) would dilute exactly the
    signal the power gate exists to catch: a low-volume treatment series is
    genuinely noisier (in percentage terms -- Poisson relative variance
    scales with 1/sqrt(count)) and that has to show up as a bigger MDE, not
    get averaged away by an unrelated control group's stability. This is
    still a simplification (a full clustered-design power calculation would
    also account for the effective-sample-size reduction clustering
    itself causes), documented here rather than silently assumed away.
    "Plausible effect size" (PLAUSIBLE_EFFECT_FRACTION, 10%) is a
    conventional floor for what counts as a business-meaningful percentage
    movement, not a value tuned to produce a particular verdict."""
    n_treat = int((panel["treat"] == 1).sum())
    n_control = int((panel["treat"] == 0).sum())
    plausible_effect = PLAUSIBLE_EFFECT_FRACTION

    treat_rows = panel[panel["treat"] == 1].copy()
    treat_rows["log_value"] = np.log(treat_rows["value"].clip(lower=1))
    demeaned = treat_rows["log_value"] - treat_rows.groupby("unit")["log_value"].transform("mean")
    resid_sd = float(demeaned.std(ddof=1)) if demeaned.notna().sum() > treat_rows["unit"].nunique() else 0.0

    if n_treat < 2 or n_control < 2 or resid_sd == 0:
        return float("inf"), plausible_effect

    analysis = TTestIndPower()
    try:
        cohens_d = analysis.solve_power(effect_size=None, nobs1=n_treat, ratio=n_control / n_treat, alpha=ALPHA, power=POWER_TARGET)
        mde = abs(cohens_d) * resid_sd
    except Exception:  # noqa: BLE001
        mde = float("inf")
    return mde, plausible_effect


def adjudicate_all(role: str = "ops_manager_west", region: str = "West", predicates: list[dict] | None = None) -> list[TestOutcome]:
    """Runs every SQL-backed predicate (default: PREDICATE_FIXTURES) through
    the identical compile -> panel -> DiD -> parallel-trends -> power-gate ->
    BH-correction pipeline. `predicates` is exposed as a parameter (not
    hardcoded to the fixtures) specifically so engine/l6_narrate_ledger.py's
    feedback loop can run an analyst's counter-hypothesis through the exact
    same rigor as any other predicate -- a counter-hypothesis that never
    actually reaches this function isn't "adjudicated," it's just unscored."""
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    conn = load_database()
    if predicates is None:
        predicates = PREDICATE_FIXTURES

    outcomes: list[TestOutcome] = []
    for raw in predicates:
        predicate = validate_predicate(raw)
        panel = fetch_unit_panel(conn, predicate, region, role, contract, WINDOWS)

        outcome = TestOutcome(
            hypothesis_id=predicate.hypothesis_id,
            test_archetype=predicate.test_archetype,
            dim=predicate.treatment.dim,
            n_treatment_units=int(panel[panel.treat == 1]["unit"].nunique()),
            n_control_units=int(panel[panel.treat == 0]["unit"].nunique()),
            did_effect=None,
            did_se=None,
            did_pvalue_raw=None,
        )

        pt_p, pt_ok, pt_note = parallel_trends_check(panel)
        outcome.parallel_trends_pvalue = pt_p
        outcome.parallel_trends_ok = pt_ok
        outcome.notes.append(pt_note)

        did = did_estimate(panel)
        if did is None:
            outcome.verdict = "INCONCLUSIVE"
            outcome.reason = "DiD regression could not be estimated on this panel (insufficient units/periods)."
            outcomes.append(outcome)
            continue
        effect, se, p_raw, resid_sd = did
        outcome.did_effect, outcome.did_se, outcome.did_pvalue_raw = round(effect, 2), round(se, 2), p_raw

        if pt_ok is False:
            outcome.verdict = "INCONCLUSIVE"
            outcome.reason = pt_note
            outcomes.append(outcome)
            continue

        mde, plausible = compute_power_gate(panel)
        outcome.mde, outcome.plausible_effect = round(mde, 2), round(plausible, 2)
        outcomes.append(outcome)

    # Benjamini-Hochberg across every raw p-value this run actually produced
    # -- the full family, including tests that already failed the
    # parallel-trends check, since BH needs the whole family to be
    # correctly calibrated. Verdicts for pre-trend failures are NOT
    # overwritten below, though: identification already failed for those,
    # so no p-value, adjusted or not, licenses a KILLED/SURVIVED verdict.
    testable = [o for o in outcomes if o.did_pvalue_raw is not None]
    if testable:
        pvals = [o.did_pvalue_raw for o in testable]
        reject, adj_p, _, _ = multipletests(pvals, alpha=FDR_Q, method="fdr_bh")
        for o, adj, rej in zip(testable, adj_p, reject):
            o.did_pvalue_bh = round(float(adj), 5)
            if o.parallel_trends_ok is False:
                continue  # verdict already correctly set to INCONCLUSIVE -- pre-trends failure is not overridable by significance
            if rej:
                # a significant NEGATIVE treat:post interaction means treatment
                # declined more than control beyond what control's own decline
                # already explains -- exactly what "survives the placebo/
                # specificity test" means. A significant POSITIVE interaction
                # contradicts the predicted mechanism outright.
                direction_ok = (o.did_effect or 0) < 0
                o.verdict = "SURVIVED" if direction_ok else "KILLED"
                o.reason = (
                    f"BH-adjusted p={o.did_pvalue_bh:.4f} < q={FDR_Q}; treatment moved {abs(o.did_effect) * 100:.1f}pp "
                    f"{'more than' if direction_ok else 'less than'} control beyond the pre-period baseline difference -- "
                    f"{'consistent with' if direction_ok else 'contradicts'} the predicted mechanism."
                )
            elif o.mde is not None and o.mde > o.plausible_effect:
                o.verdict = "INCONCLUSIVE"
                needed_n = "more weeks of history" if o.dim != "rep_id" else "more months of history"
                o.reason = (
                    f"Not significant (BH-adjusted p={o.did_pvalue_bh:.4f}), and this test's minimum detectable effect "
                    f"({o.mde * 100:.1f}pp) exceeds the plausible effect size ({o.plausible_effect * 100:.0f}pp) -- the test was "
                    f"underpowered, not evidence the effect is absent. Would need {needed_n} or more treatment units to resolve."
                )
            else:
                o.verdict = "KILLED"
                o.reason = (
                    f"Not significant (BH-adjusted p={o.did_pvalue_bh:.4f}), and the test had enough power to detect a "
                    f"plausible effect (MDE {o.mde * 100:.1f}pp <= plausible {o.plausible_effect * 100:.0f}pp) if one existed."
                )

    return outcomes


def evaluate_precedence_test(hypothesis_id: str, topic_tau: int, topic_confidence: float, kpi_tau: int, kpi_confidence: float) -> TestOutcome:
    """The formal falsification test for a "precedence" archetype
    predicate -- kills the hypothesis if the proposed cause's own BOCPD
    changepoint comes AFTER the KPI's. L3 already applies this same check
    as a generation-time filter (a topic whose changepoint follows the
    KPI's never becomes a candidate hypothesis at all -- see
    engine/l3_hypothesise.py), so in this prototype's own pipeline this
    function should never actually need to kill anything L3 proposed.

    It exists anyway, as its own independently-runnable test, because a
    predicate tagged test_archetype="precedence" isn't guaranteed to have
    come from L3's structural filter -- a live LLM, a human analyst, or a
    future retrieval path could propose "billing complaints caused the
    drop" without ever checking timing. This is the defense-in-depth
    demonstration: even a hypothesis that slipped past generation-time
    filtering gets independently re-tested and killed here, not trusted
    just because it arrived with a plausible-sounding mechanism attached.

    Not folded into the SQL-backed archetypes' Benjamini-Hochberg family
    above: this isn't a regression p-value, it's a comparison of two BOCPD
    posterior confidences, a different statistic entirely -- pooling it
    into the same correction would not be statistically meaningful."""
    both_confident = topic_confidence > TOPIC_MIN_CONFIDENCE_FOR_PRECEDENCE and kpi_confidence > TOPIC_MIN_CONFIDENCE_FOR_PRECEDENCE
    precedes = topic_tau <= kpi_tau

    if not both_confident:
        verdict = "INCONCLUSIVE"
        reason = f"Changepoint confidence too low on one or both series (topic={topic_confidence:.2f}, kpi={kpi_confidence:.2f}) to make a precedence call either way."
    elif precedes:
        verdict = "SURVIVED"
        reason = f"Candidate cause's own changepoint (week {topic_tau}) precedes the KPI's onset (week {kpi_tau}) -- refutes_if condition (effect precedes cause) not met."
    else:
        verdict = "KILLED"
        reason = (
            f"Candidate cause's own changepoint (week {topic_tau}) comes AFTER the KPI's onset (week {kpi_tau}) -- "
            "this is a downstream symptom, not a cause. refutes_if condition met: effect precedes cause."
        )

    return TestOutcome(
        hypothesis_id=hypothesis_id,
        test_archetype="precedence",
        dim="ticket_topic",
        n_treatment_units=1,
        n_control_units=0,
        did_effect=None,
        did_se=None,
        did_pvalue_raw=None,
        verdict=verdict,
        reason=reason,
        notes=[f"topic_changepoint_week={topic_tau} (confidence={topic_confidence:.2f}); kpi_changepoint_week={kpi_tau} (confidence={kpi_confidence:.2f})"],
    )


TOPIC_MIN_CONFIDENCE_FOR_PRECEDENCE = 0.6


def main() -> None:
    outcomes = adjudicate_all()

    l1_path = DATA_DIR / "l1_signal_results.json"
    l3_path = DATA_DIR / "l3_topic_candidates.json"
    if l1_path.exists() and l3_path.exists():
        l1_results = json.loads(l1_path.read_text())
        west_revenue = next(r for r in l1_results if r["kpi"] == "revenue" and r["region"] == "West")
        l3_candidates = json.loads(l3_path.read_text())
        billing_cluster = next((c for c in l3_candidates if "account" in " ".join(c["top_terms"]).lower() or "customer" in " ".join(c["top_terms"][:2]).lower()), None)
        if billing_cluster is not None:
            outcomes.append(
                evaluate_precedence_test(
                    "h_billing_complaints",
                    topic_tau=billing_cluster["changepoint_week"],
                    topic_confidence=billing_cluster["changepoint_confidence"],
                    kpi_tau=west_revenue["changepoint_period_estimate"],
                    kpi_confidence=west_revenue["changepoint_posterior_recent"],
                )
            )

    (DATA_DIR / "l5_verdicts.json").write_text(json.dumps([o.__dict__ for o in outcomes], indent=2))

    print(f"{'hypothesis':<22} {'archetype':<12} {'effect%':>9} {'p(raw)':>8} {'p(BH)':>8} {'MDE%':>7} {'plaus%':>7}  verdict")
    for o in outcomes:
        eff = f"{o.did_effect * 100:+.1f}" if o.did_effect is not None else "n/a"
        praw = f"{o.did_pvalue_raw:.3f}" if o.did_pvalue_raw is not None else "n/a"
        pbh = f"{o.did_pvalue_bh:.3f}" if o.did_pvalue_bh is not None else "n/a"
        mde = f"{o.mde * 100:.1f}" if o.mde not in (None, float("inf")) else "n/a"
        plaus = f"{o.plausible_effect * 100:.0f}" if o.plausible_effect is not None else "n/a"
        print(f"{o.hypothesis_id:<22} {o.test_archetype:<12} {eff:>9} {praw:>8} {pbh:>8} {mde:>7} {plaus:>7}  {o.verdict}")
        print(f"    {o.reason}")
        for n in o.notes:
            print(f"    note: {n}")


if __name__ == "__main__":
    main()
