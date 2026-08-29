# REFUTE vs. the Brief's "Solutioning Areas" — Coverage & Differentiation Audit

The Round 2 brief lists 8 "Solutioning Areas You Could Explore" as a hybrid menu, not a checklist —
teams are free to combine whichever fit. This document audits REFUTE against that menu the same way
[GAPS.md](GAPS.md) audited the named real-world complexities: by reading the actual source, not by
restating intent. For each area: what's genuinely built, what's deliberately not, and whether adding
the missing part would help or hurt. Written 2026-08-29.

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
| Ontology / knowledge graph | ❌ | Not built. The contract is a flat YAML with named relationships (a KPI *lists* its drivers, its sources) — that is a lightweight, ungoverned ontology in substance, but there's no actual graph structure (nodes/edges, traversal, a query layer like a knowledge-graph engine would provide). |

**Verdict: strong on governed semantics, no real graph.** Everything a knowledge graph would need
already exists as *data* in the contract (KPIs, sources, drivers, entitlements, domains all
reference each other by name) — what's missing is a *structure* that lets you traverse it
programmatically ("show me every hypothesis that touches the same dimension as a SURVIVED one,"
"which KPIs share a driver"). See the recommendation section below on whether this is worth building.

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
| Proactive alerts | ⚠️ | `determine_delivery_channel()`/`simulate_delivery()` compute and log **which channel a brief would route through** (Slack/email/dashboard, urgency-gated) — but nothing actually *pushes* on a schedule. There is no scheduler; the pipeline only runs when invoked. |
| Conversational analysis | ❌ | No chat interface anywhere — confirmed by grep, zero hits for chat/conversation UI code. |
| Augmented dashboards | ✅ | The web UI ([ui/](ui/)) — 17 panels: KPI chart with changepoint marker, priority queue, evidence tables, methods breakdown, calibration, drift, entitlement/delivery logs. |
| Decision workspaces | ✅ mostly | Hypothesis cards with expandable raw SQL, persona-filtered briefs, a feedback-submission workflow, capacity-constrained actions — this is closer to a decision workspace than a passive dashboard, though it's read-heavy (one feedback action exists; there's no broader "workspace" for an analyst to annotate, save views, or compare runs side by side). |

**Verdict: the dashboard is real and above a typical demo bar; alerts and chat are not built.**

## 5. Confidence scoring, evidence citation, alternative hypotheses, abstention mechanisms

**✅ Fully covered — this is REFUTE's strongest area, verified extensively already in GAPS.md.**
BOCPD posteriors, DiD p-values (raw + BH-adjusted), MDE-vs-plausible-effect power gate, isotonic
calibration; every verdict carries its SQL hash back to the exact query; up to 7 hypotheses tested
per movement including an adversarial self-challenge; INCONCLUSIVE as a first-class, power-gated
verdict, not a fallback. Nothing to add here — if anything, this is the one area where REFUTE
already exceeds what the brief asks for.

## 6. Action recommendations: driver → lever → action → impact → owner → confidence → monitoring

**✅ Exact match, verified in code.** `build_action_recommendation()` in
[`engine/l6_narrate_ledger.py`](engine/l6_narrate_ledger.py) populates precisely this template from
real L2/L5 numbers, further qualified against a real operational constraint
(`check_capacity_constraint()`) when the action doesn't fully fit. Nothing to add.

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
not on the novel part."* This is itself a direct, honest answer to the brief's explicit ask that
teams be clear about native vs. configured vs. custom, rather than a dodge.

---

## Should we add any of the missing pieces? Yes/no, with reasons

| Candidate | Add it? | Why |
|---|---|---|
| **Knowledge graph / ontology layer** | **Lean yes, if time allows** | The relationship data already exists in the contract; a real graph structure (even a lightweight in-memory `networkx` graph built from the existing YAML, not a new database) would let REFUTE answer genuinely new questions ("what else touches this dimension," "which surviving hypotheses share a mechanism-shape") and look more sophisticated in a demo — and it's a menu item judges may specifically look for. Low risk of contradicting REFUTE's thesis since it's pure structure, not a new source of unverified claims. |
| **Chat / conversational interface** | **Yes, but only if it stays a front-end to the SAME falsification backend** | Risky if done naively: "chat with your data" is almost certainly the single most common thing other teams will build, and it's exactly the naive pattern REFUTE's own positioning (§2, README.md) argues against ("LLM reads data → LLM writes an explanation" — a statistical guarantee of finding *something*, not evidence of causation). Done correctly — a natural-language question routes into an *existing* predicate/hypothesis lookup or triggers a *real* new falsification test, and the answer is still a verdict + evidence, never free-form LLM prose asserting a cause — it would reinforce the differentiation rather than dilute it. Not worth it if the only outcome is "now the LLM also writes the final paragraph." |
| **Proactive/scheduled alerting** | **Yes, cheap and natural** | The delivery-channel routing decision already exists (`determine_delivery_channel`); the only missing piece is a scheduler that re-runs the pipeline periodically and pushes only when a *new* movement clears the L1 gate. Low effort (a loop + the existing telemetry/ledger), genuinely closes menu item 4's "proactive" half, and is a natural, non-contradictory extension of what's already built. |
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
