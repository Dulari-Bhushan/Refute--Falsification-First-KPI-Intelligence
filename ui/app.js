const API = "";

let STATE = {
  hypotheses: [],
  action: null,
  region: "West",
  kpi: "revenue",
  role: "regional_vp",
};

// The pipeline only ever ran hypothesis testing against ONE real gated
// movement -- West revenue's week-32 changepoint (see engine/l6_narrate_
// ledger.py, which hardcodes region="West" for the same reason: L3's
// candidate generation and every predicate fixture target this KPI/region
// specifically, not a generalized "whatever's selected" analysis). The top
// selector changes the CHART; it was never wired to re-run hypothesis
// testing for a different KPI/region, because no such testing exists.
const INVESTIGATION = { region: "West", kpi: "revenue" };
function isInvestigatedContext() {
  return STATE.region === INVESTIGATION.region && STATE.kpi === INVESTIGATION.kpi;
}

// Turns machine identifiers (KPI names, hypothesis IDs) into business-facing
// labels via a generic transform -- not a per-ID lookup table (breaks
// silently on any ID it wasn't told about) and not an LLM call (pure
// formatting, no judgment involved).
function humanizeIdentifier(id) {
  if (!id) return id;
  return id
    .replace(/^h_/, "")
    .replace(/_/g, " ")
    .toLowerCase()
    .replace(/\b[a-z]/g, (c) => c.toUpperCase());
}
const CHARTABLE_KPIS = new Set(["revenue", "units_sold", "marketing_attributed_revenue_share"]);

async function getJSON(path) {
  const res = await fetch(API + path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

function fmtPct(x, digits = 1) {
  if (x === null || x === undefined) return "n/a";
  return `${(x * 100).toFixed(digits)}%`;
}

function verdictClass(v) {
  return { KILLED: "killed", SURVIVED: "survived", INCONCLUSIVE: "inconclusive" }[v] || "";
}

// KPIs are on very different scales/units -- revenue is USD, units_sold is a
// plain count, marketing_attributed_revenue_share is a 0-1 fraction. A single
// "$Xk" formatter applied to all three crushes units_sold/share into
// indistinguishable ticks (e.g. every tick rounding to "$1k").
function formatKpiTick(kpiName, v) {
  if (kpiName === "marketing_attributed_revenue_share") return `${(v * 100).toFixed(0)}%`;
  if (kpiName === "units_sold") return Math.round(v).toLocaleString();
  return `$${Math.round(v / 1000)}k`;
}
function formatKpiValue(kpiName, v) {
  if (kpiName === "marketing_attributed_revenue_share") return `${(v * 100).toFixed(1)}%`;
  if (kpiName === "units_sold") return Math.round(v).toLocaleString();
  return `$${Math.round(v).toLocaleString()}`;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

// ==================================================================== THEME
function applyThemeIcons(theme) {
  document.getElementById("themeIconDark").style.display = theme === "dark" ? "" : "none";
  document.getElementById("themeIconLight").style.display = theme === "light" ? "" : "none";
}

function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

function setTheme(theme, { redraw = true } = {}) {
  if (theme === "light") document.documentElement.setAttribute("data-theme", "light");
  else document.documentElement.removeAttribute("data-theme");
  localStorage.setItem("refute-theme", theme);
  applyThemeIcons(theme);
  if (redraw) {
    if (LAST_KPI_DATA) renderKpiChart(LAST_KPI_DATA);
    if (LAST_CF_DATA) renderCounterfactualChart(LAST_CF_DATA);
    if (LAST_GRAPH_DATA) recolorGraph();
  }
}

function initTheme() {
  const stored = localStorage.getItem("refute-theme");
  const theme = stored || (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  setTheme(theme, { redraw: false });
  document.getElementById("themeToggle").addEventListener("click", () => {
    setTheme(currentTheme() === "light" ? "dark" : "light");
  });
}

// ==================================================================== KPI CHART (Chart.js)
let kpiChartInstance = null;
let LAST_KPI_DATA = null;

function renderKpiChart(kpi) {
  LAST_KPI_DATA = kpi;
  const ctx = document.getElementById("kpiChart");
  const accent = cssVar("--accent");
  const grid = cssVar("--border-soft");
  const textFaint = cssVar("--text-faint");
  const inconclusive = cssVar("--inconclusive");

  const labels = kpi.series.map((d) => `w${d.week}`);
  const values = kpi.series.map((d) => d.value);
  const cpIndex = kpi.changepoint_week ? kpi.series.findIndex((d) => d.week === kpi.changepoint_week) : -1;

  const cpLinePlugin = {
    id: "cpLine",
    afterDatasetsDraw(chart) {
      if (cpIndex < 0) return;
      const xScale = chart.scales.x, yScale = chart.scales.y;
      const x = xScale.getPixelForValue(cpIndex);
      const ctx2 = chart.ctx;
      ctx2.save();
      ctx2.strokeStyle = inconclusive;
      ctx2.setLineDash([4, 4]);
      ctx2.lineWidth = 1.5;
      ctx2.beginPath();
      ctx2.moveTo(x, yScale.top);
      ctx2.lineTo(x, yScale.bottom);
      ctx2.stroke();
      ctx2.setLineDash([]);
      ctx2.fillStyle = inconclusive;
      ctx2.font = "600 10.5px Inter, sans-serif";
      ctx2.fillText(`week ${kpi.changepoint_week} changepoint`, x + 6, yScale.top + 12);
      ctx2.restore();
    },
  };

  if (kpiChartInstance) kpiChartInstance.destroy();
  kpiChartInstance = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [{
        data: values,
        borderColor: accent,
        backgroundColor: (context) => {
          const { chart } = context;
          const { ctx: c, chartArea } = chart;
          if (!chartArea) return "transparent";
          const g = c.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
          g.addColorStop(0, accent + "33");
          g.addColorStop(1, accent + "00");
          return g;
        },
        borderWidth: 2.25,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBackgroundColor: accent,
        tension: 0.25,
        fill: true,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: cssVar("--panel-3"),
          titleColor: cssVar("--text"),
          bodyColor: cssVar("--text-dim"),
          borderColor: cssVar("--border"),
          borderWidth: 1,
          padding: 10,
          displayColors: false,
          callbacks: { label: (item) => formatKpiValue(kpi.kpi, item.parsed.y) },
        },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: textFaint, maxTicksLimit: 8, font: { size: 10.5 } } },
        y: {
          grid: { color: grid },
          border: { display: false },
          ticks: { color: textFaint, font: { size: 10.5 }, callback: (v) => formatKpiTick(kpi.kpi, v) },
        },
      },
    },
    plugins: [cpLinePlugin],
  });
}

// ==================================================================== COUNTERFACTUAL CHART
let cfChartInstance = null;
let LAST_CF_DATA = null;

function renderCounterfactualChart(cf) {
  LAST_CF_DATA = cf;
  document.getElementById("counterfactualAssumption").textContent = cf.assumption;
  const ctx = document.getElementById("counterfactualChart");
  const noActionColor = cssVar("--text-faint");
  const recoveryColor = cssVar("--survived");
  const grid = cssVar("--border-soft");
  const textFaint = cssVar("--text-faint");

  const labels = cf.scenario_no_action.map((d) => `w${d.week}`);

  if (cfChartInstance) cfChartInstance.destroy();
  cfChartInstance = new Chart(ctx, {
    type: "line",
    data: {
      labels,
      datasets: [
        {
          label: "If no action",
          data: cf.scenario_no_action.map((d) => d.value),
          borderColor: noActionColor,
          borderDash: [5, 4],
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.2,
          fill: false,
        },
        {
          label: "If action succeeds",
          data: cf.scenario_recovery.map((d) => d.value),
          borderColor: recoveryColor,
          borderWidth: 2.25,
          pointRadius: 0,
          tension: 0.2,
          fill: {
            target: "+1",
          },
          backgroundColor: recoveryColor + "1a",
        },
        {
          label: "recovery CI high",
          data: cf.scenario_recovery.map((d) => d.ci_high),
          borderColor: "transparent",
          pointRadius: 0,
          fill: false,
          borderWidth: 0,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: {
          display: true,
          labels: { color: textFaint, boxWidth: 14, font: { size: 11 }, filter: (item) => item.text !== "recovery CI high" },
        },
        tooltip: {
          backgroundColor: cssVar("--panel-3"),
          titleColor: cssVar("--text"),
          bodyColor: cssVar("--text-dim"),
          borderColor: cssVar("--border"),
          borderWidth: 1,
          padding: 10,
          filter: (item) => item.dataset.label !== "recovery CI high",
          callbacks: { label: (item) => `${item.dataset.label}: $${Math.round(item.parsed.y).toLocaleString()}` },
        },
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: textFaint, font: { size: 10.5 } } },
        y: { grid: { color: grid }, border: { display: false }, ticks: { color: textFaint, font: { size: 10.5 }, callback: (v) => `$${Math.round(v / 1000)}k` } },
      },
    },
  });
}

// ==================================================================== OVERVIEW GRID (all KPI x region)
function sparkPath(values, w, h) {
  if (!values.length) return "";
  const min = Math.min(...values), max = Math.max(...values);
  const span = max - min || 1;
  const step = w / Math.max(values.length - 1, 1);
  return values.map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${(h - ((v - min) / span) * h).toFixed(1)}`).join(" ");
}

async function renderOverviewGrid() {
  const el = document.getElementById("overviewGrid");
  const rows = await getJSON("/api/l1-summary");
  const chartable = rows.filter((r) => CHARTABLE_KPIS.has(r.kpi));
  const other = rows.filter((r) => !CHARTABLE_KPIS.has(r.kpi));

  const seriesCache = {};
  await Promise.all(
    chartable.map(async (r) => {
      const s = await getJSON(`/api/kpi-series?region=${r.region}&kpi=${r.kpi}`);
      seriesCache[`${r.kpi}::${r.region}`] = s.series.map((d) => d.value);
    })
  );

  function cardHtml(r) {
    const key = `${r.kpi}::${r.region}`;
    const isChartable = CHARTABLE_KPIS.has(r.kpi);
    const isActive = isChartable && r.kpi === STATE.kpi && r.region === STATE.region;
    const gateColor = r.gate_passed ? cssVar("--survived") : cssVar("--text-faint");
    const spark = seriesCache[key] ? `<svg class="oc-spark" viewBox="0 0 120 26" preserveAspectRatio="none" width="100%" height="26">
        <path d="${sparkPath(seriesCache[key], 120, 26)}" fill="none" stroke="${gateColor}" stroke-width="1.6" />
      </svg>` : "";
    return `<button class="overview-card ${isActive ? "active" : ""}" data-kpi="${r.kpi}" data-region="${r.region}" data-chartable="${isChartable ? "1" : "0"}">
      <div class="oc-top">
        <span class="oc-kpi">${humanizeIdentifier(r.kpi)}</span>
        <span class="oc-region">${r.region}</span>
      </div>
      <div class="oc-impact" style="color:${r.gate_passed ? "var(--text)" : "var(--text-faint)"}">${fmtPct(r.business_impact_pct)}</div>
      ${spark}
      <div class="oc-gate ${r.gate_passed ? "pass" : "noise"}">${r.gate_passed ? "● GATE: PASS" : "○ within normal variation"}</div>
    </button>`;
  }

  el.innerHTML = [...chartable, ...other].map(cardHtml).join("");

  el.querySelectorAll(".overview-card").forEach((btn) => {
    btn.addEventListener("click", async () => {
      if (btn.dataset.chartable !== "1") {
        btn.classList.toggle("open-note");
        return;
      }
      STATE.kpi = btn.dataset.kpi;
      STATE.region = btn.dataset.region;
      document.getElementById("regionSelect").value = STATE.region;
      document.getElementById("kpiSelect").value = STATE.kpi;
      el.querySelectorAll(".overview-card").forEach((c) => c.classList.remove("active"));
      btn.classList.add("active");
      await loadKpiPanel(STATE.region, STATE.kpi);
    });
  });
}

// ==================================================================== hypothesis cards
function renderHypothesisCards(templated) {
  const container = document.getElementById("hypothesisCards");
  container.innerHTML = "";
  templated.forEach((h) => {
    const card = document.createElement("div");
    card.className = `card ${verdictClass(h.verdict).toLowerCase()}`;
    card.innerHTML = `
      <div class="card-top">
        <div class="card-title" title="${h.hypothesis_id}">${humanizeIdentifier(h.hypothesis_id)}</div>
        <div class="verdict-badge ${verdictClass(h.verdict)}">${h.verdict}</div>
      </div>
      <div class="card-archetype">${h.test_archetype}</div>
      <div class="card-reason">${h.reason}</div>
      <div class="card-detail">
        ${h.did_effect !== null && h.did_effect !== undefined ? `<div>effect: ${fmtPct(h.did_effect)}</div>` : ""}
        ${h.did_pvalue_bh !== null && h.did_pvalue_bh !== undefined ? `<div>BH-adjusted p: ${h.did_pvalue_bh.toFixed(4)}</div>` : ""}
        ${h.mde !== null && h.mde !== undefined ? `<div>MDE: ${fmtPct(h.mde)} (plausible floor: ${fmtPct(h.plausible_effect)})</div>` : ""}
        ${h.parallel_trends_pvalue !== null && h.parallel_trends_pvalue !== undefined ? `<div>parallel-trends p: ${h.parallel_trends_pvalue.toFixed(3)}</div>` : ""}
        <div>dim: ${h.dim} (${h.n_treatment_units} treatment unit(s) vs ${h.n_control_units} control)</div>
        ${(h.notes || []).map((n) => `<div>note: ${n}</div>`).join("")}
        ${h.treatment_sql_hash ? `
        <div style="margin-top:8px;padding-top:8px;border-top:1px dashed var(--border)">
          <div>treatment SQL hash: <span style="color:var(--accent)">${h.treatment_sql_hash}</span></div>
          <div>control SQL hash: <span style="color:var(--accent)">${h.control_sql_hash}</span></div>
          <details style="margin-top:4px" onclick="event.stopPropagation()">
            <summary style="cursor:pointer;color:var(--text-faint)">show generated SQL (auditable -- same predicate always compiles to this exact query)</summary>
            <pre style="white-space:pre-wrap;font-size:10px;color:var(--text-dim);margin:6px 0 0">-- treatment\n${h.treatment_sql}\n\n-- control\n${h.control_sql}</pre>
          </details>
        </div>` : `<div style="margin-top:8px;padding-top:8px;border-top:1px dashed var(--border);color:var(--text-faint)">No SQL hash -- this is a precedence test (BOCPD timing comparison, not a database query; see notes above).</div>`}
      </div>
    `;
    card.addEventListener("click", () => card.classList.toggle("open"));
    container.appendChild(card);
  });
}

// ==================================================================== action panel
function renderAction(data) {
  const el = document.getElementById("actionContent");
  if (!data.has_action) {
    el.innerHTML = `<div class="subtext">No hypothesis survived all applicable tests -- no action recommended yet. See the INCONCLUSIVE card above for what data would resolve it.</div>`;
    return;
  }
  const a = data.action;
  const c = a.capacity_constraint;
  const constraintHtml = c
    ? `<div class="k">Constraint</div><div style="color:${c.fits_within_capacity ? "var(--survived)" : "var(--inconclusive)"}">
        ${c.fits_within_capacity
          ? `Fits within capacity: ${c.accounts_needing_reassignment} accounts needed, ${c.staying_rep_headroom} headroom available.`
          : `Does NOT fully fit within capacity: ${c.accounts_needing_reassignment} accounts needed vs. ${c.staying_rep_headroom} headroom (ceiling ${c.max_accounts_per_rep_ceiling}/rep) -- ${c.shortfall} accounts short, action qualified accordingly.`}
      </div>`
    : "";
  el.innerHTML = `<div class="action-grid">
    <div class="k">Driver</div><div>${humanizeIdentifier(a.driver)}</div>
    <div class="k">Lever</div><div>${a.controllable_lever}</div>
    <div class="k">Action</div><div>${a.action}</div>
    <div class="k">Expected impact</div><div>${a.expected_impact}</div>
    <div class="k">Owner</div><div>${a.owner}</div>
    <div class="k">Confidence</div><div>${a.confidence}</div>
    <div class="k">Monitoring</div><div>${a.monitoring_plan}</div>
    ${constraintHtml}
  </div>`;
}

// ==================================================================== priority queue
function renderPriorities(priorities) {
  const el = document.getElementById("priorityQueue");
  if (!priorities || priorities.length === 0) {
    el.innerHTML = `<div class="subtext">No material movements currently in the queue.</div>`;
    return;
  }
  el.innerHTML = priorities
    .map((p) => {
      const sameEvent = p.likely_same_event_as.length ? `<span class="subtext"> (likely same event as: ${p.likely_same_event_as.join(", ")})</span>` : "";
      const isCurrent = p.kpi === STATE.kpi && p.region === STATE.region;
      return `<div class="brief-line" style="${isCurrent ? "border-left:2px solid var(--accent);padding-left:10px" : ""}">
        <span class="brief-label">#${p.rank} ${humanizeIdentifier(p.kpi)} (${p.region})</span>
        <div>impact ${fmtPct(p.business_impact_pct)} ($${Math.abs(p.business_impact_abs_usd).toLocaleString(undefined, { maximumFractionDigits: 0 })}), confidence ${p.changepoint_posterior_recent.toFixed(2)}, week ${p.changepoint_week}${sameEvent}</div>
      </div>`;
    })
    .join("");
}

// ==================================================================== contradictory evidence
function renderContradiction(data) {
  const section = document.getElementById("contradictionSection");
  const el = document.getElementById("contradictionContent");
  const c = data.contradiction;
  if (!c) {
    section.style.display = "none";
    return;
  }
  section.style.display = "";
  const typeLabel = c.verdict_type === "SAME_EVIDENCE_RETEST" ? "Same evidence, re-tested" : "Independent contradiction";
  const color = c.verdict_type === "SAME_EVIDENCE_RETEST" ? "var(--text-dim)" : "var(--inconclusive)";
  el.innerHTML = `
    <div class="brief-line"><span class="brief-label" style="color:${color}">${typeLabel}</span><div>${c.explanation}</div></div>
    ${c.survived_hypotheses.map((h) => `<div class="subtext">${h.hypothesis_id}: sql=(${h.treatment_sql_hash || "n/a"}, ${h.control_sql_hash || "n/a"})</div>`).join("")}
  `;
}

// ==================================================================== freshness table
function renderFreshness(recon) {
  const el = document.getElementById("freshnessTable");
  const rows = recon.source_freshness.map((s) => `
    <tr>
      <td>${s.source}</td>
      <td>${s.refresh_cadence}</td>
      <td>${s.covered_through}</td>
      <td class="${s.staleness_days > 5 ? "stale" : ""}">${s.staleness_days}d</td>
    </tr>`).join("");
  const missingRows = (recon.missing_data_rates || [])
    .map((m) => `<tr><td>${m.join}</td><td>${m.matched_rows}/${m.expected_rows}</td><td class="${m.missing_pct > 5 ? "stale" : ""}">${m.missing_pct}%</td></tr>`)
    .join("");
  const tierColor = (t) => (t === "high" ? "var(--survived)" : t === "medium" ? "var(--inconclusive)" : t === "low" ? "var(--killed)" : "var(--text-dim)");
  const qualityRows = (recon.data_quality_scores || [])
    .map((q) => `<tr><td>${q.source}</td><td>${q.system_of_record ? "yes" : "no"}</td><td>${q.coverage_completeness_pct !== null ? q.coverage_completeness_pct + "%" : "n/a"}</td><td style="color:${tierColor(q.quality_tier)}">${q.quality_tier.toUpperCase()}</td><td class="subtext">${q.note}</td></tr>`)
    .join("");
  el.innerHTML = `<div class="table-scroll"><table class="evidence-table">
    <thead><tr><th>Source</th><th>Cadence</th><th>Covered through</th><th>Staleness</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>
  <p class="subtext" style="margin-top:10px">Revenue source agreement: <strong>${recon.revenue_source_agreement_claim.verdict}</strong> &mdash; ${recon.revenue_source_agreement_claim.explanation || "sources agree within tolerance."}</p>
  ${recon.rep_attribution_bounds_claim ? `<p class="subtext" style="margin-top:6px">Rep-attribution bounds: <strong>${recon.rep_attribution_bounds_claim.verdict}</strong> &mdash; ${recon.rep_attribution_bounds_claim.explanation || ""}</p>` : ""}
  ${missingRows ? `<p class="subtext" style="margin-top:14px;margin-bottom:6px">Missing-data rate per cross-source join:</p>
  <div class="table-scroll"><table class="evidence-table"><thead><tr><th>Join</th><th>Matched</th><th>Missing</th></tr></thead><tbody>${missingRows}</tbody></table></div>` : ""}
  ${qualityRows ? `<p class="subtext" style="margin-top:14px;margin-bottom:6px">Data quality per source:</p>
  <div class="table-scroll"><table class="evidence-table"><thead><tr><th>Source</th><th>System of record</th><th>Completeness</th><th>Tier</th><th>Note</th></tr></thead><tbody>${qualityRows}</tbody></table></div>` : ""}`;
}

// ==================================================================== telemetry
function renderTelemetry(data) {
  const el = document.getElementById("telemetryStrip");
  const s = data.summary;
  el.innerHTML = `<div class="telemetry-strip">
    <div class="tstat"><div class="n">${s.total_calls}</div><div class="l">total stages</div></div>
    <div class="tstat"><div class="n">${s.llm_calls}</div><div class="l llm">LLM calls</div></div>
    <div class="tstat"><div class="n">${s.deterministic_calls}</div><div class="l">deterministic</div></div>
    <div class="tstat"><div class="n">${Math.round(s.total_latency_ms)}ms</div><div class="l">total latency</div></div>
    <div class="tstat"><div class="n">$${s.total_cost_usd.toFixed(4)}</div><div class="l">actual cost</div></div>
  </div>`;
}

// ==================================================================== persona brief
async function renderBrief(role) {
  const briefEl = document.getElementById("briefContent");
  const noteEl = document.getElementById("roleNote");
  const tag = document.getElementById("investigationTag");

  tag.classList.toggle("off-context", !isInvestigatedContext());

  if (!isInvestigatedContext()) {
    const kpiLabel = humanizeIdentifier(STATE.kpi);
    briefEl.innerHTML = `
      <div class="no-investigation">
        <div class="ni-title">No hypotheses were tested for ${STATE.region} · ${kpiLabel}</div>
        <div>${STATE.kpiNarrative || "This KPI/region never cleared L1's materiality gate, so no root-cause analysis ran for it -- no L3 candidate generation, no L4 predicate compilation, no L5 adjudication, and no LLM call."}</div>
        <div class="subtext">The only movement that triggered an investigation this run is <strong>${INVESTIGATION.region} · ${humanizeIdentifier(INVESTIGATION.kpi)}</strong>. Everything below (hypotheses, action, evidence) describes that event specifically.</div>
        <button class="action-btn" id="jumpToInvestigationBtn" type="button">Jump to the investigation &rarr;</button>
      </div>
    `;
    noteEl.className = "role-note";
    noteEl.textContent = "";
    document.getElementById("jumpToInvestigationBtn").addEventListener("click", async () => {
      STATE.region = INVESTIGATION.region;
      STATE.kpi = INVESTIGATION.kpi;
      document.getElementById("regionSelect").value = STATE.region;
      document.getElementById("kpiSelect").value = STATE.kpi;
      await loadKpiPanel(STATE.region, STATE.kpi);
    });
    return;
  }

  const survived = STATE.hypotheses.find((h) => h.verdict === "SURVIVED");
  const killed = STATE.hypotheses.filter((h) => h.verdict === "KILLED");
  const inconclusive = STATE.hypotheses.filter((h) => h.verdict === "INCONCLUSIVE");

  const ent = await getJSON(`/api/entitlement-check?role=${role}&dim=rep_id&region=${STATE.region}`);

  if (role === "regional_vp") {
    briefEl.innerHTML = `
      <div class="brief-line"><span class="brief-label">Headline</span><div>${STATE.kpiNarrative || ""}</div></div>
      ${survived ? `<div class="brief-line"><span class="brief-label">Cause</span><div>${humanizeIdentifier(survived.hypothesis_id)}</div></div>` : ""}
      <div class="brief-line"><span class="brief-label">Tested &amp; ruled out</span><div>${killed.length} alternative(s)${inconclusive.length ? `, ${inconclusive.length} inconclusive` : ""}</div></div>
      ${STATE.action && STATE.action.has_action ? `<div class="brief-line"><span class="brief-label">Next step</span><div>${STATE.action.action.action}</div></div>` : ""}
      ${STATE.action && STATE.action.has_action ? `<div class="brief-line"><span class="brief-label">Confidence</span><div>${STATE.action.action.confidence.split(" -- ")[0]}</div></div>` : ""}
    `;
  } else if (role === "ops_manager_west") {
    briefEl.innerHTML = `
      <div class="brief-line"><span class="brief-label">Movement</span><div>${STATE.kpiNarrative || ""}</div></div>
      <div class="brief-line"><span class="brief-label">Cause</span><div>${survived ? humanizeIdentifier(survived.hypothesis_id) : "none confirmed"}</div></div>
      <div class="brief-line"><span class="brief-label">Ruled out</span><div>${killed.map((h) => `${humanizeIdentifier(h.hypothesis_id)} (${h.test_archetype})`).join(", ") || "none"}</div></div>
      <div class="brief-line"><span class="brief-label">Inconclusive</span><div>${inconclusive.map((h) => humanizeIdentifier(h.hypothesis_id)).join(", ") || "none"}</div></div>
      ${STATE.action && STATE.action.has_action ? `<div class="brief-line"><span class="brief-label">Action</span><div>${STATE.action.action.action}</div></div>` : ""}
      ${STATE.action && STATE.action.has_action ? `<div class="brief-line"><span class="brief-label">Owner</span><div>${STATE.action.action.owner}</div></div>` : ""}
    `;
  } else {
    const rows = STATE.hypotheses
      .map((h) => {
        const stats = h.did_effect !== null && h.did_effect !== undefined
          ? `effect=${fmtPct(h.did_effect)} p_raw=${h.did_pvalue_raw?.toFixed(4) ?? "n/a"} p_BH=${h.did_pvalue_bh?.toFixed(4) ?? "n/a"} MDE=${fmtPct(h.mde)} floor=${fmtPct(h.plausible_effect)} pretrends_p=${h.parallel_trends_pvalue?.toFixed(3) ?? "n/a"}`
          : "(precedence test -- no DiD regression, see notes)";
        const hashLine = h.treatment_sql_hash ? `sql: ${h.treatment_sql_hash} / ${h.control_sql_hash}` : "sql: n/a (precedence test)";
        return `<div class="brief-line"><span class="brief-label">${h.hypothesis_id} [${h.verdict}]</span><div style="font-family:var(--mono);font-size:11px">${stats}</div><div style="font-family:var(--mono);font-size:11px;color:var(--text-faint)">${hashLine}</div></div>`;
      })
      .join("");
    briefEl.innerHTML = `${rows}<div class="brief-line" style="margin-top:10px"><span class="brief-label">Note</span><div>See Methods Breakdown below for which method category produced each number, and why.</div></div>`;
  }

  noteEl.className = `role-note ${ent.allowed ? "allowed" : "denied"}`;
  noteEl.textContent = ent.allowed
    ? `Rep-level account detail: visible to this role.`
    : `Rep-level account detail withheld: ${ent.reason}`;
}

// ==================================================================== methods breakdown
function renderMethodsBreakdown(data) {
  const el = document.getElementById("methodsBreakdown");
  const rows = data.entries
    .map(
      (e) => `<tr>
      <td>${e.stage}</td>
      <td><span style="text-transform:uppercase;font-size:10px;letter-spacing:.04em;color:${e.method_category === "llm" ? "var(--accent)" : "var(--text-dim)"}">${e.method_category.replace(/_/g, " ")}</span></td>
      <td>${e.method_name}</td>
      <td>${e.quantitative_output ? '<span style="color:var(--survived)">yes</span>' : '<span style="color:var(--text-faint)">no</span>'}</td>
    </tr>`
    )
    .join("");
  el.innerHTML = `<div class="table-scroll"><table class="evidence-table">
    <thead><tr><th>Stage</th><th>Method category</th><th>Method</th><th>Quantitative source of truth?</th></tr></thead>
    <tbody>${rows}</tbody>
  </table></div>
  <p class="subtext" style="margin-top:10px">${data.llm_stages_are_never_quantitative ? "Structurally checked: no LLM-driven stage is marked as a quantitative source of truth." : ""}</p>`;
}

// ==================================================================== calibration demo
function renderCalibration(report) {
  document.getElementById("calibrationHonesty").textContent = report.honesty_note;
  const el = document.getElementById("calibrationContent");
  const reliabilityRows = report.reliability_diagram
    .map((b) => `<tr><td>[${b.bucket_lo.toFixed(1)}-${b.bucket_hi.toFixed(1)})</td><td>${b.n}</td><td>${(b.mean_predicted_confidence * 100).toFixed(0)}%</td><td>${(b.observed_frequency * 100).toFixed(0)}%</td><td style="color:${Math.abs(b.calibration_gap) > 0.15 ? "var(--inconclusive)" : "var(--text-dim)"}">${b.calibration_gap >= 0 ? "+" : ""}${(b.calibration_gap * 100).toFixed(0)}pp</td></tr>`)
    .join("");
  const isoRows = report.isotonic_recalibration.fitted
    ? report.isotonic_recalibration.curve.map((p) => `<span style="font-family:var(--mono)">${p.raw_confidence.toFixed(1)}&rarr;${p.recalibrated_confidence.toFixed(2)}</span>`).join("  ")
    : "not enough points to fit";
  const hitRateHtml = Object.entries(report.hit_rate_by_kind)
    .map(([kind, rate]) => `<span style="margin-right:16px"><span class="brief-label">${kind.replace(/_/g, " ")}</span> ${(rate * 100).toFixed(0)}%</span>`)
    .join("");
  el.innerHTML = `
    <div class="telemetry-strip" style="margin-bottom:16px">
      <div class="tstat"><div class="n">${report.brier_score.toFixed(3)}</div><div class="l">Brier score (0=perfect)</div></div>
      <div class="tstat"><div class="n">${report.n_simulated_outcomes}</div><div class="l">simulated outcomes</div></div>
    </div>
    <div style="margin-bottom:12px">${hitRateHtml}</div>
    <div class="table-scroll"><table class="evidence-table" style="margin-bottom:12px">
      <thead><tr><th>Confidence bucket</th><th>n</th><th>Stated</th><th>Observed</th><th>Gap</th></tr></thead>
      <tbody>${reliabilityRows}</tbody>
    </table></div>
    <div class="subtext"><span class="brief-label">Isotonic recalibration curve</span><br>${isoRows}</div>
  `;
}

// ==================================================================== drift monitoring
function renderDrift(data) {
  const el = document.getElementById("driftContent");
  const { real, demo } = data;
  document.getElementById("driftHonesty").textContent =
    real.status === "assessed"
      ? `Real assessment against ${real.n_baseline_runs} prior run(s) in this ledger.`
      : demo.honesty_note;

  const verdictColor = (v) => (v === "STABLE" ? "var(--survived)" : v === "SIGNIFICANT_DRIFT" ? "var(--killed)" : "var(--inconclusive)");

  let realHtml;
  if (real.status === "assessed") {
    const rows = real.metrics
      .map((m) => `<tr><td>${m.metric}</td><td>${m.baseline_n}</td><td>${m.current_n}</td><td>${m.baseline_mean ?? "n/a"}</td><td>${m.current_mean ?? "n/a"}</td><td>${m.psi ?? "n/a"}</td><td style="color:${verdictColor(m.verdict)}">${m.verdict}</td></tr>`)
      .join("");
    realHtml = `<div class="subtext" style="margin-bottom:8px">Overall: <strong style="color:${verdictColor(real.overall_verdict)}">${real.overall_verdict}</strong></div>
      <div class="table-scroll"><table class="evidence-table"><thead><tr><th>Metric</th><th>Baseline n</th><th>Current n</th><th>Baseline mean</th><th>Current mean</th><th>PSI</th><th>Verdict</th></tr></thead><tbody>${rows}</tbody></table></div>`;
  } else {
    realHtml = `<div class="subtext">insufficient_history &mdash; ${real.n_baseline_runs} prior run(s), needs ${real.runs_needed ?? 5} more.</div>`;
  }

  const demoRow = (label, d) => `<tr><td>${label}</td><td>${d.posterior_psi}</td><td style="color:${verdictColor(d.posterior_verdict)}">${d.posterior_verdict}</td><td>${d.effect_size_psi}</td><td style="color:${verdictColor(d.effect_size_verdict)}">${d.effect_size_verdict}</td></tr>`;
  el.innerHTML = `
    ${realHtml}
    <p class="subtext" style="margin-top:14px"><strong>${demo.label}</strong> &mdash; proves the PSI mechanism itself is correct:</p>
    <div class="table-scroll"><table class="evidence-table">
      <thead><tr><th>Case</th><th>Posterior PSI</th><th>Verdict</th><th>Effect-size PSI</th><th>Verdict</th></tr></thead>
      <tbody>
        ${demoRow("Control (current == baseline)", demo.control_case_same_distribution)}
        ${demoRow("Drift (current is shifted)", demo.drift_case_shifted_distribution)}
      </tbody>
    </table></div>
  `;
}

// ==================================================================== domain-level security check
async function renderDomainCheck() {
  const el = document.getElementById("domainCheckContent");
  const scenarios = [
    { role: "ops_manager_west", kpi: "revenue" },
    { role: "ops_manager_west", kpi: "rep_attributed_revenue" },
    { role: "regional_vp", kpi: "revenue" },
    { role: "regional_vp", kpi: "rep_attributed_revenue" },
    { role: "marketing_analyst", kpi: "marketing_attributed_revenue_share" },
    { role: "marketing_analyst", kpi: "revenue" },
    { role: "platform_engineer", kpi: "rep_attributed_revenue" },
  ];
  const results = await Promise.all(scenarios.map((s) => getJSON(`/api/domain-check?role=${s.role}&kpi=${s.kpi}`)));
  const rows = scenarios
    .map((s, i) => {
      const r = results[i];
      return `<tr><td>${s.role}</td><td>${s.kpi}</td><td style="color:${r.allowed ? "var(--survived)" : "var(--killed)"}">${r.allowed ? "ALLOWED" : "DENIED"}</td><td class="subtext">${r.reason || "&mdash;"}</td></tr>`;
    })
    .join("");
  el.innerHTML = `<div class="table-scroll"><table class="evidence-table"><thead><tr><th>Role</th><th>KPI requested</th><th>Result</th><th>Reason</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

// ==================================================================== entitlement audit log
async function renderEntitlementLog() {
  const el = document.getElementById("entitlementLogContent");
  const data = await getJSON("/api/entitlement-log?limit=30");
  if (!data.rows || data.rows.length === 0) {
    el.innerHTML = `<p class="subtext">No entitlement checks recorded yet.</p>`;
    return;
  }
  const rows = data.rows
    .map((r) => `<tr>
      <td>${r.created_at.split("T")[0]} ${r.created_at.split("T")[1].slice(0, 8)}</td>
      <td>${r.check_type}</td>
      <td>${r.role}</td>
      <td>${r.scope}${r.region ? ` (${r.region})` : ""}</td>
      <td style="color:${r.allowed ? "var(--survived)" : "var(--killed)"}">${r.allowed ? "ALLOWED" : "DENIED"}</td>
      <td class="subtext">${r.reason || "&mdash;"}</td>
    </tr>`)
    .join("");
  el.innerHTML = `<div class="table-scroll"><table class="evidence-table"><thead><tr><th>When</th><th>Type</th><th>Role</th><th>Scope</th><th>Result</th><th>Reason</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

// ==================================================================== delivery-channel routing
async function renderDeliveryLog() {
  const el = document.getElementById("deliveryLogContent");
  const data = await getJSON("/api/delivery-log?limit=20");
  if (!data.rows || data.rows.length === 0) {
    el.innerHTML = `<p class="subtext">No deliveries simulated yet.</p>`;
    return;
  }
  const urgencyColor = (u) => (u === "urgent_push" ? "var(--killed)" : u === "routine_push" ? "var(--inconclusive)" : "var(--text-dim)");
  const rows = data.rows
    .map((r) => `<tr>
      <td>${r.role}</td>
      <td>${r.persona}</td>
      <td>${r.channel}</td>
      <td style="color:${urgencyColor(r.urgency)}">${r.urgency}</td>
      <td class="subtext">${r.message_preview.slice(0, 90)}&hellip;</td>
    </tr>`)
    .join("");
  el.innerHTML = `<div class="table-scroll"><table class="evidence-table"><thead><tr><th>Role</th><th>Persona</th><th>Channel</th><th>Urgency</th><th>Message preview</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

// ==================================================================== proactive alerts
function renderAlerts(data) {
  const el = document.getElementById("alertsContent");
  const urgencyColor = (u) => (u === "urgent_push" ? "var(--killed)" : u === "routine_push" ? "var(--inconclusive)" : "var(--text-dim)");
  const recentRows = (data.recent || [])
    .map((a) => `<tr><td>${a.kpi}</td><td>${a.region}</td><td>${a.role}</td><td>${a.channel}</td><td style="color:${urgencyColor(a.urgency)}">${a.urgency}</td><td class="subtext">${a.message}</td></tr>`)
    .join("");
  const recentTable = recentRows
    ? `<div class="table-scroll"><table class="evidence-table"><thead><tr><th>KPI</th><th>Region</th><th>Role</th><th>Channel</th><th>Urgency</th><th>Message</th></tr></thead><tbody>${recentRows}</tbody></table></div>`
    : `<p class="subtext">0 alerts this run &mdash; nothing currently gated is new relative to the prior run.</p>`;

  const demo = data.demo;
  el.innerHTML = `
    ${recentTable}
    <p class="subtext" style="margin-top:14px"><strong>${demo.label}</strong> &mdash; proves the new-vs-known detection logic itself is correct:</p>
    <div class="table-scroll"><table class="evidence-table">
      <thead><tr><th>Prior run gated</th><th>Current run gated</th><th>Correctly new</th><th>Correctly NOT new</th><th>Detector correct?</th></tr></thead>
      <tbody><tr>
        <td class="subtext">${demo.prior_run_gated.join(", ")}</td>
        <td class="subtext">${demo.current_run_gated.join(", ")}</td>
        <td style="color:var(--survived)">${demo.correctly_identified_as_new.join(", ")}</td>
        <td class="subtext">${demo.correctly_identified_as_not_new.join(", ")}</td>
        <td style="color:${demo.detector_correct ? "var(--survived)" : "var(--killed)"}">${demo.detector_correct ? "yes" : "no"}</td>
      </tr></tbody>
    </table></div>
  `;
}

// ==================================================================== adversarial challenge
function renderAdversarial(data) {
  const section = document.getElementById("adversarialSection");
  const el = document.getElementById("adversarialContent");
  if (!data.challenges || data.challenges.length === 0) {
    section.style.display = "none";
    return;
  }
  section.style.display = "";
  el.innerHTML = data.challenges
    .map((c) => {
      const meaning = c.verdict === "SURVIVED"
        ? "The original conclusion is more contested than a single surviving test suggested -- both should be reviewed."
        : "The original conclusion held up against the strongest counter-case the model could construct.";
      return `<div class="brief-line">
        <span class="brief-label">${humanizeIdentifier(c.hypothesis_id)}</span>
        <div><span class="verdict-badge ${verdictClass(c.verdict)}">${c.verdict}</span> ${c.reason}</div>
        <div class="subtext" style="margin-top:4px">${meaning}</div>
      </div>`;
    })
    .join("");
}

// ==================================================================== KPI + counterfactual panel (region + kpi driven)
async function loadKpiPanel(region, kpi) {
  const [kpiData, counterfactual] = await Promise.all([
    getJSON(`/api/kpi-series?region=${region}&kpi=${kpi}`),
    getJSON(`/api/counterfactual?region=${region}`),
  ]);

  const label = humanizeIdentifier(kpi);
  document.getElementById("kpiTitle").textContent = kpiData.changepoint_week
    ? `${region} · ${label} — Week ${kpiData.changepoint_week}`
    : `${region} · ${label}`;
  document.getElementById("kpiValue").textContent = fmtPct(kpiData.business_impact_pct);
  document.getElementById("kpiValue").className = `value ${kpiData.gate_passed ? "" : "neutral"}`;
  document.getElementById("kpiBadge").textContent = kpiData.gate_passed ? "GATE: PASS" : "GATE: NOISE";
  document.getElementById("kpiBadge").className = `badge ${kpiData.gate_passed ? "pass" : "noise"}`;
  document.getElementById("kpiNarrative").textContent = kpiData.narrative || "No material movement detected in this region -- within normal variation, no LLM call made.";
  STATE.kpiNarrative = kpiData.narrative;

  renderKpiChart(kpiData);
  renderCounterfactualChart(counterfactual);

  // keep dependent panels in sync with the new context
  if (STATE._initialized) {
    renderBrief(STATE.role);
    getJSON("/api/priorities").then(renderPriorities);
    document.querySelectorAll(".overview-card").forEach((c) => {
      c.classList.toggle("active", c.dataset.kpi === kpi && c.dataset.region === region);
    });
  }
}

// ==================================================================== KNOWLEDGE GRAPH (D3 force graph)
const KG_TYPE_COLOR_VAR = {
  domain: "--accent", kpi: "--text", source: "--text-dim", dimension: "--text-faint",
  role: "--accent-2", channel: "--text-dim", verdict: "--text", hypothesis: "--text",
};

function kgVerdictColorVar(v) {
  return v === "KILLED" ? "--killed" : v === "SURVIVED" ? "--survived" : v === "INCONCLUSIVE" ? "--inconclusive" : "--text-dim";
}

function kgNodeColorVar(node) {
  if (node.type === "hypothesis") return kgVerdictColorVar(node.verdict);
  if (node.type === "verdict") return kgVerdictColorVar(node.id.split(":")[1]);
  return KG_TYPE_COLOR_VAR[node.type] || "--text";
}

let KG_STATE = { nodes: {} };
let LAST_GRAPH_DATA = null;
let kgSim = null, kgSvg = null, kgZoom = null, kgNodeSel = null, kgLinkSel = null, kgLabelSel = null, kgG = null;

function recolorGraph() {
  if (!kgNodeSel) return;
  kgNodeSel.attr("fill", (d) => cssVar(kgNodeColorVar(d)));
  kgLinkSel.attr("stroke", cssVar("--border"));
  kgLabelSel.attr("fill", cssVar("--text-dim"));
}

function initKnowledgeGraphDiagram(graph) {
  LAST_GRAPH_DATA = graph;
  KG_STATE = { nodes: Object.fromEntries(graph.nodes.map((n) => [n.id, n])) };

  const wrap = document.querySelector(".kg-canvas-wrap");
  const width = wrap.clientWidth || 900;
  const height = wrap.clientHeight || 460;

  const svgEl = d3.select("#kgSvg").attr("viewBox", `0 0 ${width} ${height}`);
  svgEl.selectAll("*").remove();
  kgG = svgEl.append("g");

  const nodes = graph.nodes.map((n) => ({ ...n }));
  const links = graph.edges.map((e) => ({ ...e, source: e.source, target: e.target }));

  kgLinkSel = kgG.append("g").selectAll("line").data(links).join("line")
    .attr("stroke", cssVar("--border")).attr("stroke-width", 1);

  const nodeGroup = kgG.append("g").selectAll("g").data(nodes).join("g")
    .style("cursor", "pointer")
    .call(d3.drag()
      .on("start", (event, d) => { if (!event.active) kgSim.alphaTarget(0.25).restart(); d.fx = d.x; d.fy = d.y; })
      .on("drag", (event, d) => { d.fx = event.x; d.fy = event.y; })
      .on("end", (event, d) => { if (!event.active) kgSim.alphaTarget(0); d.fx = null; d.fy = null; }));

  kgNodeSel = nodeGroup.append("circle")
    .attr("r", (d) => (d.type === "kpi" || d.type === "hypothesis" ? 7 : 5.5))
    .attr("fill", (d) => cssVar(kgNodeColorVar(d)))
    .attr("stroke", cssVar("--panel")).attr("stroke-width", 1.5);

  kgLabelSel = nodeGroup.append("text")
    .attr("class", "kg-node-label")
    .attr("x", 9).attr("y", 3)
    .attr("fill", cssVar("--text-dim"))
    .text((d) => (d.label.length > 20 ? d.label.slice(0, 19) + "…" : d.label));

  nodeGroup.on("click", (event, d) => {
    document.getElementById("kgNodeSelect").value = d.id;
    kgQueryRelated(d.id);
    nodeGroup.select("circle").attr("stroke-width", (n) => (n.id === d.id ? 3 : 1.5)).attr("stroke", (n) => (n.id === d.id ? cssVar("--accent") : cssVar("--panel")));
  });
  nodeGroup.append("title").text((d) => `${d.type}: ${d.label}`);

  kgSim = d3.forceSimulation(nodes)
    .force("link", d3.forceLink(links).id((d) => d.id).distance(70).strength(0.5))
    .force("charge", d3.forceManyBody().strength(-190))
    .force("center", d3.forceCenter(width / 2, height / 2))
    .force("collide", d3.forceCollide(26))
    .on("tick", () => {
      kgLinkSel.attr("x1", (d) => d.source.x).attr("y1", (d) => d.source.y).attr("x2", (d) => d.target.x).attr("y2", (d) => d.target.y);
      nodeGroup.attr("transform", (d) => `translate(${d.x},${d.y})`);
    });

  kgZoom = d3.zoom().scaleExtent([0.3, 4]).on("zoom", (event) => kgG.attr("transform", event.transform));
  svgEl.call(kgZoom);

  document.getElementById("kgZoomIn").onclick = () => svgEl.transition().duration(200).call(kgZoom.scaleBy, 1.3);
  document.getElementById("kgZoomOut").onclick = () => svgEl.transition().duration(200).call(kgZoom.scaleBy, 0.75);
  document.getElementById("kgZoomReset").onclick = () => svgEl.transition().duration(300).call(kgZoom.transform, d3.zoomIdentity);

  // legend
  const types = [...new Set(graph.nodes.map((n) => n.type))];
  document.getElementById("kgLegend").innerHTML = types
    .map((t) => `<span class="item"><span class="dot" style="background:${cssVar(KG_TYPE_COLOR_VAR[t] || "--text")}"></span>${t}</span>`)
    .join("");

  // node select + search
  const select = document.getElementById("kgNodeSelect");
  const byType = {};
  graph.nodes.forEach((n) => { (byType[n.type] = byType[n.type] || []).push(n); });
  select.innerHTML = Object.entries(byType)
    .map(([type, list]) => `<optgroup label="${type}">${list.slice().sort((a, b) => a.label.localeCompare(b.label)).map((n) => `<option value="${n.id}">${n.label}</option>`).join("")}</optgroup>`)
    .join("");

  document.getElementById("kgSearch").oninput = (e) => {
    const q = e.target.value.trim().toLowerCase();
    nodeGroup.select("circle").attr("opacity", (d) => (!q || d.label.toLowerCase().includes(q) ? 1 : 0.15));
    kgLabelSel.attr("opacity", (d) => (!q || d.label.toLowerCase().includes(q) ? 1 : 0.15));
    kgLinkSel.attr("opacity", (d) => (!q ? 1 : 0.08));
  };
}

function renderKgQueryResult(title, rows, columns) {
  const el = document.getElementById("kgQueryResult");
  if (!rows || rows.length === 0) {
    el.innerHTML = `<p class="subtext"><strong>${title}:</strong> no connections found.</p>`;
    return;
  }
  const head = columns.map((c) => `<th>${c.label}</th>`).join("");
  const body = rows.map((r) => `<tr>${columns.map((c) => `<td>${c.render ? c.render(r) : r[c.key]}</td>`).join("")}</tr>`).join("");
  el.innerHTML = `<p class="subtext" style="margin-bottom:6px"><strong>${title}</strong></p><div class="table-scroll"><table class="evidence-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  flashKgResult(el);
}

// Results render below a 460px graph canvas -- easy to click the button and
// not notice anything changed if it's off-screen. Scroll it into view and
// flash the border so the update is unmistakable.
function flashKgResult(el) {
  el.scrollIntoView({ behavior: "smooth", block: "nearest" });
  el.style.transition = "none";
  el.style.boxShadow = `0 0 0 2px ${cssVar("--accent")}`;
  el.style.borderRadius = "10px";
  requestAnimationFrame(() => {
    el.style.transition = "box-shadow 0.6s ease";
    el.style.boxShadow = "0 0 0 0 transparent";
  });
}

async function kgQueryRelated(nodeId) {
  const data = await getJSON(`/api/knowledge-graph/related?node_id=${encodeURIComponent(nodeId)}`);
  renderKgQueryResult(`Direct connections of ${KG_STATE.nodes[nodeId]?.label || nodeId}`, data.related, [
    { key: "relation", label: "Relation" },
    { key: "direction", label: "Direction" },
    { key: "label", label: "Node" },
    { key: "type", label: "Type" },
  ]);
}

async function renderSharedMechanism() {
  const el = document.getElementById("kgSharedMechanism");
  const survived = (STATE.hypotheses || []).find((h) => h.verdict === "SURVIVED");
  if (!survived) {
    el.innerHTML = "";
    return;
  }
  const data = await getJSON(`/api/knowledge-graph/shared-mechanism?hypothesis_id=${survived.hypothesis_id}`);
  const rows = data.shared_mechanism || [];
  const body = rows
    .map((r) => `<tr><td>${humanizeIdentifier(r.label)}</td><td style="color:${cssVar(kgVerdictColorVar(r.verdict))}">${r.verdict}</td><td>${r.test_archetype}</td><td>${r.same_archetype ? "yes" : "no"}</td><td>${r.shared_dimensions.join(", ") || "&mdash;"}</td></tr>`)
    .join("");
  el.innerHTML = `<p class="subtext" style="margin-bottom:6px"><strong>What shares a mechanism-shape with the currently-surviving hypothesis (${humanizeIdentifier(survived.hypothesis_id)})?</strong></p>
    <div class="table-scroll"><table class="evidence-table"><thead><tr><th>Hypothesis</th><th>Verdict</th><th>Archetype</th><th>Same archetype?</th><th>Shared dimensions</th></tr></thead><tbody>${body || '<tr><td colspan="5" class="subtext">None share a dimension or archetype.</td></tr>'}</tbody></table></div>`;
}

async function initKnowledgeGraph() {
  const graph = await getJSON("/api/knowledge-graph");
  initKnowledgeGraphDiagram(graph);
  await renderSharedMechanism();

  async function withButtonBusy(btn, fn) {
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Loading…";
    try {
      await fn();
    } catch (e) {
      renderKgQueryResult("Error", null, []);
      document.getElementById("kgQueryResult").innerHTML = `<p class="subtext" style="color:var(--killed)">Failed to load: ${e.message}</p>`;
    } finally {
      btn.disabled = false;
      btn.textContent = original;
    }
  }

  document.getElementById("kgRelatedBtn").addEventListener("click", (e) =>
    withButtonBusy(e.currentTarget, () => kgQueryRelated(document.getElementById("kgNodeSelect").value))
  );

  document.getElementById("kgBlastBtn").addEventListener("click", (e) =>
    withButtonBusy(e.currentTarget, async () => {
      const nodeId = document.getElementById("kgNodeSelect").value;
      const data = await getJSON(`/api/knowledge-graph/blast-radius?node_id=${encodeURIComponent(nodeId)}&max_depth=2`);
      renderKgQueryResult(`Blast radius of ${KG_STATE.nodes[nodeId]?.label || nodeId} (up to 2 hops)`, data.blast_radius, [
        { key: "hops", label: "Hops" },
        { key: "via_relation", label: "Via" },
        { key: "label", label: "Node" },
        { key: "type", label: "Type" },
      ]);
    })
  );
}

// ==================================================================== init
async function loadInvestigationTag() {
  const data = await getJSON(`/api/kpi-series?region=${INVESTIGATION.region}&kpi=${INVESTIGATION.kpi}`);
  const tag = document.getElementById("investigationTag");
  tag.textContent = data.changepoint_week
    ? `Investigation: ${INVESTIGATION.region} · ${humanizeIdentifier(INVESTIGATION.kpi)}, Week ${data.changepoint_week} (${fmtPct(data.business_impact_pct)})`
    : `Investigation: ${INVESTIGATION.region} · ${humanizeIdentifier(INVESTIGATION.kpi)}`;
  tag.title = "The hypothesis testing below is always about this one event, regardless of the Region/KPI selector above -- it's the only movement that ever cleared the materiality gate and triggered root-cause analysis.";
}

// ==================================================================== LLM backend settings
async function loadLlmConfigIntoForm() {
  const cfg = await getJSON("/api/llm-config");
  document.querySelector(`input[name="llmBackend"][value="${cfg.backend}"]`).checked = true;
  document.getElementById("openrouterFields").style.display = cfg.backend === "openrouter" ? "" : "none";
  document.getElementById("openrouterModelInput").value = cfg.openrouter_model || "";
  document.getElementById("openrouterKeyInput").placeholder = cfg.has_api_key ? "sk-or-... (already set -- leave blank to keep it)" : "sk-or-...";
  document.getElementById("llmConfigStatus").textContent =
    cfg.backend === "local"
      ? "Active: Local GPU"
      : `Active: OpenRouter (${cfg.openrouter_model})${cfg.has_api_key ? "" : " -- no API key set yet"}`;
  return cfg;
}

function initLlmSettings() {
  loadLlmConfigIntoForm();

  document.querySelectorAll('input[name="llmBackend"]').forEach((radio) => {
    radio.addEventListener("change", (e) => {
      document.getElementById("openrouterFields").style.display = e.target.value === "openrouter" ? "" : "none";
    });
  });

  document.getElementById("saveLlmConfigBtn").addEventListener("click", async () => {
    const btn = document.getElementById("saveLlmConfigBtn");
    const backend = document.querySelector('input[name="llmBackend"]:checked').value;
    const model = document.getElementById("openrouterModelInput").value.trim();
    const key = document.getElementById("openrouterKeyInput").value.trim();
    const body = { backend, openrouter_model: model || null };
    if (key) body.api_key = key; // never send blank -- blank means "leave the stored key alone"

    btn.disabled = true;
    btn.textContent = "Saving...";
    try {
      await fetch("/api/llm-config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      document.getElementById("openrouterKeyInput").value = "";
      await loadLlmConfigIntoForm();
    } catch (e) {
      document.getElementById("llmConfigStatus").textContent = `Error saving: ${e.message}`;
    } finally {
      btn.disabled = false;
      btn.textContent = "Save settings";
    }
  });

  document.getElementById("runLlmGenerationBtn").addEventListener("click", async () => {
    const btn = document.getElementById("runLlmGenerationBtn");
    const resultEl = document.getElementById("llmGenerationResult");
    const backend = document.querySelector('input[name="llmBackend"]:checked').value;
    btn.disabled = true;
    btn.textContent = backend === "local" ? "Running (first local run can take minutes -- model download + warmup)..." : "Running...";
    resultEl.innerHTML = "";
    try {
      const res = await fetch("/api/llm-generate/run", { method: "POST" });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);

      const rows = data.generated
        .map((g) => {
          const status = g.accepted ? `<span class="verdict-badge survived">ACCEPTED</span>` : `<span class="verdict-badge killed">REJECTED</span>`;
          return `<div class="brief-line">
            <span class="brief-label">Cluster ${g.cluster_id} &mdash; ${g.top_terms.slice(0, 4).join(", ")}</span>
            <div>${status} ${g.predicate ? `<strong>${humanizeIdentifier(g.predicate.hypothesis_id)}</strong>: ${g.predicate.mechanism}` : g.reason}</div>
            <div class="subtext small">${g.prompt_tokens} in / ${g.completion_tokens} out tokens, ${Math.round(g.latency_ms)}ms${g.cache_hit ? " -- cache hit, no fresh call made" : ""}</div>
          </div>`;
        })
        .join("");
      resultEl.innerHTML = `
        <div class="info-note" style="margin-bottom:10px">Backend: <strong>${data.backend}</strong> &mdash; ${data.n_accepted}/${data.n_candidates} candidate topic(s) produced an accepted predicate, now adjudicated through the same L5 pipeline as the templated fixtures. Hypotheses, adversarial-challenge, and telemetry panels above have been refreshed.</div>
        ${rows || '<p class="subtext">No candidate topics to generate for.</p>'}
      `;

      // this run wrote new verdicts/telemetry into the ledger -- refresh
      // every panel that reads from it so the dashboard reflects what just happened
      const [hypData, telemetry, adversarial] = await Promise.all([
        getJSON("/api/hypotheses"),
        getJSON("/api/telemetry"),
        getJSON("/api/adversarial-challenges"),
      ]);
      STATE.hypotheses = hypData.templated;
      renderHypothesisCards(hypData.templated);
      renderTelemetry(telemetry);
      renderAdversarial(adversarial);
      await renderBrief(STATE.role);
    } catch (e) {
      resultEl.innerHTML = `<p class="subtext" style="color:var(--killed)">Run failed: ${e.message}</p>`;
    } finally {
      btn.disabled = false;
      btn.textContent = "Run live LLM generation now";
    }
  });
}

async function init() {
  initTheme();

  const [, , hypData, action, evidence, telemetry, methods, adversarial, priorities, contradictions, calibration, drift, alerts] = await Promise.all([
    loadKpiPanel(STATE.region, STATE.kpi),
    loadInvestigationTag(),
    getJSON("/api/hypotheses"),
    getJSON("/api/action-recommendation"),
    getJSON("/api/evidence"),
    getJSON("/api/telemetry"),
    getJSON("/api/methods-breakdown"),
    getJSON("/api/adversarial-challenges"),
    getJSON("/api/priorities"),
    getJSON("/api/contradictions"),
    getJSON("/api/calibration-demo"),
    getJSON("/api/drift"),
    getJSON("/api/alerts"),
  ]);

  STATE.hypotheses = hypData.templated;
  renderHypothesisCards(hypData.templated);

  STATE.action = action;
  renderAction(action);

  renderFreshness(evidence.reconciliation);
  renderTelemetry(telemetry);
  renderMethodsBreakdown(methods);
  renderAdversarial(adversarial);
  renderPriorities(priorities);
  renderContradiction(contradictions);
  renderCalibration(calibration);
  renderDrift(drift);
  renderDomainCheck();
  renderEntitlementLog();
  renderDeliveryLog();
  initKnowledgeGraph();
  renderAlerts(alerts);
  renderOverviewGrid();

  await renderBrief(STATE.role);
  STATE._initialized = true;

  document.getElementById("regionSelect").value = STATE.region;
  document.getElementById("kpiSelect").value = STATE.kpi;
  document.getElementById("regionSelect").addEventListener("change", async (e) => {
    STATE.region = e.target.value;
    await loadKpiPanel(STATE.region, STATE.kpi);
  });
  document.getElementById("kpiSelect").addEventListener("change", async (e) => {
    STATE.kpi = e.target.value;
    await loadKpiPanel(STATE.region, STATE.kpi);
  });

  document.querySelectorAll(".persona-tab").forEach((tab) => {
    tab.addEventListener("click", async () => {
      document.querySelectorAll(".persona-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      STATE.role = tab.dataset.role;
      await renderBrief(STATE.role);
    });
  });

  initLlmSettings();

  document.getElementById("feedbackBtn").addEventListener("click", async () => {
    const btn = document.getElementById("feedbackBtn");
    btn.disabled = true;
    btn.textContent = "Running independent re-test...";
    const resultEl = document.getElementById("feedbackResult");
    try {
      const params = new URLSearchParams({
        original_hypothesis_id: "h_rep_attrition",
        analyst_role: "ops_manager_west",
        correction_text: "I don't think it's really attrition -- feels like general demand softness to me.",
      });
      const res = await fetch(`/api/feedback?${params}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          hypothesis_id: "h_general_softness_recheck",
          mechanism: "General regional demand softness (not attrition specifically) would show up as a comparable decline across ALL West reps, including the ones who kept their accounts.",
          test_archetype: "placebo",
          treatment: { dim: "rep_id", in: ["W1", "W2", "W3", "W4"] },
          control: { dim: "rep_id", in: ["W5", "W6"] },
          outcome: { metric: "revenue", expect: "decline" },
          temporal: { cause_onset: "2025-08-04", kpi_onset: "2025-08-11" },
          refutes_if: {
            condition: "control_group_effect_size >= 0.6 * treatment_effect_size",
            rationale: "If general softness (not attrition specifically) were the cause, staying reps' accounts should have declined comparably too.",
          },
        }),
      });
      const data = await res.json();
      resultEl.className = "feedback-result show";
      resultEl.innerHTML = `<strong>${humanizeIdentifier(data.counter_hypothesis_id)}</strong> &rarr; <span class="verdict-badge ${verdictClass(data.counter_verdict)}">${data.counter_verdict}</span><br><br>${data.note}`;
      renderContradiction({ contradiction: data.contradiction });
    } catch (e) {
      resultEl.className = "feedback-result show";
      resultEl.textContent = `Error: ${e.message}`;
    } finally {
      btn.disabled = false;
      btn.textContent = "Submit skeptical re-check";
    }
  });

  window.addEventListener("resize", () => {
    if (kpiChartInstance) kpiChartInstance.resize();
    if (cfChartInstance) cfChartInstance.resize();
  });
}

init().catch((e) => {
  console.error(e);
  document.body.insertAdjacentHTML("beforeend", `<div style="padding:20px;color:#e05263">Failed to load: ${e.message}. Make sure you've run <code>uv run python run_pipeline.py</code> first.</div>`);
});
