const API = "";

let STATE = {
  hypotheses: [],
  action: null,
  region: "West",
  role: "ops_manager_west",
};

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

// ---------------------------------------------------------------- KPI chart
function drawKpiChart(canvas, series, changepointWeek) {
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight;
  canvas.width = w * dpr; canvas.height = h * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  const padL = 46, padR = 12, padT = 12, padB = 24;
  const values = series.map((d) => d.value);
  const min = Math.min(...values) * 0.95, max = Math.max(...values) * 1.05;
  const xFor = (i) => padL + (i / (series.length - 1)) * (w - padL - padR);
  const yFor = (v) => padT + (1 - (v - min) / (max - min)) * (h - padT - padB);

  // gridlines + y labels
  ctx.strokeStyle = "#232a3a";
  ctx.fillStyle = "#5b6478";
  ctx.font = "10px sans-serif";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 3; i++) {
    const v = min + (i / 3) * (max - min);
    const y = yFor(v);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
    ctx.fillText(`$${Math.round(v / 1000)}k`, 4, y + 3);
  }

  // changepoint marker
  if (changepointWeek) {
    const idx = series.findIndex((d) => d.week === changepointWeek);
    if (idx >= 0) {
      const x = xFor(idx);
      ctx.strokeStyle = "#e0a840";
      ctx.setLineDash([4, 3]);
      ctx.beginPath(); ctx.moveTo(x, padT); ctx.lineTo(x, h - padB); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = "#e0a840";
      ctx.font = "10px sans-serif";
      ctx.fillText(`week ${changepointWeek} changepoint`, x + 4, padT + 10);
    }
  }

  // line
  ctx.strokeStyle = "#6d8bff";
  ctx.lineWidth = 2;
  ctx.beginPath();
  series.forEach((d, i) => {
    const x = xFor(i), y = yFor(d.value);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();

  // x labels (every ~8 weeks)
  ctx.fillStyle = "#5b6478";
  series.forEach((d, i) => {
    if (i % 8 === 0) ctx.fillText(`w${d.week}`, xFor(i) - 6, h - 6);
  });
}

// ---------------------------------------------------------------- hypothesis cards
function renderHypothesisCards(templated) {
  const container = document.getElementById("hypothesisCards");
  container.innerHTML = "";
  templated.forEach((h) => {
    const card = document.createElement("div");
    card.className = `card ${verdictClass(h.verdict).toLowerCase()}`;
    card.innerHTML = `
      <div class="card-top">
        <div class="card-title">${h.hypothesis_id}</div>
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
      </div>
    `;
    card.addEventListener("click", () => card.classList.toggle("open"));
    container.appendChild(card);
  });
}

// ---------------------------------------------------------------- action panel
function renderAction(data) {
  const el = document.getElementById("actionContent");
  if (!data.has_action) {
    el.innerHTML = `<div class="subtext">No hypothesis survived all applicable tests -- no action recommended yet. See the INCONCLUSIVE card above for what data would resolve it.</div>`;
    return;
  }
  const a = data.action;
  el.innerHTML = `<div class="action-grid">
    <div class="k">Driver</div><div>${a.driver}</div>
    <div class="k">Lever</div><div>${a.controllable_lever}</div>
    <div class="k">Action</div><div>${a.action}</div>
    <div class="k">Expected impact</div><div>${a.expected_impact}</div>
    <div class="k">Owner</div><div>${a.owner}</div>
    <div class="k">Confidence</div><div>${a.confidence}</div>
    <div class="k">Monitoring</div><div>${a.monitoring_plan}</div>
  </div>`;
}

// ---------------------------------------------------------------- freshness table
function renderFreshness(recon) {
  const el = document.getElementById("freshnessTable");
  const rows = recon.source_freshness.map((s) => `
    <tr>
      <td>${s.source}</td>
      <td>${s.refresh_cadence}</td>
      <td>${s.covered_through}</td>
      <td class="${s.staleness_days > 5 ? "stale" : ""}">${s.staleness_days}d</td>
    </tr>`).join("");
  el.innerHTML = `<table class="evidence-table">
    <thead><tr><th>Source</th><th>Cadence</th><th>Covered through</th><th>Staleness</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>
  <p class="subtext" style="margin-top:10px">Revenue source agreement: <strong>${recon.revenue_source_agreement_claim.verdict}</strong> &mdash; ${recon.revenue_source_agreement_claim.explanation || "sources agree within tolerance."}</p>`;
}

// ---------------------------------------------------------------- telemetry
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

// ---------------------------------------------------------------- persona brief
async function renderBrief(role) {
  const briefEl = document.getElementById("briefContent");
  const noteEl = document.getElementById("roleNote");
  const survived = STATE.hypotheses.find((h) => h.verdict === "SURVIVED");
  const killed = STATE.hypotheses.filter((h) => h.verdict === "KILLED");
  const inconclusive = STATE.hypotheses.filter((h) => h.verdict === "INCONCLUSIVE");

  const ent = await getJSON(`/api/entitlement-check?role=${role}&dim=rep_id&region=${STATE.region}`);

  if (role === "ops_manager_west") {
    briefEl.innerHTML = `
      <div class="brief-line"><span class="brief-label">Movement</span><div>${STATE.kpiNarrative || ""}</div></div>
      <div class="brief-line"><span class="brief-label">Cause</span><div>${survived ? survived.hypothesis_id : "none confirmed"}</div></div>
      <div class="brief-line"><span class="brief-label">Ruled out</span><div>${killed.map((h) => h.hypothesis_id).join(", ") || "none"}</div></div>
      <div class="brief-line"><span class="brief-label">Inconclusive</span><div>${inconclusive.map((h) => h.hypothesis_id).join(", ") || "none"}</div></div>
      ${STATE.action && STATE.action.has_action ? `<div class="brief-line"><span class="brief-label">Action</span><div>${STATE.action.action.action}</div></div>` : ""}
    `;
  } else {
    briefEl.innerHTML = `
      <div class="brief-line"><span class="brief-label">Headline</span><div>${STATE.kpiNarrative || ""}</div></div>
      ${survived ? `<div class="brief-line"><span class="brief-label">Cause</span><div>${survived.hypothesis_id.replace("h_", "").replace(/_/g, " ")}</div></div>` : ""}
      <div class="brief-line"><span class="brief-label">Tested &amp; ruled out</span><div>${killed.length} alternative(s)${inconclusive.length ? `, ${inconclusive.length} inconclusive` : ""}</div></div>
      ${STATE.action && STATE.action.has_action ? `<div class="brief-line"><span class="brief-label">Next step</span><div>${STATE.action.action.action}</div></div>` : ""}
    `;
  }

  noteEl.className = `role-note ${ent.allowed ? "allowed" : "denied"}`;
  noteEl.textContent = ent.allowed
    ? `Rep-level account detail: visible to this role.`
    : `Rep-level account detail withheld: ${ent.reason}`;
}

// ---------------------------------------------------------------- init
async function init() {
  const [kpi, hypData, action, evidence, telemetry] = await Promise.all([
    getJSON(`/api/kpi-series?region=${STATE.region}&kpi=revenue`),
    getJSON("/api/hypotheses"),
    getJSON("/api/action-recommendation"),
    getJSON("/api/evidence"),
    getJSON("/api/telemetry"),
  ]);

  document.getElementById("kpiValue").textContent = fmtPct(kpi.business_impact_pct);
  document.getElementById("kpiBadge").textContent = kpi.gate_passed ? "GATE: PASS" : "GATE: NOISE";
  document.getElementById("kpiBadge").className = `badge ${kpi.gate_passed ? "pass" : ""}`;
  document.getElementById("kpiNarrative").textContent = kpi.narrative;
  STATE.kpiNarrative = kpi.narrative;
  const chartEl = document.getElementById("kpiChart");
  drawKpiChart(chartEl, kpi.series, kpi.changepoint_week);
  // canvas rasterizes at draw time, not CSS size -- without a redraw on
  // resize it keeps whatever width the container happened to be laid out
  // at on first draw (observed stretched/blurry at 230px raster vs 582px
  // CSS display width when the pane hadn't finished sizing yet).
  let resizeTimer;
  const redraw = () => drawKpiChart(chartEl, kpi.series, kpi.changepoint_week);
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(redraw, 120);
  });
  // also redraw once shortly after initial load: the very first draw can
  // land before the host page/pane finishes settling its own layout,
  // which the resize listener above won't catch since nothing "resized"
  // from the browser's point of view.
  setTimeout(redraw, 300);

  STATE.hypotheses = hypData.templated;
  renderHypothesisCards(hypData.templated);

  STATE.action = action;
  renderAction(action);

  renderFreshness(evidence.reconciliation);
  renderTelemetry(telemetry);

  await renderBrief(STATE.role);

  document.querySelectorAll(".persona-tab").forEach((tab) => {
    tab.addEventListener("click", async () => {
      document.querySelectorAll(".persona-tab").forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      STATE.role = tab.dataset.role;
      await renderBrief(STATE.role);
    });
  });

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
      resultEl.innerHTML = `<strong>${data.counter_hypothesis_id}</strong> &rarr; <span class="verdict-badge ${verdictClass(data.counter_verdict)}">${data.counter_verdict}</span><br><br>${data.note}`;
    } catch (e) {
      resultEl.className = "feedback-result show";
      resultEl.textContent = `Error: ${e.message}`;
    } finally {
      btn.disabled = false;
      btn.textContent = "Submit skeptical re-check";
    }
  });
}

init().catch((e) => {
  console.error(e);
  document.body.insertAdjacentHTML("beforeend", `<div style="padding:20px;color:#e05263">Failed to load: ${e.message}. Make sure you've run <code>uv run python run_pipeline.py</code> first.</div>`);
});
