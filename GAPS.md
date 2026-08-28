# REFUTE — Real-World-Complexity Gap Audit

The Round 2 brief names 10 "Real-World Complexities to Consider." This document is a line-by-line
audit of REFUTE against that exact list, done by reading the actual source (not the README's
self-report) — grepping for real implementations, confirming what's wired up vs. merely declared
in a docstring or YAML field. Written 2026-08-28.

Status legend: ✅ done and verified in code · ⚠️ partially done, real limit stated below · ❌ not
implemented at all.

**Update (2026-08-28, same day):** the top 3 priority items are closed. See "Priority order to
close" at the bottom for what's still open (items 4-7 are unchanged).

- **Model/data drift (item 9)** — [`engine/drift_monitor.py`](engine/drift_monitor.py) (new
  module). Real PSI-based assessment against this ledger's own accumulated run history
  (`run_snapshots` table, wired into `engine/l6_narrate_ledger.py`'s `main()`), honestly reporting
  `insufficient_history` below 5 prior runs rather than a hollow number — same pattern
  `engine/calibration.py` uses. `run_drift_demo()` proves the PSI mechanism itself is correct
  against a clearly labeled simulated run history. Exposed at `/api/drift` and rendered in the UI's
  new "Model/Data Drift Monitoring" panel.
- **Domain-level security (item 8)** — enforced for real now, not just declared.
  `kpis.<name>.domain` added to every KPI in the contract; `domain_scope` added to every role
  (including a new `marketing_analyst` role built specifically to demonstrate the mechanism is
  distinct from row/column scope — it's denied the `sales` domain outright, so it can't compile
  *any* predicate with `revenue` as the outcome, regardless of region). `check_domain_entitlement()`
  in `engine/l4_compiler.py`, called at every compile-time entry point
  (`compile_predicate`/`fetch_unit_panel`) and filtering `/api/l1-summary` by role. `regional_vp`
  requesting `rep_attributed_revenue` is now denied at the domain level (previously only the
  `rep_id` *dimension* was gated — the KPI's aggregate trend was visible regardless).
- **Marketing as a real hypothesis + the unused `dose_response` archetype (item 1 + the archetype
  finding)** — `h_marketing_spend_cut` in `engine/l4_compiler.py` (`MARKETING_DOSE_RESPONSE_FIXTURE`)
  is a real `dose_response` predicate, adjudicated by `evaluate_dose_response_test()` in
  `engine/l5_adjudicate.py`: Spearman rank correlation between per-(region,channel) marketing-spend
  change and regional revenue change, with its own MDE-equivalent power gate (Fisher z-transform,
  Cohen 1988). Run for real against the synthetic data, it returns **INCONCLUSIVE** (rho=-0.15,
  p=0.65, underpowered at n=12 strata) — an honest result, not a rigged KILLED, and arguably a
  stronger demo than a clean kill since it shows the power gate applies uniformly across
  archetypes, not just the DiD-based ones.

---

## 1. Multiple interacting drivers (price, volume, mix, marketing, supply, seasonality, competition, external events) — ⚠️ (marketing now ✅, see update above)

- Price/volume/mix: ✅ exact decomposition, [`engine/l2_localise.py`](engine/l2_localise.py).
- Supply: ✅ `h_shipping_delay` decoy, correctly killed via placebo.
- Competition: ✅ `h_competitor_launch` decoy, correctly killed via specificity.
- Seasonality: ✅ *deliberately* out of scope, not silently skipped — [`engine/l1_signal.py:14-21`](engine/l1_signal.py#L14)
  explains the 40-week analysis window is under one annual cycle, so there's no fittable STL
  seasonal period at this grain. A real, stated engineering judgment.
- Marketing: ✅ (was ⚠️) `marketing_attributed_revenue_share` is a real KPI in
  [`semantic/kpi_contract.yaml`](semantic/kpi_contract.yaml) that L1's BOCPD gate genuinely
  monitors, AND it's now a real *candidate cause* hypothesis too: `h_marketing_spend_cut`
  (`dose_response` archetype, `evaluate_dose_response_test()` in
  [`engine/l5_adjudicate.py`](engine/l5_adjudicate.py)) actually tests "did a marketing spend cut
  cause the revenue drop" via Spearman rank correlation across (region, channel) strata — returns
  INCONCLUSIVE for real (rho=-0.15, underpowered at n=12), not a rigged verdict.
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

## 8. Row-, column- and domain-level security, sensitive-data protection, auditability — ⚠️ (domain-level now ✅, see update above)

- Row-level (region): ✅ enforced at compile time, `check_entitlement()` in
  [`engine/l4_compiler.py:152`](engine/l4_compiler.py#L152).
- Column-level (`rep_detail_restricted`): ✅ enforced the same way.
- Domain-level: ✅ (was ❌ **declared but not enforced**). `kpis.<name>.domain` and
  `entitlements.<role>.domain_scope` are now real contract fields, and
  `check_domain_entitlement()` in [`engine/l4_compiler.py`](engine/l4_compiler.py) enforces them at
  every compile-time entry point plus `/api/l1-summary`'s row filtering. `regional_vp` requesting
  `rep_attributed_revenue` (any grain, not just the `rep_id` dimension) is now genuinely denied;
  the new `marketing_analyst` role demonstrates the strictest case (denied the `sales` domain
  outright, so it can't compile any predicate against `revenue` regardless of region).
- Sensitive-data protection: ✅ the strongest real case is rep-level HR/compensation data gated by
  column scope.
- Auditability: ⚠️ the ledger is a genuine immutable record of *what was tested and how* (verdict +
  SQL hash). But entitlement ALLOWED/DENIED decisions only print to the console in the demo
  script — they are never persisted. So "what evidence supports a verdict" is auditable; "who
  tried to access what and was denied" is not. (Feedback events do record `analyst_role`, so that
  slice is real.)

## 9. Model and data drift, feedback capture, continuous evaluation — ✅ (was ❌ drift / ✅ feedback, see update above)

- Feedback capture: ✅ real — `submit_feedback()` re-runs a correction through the identical
  L4/L5 pipeline and (after a real bug fix documented in README §0) correctly writes the
  counter-verdict to the ledger, not just the feedback log.
- Continuous evaluation groundwork: ✅ calibration machinery exists, honestly gated on needing
  real outcomes to accrue.
- Model/data drift: ✅ (was ❌ **zero implementation**). [`engine/drift_monitor.py`](engine/drift_monitor.py)
  now tracks L1 changepoint posteriors and L5 DiD effect sizes/MDEs per run (`run_snapshots`
  table), compares the current run against pooled prior-run history via Population Stability Index
  (Siddiqi 2006), and honestly reports `insufficient_history` below 5 prior runs instead of a
  hollow number. `run_drift_demo()` proves the PSI mechanism itself is correct against a labeled
  simulated run history.

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

- ~~**`dose_response` archetype is defined but never exercised.**~~ **Fixed** — see the marketing
  update above. All four archetypes named in the spec now have a real fixture and a real L5 code
  path.
- REFUTE also does several things **not asked for by this list at all**, worth keeping in mind
  when judges compare coverage: the Popperian `refutes_if` hard constraint, MDE/power gating so a
  null result can't be laundered as evidence of absence, Benjamini-Hochberg correction across the
  whole hypothesis family (the garden-of-forking-paths problem), the naive-RAG-trap defense in L3,
  and adversarial self-challenge before trusting a SURVIVED verdict.

---

## Priority order to close

1. ~~**Model/data drift** (item 9)~~ — **done**, see update at the top.
2. ~~**Domain-level security** (item 8)~~ — **done**, see update at the top.
3. ~~**Marketing as a real candidate hypothesis** (item 1)~~ — **done**, see update at the top.
4. Missing-data-rate metric in `data/reconciliation.py`'s freshness output (item 6). *Open.*
5. Persist entitlement ALLOWED/DENIED decisions into the ledger, not just console output (item 8,
   auditability half). *Open.*
6. A real LLM predicate-generation cache keyed on topic-cluster identity (item 10). *Open.*
7. One stub delivery-channel beyond the dashboard, even a simulated "would post to Slack" call
   (item 7). *Open.*

Items 1-3 are closed as of this update. Items 4-7 remain open and are lower-value/lower-effort
than the first three — none of them is named as explicitly by the brief, and none had a 0%-covered
starting point the way drift did.
