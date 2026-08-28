# REFUTE — Round 2 Plan: Merging the Round 1 Design with the Round 2 Brief

> This is a copy of the working plan originally kept as a Claude Code plan-mode file outside the repo (`~/.claude/plans/`), moved into version control here so it travels with the project on handoff instead of living only on one machine. Treat this file, not the external one, as authoritative going forward.

## STATUS UPDATE (2026-08-28, updated a fifth time) — read this first

**Every gap from the honest self-audit is now closed with real, running code — not documentation.** The user's explicit instruction was "make all of the objectives very veryyyy strong, we have to win this hackathon, make sure its production ready type of thing," in response to a self-audit that had flagged several objectives as "breadth, not depth." This pass closed all of them:

- **SQL traceability**: every DiD panel now carries `sql_hash()` of its exact treatment/control queries through L4→L5→ledger; hypothesis cards in the UI expand to show the raw SQL, not just the verdict.
- **Prioritisation** (objective 1's second half): `prioritize_material_movements()` in `engine/l1_signal.py` ranks every currently-gated movement by impact×confidence and flags movements likely to be the same underlying event (same region, nearby onset week) — rendered as a "Priority Queue" panel.
- **Contradictory-evidence detection** (objective 5): `detect_contradictory_verdicts()` in `engine/l6_narrate_ledger.py` checks currently-SURVIVED verdicts against each other via their SQL hashes and correctly distinguishes "same evidence, re-narrated" (identical hashes) from "genuinely independent contradiction" (different hashes, both survived) — both branches verified. Found and fixed a real bug along the way: `submit_feedback()` was writing the counter-hypothesis's verdict only to the `feedback` table, never to the `ledger` table, making it invisible to this check.
- **Capacity-constrained actions** (objective 6): `check_capacity_constraint()` reads the CRM headcount data and the semantic contract's new `operational_constraints.max_accounts_per_rep` field to check whether a recommended reassignment actually fits within the surviving team's capacity — it doesn't (43 accounts needed vs. 16 headroom), so the action text is honestly qualified with a phased-plan caveat instead of claiming a same-week full fix.
- **A second, differently-kinded reconciliation check** (objective 2): `test_rep_attribution_within_revenue_bounds()` in `data/reconciliation.py` is a logical-consistency check (rep-attributed revenue can never exceed total region revenue), not another cross-source comparison — verified CONSTRAINT_HOLDS, max ratio 13.0%, which happily also matches the synthetic generator's own `REP_CHANNEL_SHARE_OF_REGION=0.13` constant as an internal sanity check.
- **Calibration mechanism** (objective 7's second half): `engine/calibration.py` (new file) implements the real Brier score / reliability diagram / isotonic recalibration machinery, proven against a clearly-labeled **simulated backtest** (40 outcomes, deliberately imperfect confidence-accuracy relationship) — not faked as real production history, since the live ledger has 0 real scored outcomes and needs ≥30 before real calibration would be honest. The module docstring explains this reasoning explicitly, tying back to the project's own stated honesty ethos.
- **Integrated scalability path**: `bench_integrated_sql_to_verdict()` added to `tests/scalability_test.py` — unlike every other suite (which times an isolated kernel with synthetic stand-in data), this one builds a real SQLite database and runs the actual `engine.l4_compiler.compile_unit_query` → `sqlite3` execution → `engine.l5_adjudicate.did_estimate` path against it. Confirms ~linear scaling (k~0.49) at up to 100 fulfillment centers, 0.059s.

All new UI panels (Priority Queue, Contradictory Evidence Check, Calibration Mechanism, capacity-constraint qualification in the action card, rep-attribution bounds in the freshness panel) were verified end-to-end via the Claude Browser tool against a live `uvicorn` server: all `/api/*` endpoints return 200, zero console errors, and every panel's rendered text was read back and confirmed to match the underlying computed data (priority ranks, SQL hash pairs, capacity shortfall numbers, Brier score, reliability buckets, isotonic curve). README.md §0 and §6 have been updated to document all of this against the actual objectives table — every one of the 8 objectives is now backed by real running code, not architectural coverage alone.

**This is the last item on the plan.** There is nothing left flagged as "shallow" or "breadth only" — see README.md §10's updated next-step list, which now says exactly that.

---

## STATUS UPDATE (2026-08-28, updated a fourth time same day)

**Scalability was tested, not just flagged.** `tests/scalability_test.py` times each layer's actual bottleneck operation (L1 BOCPD vs. weeks of history, L2 Monte-Carlo Shapley vs. category count, L3 embeddings+clustering vs. ticket count, L5 DiD regression vs. panel size) at up to 65-225x the demo dataset's scale. Results: three of four layers scale roughly linearly and stay sub-second even at 100-150x scale; L2's Shapley sampling is the one genuinely super-linear layer (~O(N^1.6)) but is still under a second at 500 categories, far more than any real business needs. Full table in README §6 objective 8. This closes out the one item that was left honestly flagged as untested — everything on the original plan is now either done or explicitly and correctly scoped out (ledger calibration, which needs ~30 real scored outcomes to be meaningful and can't be faked).

**Everything in this plan is now built, including both Tier 3 stretch features and a third stakeholder persona (Engineer, alongside Leader/regional_vp and Manager/ops_manager_west).** `engine/methods_registry.py` is new: a single structural table every pipeline stage declares its method category against (deterministic logic / SQL / business rules / statistics / traditional ML / causal inference / LLM), with a programmatic check (`assert_llm_not_quantitative_source`) that fails loudly if any LLM-driven stage is ever marked as producing a trusted quantitative output — this is the direct, checkable answer to the brief's "the LLM should not be treated as the source of quantitative truth" requirement, not just an assertion in a doc. `build_counterfactual_projection` (l6) and `generate_adversarial_challenge` (l4_llm_generation) are both wired into the UI and confirmed working — the adversarial challenge has reliably rediscovered decoy-shaped alternative explanations (competitor pressure, operating costs) and been correctly KILLED, meaning the SURVIVED verdict holds up against the model's own best counter-case. README §6 was rewritten from a pre-build gap list into a final coverage table against the actual 8 objectives, the LLM-non-authority requirement, and the minimum prototype expectations — the only honestly-flagged remaining gap is scalability testing (never run past the ~9K-row synthetic dataset).

**Tier 1 and Tier 2 (§5) are built and validated; §7 steps 7 (live LLM wiring) and 11 (the UI) are also done.** `api/main.py` (FastAPI) + `ui/` (static dashboard, vanilla JS) render exactly what the pipeline already produces — KPI chart with changepoint marker, hypothesis cards with click-to-expand evidence, a persona toggle whose entitlement note comes from a real `engine.l4_compiler.check_entitlement` call (not a client-side guess), the freshness/lineage table, a telemetry strip, and a working feedback-loop demo button. Verified via DOM/JS inspection in the Claude Browser tool (screenshots aren't available in this headless environment) — data flow, persona switching, the entitlement denial, and the feedback POST round-trip all confirmed working. One real bug found and fixed: the KPI chart canvas rasterized at its initial (pre-layout-settled) width and never redrew on resize, leaving it stretched/blurry — fixed with a resize listener plus a one-time delayed redraw after load. The falsification core (L1–L6, §7 steps 1–8) runs end to end via `uv run python run_pipeline.py`, and reproduces the canonical worked example exactly: West revenue -8.9% in week 32, all five hypotheses land on their intended verdict (shipping_delay & competitor_launch KILLED, accessories_pricing INCONCLUSIVE, rep_attrition SURVIVED at 71% loss share, billing_complaints KILLED via precedence). Full detail, per-layer file map, and the bugs found/fixed along the way are in the project's `README.md` §0 ("Implementation status").

**Live LLM wiring (`uv run python run_pipeline.py --with-llm`):** `engine/l4_llm_generation.py` runs Qwen2.5-3B-Instruct locally on GPU (user has an RTX 5060 Laptop GPU, 8GB VRAM — no hosted API key needed or used), constrained via `outlines` to the exact same `Predicate` Pydantic schema the templated fixtures use. A second semantic-validation gate checks the model's chosen dimension values actually exist in the data. Validation isn't just "produced valid JSON": given the accessories/pricing ticket cluster, the model independently proposes the *exact same treatment/control structure* as the hand-written `h_accessories_pricing` fixture and reaches the identical INCONCLUSIVE verdict through L5. A smaller model (1.5B) was tried first, passed validation reliably, but showed real quality limits (weak dimension choice, archetype mislabeling) — documented, not hidden, in the module docstring. Getting this working also required fixing a Windows-specific `uv`/torch CUDA-pinning issue and a Triton-missing 330-second warmup (see README §0's bug list).

What §7's step 9 (sparse-history) also landed as part of L1 (Outdoor category, correctly shows low-confidence output). What's genuinely NOT done yet: **step 10 (Tier 3 stretch features)** — now the only item left on the original build sequencing, and optional per the plan's own framing.

The rest of this document (§1–§9) is kept as-is: it's still an accurate record of the reasoning behind what got built, and the guardrails in §9 are still binding.

---

## Context

Round 1 pitched REFUTE — a falsification-first KPI root-cause engine — against Accenture's "BusinessIntelligence.ai" problem statement, and it advanced. Round 2's brief expands the ask well beyond what Round 1 designed: it adds explicit requirements around multi-source reconciliation, persona-specific narratives, role-based security, feedback learning, and runtime telemetry that Round 1's architecture (L1–L6) does not yet cover. The repo currently contains only a README with the merged context — no code exists yet (confirmed: only `README.md` and `.gitattributes` in the working directory).

The user's directive: don't scope down for time — build the best possible prototype, cover every Round 2 requirement for real (not lightly), and go deeper than compliance on the objectives that are REFUTE's natural strength, so the judges see both rigor and genuine technical novelty. This plan is the merged, updated build spec that supersedes README §6–§7 as the actionable plan (the README stays as reference context; this document is what gets executed next).

---

## 1. What we already planned (Round 1 — unchanged, stays as the core)

The six-layer falsification engine is the technical spine and does not change:

- **L1 Signal** — BOCPD changepoint detection; gates whether the expensive path (LLM, hypothesis generation) even runs. No LLM call on noise.
- **L2 Localise** — exact decomposition (volume/price/mix) or Shapley attribution; tagged `"kind": "localisation"` so it can never be presented as causal.
- **L3 Hypothesise** — topic-clustered ticket/note embeddings, each with its own independent changepoint check before becoming a candidate; LLM proposes a mechanism in plain language, nothing more.
- **L4 Falsification Compiler** — LLM emits a typed, schema-validated causal predicate (never SQL); a deterministic compiler turns it into parameterised, whitelisted SQL. `refutes_if` is mandatory and hard-validated. Four archetypes: placebo, dose-response, precedence, specificity.
- **L5 Adjudicate** — DiD with clustered SEs, mandatory parallel-trends pre-check, the power gate (KILLED only if the test had power to detect the effect), Benjamini-Hochberg correction across the whole hypothesis family. Three-valued verdict: KILLED / SURVIVED / INCONCLUSIVE.
- **L6 Narrate + Ledger** — plain-English brief with confidence; every verdict logged immutably with predicted outcome, later scored against actuals (Brier score, isotonic recalibration).

Synthetic data: 1 true cause + 3 decoys (coincidental → killed by placebo; reverse-caused → killed by precedence; underpowered → must return INCONCLUSIVE, not KILLED — the single most important test case).

**This does not change.** Everything below is additive, wrapped around this core.

---

## 2. What Round 2 explicitly requires

**8 objectives** (the de facto grading checklist, since the brief frames them as "the engine should..."):

| # | Objective | REFUTE core already covers it? |
|---|---|---|
| 1 | Detect & prioritise material KPI movements | ✅ L1, mostly — needs explicit business-impact weighting added |
| 2 | Reconcile data/context across heterogeneous sources | ❌ Round 1 assumes one clean table |
| 3 | Identify & rank explanatory drivers | ✅ L2–L5 — this is REFUTE's differentiator |
| 4 | Persona-specific narratives with traceable evidence | ❌ Not designed |
| 5 | Communicate uncertainty, abstain when insufficient | ✅ Strongest-covered — INCONCLUSIVE + power gate |
| 6 | Recommend actions grounded in levers/constraints/decision rights | ⚠️ Partial — needs the structured template |
| 7 | Learn from analyst/business-user feedback | ❌ Not designed |
| 8 | Operate within security, cost, latency, scalability constraints | ⚠️ Partial — cost/latency implied, security not designed |

**Minimum prototype expectations** (explicit deliverable checklist): 3–5 connected KPIs across 2–3 sources with different grains/cadences; a lightweight KPI/semantic contract; ≥2 personas with different narratives; one multi-factor movement; one low-confidence/abstain scenario; one sparse-history/new-KPI scenario; one role-based security/entitlement scenario; evidence of freshness/method/contribution/confidence/lineage; a clear LLM-vs-non-LLM breakdown; runtime telemetry (latency, model calls, tokens, cost).

**Real-world complexities named in the brief** worth explicitly nodding to in the build/demo: multiple interacting drivers, mismatched refresh cadences/grains, inconsistent KPI definitions/hierarchies, sparse history, materiality (statistical + business), contradictory evidence, role-based personalization, row/column/domain security, model/data drift, LLM economics.

---

## 3. What Round 2 suggests (optional menu, not mandatory)

Anomaly detection / contribution analysis / forecasting / causal inference / business rules; governed semantics, metadata, lineage, ontology/knowledge graphs; LLM-assisted intent understanding, orchestration, narrative synthesis, retrieval; proactive alerts, conversational analysis, augmented dashboards; confidence scoring, evidence citation, alternative hypotheses, abstention; the action template `driver → controllable lever → action → expected impact → owner → confidence → monitoring plan`; feedback/correction/learning loops; platform-native vs. custom vs. hybrid (Databricks/Snowflake/Fabric/Tableau/Qlik/Looker or fully custom).

**Decision: build fully custom** (Python/FastAPI, no platform lock-in). Reasons: (a) REFUTE's differentiator is the falsification compiler itself, which is platform-agnostic logic — wiring it into Databricks/Fabric would spend hackathon time on plumbing, not on the novel part; (b) a custom build lets the demo clearly show "native vs. configured vs. custom" distinction the brief asks teams to be explicit about — here, everything is custom, stated plainly, which is itself an honest answer to that ask rather than a dodge.

---

## 4. Judging-criteria read (inferred from the brief's own language)

The brief repeatedly signals what it's grading on: *"focus on innovation, creativity, and technical novelty"* (explicit), the 8 objectives (explicit checklist), the 10 real-world complexities (depth of handling), and twice-repeated emphasis on honest uncertainty communication. Reading between the lines, the rubric is likely weighted across:

1. **Technical novelty / innovation** — not just "did you use an LLM," but *how* — this is where REFUTE already wins structurally (falsify, don't rank).
2. **Completeness against the 8 objectives + minimum expectations** — a literal checklist judges can tick off.
3. **Rigor in handling the named real-world complexities** — sparse data, contradictory evidence, drift, security.
4. **Business actionability** — levers, owners, monitoring plans, decision rights, not just insight.
5. **Trustworthiness / honesty of the system** — abstention, calibration, stated limitations (called out twice in the brief — this is not a minor point).
6. **Feasibility under real constraints** — cost, latency, security shown working, not asserted.
7. **Demo/communication clarity** — the video is the actual artifact judges watch.

REFUTE's thesis already dominates criteria 1, 3 (partially), and 5. The plan below is built to also dominate 2, 4, and 6 — so there's no criterion where REFUTE is visibly behind a "checklist-compliant but derivative" competitor.

---

## 5. The updated plan — one idea, extended to every surface

The organizing principle for **all new work**: REFUTE's core idea is "don't trust an LLM's claim — compile it into something deterministic, testable, and falsifiable." Round 2's gaps (reconciliation, personas, security, feedback, telemetry) are not bolted on as unrelated features — each is the **same falsification/compilation pattern applied to a new surface**. This is the actual "novel and new" answer to "stand out from the crowd": not seven unrelated add-ons, but one architectural idea proven to generalize across the whole brief. State this explicitly in the demo narration — it's the strongest single line for judges.

### Tier 1 — Core objectives (depth-first, built to the same rigor as Round 1's L1–L6)

These map to Round 2 objectives **1, 2, 3, 5, 6** — the analytical heart, built for real and made visually obvious in the demo, not just architecturally present.

**5.1 Multi-source reconciliation layer** (new — satisfies objective 2 + the "3–5 KPIs / 2–3 sources / different grains" minimum expectation)
- Three synthetic sources at different grains/cadences: (a) daily POS/transaction feed (revenue, units), (b) weekly marketing spend feed, (c) monthly headcount/CRM feed (rep roster, attrition dates — needed for the worked example anyway).
- A reconciliation layer that: aggregates to a common analysis grain, timestamps each source's freshness (`as_of`), and flags grain/definition mismatches structurally — e.g. if "revenue" is computed differently in two sources, that mismatch is itself a **falsifiable claim** ("these two sources agree on revenue for this period") tested and reported, not silently assumed. This is new work but reuses the L4 compiler pattern conceptually (a claim → a test → a verdict), so it doesn't introduce a second unrelated codebase.

**5.2 Lightweight KPI semantic contract** (new — the minimum-expectations item, and the substrate objective 2 needs)
- A small YAML/JSON contract per KPI: definition, formula, grain, source(s), refresh cadence, owner, thresholds for materiality, lineage (which source fields feed it), and access tags (for §5.5 below). This is metadata, not a new subsystem — it's what L1–L4 already implicitly assume, made explicit and visible in the UI as an "evidence" artifact (directly satisfies "evidence showing source freshness... and lineage").
- Define 4 KPIs across the 3 sources: Revenue, Units Sold, Marketing-Attributed Revenue Share, Rep-Attributed Revenue — connected (Revenue decomposes partly via the other three), satisfying "3–5 connected KPIs."

**5.3 Business-impact weighting on top of L1's statistical gate** (extends L1 — satisfies objective 1's "material" half)
- L1 already gates on statistical materiality (posterior probability of a changepoint). Add an explicit business-impact score (e.g. `|Δ| × revenue_at_risk × persona_relevance_weight`) computed deterministically from the semantic contract's thresholds, and only queue for full L2–L6 processing when **both** statistical and business materiality clear their bars. This is the literal answer to the brief's "materiality based on both statistical significance and business impact" complexity.

**5.4 Structured action recommendations** (extends L6 — satisfies objective 6 properly, not just "what to do" prose)
- Every SURVIVED verdict's recommendation is rendered in the exact Round 2 template: `driver → controllable lever → action → expected impact → owner → confidence → monitoring plan`. `owner` comes from the semantic contract's ownership field; `expected impact` comes from the DiD effect estimate already computed in L5 (no new modeling — just structured presentation of numbers REFUTE already has).

### Tier 2 — Remaining objectives (built for real, same architectural family, not shallow)

These satisfy objectives **4, 7, 8** and the sparse-history/security minimum expectations.

**5.5 Persona-specific narration as structured views over one ledger** (new — satisfies objective 4)
- Not two separate LLM prompts/pipelines. One ledger, two renderers driven by a persona config (role → depth, allowed KPIs/dimensions, action framing, channel).
  - **Persona A — Category/Ops Manager:** full evidence chain (which hypotheses were tested, which archetype killed each decoy, effect sizes, confidence), tactical action framing ("reassign these 4 accounts this week"), delivered as a detailed brief.
  - **Persona B — Regional VP / Exec:** headline + single recommended action + confidence band only, no statistical detail, delivered as a one-line alert-style summary.
- Both personas read from the *same* falsification ledger — this is the point to make explicit in the demo: personalization is a rendering/entitlement problem, not a re-analysis problem. Keeps LLM cost down too (ties into §5.7).

**5.6 Role-based entitlement filter, compiled alongside the SQL predicate** (new — satisfies objective 8's security half + the required security scenario)
- The semantic contract (§5.2) carries row/column access tags (e.g. "West region data restricted to West-team roles," "rep-level attrition data restricted to management roles"). The same L4 compiler that turns a causal predicate into whitelisted SQL also applies the requesting role's entitlement filter at compile time — deny-by-default, enforced in the generated query, not in a downstream display filter (so it can't leak via evidence dumps).
- Demo scenario: the Ops-Manager persona querying rep-attrition detail is denied at the compiler stage when accessed with a role that lacks entitlement, with a stated reason — visibly proving row/column-level security works, not just asserting it.

**5.7 Feedback loop as a first-class falsification event** (new — satisfies objective 7)
- Instead of a generic thumbs-up/down, an analyst rejecting a SURVIVED verdict ("this isn't actually the cause") is captured as a structured counter-claim, converted into a new hypothesis predicate with its own `refutes_if`, and run through L4/L5 like any other hypothesis. If the counter-hypothesis survives, the original verdict is downgraded and the ledger records the correction with provenance (who, when, why). This reuses existing L4/L5 machinery instead of building a separate correction-workflow subsystem — cheaper to build well, and more defensible in front of judges ("feedback isn't a separate feature bolted on, it's just another hypothesis entering the same falsification pipeline").

**5.8 Telemetry as its own ledger entry type** (new — satisfies objective 8's cost/latency half + the telemetry minimum expectation)
- Every LLM call (L3 mechanism proposal, L4 predicate generation) logs model used, prompt/completion tokens, latency, and estimated cost into the *same* ledger store as statistical verdicts. Surfaced in the UI as a running "cost per insight" and a visible LLM-vs-non-LLM breakdown per pipeline run (e.g. "2 LLM calls, 340 tokens, $0.006, 1.1s of a 4.8s total runtime — the rest is deterministic statistics"). This single screen does double duty: it's the literal telemetry requirement, and it's the clearest possible visual proof of "the LLM is not the source of quantitative truth," which is REFUTE's own stated design principle.

**5.9 Sparse-history KPI via honest confidence degradation** (new — satisfies the sparse-history minimum expectation)
- A newly launched product/category KPI with <8 weeks of history. BOCPD's Bayesian machinery already handles this correctly if used honestly: wide prior, explicit low-confidence output, and empirical-Bayes shrinkage toward a category-level baseline rather than either refusing to answer or overconfidently extrapolating from 3 data points. The system states plainly that it's borrowing from a category prior and names the data volume needed before it can speak with full confidence. No new algorithm — an honest application of the one already built for L1.

### Tier 3 — Stretch "wow" features (build if Tier 1–2 land cleanly; flagged as reach, not required for the checklist)

**5.10 Visible counterfactual projection** — L6's ledger already predicts direction/magnitude if a recommendation is followed. Elevate this from a logged field to a visible forward-projected confidence band on the dashboard next to the historical series — makes "expected impact" tangible and sets up the Brier-score storyline (predict now, get scored later) as a literal on-screen feature rather than only a backend concept.

**5.11 Adversarial counter-hypothesis generation** — before compiling a hypothesis for testing, prompt the LLM a second time in an adversarial role to propose the strongest plausible *alternative* explanation for the same evidence, and test both. This actively hardens the hypothesis set against confirmation bias rather than only reacting to whatever hypotheses happened to be proposed first. Flagged as reach because L4 is already the highest-risk component per Round 1's own risk assessment — only take this on once the base compiler is solid, so it doesn't jeopardize the core.

---

## 6. Updated synthetic data plan

Extends Round 1 §4/§5 (unchanged: 1 true cause + 3 decoys, canonical worked example) with:

- **3 sources, different grains:** daily POS transactions, weekly marketing spend, monthly CRM/headcount — enough to demonstrate reconciliation (§5.1) and freshness/lineage evidence.
- **4 connected KPIs** per the semantic contract (§5.2): Revenue, Units Sold, Marketing-Attributed Revenue Share, Rep-Attributed Revenue.
- **A definition-drift scenario:** two sources computing "revenue" with a subtly different rule (e.g. gross vs. net of returns) — a planted reconciliation failure for §5.1 to catch and report.
- **A sparse-history KPI:** a product/category launched ~6 weeks before the analysis window, for §5.9.
- **Two roles with different entitlements** (e.g. West-team Ops Manager vs. Regional VP with cross-region but not rep-level access) for §5.6/§5.5.
- Everything else (true cause = rep attrition, 3 decoys, worked example) stays exactly as designed in Round 1 — don't touch the scenario the deck/video/script were already built around.

---

## 7. Build sequencing (merged, depth-first on Tier 1)

1. **Semantic contract + reconciliation layer + synthetic multi-source data** (§5.1, §5.2, §6) — this is now the foundation everything else reads from; build before L1, since L1–L6 need the reconciled, contract-defined KPI series as input.
2. **L1 Signal + business-impact weighting (§5.3)** — as Round 1 sequencing, extended.
3. **L2 Localise** — unchanged from Round 1.
4. **L4 templated/deterministic path first** (hand-written predicates for the 3 worked-example hypotheses) → compiler → SQL → execution → DiD → verdict, end-to-end, before touching LLM generation. Add the entitlement filter (§5.6) into the compiler at this stage, since it's the same component.
5. **L5 Adjudicate** (power gate, BH correction) — unchanged from Round 1, this is where the underpowered-decoy → INCONCLUSIVE proof lives; still the single case that must not fail.
6. **L3 Hypothesise** (topic clustering, LLM mechanism proposals) — as Round 1 sequencing.
7. **Live LLM generation for L4 predicates**, with the `refutes_if` hard check — last of the core engine, as Round 1 flagged as highest risk. Fallback to templated path remains acceptable if this doesn't converge.
8. **L6 Narrate + Ledger**, extended with:
   - structured action template (§5.4)
   - persona rendering (§5.5)
   - telemetry entries (§5.8)
   - feedback-as-falsification loop (§5.7)
9. **Sparse-history scenario (§5.9)** — slots in once L1 exists (step 2), can be validated any time after.
10. **Tier 3 stretch features (§5.10, §5.11)** — only after 1–9 are demo-solid.
11. **Minimal UI** (FastAPI + React or server-rendered): dashboard showing the KPI series with changepoint markers → hypothesis cards (struck through / surviving) → narrated brief in both personas → evidence panel (freshness, method, contribution, confidence, lineage) → telemetry/cost strip → a live entitlement-denial example → a feedback-rejection example. This single screen flow needs to visually hit every minimum-expectation bullet in one continuous demo pass.

---

## 8. Demo-to-requirement mapping (so nothing gets built but not shown)

| Minimum prototype expectation | Where it's demoed |
|---|---|
| 3–5 KPIs / 2–3 sources / different grains | Dashboard header + evidence panel (source freshness per KPI) |
| Semantic/KPI contract | Evidence panel "definition & lineage" expandable view |
| ≥2 personas, different narratives | Toggle between Ops-Manager brief and VP brief on the same verdict |
| Multi-factor KPI movement | The canonical worked example (rep attrition, ruling out shipping + competitor) |
| Low-confidence/abstain scenario | The underpowered decoy → INCONCLUSIVE, with named resolving data |
| Sparse-history KPI | The new-product KPI panel, explicit "borrowing from category prior" note |
| Role-based security/entitlement scenario | Live denial when Ops-Manager role queries rep-level data outside entitlement |
| Freshness / method / contribution / confidence / lineage evidence | Evidence panel, per hypothesis card |
| LLM vs. non-LLM breakdown | Telemetry strip, per-run |
| Runtime telemetry (latency, calls, tokens, cost) | Telemetry strip, per-run and cumulative |

---

## 9. What doesn't change (guardrails from Round 1, still binding)

- Never claim to have invented RCA, causal inference, or refutation testing; always position against DoWhy by name.
- `refutes_if` remains a hard-validated mandatory field — no exceptions for the new claim types in §5.1/§5.7 either (reconciliation claims and feedback counter-hypotheses must state their own refutation condition too, for consistency with the core thesis).
- SURVIVED still means "survived the tests we could construct," not "proven true" — carry this caveat into persona narration for both audiences, just at different depth.
- The underpowered-decoy → INCONCLUSIVE case remains the single most important thing to get right if time runs short despite the "build it all" directive — it's still the one judges are most likely to notice is *missing* if corners get cut late.
- Project name, core mechanic, and honest-positioning language stay as-is.

---

## 10. Next step

**Original text superseded — see the STATUS UPDATE at the top of this file.** §7 steps 1–8 (semantic contract through L6 with structured actions, personas, telemetry, and the feedback loop) are done and validated. Structural note: `feedback/` and `telemetry/` as separate top-level packages (as sketched in the file-structure line originally here) were consolidated into `engine/l6_narrate_ledger.py` during the actual build — both were thin enough that a separate package added indirection without payoff. `api/` and `ui/` are still empty.

Remaining, in priority order:

1. ~~§7 step 7 — live LLM wiring~~ — **done**. `engine/l4_llm_generation.py`, Qwen2.5-3B-Instruct on the user's local GPU via `outlines`. The templated fixtures remain in place as the audited fallback/reference path, per the original design — this didn't replace them, it added a parallel path that's cross-validated against them.
2. ~~§7 step 11 — minimal UI~~ — **done**. `api/main.py` (FastAPI, reads the pipeline's existing JSON/SQLite output, no new analysis logic) + `ui/` (static vanilla-JS dashboard). Verified via DOM/JS inspection: data flow, persona toggle with a real entitlement check, hypothesis card evidence expansion, telemetry strip, and the feedback-loop POST round-trip all confirmed working.
3. **§7 step 10 — Tier 3 stretch features** (§5.10 visible counterfactual projection, §5.11 adversarial counter-hypothesis generation) — now the only remaining item, and optional/reach per the plan's own original framing.
4. Ledger calibration scoring (Brier score, reliability diagram, isotonic recalibration) — deferred until real scored outcomes accrue (needs ~30 per §9's own honesty constraint), not before.
