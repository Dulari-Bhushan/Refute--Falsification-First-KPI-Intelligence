"""
METHODS REGISTRY -- the explicit, structural answer to the brief's own
requirement: "The LLM should not be treated as the source of quantitative
truth. Teams should explicitly demonstrate when they use deterministic
logic, SQL, business rules, statistics, traditional ML, causal inference,
retrieval or LLMs -- and why."

This is not a design doc restating what the code does -- it's a single
importable table every stage of the pipeline is required to declare itself
against, so the claim "the LLM never touches the numbers" is checked in
one place rather than asserted in scattered comments. engine/l6_narrate_
ledger.py and api/main.py both read this directly (not a copy) so the UI's
"Methods Breakdown" panel can never drift from what the code actually does.

Each entry: which stage, which method CATEGORY (one of the exact terms the
brief lists), and why that category and not an LLM. `quantitative_output`
is the tell: True means this stage's method produces the number a verdict
or figure is actually based on; every True row's method is non-LLM. If
that property is ever violated, mark it here and treat it as a bug.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MethodEntry:
    stage: str
    method_category: str  # one of: deterministic_logic, sql, business_rules, statistics, traditional_ml, causal_inference, retrieval, llm
    method_name: str
    why: str
    quantitative_output: bool  # True if this method's output is a number/verdict something downstream trusts as fact


REGISTRY: list[MethodEntry] = [
    MethodEntry(
        "L1 Signal — changepoint detection",
        "statistics",
        "Bayesian Online Changepoint Detection (NIG conjugate prior, constant hazard)",
        "Needs a full posterior over WHEN a break happened, not just whether one did — L4's precedence test and L5's pre/post windowing both depend on that distribution, not a point estimate an LLM's prose could provide.",
        True,
    ),
    MethodEntry(
        "L1 Signal — materiality gate",
        "business_rules",
        "Statistical posterior threshold AND business-impact-vs-baseline threshold (from semantic contract)",
        "The brief explicitly asks for materiality = statistical significance AND business impact, not one or the other — this is exactly what a rule-based gate should decide, not an LLM's judgment call.",
        True,
    ),
    MethodEntry(
        "L2 Localise — price/volume/mix bridge",
        "deterministic_logic",
        "Exact three-term arithmetic decomposition (no residual error)",
        "Revenue = units x price is exact by definition — decomposing a known formula is arithmetic, not a modeling or language problem, and an exact method exists, so use it.",
        True,
    ),
    MethodEntry(
        "L2 Localise — category/rep contribution",
        "statistics",
        "Monte-Carlo Shapley value attribution with bootstrap confidence intervals",
        "Attribution across dimensions with potential interaction effects is a coalition-game problem with a known correct solution concept (Shapley value) — approximated via Monte Carlo since exact Shapley is O(2^n).",
        True,
    ),
    MethodEntry(
        "L3 Hypothesise — ticket embedding",
        "traditional_ml",
        "Sentence-transformer embeddings (all-MiniLM-L6-v2)",
        "Needed genuine semantic similarity, not lexical overlap — a TF-IDF baseline was tried first and rejected because short, varied ticket phrasing shares almost no vocabulary even within the same topic. This is representation learning, not generation, and it doesn't touch the KPI numbers.",
        False,
    ),
    MethodEntry(
        "L3 Hypothesise — topic clustering",
        "traditional_ml",
        "Agglomerative clustering on embeddings (UMAP/HDBSCAN deliberately NOT used)",
        "The corpus is 52 documents; the brief's own honesty section states topic clustering degrades below ~200 documents per window, and UMAP/HDBSCAN's manifold/density assumptions need far more points than that to be meaningful at this scale.",
        False,
    ),
    MethodEntry(
        "L3 Hypothesise — per-topic changepoint + precedence filter",
        "statistics",
        "The same BOCPD from L1, run independently per topic; structural comparison of topic tau vs. KPI tau",
        "This is the naive-RAG-trap defense: retrieving tickets from the anomaly window is fatally biased (ticket volume moves in every bad week regardless of cause). Requiring an INDEPENDENT changepoint that precedes the KPI's is a statistical, falsifiable gate, not a plausibility check an LLM could rubber-stamp.",
        True,
    ),
    MethodEntry(
        "L3 / L4 — mechanism + predicate proposal",
        "llm",
        "Qwen2.5-3B-Instruct, local GPU, schema-constrained via `outlines`",
        "Proposing a plausible causal story in natural language, mapped onto a structured claim, is exactly the kind of open-ended synthesis LLMs are for. It is explicitly NOT trusted to be true — quantitative_output=False on this row is the whole point: everything this stage produces is re-validated (schema + semantic domain gates) and then TESTED by L4/L5's deterministic/statistical machinery before any verdict is assigned.",
        False,
    ),
    MethodEntry(
        "L4 Compiler — predicate validation",
        "deterministic_logic",
        "Pydantic schema validation, hard-required refutes_if field",
        "Popper's demarcation criterion (a claim that can't be disproven hasn't explained anything) enforced as a schema constraint, not a prompt instruction the model might forget to follow.",
        True,
    ),
    MethodEntry(
        "L4 Compiler — query generation",
        "sql",
        "Parameterised SQL built from a whitelisted table/column registry (identifiers whitelisted, values bind-parameterised)",
        "The brief's own language: 'the LLM never writes SQL.' A predicate's field VALUES are untrusted input; only a fixed registry the LLM never touches decides which tables/columns exist.",
        True,
    ),
    MethodEntry(
        "L4 Compiler — entitlement check",
        "business_rules",
        "Deny-by-default row/column scope check against the semantic contract",
        "Access control is a policy decision with a definite right answer per role — this runs at compile time, before any query executes, so a denial can never be bypassed by asking a different question.",
        True,
    ),
    MethodEntry(
        "L5 Adjudicate — effect estimation",
        "causal_inference",
        "Difference-in-differences (log-scale, unit fixed effects, cluster-robust -> HC1 fallback)",
        "DiD is the standard quasi-experimental design for 'did this specific slice move differently than an untreated comparison slice, beyond its own pre-existing trend' — the actual causal question a placebo/specificity test is asking.",
        True,
    ),
    MethodEntry(
        "L5 Adjudicate — identification check",
        "statistics",
        "Parallel pre-trends test (log-scale, to avoid cross-group scale artifacts)",
        "DiD's validity depends entirely on the parallel-trends assumption; skipping this check would let a spuriously significant (or insignificant) result through unchallenged.",
        True,
    ),
    MethodEntry(
        "L5 Adjudicate — power gate",
        "statistics",
        "Minimum detectable effect (MDE) at 80% power vs. a plausible-effect floor",
        "The single most load-bearing decision in the whole system: a non-significant result is only evidence of absence if the test had power to detect a real effect. Without this, an underpowered test would be laundered into a false KILLED verdict.",
        True,
    ),
    MethodEntry(
        "L5 Adjudicate — multiple-comparisons correction",
        "statistics",
        "Benjamini-Hochberg FDR control (q=0.10), not Bonferroni",
        "The tests share underlying data and are positively dependent — Bonferroni's independence assumption doesn't hold here and would over-correct, pushing almost everything to INCONCLUSIVE.",
        True,
    ),
    MethodEntry(
        "L6 Narrate — persona rendering",
        "deterministic_logic",
        "Template rendering over the ledger's own fields, gated by entitlement checks",
        "Personalization here is a filtering/formatting problem (who sees what depth, at what detail) over numbers that already exist — templating is exact and auditable; an LLM call would add cost, latency, and a paraphrase-fidelity risk for zero benefit.",
        False,
    ),
    MethodEntry(
        "L6 Feedback loop — counter-hypothesis adjudication",
        "causal_inference",
        "Re-runs the exact same L4/L5 pipeline on the analyst's counter-predicate",
        "A correction is only trustworthy if it's tested exactly as rigorously as the original claim — reusing the identical pipeline (not a lighter-weight approval flow) is what makes 'downgraded pending review' mean something.",
        True,
    ),
]


def summary_by_category() -> dict[str, list[MethodEntry]]:
    out: dict[str, list[MethodEntry]] = {}
    for entry in REGISTRY:
        out.setdefault(entry.method_category, []).append(entry)
    return out


def assert_llm_not_quantitative_source() -> None:
    """The structural check this whole module exists for: every row where
    method_category == 'llm' must have quantitative_output == False. If
    this ever fails, an LLM call has become something a verdict trusts as
    fact, which is exactly what the brief says not to do."""
    violations = [e for e in REGISTRY if e.method_category == "llm" and e.quantitative_output]
    if violations:
        raise AssertionError(f"LLM stage(s) marked as a quantitative source of truth: {[e.stage for e in violations]}")


if __name__ == "__main__":
    assert_llm_not_quantitative_source()
    by_cat = summary_by_category()
    print(f"{len(REGISTRY)} pipeline stages across {len(by_cat)} method categories:\n")
    for cat, entries in by_cat.items():
        print(f"{cat} ({len(entries)}):")
        for e in entries:
            flag = "quantitative" if e.quantitative_output else "non-quantitative"
            print(f"  [{flag:>16}] {e.stage} -- {e.method_name}")
        print()
    print("Check passed: no LLM stage is treated as a quantitative source of truth.")
