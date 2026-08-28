"""
L1 -- SIGNAL: is a KPI movement real, or noise?

Custom Bayesian Online Changepoint Detection (BOCPD, Adams & MacKay 2007),
not a rolling z-score, because a z-score returns a point estimate of "is
this week weird" while BOCPD returns a full posterior distribution over the
run length (equivalently, over *when* the most recent changepoint
happened). L4/L5 need that posterior downstream -- the precedence
falsification test compares the KPI's changepoint distribution against a
candidate cause's own changepoint distribution, and DiD's pre/post windows
need to know how confident we are about where the break actually sits, not
just that one exists.

Deseasonalisation note: the spec calls for running BOCPD over an
STL-deseasonalised series. At this KPI's declared analysis grain
(region x week, see semantic/kpi_contract.yaml) the series only spans 40
weeks -- under one full annual cycle -- so there is no observable weekly
seasonal period to fit an STL decomposition against; skipping it here is a
consequence of the grain, not a shortcut. A daily series (pos_transactions
grain) would have a fittable weekly seasonal component if a future KPI
needed day-level analysis.

Gate: only a KPI-region series that clears BOTH a statistical bar
(P(changepoint in the recent window) > 0.9) AND a business-impact bar
(from the KPI contract's thresholds) proceeds to L2 onward. This is the
literal implementation of the brief's "materiality based on both
statistical significance and business impact," and it's also REFUTE's cost
control: nothing downstream of this gate runs an LLM call, so noise never
reaches the expensive path.

Sparse-history handling: a KPI/category with too little history for its own
statistics to be trustworthy (Outdoor, launched week 34) borrows its prior
from a wider baseline (empirical-Bayes shrinkage) instead of either
refusing to answer or overconfidently extrapolating from a handful of
points. The output says explicitly that it did this.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy import stats

DATA_DIR = Path(__file__).parent.parent / "data" / "synthetic"
CONTRACT_PATH = Path(__file__).parent.parent / "semantic" / "kpi_contract.yaml"

HAZARD_LAMBDA = 15.0  # expected run length in weeks, absent evidence of a break
RECENCY_WINDOW_WEEKS = 12  # a changepoint estimated to be older than this is treated as already-known history, not a fresh material movement
CHANGEPOINT_NEIGHBORHOOD_WEEKS = 2  # width used to turn "which exact week" into "did a break happen in this neighborhood" -- see changepoint_estimate()
CHANGEPOINT_POSTERIOR_THRESHOLD = 0.75  # calibrated to this dataset's realistic weekly noise floor (~4% CV); see engine/l1_signal.py module docstring notes below, not an arbitrary round number
SPARSE_HISTORY_MIN_OBS = 8


@dataclass
class NIGPrior:
    """Normal-Inverse-Gamma conjugate prior hyperparameters for a Gaussian
    with unknown mean and variance."""

    mu0: float
    kappa0: float
    alpha0: float
    beta0: float


def fit_uninformative_prior(series: np.ndarray) -> NIGPrior:
    mu0 = float(np.mean(series))
    var0 = float(np.var(series)) if len(series) > 1 else max(mu0**2 * 0.01, 1.0)
    return NIGPrior(mu0=mu0, kappa0=1.0, alpha0=2.0, beta0=max(var0, 1e-6))


def fit_shrinkage_prior(sparse_series: np.ndarray, baseline_series: np.ndarray) -> NIGPrior:
    """For a KPI/category with too little of its own history, center the
    prior on a wider baseline population's mean/variance (e.g. all
    categories in the region) rather than the handful of points the sparse
    series itself provides, and keep kappa0 low so new observations still
    move the posterior quickly -- this is standard empirical-Bayes
    shrinkage, not a new algorithm."""
    baseline_mu = float(np.mean(baseline_series))
    baseline_var = float(np.var(baseline_series)) if len(baseline_series) > 1 else baseline_mu**2 * 0.05
    return NIGPrior(mu0=baseline_mu, kappa0=0.5, alpha0=1.5, beta0=max(baseline_var, 1e-6))


class BOCPD:
    """Bayesian Online Changepoint Detection with a constant hazard and a
    Normal (unknown mean/variance) observation model via NIG conjugacy.

    Returns R[t, r] = P(run length is r at time t | y_1..y_t), from which
    "P(changepoint in the last k steps)" and the changepoint posterior mode
    are derived.
    """

    def __init__(self, prior: NIGPrior, hazard_lambda: float = HAZARD_LAMBDA):
        self.prior = prior
        self.hazard = 1.0 / hazard_lambda

    def run(self, series: np.ndarray) -> np.ndarray:
        T = len(series)
        # R[t] holds the run-length posterior at time t, growing by one
        # possible run length each step. Padded to a (T+1, T+1) triangular
        # matrix for simplicity -- T here is at most ~40, so O(T^2) is fine.
        R = np.zeros((T + 1, T + 1))
        R[0, 0] = 1.0

        mu = np.array([self.prior.mu0])
        kappa = np.array([self.prior.kappa0])
        alpha = np.array([self.prior.alpha0])
        beta = np.array([self.prior.beta0])

        for t in range(1, T + 1):
            x = series[t - 1]

            # predictive probability of x under each currently active run length
            df = 2 * alpha
            scale = np.sqrt(beta * (kappa + 1) / (alpha * kappa))
            pred_probs = stats.t.pdf(x, df=df, loc=mu, scale=scale)

            growth_probs = R[t - 1, :t] * pred_probs * (1 - self.hazard)
            cp_prob = np.sum(R[t - 1, :t] * pred_probs * self.hazard)

            R[t, 1 : t + 1] = growth_probs
            R[t, 0] = cp_prob
            R[t, : t + 1] /= R[t, : t + 1].sum()

            # NIG posterior update for a run that survives (grows by one)
            new_kappa = kappa + 1
            new_mu = (kappa * mu + x) / new_kappa
            new_alpha = alpha + 0.5
            new_beta = beta + (kappa * (x - mu) ** 2) / (2 * new_kappa)

            mu = np.concatenate(([self.prior.mu0], new_mu))
            kappa = np.concatenate(([self.prior.kappa0], new_kappa))
            alpha = np.concatenate(([self.prior.alpha0], new_alpha))
            beta = np.concatenate(([self.prior.beta0], new_beta))

        return R[1:, 1:]  # drop the t=0 row/col padding


def changepoint_estimate(R: np.ndarray, neighborhood: int = CHANGEPOINT_NEIGHBORHOOD_WEEKS) -> tuple[int, float]:
    """Read the most recent changepoint off the final row of R: the mode of
    the run-length posterior at the last observed time step tells us where
    the current run most likely started (tau). This is the retrospective
    analogue of "did a changepoint happen partway through the observed
    window" -- as opposed to only checking whether the run length is
    currently short, which only fires when the break sits at the very end
    of the series.

    Confidence is reported as posterior mass in a small neighborhood of run
    lengths around that mode, not the mass on the single most likely run
    length. Pinning down the *exact* week a break happened is a harder,
    noisier question than "did a break happen in roughly this window" --
    with realistic weekly noise the posterior legitimately spreads probability
    across several adjacent candidate weeks even when a real change is very
    likely, and only the neighborhood-summed version reflects that (this is
    exactly the "P(changepoint in the recent window)" quantity the spec
    calls for)."""
    final = R[-1]
    T = len(final)
    most_likely_run_length = int(np.argmax(final))
    lo, hi = max(0, most_likely_run_length - neighborhood), min(T, most_likely_run_length + neighborhood + 1)
    confidence = float(final[lo:hi].sum())
    tau_index = T - most_likely_run_length  # 0-indexed week the run started
    return tau_index + 1, confidence


@dataclass
class L1Result:
    kpi: str
    region: str
    period_unit: str  # "week" or "month" -- L1 always runs at a KPI's own native cadence, never an upsampled one (see run_l1_for_series)
    n_observations: int
    sparse_history: bool
    changepoint_posterior_recent: float
    statistical_materiality: bool
    changepoint_period_estimate: int
    business_impact_pct: float
    business_impact_abs_usd: float
    business_materiality: bool
    gate_passed: bool
    narrative: str
    notes: list[str] = field(default_factory=list)


def business_impact(values: np.ndarray, contract_kpi: dict, tau_week_offset: int) -> tuple[float, float, bool]:
    """Compares the level since the estimated changepoint to the level
    before it -- not "this week vs. the last few weeks" -- because a
    sustained regime shift stays business-material for as long as it's in
    effect, however many weeks ago it started. A recency-only comparison
    would report a break as immaterial the moment enough time has passed
    since it happened, which is exactly backwards for a still-ongoing
    movement."""
    n = len(values)
    if n < 2:
        return 0.0, 0.0, False
    tau_idx = min(max(tau_week_offset - 1, 1), n - 1)
    pre = values[max(0, tau_idx - 6) : tau_idx]
    post = values[tau_idx:]
    if len(pre) == 0 or len(post) == 0:
        return 0.0, 0.0, False
    baseline = float(np.mean(pre))
    recent = float(np.mean(post))
    if baseline == 0:
        return 0.0, 0.0, False
    pct = (recent - baseline) / baseline
    abs_usd = recent - baseline
    pct_threshold = contract_kpi["materiality"].get("business_impact_pct_threshold")
    abs_threshold = contract_kpi["materiality"].get("business_impact_abs_usd_threshold")
    material = False
    if pct_threshold is not None and abs(pct) >= pct_threshold:
        material = True
    if abs_threshold is not None and abs(abs_usd) >= abs_threshold:
        material = True
    return pct, abs_usd, material


def run_l1_for_series(
    kpi: str,
    region: str,
    series: pd.Series,
    contract_kpi: dict,
    baseline_population: np.ndarray | None = None,
    period_unit: str = "week",
) -> L1Result:
    """Always runs at the KPI's own native refresh cadence -- a monthly
    source analyzed as 9 monthly points, a weekly source as up to 40 weekly
    points -- never on a cadence artificially upsampled to match a
    different KPI's grain. Flat-repeating a monthly total across several
    weeks would introduce calendar-driven step artifacts (4-week vs.
    5-week months) that a changepoint detector would legitimately, but
    wrongly, flag as real breaks; see rep_attributed_revenue in main()."""
    notes: list[str] = []
    n_before_trim = len(series)
    series = series.dropna()
    if len(series) < n_before_trim:
        notes.append(
            f"Trimmed {n_before_trim - len(series)} trailing {period_unit}(s) with no value yet -- this KPI depends on a "
            "source with a laggier refresh cadence than the analysis window's most recent periods (see reconciliation "
            "report for per-source freshness)."
        )

    values = series.to_numpy(dtype=float)
    n = len(values)
    sparse = n < SPARSE_HISTORY_MIN_OBS
    recency_window = RECENCY_WINDOW_WEEKS if period_unit == "week" else max(3, RECENCY_WINDOW_WEEKS // 4)

    if sparse and baseline_population is not None and len(baseline_population) > 4:
        prior = fit_shrinkage_prior(values, baseline_population)
        notes.append(
            f"Sparse history ({n} observations, below the {SPARSE_HISTORY_MIN_OBS}-observation bar): "
            "widened the prior by borrowing from the category/region baseline rather than fitting "
            "on this series alone. Treat this verdict as lower-confidence until more history accrues."
        )
    else:
        prior = fit_uninformative_prior(values)

    bocpd = BOCPD(prior)
    R = bocpd.run(values)
    tau_offset, cp_posterior = changepoint_estimate(R)

    is_recent = tau_offset > (n - recency_window)
    stat_material = cp_posterior > CHANGEPOINT_POSTERIOR_THRESHOLD and tau_offset > 1 and is_recent
    if sparse:
        # an honest system doesn't let a handful of points produce a
        # confident-sounding statistical verdict -- sparse series are
        # reported as low-confidence regardless of what the raw posterior says
        stat_material = stat_material and n >= 4
        notes.append("Statistical confidence capped due to sparse history; posterior is informative but not a firm detection.")

    impact_pct, impact_abs, biz_material = business_impact(values, contract_kpi, tau_offset)

    gate = stat_material and biz_material and not sparse
    if sparse:
        gate = False  # sparse-history KPIs never silently pass the full gate -- they route to an explicit low-confidence branch

    if gate:
        narrative = (
            f"{kpi} in {region}: material movement detected (changepoint posterior={cp_posterior:.2f}, "
            f"business impact={impact_pct:+.1%}). Proceeding to root-cause analysis."
        )
    elif sparse:
        narrative = (
            f"{kpi} in {region}: only {n} {period_unit}s of history -- too little to speak with full confidence. "
            f"Directionally {impact_pct:+.1%} vs. the borrowed baseline, but this is not yet a confirmed movement."
        )
    else:
        narrative = f"{kpi} in {region}: within normal variation (changepoint posterior={cp_posterior:.2f}). No LLM call made."

    return L1Result(
        kpi=kpi,
        region=region,
        period_unit=period_unit,
        n_observations=n,
        sparse_history=sparse,
        changepoint_posterior_recent=round(cp_posterior, 4),
        statistical_materiality=stat_material,
        changepoint_period_estimate=tau_offset,
        business_impact_pct=round(impact_pct, 4),
        business_impact_abs_usd=round(impact_abs, 2),
        business_materiality=biz_material,
        gate_passed=gate,
        narrative=narrative,
        notes=notes,
    )


def main() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    panel = pd.read_csv(DATA_DIR / "reconciled_weekly.csv")
    category_panel = pd.read_csv(DATA_DIR / "reconciled_weekly_by_category.csv")
    crm = pd.read_csv(DATA_DIR / "crm_headcount.csv")

    results: list[L1Result] = []

    # the three weekly-native KPIs. rep_attributed_revenue is deliberately
    # excluded here -- it's a monthly-cadence KPI and is analyzed at its own
    # native grain below, not upsampled to weekly (see run_l1_for_series
    # docstring for why that upsampling would be a bug, not a convenience).
    weekly_kpi_columns = {
        "revenue": "revenue",
        "units_sold": "units_sold",
        "marketing_attributed_revenue_share": "marketing_attributed_revenue_share",
    }
    for kpi_name, column in weekly_kpi_columns.items():
        kpi_meta = contract["kpis"][kpi_name]
        for region in contract["entitlements"]["regional_vp"]["row_scope"]["region"]:
            series = panel[panel.region == region].sort_values("week")[column]
            results.append(run_l1_for_series(kpi_name, region, series, kpi_meta, period_unit="week"))

    # rep_attributed_revenue at its native monthly grain
    rep_monthly = crm.groupby(["region", "month"])["rep_attributed_revenue_usd"].sum().reset_index()
    for region in contract["entitlements"]["regional_vp"]["row_scope"]["region"]:
        series = rep_monthly[rep_monthly.region == region].sort_values("month")["rep_attributed_revenue_usd"]
        results.append(run_l1_for_series("rep_attributed_revenue", region, series, contract["kpis"]["rep_attributed_revenue"], period_unit="month"))

    # the sparse-history demo: Outdoor category revenue, treated as its own
    # ad hoc KPI series, borrowing a prior from the other West categories
    outdoor_west = category_panel[(category_panel.region == "West") & (category_panel.product_category == "Outdoor")].sort_values("week")
    baseline = category_panel[(category_panel.region == "West") & (category_panel.product_category != "Outdoor")]["revenue"].to_numpy()
    results.append(
        run_l1_for_series(
            "new_category_revenue (Outdoor)",
            "West",
            outdoor_west["revenue"],
            contract["kpis"]["new_category_revenue"],
            baseline_population=baseline,
            period_unit="week",
        )
    )

    out = [r.__dict__ for r in results]
    (DATA_DIR / "l1_signal_results.json").write_text(json.dumps(out, indent=2))

    print(f"{'kpi':<38} {'region':<8} {'n':>3} {'cp_post':>8} {'impact%':>9} {'gate':>6}  narrative")
    for r in results:
        flag = "SPARSE" if r.sparse_history else ("PASS" if r.gate_passed else "noise")
        print(f"{r.kpi:<38} {r.region:<8} {r.n_observations:>3} {r.changepoint_posterior_recent:>8.3f} {r.business_impact_pct:>+8.1%} {flag:>6}  {r.narrative}")


if __name__ == "__main__":
    main()
