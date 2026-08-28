# REFUTE — Falsification-First KPI Intelligence

**Accenture Hackathon — Problem Statement 3: BusinessIntelligence.ai**

> Every automated root-cause tool ranks hypotheses by supporting evidence. REFUTE generates the test that would kill each hypothesis, runs it, and publishes its own hit rate.

This README captures the accumulated context from Round 1 (accepted concept) and Round 2 (expanded prototype brief), plus the current build status. Sections 1–10 below are the original architecture/context document (still accurate as design intent); this section is the living status — check here first.

---

## 0. Implementation status (updated 2026-08-28)

**The full engine (L1–L6, plus live LLM predicate generation) is built and validated end-to-end against planted ground truth.** Run it yourself:

```bash
uv run python run_pipeline.py               # templated path: all six layers, no LLM calls, a few seconds
uv run python run_pipeline.py --with-llm    # + live local-GPU LLM predicate generation (needs CUDA; downloads ~6GB on first run)
uv run uvicorn api.main:app --reload        # then open http://127.0.0.1:8000 -- the web dashboard (run the pipeline first)
```

### What's working

| Layer | File | Status |
|---|---|---|
| Semantic contract | [semantic/kpi_contract.yaml](semantic/kpi_contract.yaml) | 4 KPIs, 5 sources, 3 entitlement roles, maturity rule, lineage, an `operational_constraints.max_accounts_per_rep` capacity ceiling |
| Synthetic data | [data/generate_synthetic_data.py](data/generate_synthetic_data.py) | 9,228 POS rows, 3 sources at 3 cadences, 1 true cause + 4 decoys, sparse-history category, definition-drift pair |
| Reconciliation | [data/reconciliation.py](data/reconciliation.py) | Multi-cadence resampling, freshness tracking, **two** independently-kinded falsifiable claims: cross-source revenue agreement (correctly flags the planted 2% gross/net definition drift) and rep-attributed-revenue-within-region-revenue logical bounds (correctly holds, max ratio 13.0%) |
| L1 Signal | [engine/l1_signal.py](engine/l1_signal.py) | Custom BOCPD (NIG conjugate prior, constant hazard), statistical × business-impact gate, sparse-history empirical-Bayes shrinkage, `prioritize_material_movements()` ranking every gated movement by impact×confidence and flagging likely-same-event pairs |
| L2 Localise | [engine/l2_localise.py](engine/l2_localise.py) | Exact price/volume/mix bridge, Monte-Carlo Shapley category attribution with bootstrap CIs |
| L3 Hypothesise | [engine/l3_hypothesise.py](engine/l3_hypothesise.py) | Sentence-transformer embeddings + agglomerative clustering over support tickets, per-topic BOCPD, structural precedence filter (the naive-RAG-trap defense) |
| L4 Compiler | [engine/l4_compiler.py](engine/l4_compiler.py) | Pydantic predicate schema with hard-validated `refutes_if`, whitelisted parameterized SQL compiler, compile-time entitlement checks, `sql_hash()` traceability attached to every fetched panel |
| L5 Adjudicate | [engine/l5_adjudicate.py](engine/l5_adjudicate.py) | DiD (log-scale, unit fixed effects, cluster-robust → HC1 fallback below 4 clusters/side), mandatory parallel-trends check, the power gate, BH-FDR correction, every verdict carries its treatment/control SQL + hash through to the ledger |
| L6 Narrate + Ledger | [engine/l6_narrate_ledger.py](engine/l6_narrate_ledger.py) | Structured action template (capacity-constraint-qualified), persona rendering (3 roles, entitlement-filtered), SQLite ledger with SQL-traceability columns, telemetry, feedback-as-falsification-event, `detect_contradictory_verdicts()` distinguishing same-evidence re-narration from genuine independent contradiction |
| L4 Live LLM generation | [engine/l4_llm_generation.py](engine/l4_llm_generation.py) | Local GPU (Qwen2.5-3B-Instruct via `outlines`, token-level schema-constrained decoding), two-gate validation (schema + semantic domain), graceful fallback, real telemetry |
| Calibration mechanism | [engine/calibration.py](engine/calibration.py) | Brier score, reliability diagram, `sklearn.isotonic` recalibration — proven against a clearly-labeled **simulated backtest** (40 outcomes, deliberately imperfect confidence-accuracy relationship) since the live ledger has 0 real scored outcomes yet and needs ≥30 before real calibration would be honest; see the module docstring for why faking history was rejected |
| Web UI | [api/main.py](api/main.py), [ui/](ui/) | FastAPI backend reading the pipeline's own JSON/SQLite output (no separate analysis logic) + a static dashboard: KPI chart with changepoint marker, priority queue, contradictory-evidence panel, hypothesis cards with expandable raw-SQL evidence, **three** persona views (Leader/Manager/Engineer, with a *real* `check_entitlement` call behind each entitlement note), evidence/freshness table (now including the rep-attribution bounds claim), telemetry strip, methods-breakdown table, counterfactual projection chart, adversarial-challenge panel, calibration panel, live feedback-loop demo |
| Methods registry | [engine/methods_registry.py](engine/methods_registry.py) | Single source of truth for which method category (deterministic logic / SQL / business rules / statistics / traditional ML / causal inference / LLM) each stage uses and why, with a structural check that no LLM stage is ever marked a quantitative source of truth — imported directly by the UI and the Engineer persona view, not restated |
| Scalability benchmark | [tests/scalability_test.py](tests/scalability_test.py) | Times each layer's actual bottleneck operation at up to 65-225x the demo dataset's scale, plus an **integrated** real-SQLite → real compiler → real DiD path (not an isolated-kernel proxy) — see §6, objective 8, for results |

### The canonical worked example, actually running

West revenue fell **-8.9%** in week 32 (L1 gate: PASS, posterior 0.79 — East/Central correctly stay noise, no LLM call). Five hypotheses tested, all landing on the intended verdict:

| Hypothesis | Archetype | Verdict | Why |
|---|---|---|---|
| `h_shipping_delay` | placebo | **KILLED** | Not significant, and the test had power to detect a real effect (MDE 8% ≤ 10% floor) |
| `h_competitor_launch` | specificity | **KILLED** | Same — a properly-powered non-result |
| `h_billing_complaints` | precedence | **KILLED** | Its own changepoint (week 33) comes *after* the KPI's (week 32) — caught structurally at L3 generation time, independently re-confirmed by L5's formal precedence test |
| `h_accessories_pricing` | specificity | **INCONCLUSIVE** | MDE (28%) far exceeds the plausible-effect floor (10%) — genuinely underpowered, not swept into KILLED |
| `h_rep_attrition` | placebo | **SURVIVED** | 300pp differential effect, p<0.001; **71% of the loss sits in the four departed reps' accounts** (calibrated to match the canonical narrative exactly) |

This is the exact three-way split (obvious decoys killed / underpowered decoy inconclusive / true cause survives) that §8's evaluation targets call the most important thing to get right — and it's now real, not aspirational.

### Live LLM predicate generation, also actually running

`engine/l4_llm_generation.py` loads Qwen2.5-3B-Instruct locally on GPU (no API key, no hosted cost) and uses `outlines` to constrain its output to the exact same `Predicate` Pydantic schema the templated fixtures use — token-level constrained decoding, not "ask for JSON and retry." A second gate (`validate_semantics`) checks the model's chosen dimension values actually exist in the data and the archetype is one the compiler implements; either gate failing means the predicate is rejected and the templated fixtures remain the fallback, never a guess.

The evidence this works isn't "it produced valid JSON" — it's convergence: given the accessories/pricing support-ticket cluster, the model independently proposes treatment=Accessories vs. control=[Electronics, Home, Apparel] on `product_category` — the *exact structure* of the hand-written `h_accessories_pricing` fixture — and that independently-generated predicate reaches the identical **INCONCLUSIVE** verdict through L5's adjudication. The other two candidate clusters converge on the same treatment/control structure as `h_shipping_delay` and correctly come back **KILLED**. A smaller model (Qwen2.5-1.5B-Instruct) was tried first and reliably passed both validation gates but chose weaker dimensions and mislabeled every archetype as "precedence" — a genuine, documented finding about model-size requirements for this task, not swept under the rug (see the module docstring).

Runtime cost for this run is genuinely **$0** (local GPU compute) — the ledger also computes what an equivalent hosted-API call would have cost, for comparison, without pretending that's what this run actually spent.

### Real bugs found and fixed along the way (not silently patched)

Documented in the code where they were found, since they're the kind of thing worth a reader knowing about:
- An organic growth trend was masking the planted level-shift from BOCPD entirely.
- The newly-launched Outdoor category was contaminating every region's aggregate revenue simultaneously — fixed via an explicit category-maturity business rule (a real complexity, not a workaround).
- Monthly CRM data flat-repeated across weeks created spurious calendar-quantization changepoints — fixed by analyzing each KPI at its own native refresh cadence instead of upsampling.
- Raw-dollar-level DiD regression was spuriously significant for the two "obvious" decoys purely from cross-group scale differences (one fulfillment center is 4x the size of the others) — fixed via log-transform + unit fixed effects.
- Pooling residual variance across treatment *and* control diluted the underpowered decoy's genuinely higher relative noise — fixed by computing the power-gate noise from the treatment group's own within-unit deviation only.
- A feedback-loop counter-hypothesis was silently defaulting to INCONCLUSIVE via a failed lookup, not genuine adjudication — fixed by making `adjudicate_all()` accept ad-hoc predicates instead of only the static fixture list.
- On Windows, `uv add`/`uv sync` silently resolves `torch` back to the CPU-only wheel (there is no CUDA build on PyPI itself) even after a manual `uv pip install` of the CUDA build — fixed by pinning `torch` to the PyTorch CUDA wheel index permanently via `[tool.uv.sources]` in `pyproject.toml`.
- `outlines`' underlying kernel tried to `torch.compile` itself and silently fell back to eager mode because Triton isn't installed on Windows by default — harmless, but added a 330-second one-time warmup per process. Installing `triton-windows` dropped that to ~6 seconds.
- A telemetry span was timing an empty `with ...: pass` block instead of the LLM call that happened before it opened, recording near-zero latency for real 8-10 second generations — fixed by threading the already-measured latency through instead of re-timing nothing.

### What's honestly labeled as simulated, and why

Everything on the original gap list is now built. One thing remains deliberately simulated rather than faked as real: **calibration** (`engine/calibration.py`) proves the Brier-score/reliability-diagram/isotonic-recalibration mechanism against 40 clearly-labeled synthetic outcomes, not the live ledger — because the live ledger has 0 real scored outcomes (nothing has had time to play out yet) and needs ≥30 before real calibration would mean anything. Inventing 30 fake "historical" verdicts to make the number look real would violate the project's own stated honesty constraint (§4) more than just saying so plainly. The UI's Calibration panel carries this label directly, not buried in a footnote.

### Structural note

The original plan sketched separate top-level `feedback/` and `telemetry/` packages; in the actual build these consolidated into `engine/l6_narrate_ledger.py` since both are thin enough (a SQLite table + a context-manager span, a predicate-reuse function) that a separate package would have been indirection without payoff. `api/` and `ui/` remain empty, reserved for the next phase.

---

## 1. Where we are

- **Round 1 (done):** Pitched REFUTE against Problem Statement 3. Concept accepted, advanced to Round 2.
- **Round 2 (current):** Build a working prototype. This document is the merged spec: the Round 1 architecture (detailed, opinionated, already designed) plus the Round 2 brief's expanded requirements (broader, some of which Round 1 doesn't yet explicitly cover — see [§6](#6-round-2-gap-check-against-the-round-1-design)).
- **Do not silently change:** the project name (REFUTE), the core mechanic (falsification, not ranking), or the honest-positioning language in [§4](#4-positioning--prior-art-read-before-writing-any-docs-or-copy) without flagging it first.

---

## 2. The core idea

**The naive build:** LLM reads data → LLM writes an explanation. This fails structurally: slice business data enough ways (region × channel × segment × time...) and *something* will always correlate with an anomaly window — a statistical guarantee (garden of forking paths, Gelman & Loken), not a fixable data-quality problem.

**What shipping RCA tools do (Tellius, causaLens, Tredence, cloud-native RCA):** generate hypotheses, rank by supporting evidence, return the top-ranked one. Confident, fluent, well-cited — and never wrong "out loud," because it was never at risk of being falsified.

**REFUTE's inversion:** instead of ranking hypotheses by supporting evidence, generate the test that would *disprove* each hypothesis, run it, and report only what survives. This automates a falsificationist final step (Popper's demarcation criterion) on top of the existing RCA stack. The closest real precedent is Microsoft's **DoWhy**, which ships `refute_estimate()` methods (placebo, random common cause, data subset, dummy outcome) as manual, analyst-invoked tools for causal *estimates*. REFUTE's specific claim: automating that refutation-testing pattern end-to-end for business-KPI root-cause hypotheses, without a human hand-writing each test.

We did not invent refutation testing. We automated it for a domain (business KPIs) and workflow (root-cause hypothesis generation) where, as far as verified, nobody currently does this automatically.

---

## 3. Architecture — six layers (Round 1 spec, authoritative)

Build in this order; each layer is a genuine sub-problem, not a rebrand of the previous one.

### L1 — SIGNAL (real move, or noise?)
- Bayesian Online Changepoint Detection (BOCPD, Adams & MacKay 2007) over an STL-deseasonalised series. Run-length posterior `P(rₜ | y₁:ₜ)`, constant hazard, Student-t predictive via Normal-Inverse-Gamma conjugate prior.
- Returns a posterior over *when* the break occurred (τ), not a point estimate — needed downstream for DiD pre/post windows.
- **Gate:** proceed past L1 only if `P(changepoint ∈ recent window) > 0.9`. Otherwise: "within normal variation," and **no LLM call is made.** This is both the noise filter and the cost-control mechanism.
- Libraries: `ruptures` and/or custom BOCPD (~150 LOC), `statsmodels` for STL.

### L2 — LOCALISE (where, never why)
- Additive metrics (`Revenue = Σ quantity_i × price_i`): exact decomposition into volume / price / mix-interaction effects.
- Non-additive/interacting dimensions: Shapley value attribution via Monte-Carlo permutation (`m ≈ 2000`), reporting the attribution **confidence interval**, not a bare point estimate.
- **Enforced by schema/type, not prompt instruction:** L2 output tagged `"kind": "localisation"`, can never be rendered downstream as a causal claim.

### L3 — HYPOTHESISE (candidate causes, structured + unstructured)
- Avoids the naive-RAG trap (retrieving tickets from the anomaly window is fatally biased — ticket volume/sentiment moves in every bad week).
- Correct approach: embed all tickets/notes continuously (`sentence-transformers`) → cluster into topics (UMAP + HDBSCAN, or BERTopic) → build a per-topic time series → run the **same BOCPD from L1** independently per topic → a topic only becomes a candidate if it has its *own* independent changepoint whose τ precedes the KPI's τ (checked structurally, not left to the LLM).
- LLM's role: given a topic cluster + localisation slice, state a plausible causal mechanism in natural language. It proposes; it does not adjudicate.

### L4 — FALSIFICATION COMPILER ⭐ (core engineering contribution, highest risk, build/stub first)
- The LLM never writes SQL. It emits a typed JSON "causal predicate" under constrained/structured decoding (Pydantic schema validation). A separate deterministic compiler turns the predicate into parameterised, read-only SQL against a whitelisted schema.
- Predicate schema (required fields): `hypothesis_id`, `mechanism`, `test_archetype`, `treatment`, `control`, `outcome`, `temporal` (`cause_onset`, `kpi_onset`), `refutes_if` (`condition`, `rationale`).
- **`refutes_if` is mandatory.** A hypothesis that cannot state its own refutation condition is rejected before testing — Popper's demarcation criterion as a hard schema/validation constraint, not a prompt instruction.
- Four test archetypes (compiler selects applicable ones based on predicate structure):

| Archetype | Question | Kills the hypothesis when | Method |
|---|---|---|---|
| Placebo | Does effect appear where mechanism can't reach? | Unexposed control shows same drop | Difference-in-differences |
| Dose-response | Does effect scale with exposure? | No monotone relationship across strata | Rank correlation / stratified regression |
| Precedence | Did cause actually come first? | Effect onset precedes cause onset | BOCPD τ comparison + Granger causality |
| Specificity | Is effect confined to the right metric? | Unrelated metrics moved identically | Multi-outcome DiD |

- Must survive **all applicable** archetypes to be reported SURVIVED.
- Graceful degradation: if the LLM fails to produce a valid predicate, fall back to template archetypes over the top-3 localisation slices — never fall back to guessing.
- Tooling: Pydantic, SQLGlot (build SQL AST from validated predicate, not string concatenation).

### L5 — ADJUDICATE (run the test, three-valued verdict)
- DiD for placebo/specificity: `Y_it = α + β·treat_i + γ·post_t + δ·(treat_i × post_t) + ε_it`, SEs clustered at treatment-unit level (naive SEs overstate significance — Bertrand, Duflo, Mullainathan).
- **Parallel-trends pre-check is mandatory.** Non-parallel pre-trends (`p < 0.1` on any lead) → **INCONCLUSIVE**, not a verdict.
- **The power gate — single most important design decision, do not skip:** compute minimum detectable effect (MDE) at 80% power before accepting a null as KILLED.
  ```
  if effect is statistically significant  →  SURVIVES this test
  elif MDE > plausible_effect_size        →  INCONCLUSIVE (underpowered — name the sample size that would resolve it)
  else                                     →  KILLED
  ```
  Reporting KILLED on an underpowered test launders thin data as evidence of absence — worse than not testing. The three-valued verdict (KILLED / SURVIVED / INCONCLUSIVE) is load-bearing, not a nice-to-have.
- **Multiple-comparisons correction:** Benjamini-Hochberg FDR at `q = 0.10` across all `~4n` tests (BH, not Bonferroni — tests are positively dependent). Report the BH-adjusted threshold in the output.
- Libraries: `linearmodels` (PanelOLS, clustered SEs), `statsmodels` (power analysis).

### L6 — NARRATE + LEDGER
- **Narrate:** whatever survives → plain-English brief: what changed, why (surviving mechanism), what to do, stated confidence. When nothing survives, the brief says so explicitly and names the specific additional data that would resolve the ambiguity — a literal, visible system behavior, not just philosophy.
- **Ledger:** every verdict writes an immutable record — predicate, generated-SQL hash, effect estimate + CI, power, BH-adjusted p-value, verdict, predicted direction/magnitude if the recommendation is followed. At a defined horizon, score actual vs. predicted: Brier score, reliability diagram, isotonic regression recalibration (`sklearn.isotonic`).
- Source of the headline demo stat ("31 of 38") — replace with a real computed number from the synthetic eval run, or clearly label as illustrative placeholder if the ledger hasn't accumulated 38 scored outcomes yet.

---

## 4. Positioning & prior art (read before writing any docs or copy)

Verified by live web search (mid-August 2026):

**Already exists:**
- Automated RCA on business KPIs is a shipping commercial category: Tellius (dimensional traversal + Shapley + significance testing, sub-60s), causaLens (causal-graph RCA), Tredence (2024 LLM pipeline: anomaly detection → RCA hypothesis generation → testing). **All rank hypotheses by supporting evidence.**
- DoWhy ships `refute_estimate()` — placebo, random common cause, data subset, dummy outcome refuters — as manual, analyst-invoked tools for causal *estimates*, not an automated end-to-end pipeline for business-KPI root-cause hypotheses.
- LLM guardrail/observability platforms (NeMo Guardrails, Guardrails AI, Bedrock Guardrails) are a separate, crowded space — explored as an alternative direction, **not** part of REFUTE's scope.

**Verified NOT already automated (our actual claim):**
1. Automatic generation of a *disconfirming* test per hypothesis (not just ranking by supporting evidence) — no product does this automatically.
2. "Insufficient evidence" / INCONCLUSIVE as a first-class output with a stated resolution path — competing products architecturally always return a ranked answer.
3. A self-scoring accuracy ledger — no RCA product publishes its own historical hit rate.

**Honesty constraints for any generated docs/copy:**
- Never claim to have invented RCA, causal inference, or refutation testing.
- Always position against DoWhy specifically — citing it correctly is more credible than staying silent.
- State limitations plainly: DiD needs a valid control group (report identification failure, don't force a weaker design); SURVIVED means "survived the tests we could construct," not "proven true" — unobserved confounders remain unobserved; topic clustering degrades below ~200 documents/window; the ledger needs ~30 scored outcomes before calibration is meaningful — early runs labeled uncalibrated, not shown with a confident hit-rate they haven't earned.

---

## 5. Data plan

No production/scraped data — **synthetic and hand-authored is a requirement, not a shortcut.** Falsification can only be validated against known ground truth.

- ~500 rows transaction-level or weekly-aggregated KPI data, ≥2 dimensions (e.g. region × fulfillment_center), enough time span for clean pre/post windows around a planted changepoint.
- ~50 synthetic support tickets/notes, embeddable, realistic-sounding but fabricated.
- One planted true cause.
- **At least three planted decoys, each failing a different way:**
  1. Coincidentally timed (correlates, no mechanism) — killed via **placebo**.
  2. Reverse-caused (KPI change caused the "cause," not vice versa) — killed via **precedence**.
  3. Genuinely underpowered (real but tiny, or too little data) — must return **INCONCLUSIVE, not KILLED**. This is the single most important test case: any system can kill an obvious decoy; correctly declining to kill an underpowered one — and saying so — is what separates an honest system from a confident one.

**Canonical worked example (already used in deck/script/diagrams — reproduce exactly):**
- Region West revenue fell 8% in week 32.
- Candidate 1: "Shipping delays" → KILLED (unaffected products fell equally — fails placebo).
- Candidate 2: "Competitor launch" → KILLED (non-overlapping categories fell just as hard — fails specificity).
- Candidate 3: "Rep attrition" → SURVIVED (71% of loss sits in four departed reps' accounts, timing aligns, survives all applicable tests).
- Final brief: *"Revenue fell 8%. The cause is rep attrition — 71% of the loss sits in four departed reps' accounts. Shipping delays and the competitor launch were both tested and ruled out. Next step: reassign those accounts this week."*

---

## 6. Round 2 coverage — final status (updated 2026-08-28)

This section originally tracked gaps between the Round 1 design and the Round 2 brief *before* any of it was built (see git history if you want that snapshot). It's now a straight coverage check against what's actually running.

### The 8 objectives

| # | Objective | Status | Where |
|---|---|---|---|
| 1 | Detect & prioritise material KPI movements | ✅ | L1's BOCPD posterior × business-impact-vs-baseline gate detects (`engine/l1_signal.py`); `prioritize_material_movements()` ranks every currently-gated movement by impact×confidence and flags likely-same-event pairs (`/api/priorities`, "Priority Queue" panel) — the "prioritise" half isn't just implied by the gate, it's a separate ranked output |
| 2 | Reconcile data/context across heterogeneous sources | ✅ | `data/reconciliation.py` — 3 cadences resampled to a common grain, freshness tracked, **two** independently-kinded falsifiable claims tested: cross-source revenue agreement (flags the planted definition drift) and rep-attribution logical bounds (a genuinely different kind of check — an internal-consistency constraint, not a cross-source comparison) |
| 3 | Identify & rank explanatory drivers using appropriate analytical methods | ✅ | L2 (exact decomposition + Shapley) locates *where*; L4/L5 (falsification, not ranking) decide *why*; every verdict now carries the exact SQL + hash that produced it, expandable per hypothesis card — traceability all the way to the query, not just the conclusion |
| 4 | Generate persona-specific narratives supported by traceable evidence | ✅ | **Three** stakeholder views (exceeds the ≥2 minimum) — Leader, Manager, Engineer — one ledger, three renderers, entitlement-checked live |
| 5 | Communicate uncertainty and abstain when evidence is insufficient/contradictory | ✅ | INCONCLUSIVE verdict, the power gate, BH correction — **and** `detect_contradictory_verdicts()` (§ below), which actually checks currently-SURVIVED verdicts against each other and distinguishes "same evidence, re-narrated" from "genuinely independent contradiction," rather than assuming contradictions can't happen |
| 6 | Recommend practical actions grounded in business levers, constraints, decision rights | ✅ | `driver → controllable lever → action → expected impact → owner → confidence → monitoring plan`, populated from real L2/L5 numbers; the action is now also checked against a real operational constraint (`max_accounts_per_rep` capacity ceiling from the semantic contract) and qualified when it doesn't fully fit — a recommendation that ignores whether the team has room to execute it isn't actually actionable |
| 7 | Mechanism to learn from analyst and business-user feedback | ✅ | Two real, working halves: (a) capture+re-test — a correction becomes a new predicate run through the identical L4/L5 pipeline (`submit_feedback`, now correctly writing the counter-verdict to the ledger too, not just the feedback log); (b) calibration mechanics (`engine/calibration.py`) — Brier score, reliability diagram, isotonic recalibration, proven against a clearly-labeled simulated backtest since the live ledger has 0 real scored outcomes yet |
| 8 | Operate within realistic security, cost, latency and scalability constraints | ✅ | Security: real (compile-time entitlement checks, 3 roles with distinct row/column scope). Cost/latency: real telemetry, $0 marginal LLM cost via local GPU, sub-minute end-to-end runtime. **Scalability: measured, not asserted** — see `tests/scalability_test.py` and the results below, including a real integrated path, not just isolated kernels. |

**Scalability benchmark results** (`uv run python tests/scalability_test.py`): each layer's actual bottleneck operation, timed at up to 65-225x the demo dataset's scale, independent of the calibrated demo scenario (this measures runtime, not verdict correctness — correctness is already validated against the demo data).

| Layer | Scales with | Max tested | Time at max | Observed scaling |
|---|---|---|---|---|
| L1 BOCPD | weeks of history | 4,000 weeks (100x) | 0.65s | ~linear |
| L2 Monte-Carlo Shapley | categories/dimensions | 500 (125x) | 0.68s | ~O(N^1.6) — the one genuinely super-linear layer, but still sub-second at 500 categories, far more than any real business's category count |
| L3 embeddings + clustering | support tickets | 8,000 (154x) | 6.2s | ~linear (a naive O(N²) bound was expected from full-linkage agglomerative clustering; sklearn's implementation didn't show it at this scale) |
| L5 DiD adjudication | panel units (×20 periods) | 400 (100x) | 0.47s | ~linear |
| **Integrated SQL→panel→DiD** (real SQLite + real `compile_unit_query` + real `did_estimate`, not a proxy) | fulfillment centers | 100 (33x) | 0.059s | ~linear (k~0.49) |

None of the five showed catastrophic (worse-than-quadratic) growth within two orders of magnitude — including the one suite that exercises the actual compile→SQL→execute→adjudicate path end-to-end rather than an isolated kernel with synthetic stand-in data. This is a measured result at the scales actually tested (up to ~150x, not literally 1000x) — extrapolating Shapley's O(N^1.6) out to, say, 5,000 categories would land around a minute, which would matter for an unusually category-heavy business but isn't a concern at realistic scale. The honest residual caveat: this is a compute-scaling benchmark on synthetic data, not a full production load test (concurrent users, database contention, network latency to a real warehouse aren't modeled here).

### "The LLM should not be treated as the source of quantitative truth"

Answered structurally, not just asserted: [`engine/methods_registry.py`](engine/methods_registry.py) is a single table every pipeline stage declares itself against — which method category (deterministic logic / SQL / business rules / statistics / traditional ML / causal inference / LLM) it uses, and why, with a `quantitative_output` flag per stage. `assert_llm_not_quantitative_source()` checks programmatically that no LLM-driven stage is marked as a source of quantitative truth — it fails loudly if that's ever violated, rather than relying on someone remembering to keep the docs honest. The one `llm` row in the registry (mechanism + predicate *proposal*, Qwen2.5-3B-Instruct) is explicitly `quantitative_output: False`: an LLM proposal is re-validated (schema + semantic domain gates) and then *tested* by deterministic/statistical machinery before any verdict exists — it never gets to just assert an answer. The UI's "Methods Breakdown" panel and the Engineer persona view both render this table live from the code, not from a copy of it.

Retrieval gets the same treatment: L3's ticket-topic clustering is explicitly *not* naive RAG (see `engine/l3_hypothesise.py`'s docstring) — retrieving tickets from the anomaly window is fatally biased since ticket volume moves in every bad week regardless of cause, so a topic only becomes a candidate if it has its own independent BOCPD changepoint that precedes the KPI's, checked structurally, not left to an LLM's judgment.

### Minimum prototype expectations

All satisfied — 5 connected KPIs across 3 sources at 3 cadences; the semantic contract (`semantic/kpi_contract.yaml`); 3 personas; the canonical multi-factor worked example; the accessories-pricing low-confidence/abstain scenario; the Outdoor sparse-history scenario; the `regional_vp` role-based entitlement denial (now alongside a third `platform_engineer` role); the evidence panel (freshness/method/contribution/confidence/lineage together); the LLM-vs-non-LLM breakdown (telemetry strip + methods registry); runtime telemetry (latency, real token counts, real model calls, $0 actual cost with a hosted-API cost comparison for context).

### Beyond the brief (Tier 3 stretch, not required but built)

- **Visible counterfactual projection** (`build_counterfactual_projection` in `engine/l6_narrate_ledger.py`) — a forward-projected recovery band under a stated assumption, not a guarantee, rendered as a chart.
- **Adversarial counter-hypothesis generation** (`generate_adversarial_challenge` in `engine/l4_llm_generation.py`) — before trusting a SURVIVED verdict, the model is asked to argue the strongest opposing case using a different dimension, and that challenge is run through the identical L4/L5 pipeline. In practice it has reliably rediscovered the same decoy structures (a competitor/operating-cost story) and been correctly KILLED — the surviving conclusion holding up against the best counter-case the model itself could construct.

---

## 7. Build priorities (Round 1 sequencing — still the right order for the core engine)

**Critical path risk:** L4 (falsification compiler) is the highest-risk component — getting an LLM to reliably emit a genuinely *disconfirming* predicate (not a supporting hypothesis dressed up in the schema) is the real engineering risk.

1. **Templated/deterministic fallback path first.** Hand-write the three worked-example predicates (shipping delay, competitor launch, rep attrition) as static JSON. Get compiler → SQL → execution → DiD → verdict working end-to-end against these before touching LLM generation.
2. Add L1 (BOCPD) + L2 (localisation) — well-understood, lower risk.
3. Add L3 (topic clustering + hypothesis candidates from unstructured data) — moderate risk, degrades gracefully.
4. **Last:** wire in live LLM generation for the causal predicate in L4, with constrained decoding / Pydantic validation and the `refutes_if` hard check. If this doesn't converge in time, the templated fallback is an acceptable demo — it still proves the architecture.
5. L6 narration (templated string formatting is fine for the demo) + ledger (SQLite).
6. **Minimal UI last:** demo footage of the worked example — three hypothesis cards, two struck through, one surviving, then the narrated brief. FastAPI backend + minimal React/server-rendered frontend is enough.

**Suggested stack:** Python throughout — `ruptures`/custom BOCPD + `statsmodels` (L1), `pandas` + Monte-Carlo Shapley (L2), `sentence-transformers` + UMAP + HDBSCAN (L3), Pydantic + SQLGlot (L4), `linearmodels` + `statsmodels.power` (L5), SQLite + `sklearn.isotonic` (L6), FastAPI backend, React frontend if time allows.

**Complexity target:** sub-60s end-to-end, dominated by I/O not compute — falsification tests are embarrassingly parallel across archetypes.

---

## 8. Evaluation targets

| Metric | Target | Why it matters |
|---|---|---|
| True planted cause survives all applicable tests | 100% | Core correctness |
| Both "obvious" decoys correctly killed | >90% | Proves the falsification mechanic works |
| Underpowered decoy returns INCONCLUSIVE, not KILLED | 100% — must not fail | Proves the power gate is real, not decorative |
| BH-adjusted significance reported and used | Present in output | System holds itself to its own stated standard |
| Time to verdict, end-to-end | <60s | Feasibility/demo-ability |
| `refutes_if` enforced (predicates without it rejected) | 100% | Proves the Popperian constraint is a real code-level check |

**If only one thing can be fully correct, make it the underpowered-decoy → INCONCLUSIVE case.** It's the single test proving this is meaningfully different from "yet another RCA ranking tool," and the one most likely to be silently skipped under time pressure.

---

## 9. What's already built (Round 1 deliverables, not part of the coding task)

1. Written submission copy (problem statement ~197 words, solution ~193 words).
2. Full technical specification document (longer prose version of §3 above, with additional implementation notes and an honest-limitations section).
3. Video narration script, timed ~2:50, slide-synced.
4. 15-slide deck (`REFUTE.pptx`, built via pptxgenjs): 11 narration slides + 3 appendix slides + 1 narration cue sheet. Custom flat SVG diagrams (no default Mermaid styling). Strict color discipline: red/green-teal reserved exclusively for KILLED/SURVIVED verdicts.
5. Logo assets: wordmark (light + dark), icon mark (three bars — two struck red, one checked green), plus unfinalized alternates.

---

## 10. Next step

**Superseded by [§0](#0-implementation-status-updated-2026-08-28) above** — the gap list this section originally pointed to has been built and validated (see §0's table and worked-example results). This section is kept for history; the live plan is the one at `.claude/plans/so-tell-me-what-federated-brook.md` (see that file's own updated status/next-step sections), and the actionable next-step list is:

1. ~~Live LLM wiring~~ — **done** (`engine/l4_llm_generation.py`, Qwen2.5-3B-Instruct on local GPU via `outlines`).
2. ~~Minimal UI~~ — **done**, now with three persona views, a methods-breakdown panel, and both Tier 3 stretch features rendered live (`api/main.py` + `ui/`, `uv run uvicorn api.main:app --reload`).
3. ~~Tier 3 stretch features~~ — **done**: visible counterfactual projection (`build_counterfactual_projection`) and adversarial counter-hypothesis generation (`generate_adversarial_challenge`), both in the UI.
4. ~~Scalability testing~~ — **done**, `tests/scalability_test.py`, results in §6 objective 8, including a real integrated SQL→compiler→DiD path.
5. ~~Ledger calibration mechanism~~ — **done** (`engine/calibration.py`), proven against a clearly-labeled simulated backtest rather than faking real production history the live ledger hasn't accumulated yet (needs ≥30 real scored outcomes; has 0).
6. ~~SQL traceability, prioritisation, contradictory-evidence detection, capacity-constrained actions, a second reconciliation check~~ — **done**, closing every gap the honest self-audit had previously flagged as "breadth, not depth." Every objective in §6's table is now backed by real, running code, not just architectural coverage.

Every item on the original build plan is complete. Remaining work, if any, is refinement (more synthetic scenarios, a real production data source, accruing real scored outcomes for genuine calibration) rather than closing a known gap.
