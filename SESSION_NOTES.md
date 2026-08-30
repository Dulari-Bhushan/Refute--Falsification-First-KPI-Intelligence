# Session Notes — REFUTE dashboard work

Repo: cloned from `Dulari-Bhushan/Refute--Falsification-First-KPI-Intelligence`.
Branch: `ui-redesign` (created this session, **not committed/pushed yet** — working tree has the changes below, staged+unstaged mixed). Run `git status` first.

## Setup gotchas (if re-provisioning)
- Needs Python 3.12 (`requires-python = ">=3.12"`). `uv`'s own managed-Python install was **broken on this machine** (`Missing expected target directory for Python minor version link`, reproducible even after cache clear). Fix used: installed Python 3.12 via `winget install Python.Python.3.12`, then `uv sync --python "<path-to-3.12>\python.exe"`.
- `uv` itself wasn't installed; added via `pip install --user uv`.
- Run pipeline: `uv run python run_pipeline.py` (templated, no LLM) before starting the API.
- Run dashboard: `uv run uvicorn api.main:app --reload` (or via `.claude/launch.json` + Browser-pane `preview_start`).
- **Browser cache bit** you: editing `ui/app.js`/`ui/style.css` and reloading can serve a stale cached copy with zero indication. Fixed by cache-busting query params on the `<script>`/`<link>` tags in `ui/index.html` (`app.js?v=4`, `style.css?v=4`) — **bump that version number** whenever you edit those files and things don't seem to update.

## What was built this session

**1. Dashboard interactivity** (was static, hardcoded to West/revenue)
- Region selector (`ui/index.html` topbar) now drives KPI chart, gate badge, narrative, counterfactual chart — was previously wired but inert on first pass, fixed.
- Added KPI selector (revenue / units_sold / marketing_attributed_revenue_share) — same wiring.
- Added "All Movements" overview grid: every KPI×region L1 result as a clickable card (from `/api/l1-summary`), click loads it into the main chart. Answers "are other spikes evaluated" — yes, all of them, this makes it visible.

**2. Full UI redesign** (`ui/style.css`, `ui/index.html`, `ui/app.js` — near-total rewrite)
- Light/dark theme toggle, CSS-variable token system, persisted in localStorage.
- Replaced hand-rolled `<canvas>` chart drawing with Chart.js (CDN) for KPI chart + counterfactual chart.
- Replaced static SVG knowledge-graph layout with a real D3 force-directed graph: draggable, zoomable, click-node-to-query, search filter. (Later session: clicking a node now also highlights it plus its direct neighbors/edges and dims everything else, clearable by clicking the same node again or empty canvas — see the dated entry below.)
- Restyled persona tabs (Leader/Manager/Engineer) as a segmented control, kept inline (not full-page tabs) per explicit user direction.

**3. Investigation-context clarity** (user-reported confusion: "next step is same for all KPIs?")
- Root cause: hypothesis testing/action-recommendation panels are **not** region/KPI-scoped — they only ever describe the ONE real gated movement (West revenue, week 32). Only the top chart was ever wired to the selector.
- Fix: added a persistent "Investigation: West · Revenue, Week 32 (-8.9%)" tag under the Narrated Brief header (dims when off-context). When the selected region/KPI ≠ the investigated one, the brief panel now shows "No hypotheses were tested for X · Y — it never cleared the materiality gate" + a "Jump to the investigation →" button, instead of silently showing the West story regardless of selection.

**4. LLM backend switcher** (local GPU ↔ OpenRouter)
- New `engine/llm_config.py`: persists backend choice + OpenRouter API key + model to gitignored `.llm_config.json` (never committed, key never echoed back to browser — only "is a key set" boolean exposed).
- `engine/l4_llm_generation.py`: added `_call_openrouter()` (OpenAI-compatible API, `response_format: json_object`) as an alternate path to the local `outlines`+Qwen path. **Same two-gate validation** (schema + semantic domain) for both — neither backend trusted more. torch/transformers/outlines imports made lazy (inside `_load()`) so OpenRouter mode needs no CUDA/GPU/6GB download at all. Cache key + telemetry now include backend+model (switching backends never silently reuses the other's cached output or misreports cost).
- `api/main.py`: `GET/POST /api/llm-config`, `POST /api/llm-generate/run` (synchronous — triggers a real generation run against whichever backend is configured, adjudicates through L5, returns results).
- Dashboard: new "Live LLM Predicate Generation — Backend Settings" panel — radio choice, model/key inputs, "Run live LLM generation now" button that refreshes hypothesis/adversarial/telemetry panels on completion.
- Tested end-to-end including the failure path (bad key → real 401 from OpenRouter → 3 retries/topic → clean rejection message in UI, no crash).

## Bugs found + fixed this session
1. **KPI chart y-axis formatted everything as `$Xk`**, including `units_sold` (raw counts, crushed to "$1k" on every tick) and would've broken `marketing_attributed_revenue_share` (0–1 fraction) too. Fixed with `formatKpiTick()`/`formatKpiValue()`, branch on KPI name.
2. **Grid overflow**: `.grid-2` columns had no `min-width: 0`, so the 3-button persona-tabs flex row forced the whole page wider than viewport (page scrollWidth 1433 vs clientWidth 1270). Fixed with `minmax(0, ...)` grid columns + `min-width:0` on flex children.
3. **Hardcoded brief text**: `engine/l6_narrate_ledger.py`'s `render_ops_manager_brief`/`render_vp_brief` had `"Revenue fell ~8.9% in West (week 32)"` as a literal string, ignoring actual data. Now takes a `kpi_headline` param sourced from real L1 results. (Note: this only affects the CLI/ledger console output, **not** the dashboard — the dashboard's brief panel was always built independently in JS from live API data.)
4. **KG "Show/Hide connections" and "Blast radius" buttons "didn't work"**: actually did work (verified via real click events) — the result rendered silently below a 460px graph canvas with zero feedback, so it looked broken if off-screen. Fixed: `scrollIntoView` + border-flash on render, plus button loading state.

## Known open items (not done, flagged not fixed)
- Longer-term/deferred: contract-level `display_name` field in `semantic/kpi_contract.yaml` as the "proper" source of human labels, with the regex transform as fallback when absent. Not started.

## Resolved this follow-up session
- **Raw snake_case identifiers leaking into business-facing UI** — fixed. Added `humanizeIdentifier()` to `ui/app.js` (strips `h_` prefix, `_`→space, title-cases every word incl. inside parens) and replaced every business-facing use of the raw `KPI_LABELS` dict (now deleted) and raw `hypothesis_id`/`driver` display with it: overview grid, hypothesis cards (raw id kept as a `title=` tooltip for traceability), priority queue, action panel driver, VP/ops-manager brief panels, adversarial-challenge cards, KPI panel title, investigation tag, shared-mechanism table, live-LLM-generation result, feedback-loop result. Deliberately left raw (by design, not oversight): the **Engineer persona** brief row (`renderBrief`'s `else` branch) still pairs the raw `hypothesis_id` with SQL hashes/p-values — that's the intentional technical/audit view, per the persona's purpose. Also left alone: API call params that use `hypothesis_id` as a query/body value (not display), and the D3 knowledge-graph diagram's own `.label` field (separate data source, out of the stated scope). Verified live in-browser (own preview_start against the already-running `--reload` server, since it watches the same files on disk): overview grid shows "Rep Attributed Revenue", "New Category Revenue (Outdoor)" (parenthetical correctly capitalized too); hypothesis cards show "Rep Attrition", "Shipping Delay", etc.; priority queue/action panel/brief panels all humanized; Engineer view still shows raw `H_REP_ATTRITION [SURVIVED]` etc. as intended; no console errors. Bumped `ui/index.html` cache-bust query params to `?v=5` per this file's own browser-cache gotcha note above.
- Git: branch created, changes NOT committed or pushed. User needs to review diff and decide commit message / whether to squash.
- Browser-pane screenshot tool has a reproducible quirk: screenshots taken at a scrolled position on this page render blank (confirmed via `get_page_text`/`getBoundingClientRect` that the actual DOM/layout is correct — it's a tool rendering artifact, not a real bug). Not fixable in-repo; just use non-screenshot verification (page text, JS state checks) for scrolled content when testing in this environment.

## File map (touched this session)
- `ui/index.html`, `ui/app.js`, `ui/style.css` — near-full rewrite
- `engine/l6_narrate_ledger.py` — hardcoded-string fix
- `engine/l4_llm_generation.py` — OpenRouter backend support
- `engine/llm_config.py` — new
- `api/main.py` — new LLM-config/generate endpoints
- `pyproject.toml` — added `httpx` as direct dep
- `.gitignore` — added `.llm_config.json`
- `.claude/launch.json` — new, dev-server config for the Browser-pane tool

## Resolved this session (branch `ui-redesign-v2` → `feature/investigation-aware-actions`, committed + pushed)

1. **Live-LLM-generated hypotheses were tested but invisible.** `engine/l4_llm_generation.py`'s
   `main()` genuinely generated + adjudicated predicates through the real L5 pipeline, but only wrote
   results to the ledger + `data/synthetic/l4_llm_generated_predicates.json` — never into
   `data/synthetic/l5_verdicts.json`, which `/api/hypotheses` actually serves to the main grid/brief.
   A real SURVIVED/KILLED/INCONCLUSIVE verdict would show once in the run's results panel and then be
   gone. Fixed: `_persist_llm_verdicts()` appends accepted, adjudicated live verdicts into
   `l5_verdicts.json` (tagged `region`/`kpi`/`source="llm_generated"`), with collision-safe id
   renaming (`h_shipping_delay` → `h_shipping_delay_llm2` when a live-generated id collides with an
   existing hand-authored one — confirmed this actually happens with OpenRouter). UI shows a distinct
   "LLM-generated" badge on those cards (`ui/app.js`, `ui/style.css`'s `.llm-badge`) — identical
   statistical trust, visibly labeled provenance. Verified West's original 7 + Central's 2 verdicts
   stay byte-identical after the change.
2. **Knowledge graph was click-to-query only, no visual highlighting.** Clicking a node opened the
   "related connections" table below the canvas but did nothing to the graph itself. Added
   `highlightKgNode()`/`clearKgHighlight()` (`ui/app.js`): clicking a node now dims every non-neighbor
   node/edge to low opacity and gives the clicked node an accent-colored stroke; clicking it again or
   clicking empty canvas clears the highlight. Survives theme toggling (`recolorGraph()` re-applies
   the current highlight instead of resetting it).
3. **Action recommendation was hardcoded to West, with no operational data for any other
   investigation.** `/api/action-recommendation` and `/api/delivery-channel` called `adjudicate_all()`
   with zero args (defaults to `region="West"`), and `build_action_recommendation()` hardcoded West's
   rep IDs (`W1`-`W4`) and `crm_headcount.csv`-based capacity math — Central's real surviving cause (a
   CENTRAL_DC WMS-migration/fulfillment-backlog mechanism) had no equivalent operational dataset to
   ground a feasibility check in at all. Fixed with a new synthetic dataset,
   `data/synthetic/fulfillment_center_ops.csv` (generated by `data/generate_fulfillment_ops_data.py`:
   daily incoming/processing capacity, backlog, staffing, WMS status for all 3 fulfillment centers —
   Central's backlog jumps from 10 to 1,810 orders in September when the migration cuts capacity below
   intake), and a new generic module `engine/action_recommendation.py` that works for any investigation
   in `engine/investigations.py`: gathers that investigation's SURVIVED verdict(s) + L1 context +
   whichever operational dataset applies to the surviving hypothesis's dimension, then has an LLM
   synthesize a grounded root-cause-and-action writeup (deterministic fallback when no LLM backend is
   selected, or a call fails/fails schema validation — never a broken panel). Frontend now refetches
   the action recommendation on every region/KPI switch (`loadKpiPanel`) instead of reusing West's
   stale result; found and fixed a real race where the Narrated Brief rendered before the new
   investigation's action data arrived. Verified end-to-end with the safe `none` backend and with real
   OpenRouter calls for both West and Central.

Not done in this session (out of scope, flagged not fixed): the Counterfactual Projection chart
(`ui/app.js`'s `renderCounterfactualChart`) was raised as looking "stuck and static" — confirmed it's
a chart-design issue, not a data bug (the numbers genuinely differ per investigation): it only plots
future weeks with no historical lead-in, "if no action" is always a perfectly flat line, "if action
succeeds" is always a straight linear ramp, and the computed confidence band (`ci_low`/`ci_high`) is
fetched but never actually rendered. Proposed fix (prepend real observed history, shade the confidence
band) was scoped but not implemented — pick up here if continuing.
