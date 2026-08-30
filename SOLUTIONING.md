# REFUTE vs. the Brief's "Solutioning Areas" — Coverage & Differentiation Audit

The Round 2 brief lists 8 "Solutioning Areas You Could Explore" as a hybrid menu, not a checklist —
teams are free to combine whichever fit. This document audits REFUTE against that menu the same way
[GAPS.md](GAPS.md) audited the named real-world complexities: by reading the actual source, not by
restating intent. For each area: what's genuinely built, what's deliberately not, and whether adding
the missing part would help or hurt. Written 2026-08-29.

**Update (2026-08-29, same day):** the two items flagged below as "cheap, low-risk, worth doing" —
a real knowledge-graph layer and proactive/scheduled alerting — are now built. See the updated
entries for area 2 and area 4, and the updated recommendation table, for what's real.

---

## 1. Anomaly detection, contribution analysis, forecasting, causal inference, business-rule reasoning

| Sub-area | Status | Where |
|---|---|---|
| Anomaly detection | ✅ | Custom BOCPD, [`engine/l1_signal.py`](engine/l1_signal.py) |
| Contribution analysis | ✅ | Exact price/volume/mix decomposition + Monte-Carlo Shapley, [`engine/l2_localise.py`](engine/l2_localise.py) |
| Causal inference | ✅ | DiD with clustered SEs, parallel-trends check, power gate, [`engine/l5_adjudicate.py`](engine/l5_adjudicate.py) |
| Business-rule reasoning | ✅ | Materiality gate, category-maturity rule, capacity-constraint check |
| Forecasting | ⚠️ | `build_counterfactual_projection()` is a **linear interpolation toward a stated baseline under an assumption**, not a fitted forecasting model (no ARIMA/Prophet/exponential-smoothing anywhere in the codebase — verified by grep, zero hits). Real for what it claims to be, but "forecasting" in the brief's sense (predicting a future value from a model) isn't built. |

**Verdict: mostly covered, forecasting is the honest gap.** Four of five sub-areas are real and are
in fact REFUTE's core. Forecasting was never a stated goal — the counterfactual projection exists to
support scoring a recommendation's outcome later, not to predict the future independent of an action.

## 2. Governed KPI semantics, metadata, lineage, business rules, ontology / knowledge graphs

| Sub-area | Status | Where |
|---|---|---|
| Governed KPI semantics | ✅ | [`semantic/kpi_contract.yaml`](semantic/kpi_contract.yaml) — definitions, formulas, drivers, owners |
| Metadata | ✅ | Grain, cadence, `system_of_record`, `access_tags`, `domain` per source/KPI |
| Lineage | ✅ | `lineage` field per KPI, rendered in the UI's evidence panel |
| Business rules | ✅ | Maturity rule, capacity ceiling, materiality thresholds — all contract-declared, code-enforced |
| Ontology / knowledge graph | ✅ (was ❌) | [`engine/knowledge_graph.py`](engine/knowledge_graph.py) (new module) — a real, dependency-free directed graph (custom adjacency-list, not networkx; ~35 nodes doesn't need a graph library) built from the SAME contract data (KPI↔source, KPI↔domain, role↔domain, dimension↔table) plus this run's actual verdicts (hypothesis↔dimension, hypothesis↔verdict) — not a separately maintained copy of the truth. |

**Verdict: strong on governed semantics, and now a real graph too.** Three genuinely new queries
the flat YAML couldn't answer, verified against the actual data: `related()` — "what touches
`rep_id`?" correctly returns `crm_headcount` (backing table) and `h_rep_attrition` (the only
hypothesis that tests it); `blast_radius()` — "what depends on `pos_transactions`?" correctly
traces through 5 KPIs, 2 dimensions, and transitively all 7 hypotheses plus the domains and the
reconciled `finance_gl_extract` source; `shared_mechanism()` — "what looks like the surviving
hypothesis, structurally?" correctly finds `h_shipping_delay` (same `placebo` archetype) and
correctly excludes the `dose_response`/`precedence`/`specificity` hypotheses. Exposed at
`/api/knowledge-graph` (+ `/related`, `/blast-radius`, `/shared-mechanism`) and rendered as a real
SVG diagram plus an interactive explorer in the UI's new "Knowledge Graph" panel.

## 3. LLM-assisted intent understanding, orchestration, narrative synthesis, contextual retrieval

| Sub-area | Status | Where |
|---|---|---|
| Intent understanding | ❌ | No natural-language query interface exists — nothing parses a user's question into an action. |
| Orchestration (LLM decides what to run) | ❌ **deliberately** | The pipeline order (L1→L2→L3→L4→L5→L6) is fixed, deterministic Python — never LLM-decided. This is not an oversight; it's the point (see §"What we have that isn't on this menu" below). |
| Narrative synthesis | ⚠️ | `render_ops_manager_brief`/`render_vp_brief`/`render_engineer_brief` in [`engine/l6_narrate_ledger.py`](engine/l6_narrate_ledger.py) are **deterministic Python f-string templates over the ledger's fields — not LLM-written prose.** Verified by reading the functions directly: no LLM call anywhere in L6. The only LLM-authored *text* anywhere in the system is a one-sentence `mechanism` field per predicate (L4) and the adversarial challenge's counter-mechanism — both schema-constrained, both re-tested before being trusted. |
| Contextual retrieval | ⚠️ | L3's ticket embedding+clustering (`engine/l3_hypothesise.py`) is retrieval-*adjacent* (sentence-transformer embeddings, semantic grouping) but it's an offline structural filter, not retrieval-augmented generation in the RAG sense — there's no vector-DB query happening in response to a live question. |

**Verdict: the LLM's footprint is intentionally narrow.** REFUTE explicitly avoids "orchestration"
and "narrative synthesis" in the way most teams will build them (an LLM composing the final
explanation) — the brief allows this menu item, but taking it would directly contradict REFUTE's
own thesis (§4 of README.md): the LLM proposes a testable claim, it never gets to write the verdict
prose from its own judgment. This is a considered non-use, not a missed opportunity.

## 4. Proactive alerts, conversational analysis, augmented dashboards, decision workspaces

| Sub-area | Status | Where |
|---|---|---|
| Proactive alerts | ✅ (was ⚠️) | [`engine/proactive_monitor.py`](engine/proactive_monitor.py) (new module) — `detect_new_alerts()` compares this run's L1-gated (KPI, region) movements against the immediately prior run's, routes each genuinely NEW one to every role whose `domain_scope` covers that KPI's domain (reusing the exact same domain data `check_domain_entitlement` enforces, not a separate routing table), and logs a real, urgency-gated, honestly-SIMULATED alert per role. Verified: revenue/units_sold (sales domain) correctly route to all 3 roles with that domain; `rep_attributed_revenue` (hr domain) correctly excludes `regional_vp`, matching the domain-security rule exactly. A repeat run correctly shows 0 new alerts (this demo's data is deterministic — that's the honest result, not a missed detection), and `run_alert_demo()` proves the new-vs-known logic itself is correct against a labeled synthetic two-run history. No always-on scheduler process is bundled (would be undemonstrable and dishonest in this environment) — the module is meant to be invoked by a real cron/Task Scheduler entry against live data; the docstring says so plainly. |
| Conversational analysis | ❌ | No chat interface anywhere — confirmed by grep, zero hits for chat/conversation UI code. |
| Augmented dashboards | ✅ | The web UI ([ui/](ui/)) — 19 panels: KPI chart with changepoint marker, priority queue, evidence tables, methods breakdown, calibration, drift, entitlement/delivery logs, a knowledge-graph diagram, proactive alerts. |
| Decision workspaces | ✅ mostly | Hypothesis cards with expandable raw SQL, persona-filtered briefs, a feedback-submission workflow, capacity-constrained actions, an interactive graph explorer — closer to a decision workspace than a passive dashboard, though it's read-heavy (there's no broader "workspace" for an analyst to annotate, save views, or compare runs side by side). |

**Verdict: the dashboard and proactive alerting are both real now; only chat is not built** (and
deliberately so — see the recommendation table below).

## 5. Confidence scoring, evidence citation, alternative hypotheses, abstention mechanisms

**✅ Fully covered — this is REFUTE's strongest area, verified extensively already in GAPS.md.**
BOCPD posteriors, DiD p-values (raw + BH-adjusted), MDE-vs-plausible-effect power gate, isotonic
calibration; every verdict carries its SQL hash back to the exact query; up to 7 hypotheses tested
per movement including an adversarial self-challenge; INCONCLUSIVE as a first-class, power-gated
verdict, not a fallback. Nothing to add here — if anything, this is the one area where REFUTE
already exceeds what the brief asks for.

## 6. Action recommendations: driver → lever → action → impact → owner → confidence → monitoring

**✅ Exact match, verified in code.** `generate_action_recommendation()` in
[`engine/action_recommendation.py`](engine/action_recommendation.py) populates precisely this
template — investigation-aware, not hardcoded to West: it gathers whichever investigation's SURVIVED
verdict(s), L1 context, and real operational-capacity data exist for the surviving hypothesis's
dimension (`check_capacity_constraint()` for `rep_id` against `crm_headcount.csv`,
`check_fulfillment_capacity_constraint()` for `fulfillment_center` against the new
`data/synthetic/fulfillment_center_ops.csv`), then has an LLM synthesize the writeup (or falls back to
a deterministic composition of the same real evidence with no LLM backend selected). When no
operational dataset exists yet for a dimension, the response says so explicitly
(`inferred_without_operational_data`) and caps confidence, rather than fabricating a feasibility
number. (The older `build_action_recommendation()` in
[`engine/l6_narrate_ledger.py`](engine/l6_narrate_ledger.py) still exists, used only by the CLI/ledger
console narration path — the API and dashboard now go through the generic module above.) Nothing to
add.

## 7. Human feedback, expert validation, correction workflows, learning loops

| Sub-area | Status | Where |
|---|---|---|
| Human feedback | ✅ | `submit_feedback()` — a correction becomes a new falsifiable predicate, run through the identical L4/L5 pipeline |
| Correction workflows | ✅ | Same mechanism — re-adjudication, not a rubber-stamp approval queue |
| Expert validation | ⚠️ | There's no explicit "an expert reviewed and signed off on this verdict" state tracked anywhere — feedback triggers automatic re-testing, but nothing records a human's approval/rejection of a verdict as a distinct fact. |
| Learning loops | ⚠️ | Calibration mechanism (`engine/calibration.py`) is real machinery, honestly gated on needing ≥30 real scored outcomes (has 0) — proven against a labeled simulated backtest, not faked as live learning. |

**Verdict: the re-test loop is real and arguably the more valuable half; explicit sign-off tracking
is the missing piece**, and it's cheap to add if wanted (a `reviewed_by`/`review_status` column on
the ledger's feedback table).

## 8. Platform-native and custom capabilities (Databricks, Snowflake, Fabric, Tableau, Qlik, Looker, or custom)

**Deliberately fully custom** (Python/FastAPI/SQLite, documented decision in
[PLAN.md](PLAN.md) §3): *"REFUTE's differentiator is the falsification compiler itself, which is
platform-agnostic logic — wiring it into Databricks/Fabric would spend hackathon time on plumbing,
not on the novel part."*

The brief separately asks (beyond just naming a platform or not) that teams *"distinguish between
native, configured, custom-built and externally integrated capabilities."* "Fully custom" is the
right one-line answer to "which platform" but it understates what's actually true at the
capability level — REFUTE calls a real set of externally-built libraries and one externally-trained
model; it did not reimplement statistics or deep learning from scratch. Here's the honest,
capability-by-capability breakdown, checked against `pyproject.toml` and actual imports (not
asserted from memory):

| Category | What's in it |
|---|---|
| **Native** (a platform's built-in feature, no code) | **None.** REFUTE integrates with zero BI/data platforms (no Databricks, Snowflake, Fabric, Tableau, Qlik, or Looker anywhere in the stack) — there is nothing that could be "native" by definition. |
| **Configured** (a platform capability set up via config/GUI, not code) | **None**, same reason. |
| **Custom-built** (bespoke code written for REFUTE) | The falsification-specific logic in every layer: the BOCPD implementation itself (L1, not `ruptures`), the exact price/volume/mix decomposition and Monte-Carlo Shapley sampler (L2), the naive-RAG-trap structural precedence filter (L3), the `Predicate` schema + whitelisted parameterized-SQL builder + entitlement/domain checks (L4), the DiD/parallel-trends/power-gate/BH-correction orchestration (L5 — the *decision logic*, even though it calls out to a library for the underlying regression), the narration templates, ledger schema, feedback loop, delivery-channel routing, and entitlement audit log (L6), the calibration/drift-monitoring orchestration, the knowledge graph, the proactive monitor, and the entire web UI (vanilla JS/HTML/CSS, hand-drawn SVG charts and graph diagram — no charting or graph-drawing library anywhere in `ui/`). |
| **Externally integrated** (third-party library, framework, or model called but not built here) | `numpy`/`pandas` (array/dataframe primitives), `scipy` (Spearman correlation, the normal-distribution power-formula primitive), `scikit-learn` (`IsotonicRegression`, `AgglomerativeClustering`), `statsmodels` (the OLS engine and `TTestIndPower` — REFUTE's own code decides *when* to trust the result, not how to fit it), `pydantic` (schema validation), `sentence-transformers` (the pretrained `all-MiniLM-L6-v2` embedding model), `transformers` + `torch` + `accelerate` (model loading/inference runtime), `outlines` (constrained decoding), **Qwen2.5-3B-Instruct itself** (Alibaba Cloud/Qwen team's model weights, run locally — REFUTE did not train this model), `FastAPI` + `uvicorn` (web framework/server), and SQLite (Python's bundled `sqlite3`, an embedded database, not a hosted platform). |

**One real discrepancy this specific audit found and fixed:** `sqlglot` was declared as a
dependency (and named in the original build plan as the intended tool for "build SQL AST from
validated predicate, not string concatenation") but was **never actually imported anywhere** —
confirmed by grep across the whole codebase, zero hits. The compiler instead whitelists table/column
*identifiers* against a fixed registry (`DIM_REGISTRY` in `engine/l4_compiler.py`) and binds every
*value* as a SQLite parameter — a simpler, equally-safe pattern for this specific whitelist-driven
use case that never needed a full SQL-AST library. Removed the unused dependency from
`pyproject.toml` and re-locked (`uv lock` / `uv sync`) rather than leave a declared-but-dead import
sitting in the manifest — the same "don't claim what the code doesn't do" discipline this project
applies to every generated brief/copy claim, applied here to its own dependency list.

---

## Should we add any of the missing pieces? Yes/no, with reasons

| Candidate | Add it? | Why |
|---|---|---|
| **Knowledge graph / ontology layer** | ✅ **Done** | [`engine/knowledge_graph.py`](engine/knowledge_graph.py) — see area 2 above for the verified queries. Built dependency-free (no `networkx`) since ~35 nodes doesn't need a graph library. |
| **Chat / conversational interface** | **Yes, but only if it stays a front-end to the SAME falsification backend** | Risky if done naively: "chat with your data" is almost certainly the single most common thing other teams will build, and it's exactly the naive pattern REFUTE's own positioning (§2, README.md) argues against ("LLM reads data → LLM writes an explanation" — a statistical guarantee of finding *something*, not evidence of causation). Done correctly — a natural-language question routes into an *existing* predicate/hypothesis lookup or triggers a *real* new falsification test, and the answer is still a verdict + evidence, never free-form LLM prose asserting a cause — it would reinforce the differentiation rather than dilute it. Not worth it if the only outcome is "now the LLM also writes the final paragraph." Still not built — the one open recommendation on this list. |
| **Proactive/scheduled alerting** | ✅ **Done** | [`engine/proactive_monitor.py`](engine/proactive_monitor.py) — see area 4 above. No always-on scheduler process is bundled (documented as a real, deliberate limitation: schedule the module's `main()` via a real cron/Task Scheduler entry against live data), since faking one running inside this demo environment would be undemonstrable and dishonest. |
| **Real forecasting model** | **No, low priority** | Would require a genuine time-series model (ARIMA/Prophet-class) with its own validation story, and risks scope creep into a second discipline (forecasting) that isn't REFUTE's differentiator. The existing stated-assumption counterfactual projection already does the job the brief actually needs (show what recovery would look like if the action works), honestly labeled as a projection, not a forecast. |
| **LLM orchestration (agentic pipeline control)** | **No** | Would directly undermine the one structural guarantee REFUTE stands on (`assert_llm_not_quantitative_source()` in `engine/methods_registry.py`) — the LLM proposes, it never decides what runs or what the verdict means. This menu item exists for teams building an agent; REFUTE's whole pitch is that an agent freely deciding things is the failure mode being fixed. |
| **Expert sign-off tracking** | **Yes, cheap** | A `reviewed_by`/`review_status` field on the feedback/ledger tables is a small, real addition that closes the one genuinely thin sub-point in area 7, and pairs naturally with the entitlement audit log already built. |

---

## What we have that isn't on this menu at all

Worth keeping in mind when judges compare coverage against teams who hit every menu item but do so
by ranking hypotheses (the default failure mode the brief itself names as the naive approach):

- **The falsification-first inversion itself.** Every menu item above assumes a system that explains
  a KPI movement; REFUTE's actual claim is narrower and rarer — it generates the test that would
  *disprove* each explanation and reports only what survives. None of the 8 solutioning areas asks
  for this; it's REFUTE's entire premise.
- **`refutes_if` as a hard schema constraint** (Popper's demarcation criterion enforced in code, not
  a prompt instruction) — a hypothesis that can't state its own refutation condition is rejected
  before it's ever tested.
- **The power gate (MDE vs. plausible effect)** — a non-significant result only counts as evidence
  of absence if the test had power to detect a real effect; otherwise it's INCONCLUSIVE with the
  sample size that would resolve it named explicitly.
- **Benjamini-Hochberg correction across the whole hypothesis family** — the garden-of-forking-paths
  problem (slice data enough ways and something correlates by chance) is corrected for structurally,
  not left to a single test's p-value.
- **The naive-RAG-trap defense in L3** — ticket-topic candidates are excluded structurally if their
  own changepoint follows the KPI's, so "ticket volume spiked because the KPI moved" never gets
  mistaken for a cause.
- **Adversarial self-challenge** — before trusting a SURVIVED verdict, the model is asked to argue
  the best case against it, using a different dimension, and that challenge is run through the
  identical pipeline.
- **A self-scoring accuracy ledger** — verified via web search early in this project (see README.md
  §4) that no shipping RCA product publishes its own historical hit rate. REFUTE's calibration
  mechanism is built for exactly this, honestly gated on needing real outcomes to accrue.
- **Domain-level security as a third, genuinely distinct mechanism** from row/column scope (see
  GAPS.md item 8) — most role-based-access implementations stop at one flat layer.
- **SQL-hash traceability from verdict back to the exact query**, auditable by re-running the hash.
- **A structural (not documentation-only) check that no LLM stage is ever the quantitative source of
  truth** (`assert_llm_not_quantitative_source()`), enforced at every request, not just asserted in
  a README.

These are the points worth leading with in a judged comparison — they're the parts a team that
picked "causal inference + LLM narrative synthesis + a dashboard" off this same menu is structurally
unlikely to have, because ranking-by-evidence and falsification-by-refutation are different designs,
not different amounts of effort on the same design.
