const API = "";

let STATE = {
  hypotheses: [],
  action: null,
  region: "West",
  role: "regional_vp",
};

const ROLE_LABELS = { regional_vp: "Leader", ops_manager_west: "Manager", platform_engineer: "Engineer" };

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

// ---------------------------------------------------------------- action panel
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
    <div class="k">Driver</div><div>${a.driver}</div>
    <div class="k">Lever</div><div>${a.controllable_lever}</div>
    <div class="k">Action</div><div>${a.action}</div>
    <div class="k">Expected impact</div><div>${a.expected_impact}</div>
    <div class="k">Owner</div><div>${a.owner}</div>
    <div class="k">Confidence</div><div>${a.confidence}</div>
    <div class="k">Monitoring</div><div>${a.monitoring_plan}</div>
    ${constraintHtml}
  </div>`;
}

// ---------------------------------------------------------------- priority queue
function renderPriorities(priorities) {
  const el = document.getElementById("priorityQueue");
  if (!priorities || priorities.length === 0) {
    el.innerHTML = `<div class="subtext">No material movements currently in the queue.</div>`;
    return;
  }
  el.innerHTML = priorities
    .map((p) => {
      const sameEvent = p.likely_same_event_as.length ? `<span class="subtext"> (likely same event as: ${p.likely_same_event_as.join(", ")})</span>` : "";
      return `<div class="brief-line">
        <span class="brief-label">#${p.rank} ${p.kpi} (${p.region})</span>
        <div>impact ${fmtPct(p.business_impact_pct)} ($${Math.abs(p.business_impact_abs_usd).toLocaleString(undefined, { maximumFractionDigits: 0 })}), confidence ${p.changepoint_posterior_recent.toFixed(2)}, week ${p.changepoint_week}${sameEvent}</div>
      </div>`;
    })
    .join("");
}

// ---------------------------------------------------------------- contradictory evidence
function renderContradiction(data) {
  const panel = document.getElementById("contradictionPanel");
  const el = document.getElementById("contradictionContent");
  const c = data.contradiction;
  if (!c) {
    panel.style.display = "none";
    return;
  }
  panel.style.display = "";
  const typeLabel = c.verdict_type === "SAME_EVIDENCE_RETEST" ? "Same evidence, re-tested" : "Independent contradiction";
  const color = c.verdict_type === "SAME_EVIDENCE_RETEST" ? "var(--text-dim)" : "var(--inconclusive)";
  el.innerHTML = `
    <div class="brief-line"><span class="brief-label" style="color:${color}">${typeLabel}</span><div>${c.explanation}</div></div>
    ${c.survived_hypotheses.map((h) => `<div class="subtext">${h.hypothesis_id}: sql=(${h.treatment_sql_hash || "n/a"}, ${h.control_sql_hash || "n/a"})</div>`).join("")}
  `;
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
  const missingRows = (recon.missing_data_rates || [])
    .map((m) => `<tr><td>${m.join}</td><td>${m.matched_rows}/${m.expected_rows}</td><td class="${m.missing_pct > 5 ? "stale" : ""}">${m.missing_pct}%</td></tr>`)
    .join("");
  el.innerHTML = `<table class="evidence-table">
    <thead><tr><th>Source</th><th>Cadence</th><th>Covered through</th><th>Staleness</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>
  <p class="subtext" style="margin-top:10px">Revenue source agreement: <strong>${recon.revenue_source_agreement_claim.verdict}</strong> &mdash; ${recon.revenue_source_agreement_claim.explanation || "sources agree within tolerance."}</p>
  ${recon.rep_attribution_bounds_claim ? `<p class="subtext" style="margin-top:6px">Rep-attribution bounds: <strong>${recon.rep_attribution_bounds_claim.verdict}</strong> &mdash; ${recon.rep_attribution_bounds_claim.explanation || ""}</p>` : ""}
  ${missingRows ? `<p class="subtext" style="margin-top:14px;margin-bottom:6px">Missing-data rate per cross-source join (an inner/left join silently drops or NaNs unmatched rows -- this quantifies that instead):</p>
  <table class="evidence-table"><thead><tr><th>Join</th><th>Matched</th><th>Missing</th></tr></thead><tbody>${missingRows}</tbody></table>` : ""}`;
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
// Three renderers over the SAME ledger data (STATE.hypotheses / STATE.action)
// -- personalization here is a filtering/formatting decision, not a
// re-analysis. Only the entitlement note comes from a live API call, since
// that's a real access-control decision (engine.l4_compiler.check_entitlement),
// not something the frontend should ever decide on its own.
async function renderBrief(role) {
  const briefEl = document.getElementById("briefContent");
  const noteEl = document.getElementById("roleNote");
  const survived = STATE.hypotheses.find((h) => h.verdict === "SURVIVED");
  const killed = STATE.hypotheses.filter((h) => h.verdict === "KILLED");
  const inconclusive = STATE.hypotheses.filter((h) => h.verdict === "INCONCLUSIVE");

  const ent = await getJSON(`/api/entitlement-check?role=${role}&dim=rep_id&region=${STATE.region}`);

  if (role === "regional_vp") {
    // LEADER: headline + one action + confidence, no statistical detail.
    briefEl.innerHTML = `
      <div class="brief-line"><span class="brief-label">Headline</span><div>${STATE.kpiNarrative || ""}</div></div>
      ${survived ? `<div class="brief-line"><span class="brief-label">Cause</span><div>${survived.hypothesis_id.replace("h_", "").replace(/_/g, " ")}</div></div>` : ""}
      <div class="brief-line"><span class="brief-label">Tested &amp; ruled out</span><div>${killed.length} alternative(s)${inconclusive.length ? `, ${inconclusive.length} inconclusive` : ""}</div></div>
      ${STATE.action && STATE.action.has_action ? `<div class="brief-line"><span class="brief-label">Next step</span><div>${STATE.action.action.action}</div></div>` : ""}
      ${STATE.action && STATE.action.has_action ? `<div class="brief-line"><span class="brief-label">Confidence</span><div>${STATE.action.action.confidence.split(" -- ")[0]}</div></div>` : ""}
    `;
  } else if (role === "ops_manager_west") {
    // MANAGER: full evidence chain for their own region, tactical action framing.
    briefEl.innerHTML = `
      <div class="brief-line"><span class="brief-label">Movement</span><div>${STATE.kpiNarrative || ""}</div></div>
      <div class="brief-line"><span class="brief-label">Cause</span><div>${survived ? survived.hypothesis_id : "none confirmed"}</div></div>
      <div class="brief-line"><span class="brief-label">Ruled out</span><div>${killed.map((h) => `${h.hypothesis_id} (${h.test_archetype})`).join(", ") || "none"}</div></div>
      <div class="brief-line"><span class="brief-label">Inconclusive</span><div>${inconclusive.map((h) => h.hypothesis_id).join(", ") || "none"}</div></div>
      ${STATE.action && STATE.action.has_action ? `<div class="brief-line"><span class="brief-label">Action</span><div>${STATE.action.action.action}</div></div>` : ""}
      ${STATE.action && STATE.action.has_action ? `<div class="brief-line"><span class="brief-label">Owner</span><div>${STATE.action.action.owner}</div></div>` : ""}
    `;
  } else {
    // ENGINEER: full statistical + methodological audit.
    const rows = STATE.hypotheses
      .map((h) => {
        const stats = h.did_effect !== null && h.did_effect !== undefined
          ? `effect=${fmtPct(h.did_effect)} p_raw=${h.did_pvalue_raw?.toFixed(4) ?? "n/a"} p_BH=${h.did_pvalue_bh?.toFixed(4) ?? "n/a"} MDE=${fmtPct(h.mde)} floor=${fmtPct(h.plausible_effect)} pretrends_p=${h.parallel_trends_pvalue?.toFixed(3) ?? "n/a"}`
          : "(precedence test -- no DiD regression, see notes)";
        const hashLine = h.treatment_sql_hash ? `sql: ${h.treatment_sql_hash} / ${h.control_sql_hash}` : "sql: n/a (precedence test)";
        return `<div class="brief-line"><span class="brief-label">${h.hypothesis_id} [${h.verdict}]</span><div style="font-family:var(--mono);font-size:11px">${stats}</div><div style="font-family:var(--mono);font-size:11px;color:var(--text-faint)">${hashLine}</div></div>`;
      })
      .join("");
    briefEl.innerHTML = `${rows}<div class="brief-line" style="margin-top:10px"><span class="brief-label">Note</span><div>See Methods Breakdown below for which method category (statistics / SQL / causal inference / LLM / etc.) produced each number, and why.</div></div>`;
  }

  noteEl.className = `role-note ${ent.allowed ? "allowed" : "denied"}`;
  noteEl.textContent = ent.allowed
    ? `Rep-level account detail: visible to this role.`
    : `Rep-level account detail withheld: ${ent.reason}`;
}

// ---------------------------------------------------------------- methods breakdown
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
  el.innerHTML = `<table class="evidence-table">
    <thead><tr><th>Stage</th><th>Method category</th><th>Method</th><th>Quantitative source of truth?</th></tr></thead>
    <tbody>${rows}</tbody>
  </table>
  <p class="subtext" style="margin-top:10px">${data.llm_stages_are_never_quantitative ? "Structurally checked: no LLM-driven stage is marked as a quantitative source of truth." : ""}</p>`;
}

// ---------------------------------------------------------------- counterfactual chart
function drawCounterfactualChart(canvas, cf) {
  const ctx = canvas.getContext("2d");
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth, h = canvas.clientHeight || 200;
  canvas.width = w * dpr; canvas.height = h * dpr;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  const allVals = [...cf.scenario_no_action, ...cf.scenario_recovery].flatMap((d) => [d.ci_low, d.ci_high]);
  const min = Math.min(...allVals) * 0.98, max = Math.max(...allVals) * 1.02;
  const padL = 46, padR = 12, padT = 12, padB = 20;
  const n = cf.scenario_no_action.length;
  const xFor = (i) => padL + (i / Math.max(n - 1, 1)) * (w - padL - padR);
  const yFor = (v) => padT + (1 - (v - min) / (max - min)) * (h - padT - padB);

  ctx.strokeStyle = "#232a3a";
  ctx.fillStyle = "#5b6478";
  ctx.font = "10px sans-serif";
  for (let i = 0; i <= 2; i++) {
    const v = min + (i / 2) * (max - min);
    const y = yFor(v);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
    ctx.fillText(`$${Math.round(v / 1000)}k`, 4, y + 3);
  }

  const drawSeries = (series, color, dashed) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.setLineDash(dashed ? [5, 3] : []);
    ctx.beginPath();
    series.forEach((d, i) => {
      const x = xFor(i), y = yFor(d.value);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
    ctx.setLineDash([]);
    // CI band
    ctx.fillStyle = color + "22";
    ctx.beginPath();
    series.forEach((d, i) => ctx.lineTo(xFor(i), yFor(d.ci_high)));
    for (let i = series.length - 1; i >= 0; i--) ctx.lineTo(xFor(i), yFor(series[i].ci_low));
    ctx.closePath();
    ctx.fill();
  };

  drawSeries(cf.scenario_no_action, "#8993a8", true);
  drawSeries(cf.scenario_recovery, "#34c78e", false);

  ctx.fillStyle = "#5b6478";
  cf.scenario_no_action.forEach((d, i) => ctx.fillText(`w${d.week}`, xFor(i) - 6, h - 4));

  ctx.fillStyle = "#8993a8"; ctx.fillText("- - if no action", padL, padT + 8);
  ctx.fillStyle = "#34c78e"; ctx.fillText("— if action succeeds", padL, padT + 20);
}

function renderCounterfactual(cf) {
  document.getElementById("counterfactualAssumption").textContent = cf.assumption;
  const canvas = document.getElementById("counterfactualChart");
  const redraw = () => drawCounterfactualChart(canvas, cf);
  redraw();
  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(redraw, 120);
  });
  setTimeout(redraw, 300);
}

// ---------------------------------------------------------------- calibration demo
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
    <table class="evidence-table" style="margin-bottom:12px">
      <thead><tr><th>Confidence bucket</th><th>n</th><th>Stated</th><th>Observed</th><th>Gap</th></tr></thead>
      <tbody>${reliabilityRows}</tbody>
    </table>
    <div class="subtext"><span class="brief-label">Isotonic recalibration curve</span><br>${isoRows}</div>
  `;
}

// ---------------------------------------------------------------- drift monitoring
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
      <table class="evidence-table"><thead><tr><th>Metric</th><th>Baseline n</th><th>Current n</th><th>Baseline mean</th><th>Current mean</th><th>PSI</th><th>Verdict</th></tr></thead><tbody>${rows}</tbody></table>`;
  } else {
    realHtml = `<div class="subtext">insufficient_history &mdash; ${real.n_baseline_runs} prior run(s), needs ${real.runs_needed ?? 5} more. Run the pipeline a few more times (<code>uv run python -m engine.l6_narrate_ledger</code>) to accrue real history.</div>`;
  }

  const demoRow = (label, d) => `<tr><td>${label}</td><td>${d.posterior_psi}</td><td style="color:${verdictColor(d.posterior_verdict)}">${d.posterior_verdict}</td><td>${d.effect_size_psi}</td><td style="color:${verdictColor(d.effect_size_verdict)}">${d.effect_size_verdict}</td></tr>`;
  el.innerHTML = `
    ${realHtml}
    <p class="subtext" style="margin-top:14px"><strong>${demo.label}</strong> &mdash; proves the PSI mechanism itself is correct:</p>
    <table class="evidence-table">
      <thead><tr><th>Case</th><th>Posterior PSI</th><th>Verdict</th><th>Effect-size PSI</th><th>Verdict</th></tr></thead>
      <tbody>
        ${demoRow("Control (current == baseline)", demo.control_case_same_distribution)}
        ${demoRow("Drift (current is shifted)", demo.drift_case_shifted_distribution)}
      </tbody>
    </table>
  `;
}

// ---------------------------------------------------------------- domain-level security check
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
  el.innerHTML = `<table class="evidence-table"><thead><tr><th>Role</th><th>KPI requested</th><th>Result</th><th>Reason</th></tr></thead><tbody>${rows}</tbody></table>`;
}

// ---------------------------------------------------------------- entitlement audit log
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
  el.innerHTML = `<table class="evidence-table"><thead><tr><th>When</th><th>Type</th><th>Role</th><th>Scope</th><th>Result</th><th>Reason</th></tr></thead><tbody>${rows}</tbody></table>`;
}

// ---------------------------------------------------------------- delivery-channel routing (simulated)
async function renderDeliveryLog() {
  const el = document.getElementById("deliveryLogContent");
  const data = await getJSON("/api/delivery-log?limit=20");
  if (!data.rows || data.rows.length === 0) {
    el.innerHTML = `<p class="subtext">No deliveries simulated yet -- run the pipeline (<code>uv run python -m engine.l6_narrate_ledger</code>).</p>`;
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
  el.innerHTML = `<table class="evidence-table"><thead><tr><th>Role</th><th>Persona</th><th>Channel</th><th>Urgency</th><th>Message preview</th></tr></thead><tbody>${rows}</tbody></table>`;
}

// ---------------------------------------------------------------- adversarial challenge
function renderAdversarial(data) {
  const panel = document.getElementById("adversarialPanel");
  const el = document.getElementById("adversarialContent");
  if (!data.challenges || data.challenges.length === 0) {
    panel.style.display = "none";
    return;
  }
  panel.style.display = "";
  el.innerHTML = data.challenges
    .map((c) => {
      const meaning = c.verdict === "SURVIVED"
        ? "The original conclusion is more contested than a single surviving test suggested -- both should be reviewed."
        : "The original conclusion held up against the strongest counter-case the model could construct.";
      return `<div class="brief-line">
        <span class="brief-label">${c.hypothesis_id}</span>
        <div><span class="verdict-badge ${verdictClass(c.verdict)}">${c.verdict}</span> ${c.reason}</div>
        <div class="subtext" style="margin-top:4px">${meaning}</div>
      </div>`;
    })
    .join("");
}

// ---------------------------------------------------------------- init
async function init() {
  const [kpi, hypData, action, evidence, telemetry, methods, counterfactual, adversarial, priorities, contradictions, calibration, drift] = await Promise.all([
    getJSON(`/api/kpi-series?region=${STATE.region}&kpi=revenue`),
    getJSON("/api/hypotheses"),
    getJSON("/api/action-recommendation"),
    getJSON("/api/evidence"),
    getJSON("/api/telemetry"),
    getJSON("/api/methods-breakdown"),
    getJSON(`/api/counterfactual?region=${STATE.region}`),
    getJSON("/api/adversarial-challenges"),
    getJSON("/api/priorities"),
    getJSON("/api/contradictions"),
    getJSON("/api/calibration-demo"),
    getJSON("/api/drift"),
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
  renderMethodsBreakdown(methods);
  renderCounterfactual(counterfactual);
  renderAdversarial(adversarial);
  renderPriorities(priorities);
  renderContradiction(contradictions);
  renderCalibration(calibration);
  renderDrift(drift);
  renderDomainCheck();
  renderEntitlementLog();
  renderDeliveryLog();

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
      renderContradiction({ contradiction: data.contradiction });
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
