# REFUTE — Real-World-Complexity Gap Audit

The Round 2 brief names 10 "Real-World Complexities to Consider." This document is a line-by-line
audit of REFUTE against that exact list, done by reading the actual source (not the README's
self-report) — grepping for real implementations, confirming what's wired up vs. merely declared
in a docstring or YAML field. Written 2026-08-28.

Status legend: ✅ done and verified in code · ⚠️ partially done, real limit stated below · ❌ not
implemented at all.

---

## 1. Multiple interacting drivers (price, volume, mix, marketing, supply, seasonality, competition, external events) — ⚠️

- Price/volume/mix: ✅ exact decomposition, [`engine/l2_localise.py`](engine/l2_localise.py).
- Supply: ✅ `h_shipping_delay` decoy, correctly killed via placebo.
- Competition: ✅ `h_competitor_launch` decoy, correctly killed via specificity.
- Seasonality: ✅ *deliberately* out of scope, not silently skipped — [`engine/l1_signal.py:14-21`](engine/l1_signal.py#L14)
  explains the 40-week analysis window is under one annual cycle, so there's no fittable STL
  seasonal period at this grain. A real, stated engineering judgment.
- Marketing: ⚠️ `marketing_attributed_revenue_share` is a real KPI in
  [`semantic/kpi_contract.yaml`](semantic/kpi_contract.yaml) that L1's BOCPD gate genuinely
  monitors — but it is never wired as a *candidate cause* hypothesis in L3/L4. Nothing tests
  "did a marketing spend cut cause the revenue drop." Observed, not falsification-tested.
- External events: ❌ no planted scenario (macro shock, weather, etc.) and no explicit category
  for it — in principle any L3 topic cluster could surface one, but nothing demonstrates it.

## 2. Different source-system refresh cadences, grains, data quality levels, historical coverage — ⚠️

- Cadence/grain heterogeneity: ✅ strong — daily/weekly/monthly/near-real-time sources genuinely
  resampled to common grains with freshness tracking, [`data/reconciliation.py`](data/reconciliation.py).
- Historical coverage: ✅ Outdoor category sparse-history handling with empirical-Bayes shrinkage.
- Data quality levels: ⚠️ `system_of_record: true/false` per source is the only quality signal in
  the contract — there's no explicit per-source quality/error-rate score, so "quality levels"
  (plural, implying gradation) is thinner than the cadence/grain handling next to it.

## 3. Inconsistent KPI definitions, hierarchies, calendars, business rules, aggregation logic — ✅

Gross-vs-net revenue definition drift is planted and detected. The category-maturity business
rule (8-week bar before a category folds into core revenue) is real gating logic, not a comment.
`analysis_calendar` is declared and used. Weekly/monthly aggregation/resampling logic is explicit
and lineage-labeled (`data/reconciliation.py` states which fields were resampled vs. natively
observed).

## 4. Sparse history for new products, categories or markets — ✅

Outdoor category (launched week 34), empirical-Bayes shrinkage borrowing a wider baseline prior,
output explicitly flags that it did this.

## 5. Materiality based on both statistical significance and business impact — ✅

L1's gate is a genuine dual condition (BOCPD posterior > 0.9 AND business-impact threshold from
the contract) — both checked, not just one asserted.

## 6. Contradictory evidence, missing data, confidence calibration — ⚠️

- Contradictory evidence: ✅ `detect_contradictory_verdicts()` in
  [`engine/l6_narrate_ledger.py`](engine/l6_narrate_ledger.py) actually distinguishes
  same-SQL-hash re-narration from genuinely independent contradiction.
- Confidence calibration: ✅ real Brier score / reliability diagram / isotonic recalibration
  machinery, honestly labeled as a simulated backtest since the live ledger has 0 real scored
  outcomes yet (needs ≥30).
- Missing data: ⚠️ weakest of the three. Handled implicitly (`dropna()` in L1; inner/left joins in
  reconciliation) but there's no explicit missing-data-rate metric or stated policy — an inner
  join silently excludes unmatched rows rather than surfacing e.g. "12% of expected
  region-months had no GL match."

## 7. Role-based personalization of insight depth, recommended actions, delivery channels — ⚠️

- Insight depth: ✅ 3 personas (Leader/Manager/Engineer), one ledger, three renderers.
- Recommended actions: ✅ capacity-constraint-qualified per role, real numbers from L2/L5.
- Delivery channels: ❌ none. Everything is the single web dashboard — no email/Slack/multi-channel
  variation exists anywhere in the code.

## 8. Row-, column- and domain-level security, sensitive-data protection, auditability — ⚠️

- Row-level (region): ✅ enforced at compile time, `check_entitlement()` in
  [`engine/l4_compiler.py:152`](engine/l4_compiler.py#L152).
- Column-level (`rep_detail_restricted`): ✅ enforced the same way.
- Domain-level: ❌ **declared but not enforced.** The contract sets `access_tags` per KPI, but no
  code path anywhere reads that field — confirmed by grep. It's schema decoration, not a real
  check. This is the sharpest gap in the whole audit: the semantic contract claims a security
  dimension the code doesn't actually check.
- Sensitive-data protection: ✅ the strongest real case is rep-level HR/compensation data gated by
  column scope.
- Auditability: ⚠️ the ledger is a genuine immutable record of *what was tested and how* (verdict +
  SQL hash). But entitlement ALLOWED/DENIED decisions only print to the console in the demo
  script — they are never persisted. So "what evidence supports a verdict" is auditable; "who
  tried to access what and was denied" is not. (Feedback events do record `analyst_role`, so that
  slice is real.)

## 9. Model and data drift, feedback capture, continuous evaluation — ❌ (drift), ✅ (feedback)

- Feedback capture: ✅ real — `submit_feedback()` re-runs a correction through the identical
  L4/L5 pipeline and (after a real bug fix documented in README §0) correctly writes the
  counter-verdict to the ledger, not just the feedback log.
- Continuous evaluation groundwork: ✅ calibration machinery exists, honestly gated on needing
  real outcomes to accrue.
- Model/data drift: ❌ **zero implementation.** The only "drift" hits in the codebase are
  unrelated — a comment about docs staying in sync with code, and the planted KPI *definition*
  drift scenario, which is a one-time discrepancy, not drift-over-time. Nothing monitors whether
  L1's changepoint posteriors, L4's LLM predicate-acceptance rate, or effect-size estimates are
  degrading/shifting as new data arrives. This is the single most complete gap on the list —
  explicitly named by the brief, and currently 0% covered.

## 10. LLM economics: model choice, token consumption, latency, caching, cost per insight — ⚠️

- Model choice: ✅ real, documented reasoning (Qwen2.5-3B vs. 1.5B tradeoff, a genuine finding
  about quality vs. size for this task, not assumed).
- Tokens/latency: ✅ real per-call telemetry, [`engine/l6_narrate_ledger.py`](engine/l6_narrate_ledger.py).
- Cost: ✅ $0 actual (local GPU) with a hosted-API cost comparison shown for context, not hidden.
- Caching: ❌ **not implemented.** Every pipeline run regenerates every predicate from scratch,
  even for a repeated topic cluster. The phrase "cached answer" exists once in narration copy —
  it's flavor text in the feedback-loop demo, not a mechanism.

---

## Other findings from the audit (not on the brief's list, but relevant)

- **`dose_response` archetype is defined but never exercised.** It's a valid `Literal` value in
  the `Predicate` schema ([`engine/l4_compiler.py:96`](engine/l4_compiler.py#L96)) and is
  mentioned in docstrings as one of four archetypes, but no fixture uses it and L5 has no code
  path that adjudicates it. The spec calls for four archetypes; only three actually run.
- REFUTE also does several things **not asked for by this list at all**, worth keeping in mind
  when judges compare coverage: the Popperian `refutes_if` hard constraint, MDE/power gating so a
  null result can't be laundered as evidence of absence, Benjamini-Hochberg correction across the
  whole hypothesis family (the garden-of-forking-paths problem), the naive-RAG-trap defense in L3,
  and adversarial self-challenge before trusting a SURVIVED verdict.

---

## Priority order to close

1. **Model/data drift** (item 9) — named explicitly, 0% covered. Highest-value gap to close.
2. **Domain-level security** (item 8) — either enforce `access_tags` for real or remove it from
   the contract; right now it's a claim the code doesn't back up, which cuts against this
   project's own honesty ethos.
3. **Marketing as a real candidate hypothesis** (item 1) — using the currently-unused
   `dose_response` archetype closes two gaps at once (marketing-as-driver, unused archetype).
4. Missing-data-rate metric in `data/reconciliation.py`'s freshness output (item 6).
5. Persist entitlement ALLOWED/DENIED decisions into the ledger, not just console output (item 8,
   auditability half).
6. A real LLM predicate-generation cache keyed on topic-cluster identity (item 10).
7. One stub delivery-channel beyond the dashboard, even a simulated "would post to Slack" call
   (item 7).

Items 1-3 are being implemented now (see git log / README §0 for status once done). Items 4-7
remain open.
