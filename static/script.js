const API_BASE_URL = "";
const API_TIMEOUT_MS = 12000;

let planExecuted = false;
let lastExecutionResult = null;
let mapInstance = null;
let baseLayer = null;
let spaceLayer = null;

const ACTION_LABELS = {
  REROUTE_FLEET: "Move dumpers to an alternate pit",
  DEWATERING: "Pump water from the affected pit",
  PREVENTIVE_MAINTENANCE: "Inspect and service equipment",
  CONTINGENCY_LABOR: "Arrange additional workers",
  BLENDING: "Blend ore with available stockpile",
};

class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function apiFetch(path, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), API_TIMEOUT_MS);
  let res;
  try {
    res = await fetch(API_BASE_URL + path, {
      ...options,
      signal: controller.signal,
      headers: options.body
        ? { "Content-Type": "application/json", ...(options.headers || {}) }
        : options.headers,
    });
  } catch (err) {
    clearTimeout(timer);
    if (err && err.name === "AbortError") {
      throw new ApiError(0, "The request timed out. Check that the backend is running.");
    }
    throw new ApiError(0, "Cannot reach the backend. Check that the Flask server is running.");
  }
  clearTimeout(timer);
  if (!res.ok) {
    let message = `Request failed (HTTP ${res.status})`;
    try {
      const body = await res.json();
      if (body && body.message) message = body.message;
    } catch (_) { /* keep default message */ }
    throw new ApiError(res.status, message);
  }
  try {
    return await res.json();
  } catch (_) {
    throw new ApiError(502, "The backend returned an invalid response.");
  }
}

function fmtNum(value, digits = 0) {
  const n = Number(value);
  if (!isFinite(n)) return "N/A";
  return n.toLocaleString("en-IN", { maximumFractionDigits: digits });
}

function fmtTons(value) {
  const n = Number(value);
  return isFinite(n) ? `${fmtNum(n)} MT` : "N/A";
}

function setStatus(message, kind = "error") {
  const banner = document.getElementById("api-status-banner");
  if (!banner) return;
  if (!message) {
    banner.hidden = true;
    return;
  }
  banner.textContent = message;
  banner.className = `api-status-banner ${kind}`;
  banner.hidden = false;
}

function getControls() {
  return {
    rainfall_mm: parseFloat(document.getElementById("slider-rainfall").value),
    mtbf_hrs: parseFloat(document.getElementById("slider-mtbf").value),
    labor_drop_pct: parseFloat(document.getElementById("slider-labor").value),
    target_tonnage: parseInt(document.getElementById("input-target").value, 10),
  };
}

function updateControlBadges(controls) {
  const set = (id, text) => {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  };
  set("val-rainfall", `${controls.rainfall_mm.toFixed(1)} mm`);
  set("val-mtbf", `${controls.mtbf_hrs.toFixed(1)} hrs`);
  set("val-labor", `${Math.round(controls.labor_drop_pct)} %`);
  set("val-target-label", `${fmtNum(controls.target_tonnage)} MT`);
}

function updateClock() {
  const el = document.getElementById("system-time");
  if (el) el.textContent = `${new Date().toISOString().slice(11, 19)} UTC`;
}

function setButtonsLoading(loading) {
  const exec = document.getElementById("btn-execute-plan");
  const reset = document.getElementById("btn-reset-plan");
  if (exec) exec.disabled = loading || planExecuted;
  if (reset) reset.disabled = loading || !planExecuted;
}

function updateExecuteButtons() {
  const exec = document.getElementById("btn-execute-plan");
  const reset = document.getElementById("btn-reset-plan");
  if (exec) exec.disabled = planExecuted;
  if (reset) reset.disabled = !planExecuted;
}

function renderPrediction(data) {
  const prediction = data.prediction || {};
  const params = data.parameters || {};
  const shortfall = Number(prediction.shortfall_tons) || Number(prediction.shortfall_tonnage) || 0;
  const predicted = Number(prediction.predicted_output) || Number(prediction.predicted_tonnage) || 0;
  const target = Number(prediction.base_target) || Number(params.target_tonnage) || 0;
  const lossCr = Number(prediction.loss_crores) || 0;

  document.getElementById("hero-shortfall-title").textContent =
    shortfall > 0
      ? `SHORTFALL ALERT: ${fmtNum(shortfall)} MT DEFICIT PREDICTED`
      : "TARGET ON TRACK";
  document.getElementById("hero-loss-val").textContent = `₹${fmtNum(lossCr, 2)} Cr`;
  document.getElementById("hero-output-val").textContent = fmtTons(predicted);
  document.getElementById("hero-target-val").textContent = fmtTons(target);
  document.getElementById("hero-sim-state").textContent = data.simulation_state || "UNMITIGATED RISK";

  planExecuted = !!data.plan_executed;
  updateExecuteButtons();
}

function buildRecItem(opt) {
  const item = document.createElement("div");
  item.className = `rec-item rec-action ${opt.recommended ? "type-2" : "type-3"}`;

  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.className = "rec-checkbox";
  checkbox.dataset.action = opt.action_code || opt.action || "";
  checkbox.checked = opt.expected_recovery_tonnes > 0;
  item.appendChild(checkbox);

  const content = document.createElement("div");
  content.className = "rec-content";

  const title = document.createElement("div");
  title.className = "rec-title";
  title.textContent = opt.action_label || opt.action || "Corrective action";
  if (opt.recommended) {
    const tag = document.createElement("span");
    tag.className = "rec-tag";
    tag.textContent = "AI RECOMMENDED";
    title.appendChild(tag);
  }

  const desc = document.createElement("div");
  desc.className = "rec-desc";
  desc.textContent = opt.scenario_reason || opt.reason || "";

  content.appendChild(title);
  content.appendChild(desc);

  const gain = document.createElement("div");
  gain.className = "rec-gain";
  gain.textContent = `+${fmtNum(opt.expected_recovery_tonnes)} t`;

  item.appendChild(content);
  item.appendChild(gain);
  return item;
}

function getSelectedActions() {
  return Array.from(
    document.querySelectorAll("#recommendations-container input.rec-checkbox:checked"),
  )
    .map((el) => el.dataset.action)
    .filter(Boolean);
}

function buildExecMetric(label, value, kind) {
  const metric = document.createElement("div");
  metric.className = `exec-metric ${kind}`;
  const lbl = document.createElement("div");
  lbl.className = "exec-metric-label";
  lbl.textContent = label;
  const val = document.createElement("div");
  val.className = "exec-metric-value";
  val.textContent = value;
  metric.appendChild(lbl);
  metric.appendChild(val);
  return metric;
}

function renderExecutionResults(data) {
  const container = document.getElementById("recommendations-container");
  if (!container) return;

  const ex = data.execution || {};
  const recovered =
    Number(data.recovered_tonnage != null ? data.recovered_tonnage : ex.total_expected_recovery_tonnes) || 0;
  const remaining =
    Number(data.remaining_shortfall != null ? data.remaining_shortfall : ex.final_remaining_shortfall) || 0;
  const steps = ex.per_action_recovery || ex.action_sequence || [];

  container.innerHTML = "";

  const status = document.createElement("div");
  status.className = "exec-status";
  const glyph = document.createElement("span");
  glyph.className = "exec-status-glyph";
  glyph.textContent = "✓";
  const label = document.createElement("span");
  label.textContent = "PLAN EXECUTED";
  status.appendChild(glyph);
  status.appendChild(label);
  container.appendChild(status);

  const summary = document.createElement("div");
  summary.className = "exec-summary";
  summary.appendChild(buildExecMetric("RECOVERED", `+${fmtNum(recovered)} t`, "ok"));
  summary.appendChild(buildExecMetric("REMAINING SHORTFALL", `${fmtNum(remaining)} t`, "warn"));
  summary.appendChild(buildExecMetric("ACTIONS EXECUTED", `${steps.length}`, "info"));
  container.appendChild(summary);

  if (steps.length) {
    const title = document.createElement("div");
    title.className = "exec-breakdown-title";
    title.textContent = "RECOVERY BREAKDOWN";
    container.appendChild(title);

    const breakdown = document.createElement("div");
    breakdown.className = "recovery-breakdown";
    steps.forEach((step) => {
      const row = document.createElement("div");
      row.className = "recovery-row";

      const left = document.createElement("div");
      left.className = "recovery-row-left";
      const name = document.createElement("div");
      name.className = "recovery-name";
      name.textContent = step.action_label || ACTION_LABELS[step.action] || step.action;
      left.appendChild(name);
      if (step.scenario_reason) {
        const why = document.createElement("div");
        why.className = "recovery-reason";
        why.textContent = step.scenario_reason;
        left.appendChild(why);
      }
      row.appendChild(left);

      const value = document.createElement("div");
      value.className = "recovery-value";
      value.textContent = `+${fmtNum(step.expected_recovery_tonnes)} t`;
      row.appendChild(value);
      breakdown.appendChild(row);
    });
    container.appendChild(breakdown);
  }

  const pill = document.getElementById("gain-pill");
  if (pill) {
    pill.textContent = `+${fmtNum(recovered)} MT RECOVERED`;
    pill.className = "gain-pill exec-pill";
  }
}

function renderPrescriptive(data) {
  if (data && data.plan_executed && data.execution) {
    renderExecutionResults(data);
  } else {
    renderRecommendations(data);
  }
}

function renderRecommendations(data) {
  const rec = (data && data.recommendation) || {};
  const options = rec.options || [];
  const recoverable =
    Number(rec.recoverable_tonnage != null ? rec.recoverable_tonnage : rec.expected_recovery_tonnes) || 0;

  const pill = document.getElementById("gain-pill");
  if (pill) {
    pill.textContent = `+${fmtNum(recoverable)} MT RECOVERABLE`;
    pill.className = "gain-pill";
  }

  const container = document.getElementById("recommendations-container");
  if (!container) return;
  container.innerHTML = "";

  if (!options.length) {
    const item = document.createElement("div");
    item.className = "rec-item";
    const content = document.createElement("div");
    content.className = "rec-content";
    const title = document.createElement("div");
    title.className = "rec-title";
    title.textContent = "No corrective actions available";
    const desc = document.createElement("div");
    desc.className = "rec-desc";
    desc.textContent = "The engine could not generate recommendations for the current conditions.";
    content.appendChild(title);
    content.appendChild(desc);
    item.appendChild(content);
    container.appendChild(item);
    return;
  }

  options.forEach((opt) => container.appendChild(buildRecItem(opt)));
}

async function postPredictions() {
  return apiFetch("/api/predictions", {
    method: "POST",
    body: JSON.stringify(getControls()),
  });
}

async function loadPrescriptive() {
  const container = document.getElementById("recommendations-container");
  if (container) {
    container.innerHTML = "";
    const loading = document.createElement("div");
    loading.className = "rec-item rec-loading";
    loading.textContent = "Loading corrective actions…";
    container.appendChild(loading);
  }
  try {
    renderPrescriptive(await apiFetch("/api/prescriptive"));
  } catch (err) {
    if (container) {
      container.innerHTML = "";
      const msg = document.createElement("div");
      msg.className = "rec-item rec-loading";
      msg.textContent = "Prescriptive engine unavailable — showing static snapshot.";
      container.appendChild(msg);
    }
    setStatus(err.message || "Prescriptive engine unavailable.", "error");
  }
}

function renderXai(data) {
  const conf = Number(data.confidence_pct);
  document.getElementById("xai-conf-badge").textContent = isFinite(conf)
    ? `Model Conf: ${conf.toFixed(1)}%`
    : "Model Conf: --";

  const entries = Object.entries(data.attributions || {})
    .filter(([, v]) => isFinite(Number(v)))
    .sort((a, b) => Number(b[1]) - Number(a[1]));

  const rows = [
    { label: "xai-lbl-rain", value: "xai-val-rain", bar: "bar-rain" },
    { label: "xai-lbl-mtbf", value: "xai-val-mtbf", bar: "bar-mtbf" },
    { label: "xai-lbl-grade", value: "xai-val-grade", bar: "bar-grade" },
  ];
  rows.forEach((row, i) => {
    const entry = entries[i];
    const pct = entry ? Number(entry[1]) : 0;
    const label = document.getElementById(row.label);
    const value = document.getElementById(row.value);
    const bar = document.getElementById(row.bar);
    if (label) label.textContent = entry ? entry[0] : "—";
    if (value) value.textContent = entry ? `${pct.toFixed(1)}%` : "0%";
    if (bar) bar.style.width = `${Math.max(0, Math.min(100, pct))}%`;
  });

  const narrative = document.getElementById("xai-narrative-text");
  if (narrative && data.narrative) narrative.textContent = data.narrative;
}

async function loadXai() {
  try {
    renderXai(await apiFetch("/api/xai"));
  } catch (_) {
    renderXai({ confidence_pct: null, attributions: {}, narrative: null });
  }
}

function drawSpectralChart(data) {
  const canvas = document.getElementById("spectralCanvas");
  if (!canvas || !canvas.getContext) return;
  const ctx = canvas.getContext("2d");
  const wavelengths = data.wavelengths_um || {};
  const live = data.live_reflectance || {};
  const reference = data.reference_reflectance || {};
  const keys = Object.keys(wavelengths).filter(
    (k) => isFinite(Number(wavelengths[k])) && isFinite(Number(live[k])),
  );
  if (keys.length < 2) return;

  const W = canvas.width;
  const H = canvas.height;
  ctx.clearRect(0, 0, W, H);

  const xs = keys.map((k) => Number(wavelengths[k]));
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  const allVals = keys
    .flatMap((k) => [Number(live[k]), Number(reference[k])])
    .filter(isFinite);
  const minV = Math.min(...allVals, 0);
  const maxV = Math.max(...allVals, 0.0001);

  const px = (x) => ((x - minX) / (maxX - minX || 1)) * (W - 16) + 8;
  const py = (v) => H - 10 - ((v - minV) / (maxV - minV || 1)) * (H - 18);

  const drawLine = (series, color, width) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    keys.forEach((k, i) => {
      const x = px(Number(wavelengths[k]));
      const y = py(Number(series[k]));
      if (i === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  };

  drawLine(reference, "#94A3B8", 1.5);
  drawLine(live, "#06B6D4", 2);
}

function renderSpectral(data) {
  const sim = Number(data.similarity);
  const title = document.getElementById("spectral-title");
  if (title) {
    title.textContent = `${isFinite(sim) ? (sim * 100).toFixed(2) : "--"}% ${(data.label || "SPECTRAL SIMILARITY").toUpperCase()}`;
  }
  const tags = document.getElementById("spectral-tags");
  if (tags) {
    tags.innerHTML = "";
    const scene = data.scene || {};
    const parts = [];
    if (scene.satellite_sensor) parts.push(scene.satellite_sensor);
    if (scene.tile) parts.push(scene.tile);
    if (scene.date) parts.push(scene.date);
    if (scene.cloud_cover_pct != null) parts.push(`Cloud cover ${scene.cloud_cover_pct}%`);
    if (data.spectral_potential) parts.push(`${data.spectral_potential} potential`);
    if (data.interpretation) parts.push(data.interpretation);
    parts.forEach((text) => {
      const s = document.createElement("span");
      s.className = "spec-tag";
      s.textContent = text;
      tags.appendChild(s);
    });
  }
  drawSpectralChart(data);
}

function renderPitGrid(pockets) {
  const grid = document.getElementById("pit-telemetry-grid");
  if (!grid) return;
  grid.innerHTML = "";
  if (!pockets || !pockets.length) {
    const box = document.createElement("div");
    box.className = "pit-box";
    const name = document.createElement("div");
    name.className = "pit-name";
    name.textContent = "No pit telemetry";
    const st = document.createElement("div");
    st.className = "pit-state warning";
    st.textContent = "Awaiting data feed";
    box.appendChild(name);
    box.appendChild(st);
    grid.appendChild(box);
    return;
  }
  pockets.forEach((p) => {
    const box = document.createElement("div");
    box.className = "pit-box";
    const name = document.createElement("div");
    name.className = "pit-name";
    name.textContent = p.name || p.pocket_id || p.id || "Pit";
    box.appendChild(name);

    const grade = Number(p.grade_pct);
    const st = document.createElement("div");
    if (isFinite(grade)) {
      if (grade >= 44) {
        st.className = "pit-state success";
        st.textContent = `${grade}% Mn · HIGH GRADE`;
      } else if (grade >= 34) {
        st.className = "pit-state warning";
        st.textContent = `${grade}% Mn · MEDIUM GRADE`;
      } else {
        st.className = "pit-state danger";
        st.textContent = `${grade}% Mn · LOW GRADE`;
      }
    } else if (p.status) {
      st.className = "pit-state warning";
      st.textContent = String(p.status).toUpperCase();
    } else {
      st.className = "pit-state warning";
      st.textContent = "Telemetry pending";
    }
    box.appendChild(st);

    const subText = p.mine_name || p.mine || null;
    if (p.water_depth_m != null) {
      const sub = document.createElement("div");
      sub.className = "pit-subdetail";
      sub.textContent = `Water depth: ${p.water_depth_m} m`;
      box.appendChild(sub);
    } else if (subText) {
      const sub = document.createElement("div");
      sub.className = "pit-subdetail";
      sub.textContent = subText;
      box.appendChild(sub);
    }
    grid.appendChild(box);
  });
}

function initMap(center) {
  const el = document.getElementById("spatial-map");
  if (!el) return;
  if (typeof L === "undefined") {
    el.textContent = "Map tiles unavailable.";
    return;
  }
  if (mapInstance) return;
  try {
    mapInstance = L.map("spatial-map").setView(Array.isArray(center) ? center : [21.70, 79.80], 9);
    baseLayer = L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
      attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
      subdomains: "abcd",
      maxZoom: 19,
    }).addTo(mapInstance);
    spaceLayer = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { attribution: "Tiles &copy; Esri, Maxar, Earthstar Geographics", maxZoom: 19 },
    );
    const toggle = document.getElementById("toggle-space-layer");
    if (toggle) {
      toggle.addEventListener("change", () => {
        if (toggle.checked) {
          spaceLayer.addTo(mapInstance);
          baseLayer.remove();
        } else {
          baseLayer.addTo(mapInstance);
          spaceLayer.remove();
        }
      });
    }
  } catch (_) {
    el.textContent = "Map could not be initialised.";
  }
}

async function loadTelemetry() {
  try {
    const data = await apiFetch("/api/telemetry");
    const site = document.getElementById("site-name");
    if (site && data.site) site.textContent = data.site;
    renderPitGrid(data.ore_pockets || []);
    initMap(data.center);
  } catch (_) {
    renderPitGrid([]);
  }
}

async function loadSpectral() {
  try {
    renderSpectral(await apiFetch("/api/spectral"));
  } catch (_) {
    drawSpectralChart({});
  }
}

async function refreshAll(keepStatus = false) {
  try {
    const prediction = await postPredictions();
    renderPrediction(prediction);
    if (!keepStatus) setStatus("");
  } catch (err) {
    setStatus(err.message || "Could not update predictions.", "error");
  }
  await Promise.allSettled([loadPrescriptive(), loadXai()]);
}

async function onExecute() {
  setButtonsLoading(true);
  setStatus("Executing optimization plan…", "warn");
  try {
    const selected = getSelectedActions();
    const body = { action: "EXECUTE" };
    if (selected.length) body.selected_actions = selected;
    const data = await apiFetch("/api/prescriptive", {
      method: "POST",
      body: JSON.stringify(body),
    });
    lastExecutionResult = data;
    planExecuted = true;
    renderExecutionResults(data);
    setStatus(
      `Plan executed. Recovery: +${fmtNum(data.recovered_tonnage)} t · remaining shortfall: ${fmtNum(data.remaining_shortfall)} t`,
      "ok",
    );
  } catch (err) {
    setStatus(err.message || "Could not execute the optimization plan.", "error");
  } finally {
    setButtonsLoading(false);
  }
  await refreshAll(true);
}

async function onReset() {
  setButtonsLoading(true);
  try {
    await apiFetch("/api/prescriptive", {
      method: "POST",
      body: JSON.stringify({ action: "RESET" }),
    });
    lastExecutionResult = null;
    planExecuted = false;
    setStatus("Simulation reset — recommendations reflect unmitigated risk.", "ok");
  } catch (err) {
    setStatus(err.message || "Could not reset the plan.", "error");
  } finally {
    setButtonsLoading(false);
  }
  await refreshAll(true);
}

function bindControls() {
  const ids = ["slider-rainfall", "slider-mtbf", "slider-labor", "input-target"];
  ids.forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.addEventListener("input", () => updateControlBadges(getControls()));
    el.addEventListener("change", () => refreshAll());
  });
  const execute = document.getElementById("btn-execute-plan");
  const reset = document.getElementById("btn-reset-plan");
  if (execute) execute.addEventListener("click", onExecute);
  if (reset) reset.addEventListener("click", onReset);
}

async function init() {
  updateControlBadges(getControls());
  await Promise.allSettled([loadTelemetry(), loadSpectral()]);
  await refreshAll();
}

document.addEventListener("DOMContentLoaded", () => {
  updateClock();
  setInterval(updateClock, 1000);
  bindControls();
  init();
});