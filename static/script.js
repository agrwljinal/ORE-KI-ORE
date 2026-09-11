// MOIL Command Center - integrated spatial + spectral map + ML operations dashboards.
// Merged frontend: zone-map command center (spatial/spectral/telemetry layers,
// per-zone inspector, workflow strip, basemap themes) + modern ML dashboards
// (prediction, prescriptive execution, XAI attribution, spectral chart, pit grid).

const API_BASE_URL = "";
const API_TIMEOUT_MS = 12000;

let planExecuted = false;
let lastExecutionResult = null;

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

// ---------------- MAP + ZONE INTELLIGENCE ----------------
let map;
let mapInstance = null;
let layers = { spatial: null, spectral: null, telemetry: null, aoi: null };
let baseMapLayer;
let baseMapMode = "dark";

// Pyrolusite reference vector (matches modules.spectral.PYROLUSITE_REFERENCE).
// Only used for visual comparison in the fingerprint bars.
const PYROLUSITE_REF = { B04: 0.05571, B08: 0.05838, B11: 0.09301, B12: 0.08208 };

const $ = (id) => document.getElementById(id);
const fmt = (n, s = "") => (n === null || n === undefined) ? "--" : `${n}${s}`;

const priorityColor = (p) =>
  p === "HIGH" ? "#EF4444" : p === "MEDIUM" ? "#F59E0B" : "#22D3EE";

// Spectral confirmation tiers - calibrated to observed 85-98% range.
function confirmationTier(spectralSimilarity) {
  if (spectralSimilarity === null || spectralSimilarity === undefined)
    return { label: "NO DATA", color: "#64748B", pulse: false, dash: "6 4" };
  if (spectralSimilarity >= 97)
    return { label: "HIGH SIMILARITY", color: "#10B981", pulse: true,  dash: null };
  if (spectralSimilarity >= 94)
    return { label: "LIKELY",    color: "#22C55E", pulse: false, dash: null };
  if (spectralSimilarity >= 88)
    return { label: "WEAK",      color: "#F59E0B", pulse: false, dash: "6 4" };
  return { label: "MISMATCH",    color: "#EF4444", pulse: false, dash: "2 6" };
}

// Map backend provenance enum -> human-friendly chip text/color.
function provenanceChip(raw) {
  if (!raw) return { text: "UNKNOWN", cls: "prov-unknown" };
  if (raw === "REAL_SATELLITE") return { text: "REAL SATELLITE DATA", cls: "prov-real" };
  if (raw.indexOf("SYNTHETIC") !== -1)
    return { text: "SYNTHETIC DEMO DATA (schema-faithful spectra)", cls: "prov-demo" };
  if (raw === "REAL_SPATIAL_SYNTHETIC_SPECTRAL")
    return { text: "MIXED (real spatial + synthetic spectral)", cls: "prov-mixed" };
  if (raw === "ZONE_LEVEL_SPECTRAL_UNAVAILABLE")
    return { text: "SPECTRAL DATA UNAVAILABLE FOR THIS ZONE", cls: "prov-unavail" };
  return { text: raw, cls: "prov-unknown" };
}

// Highlight the current step in the SPATIAL -> SPECTRAL -> FUSION -> VERIFY strip.
function setWorkflowStep(step) {
  document.querySelectorAll(".workflow-step").forEach((el) => {
    const s = parseInt(el.dataset.step, 10);
    el.classList.toggle("workflow-step-active", s <= step);
  });
}

function updateBasemap() {
  if (!map) return;
  const basemaps = {
    dark: {
      url: "https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png",
      attribution: "&copy; OpenStreetMap &copy; CARTO",
      subdomains: "abcd",
    },
    satellite: {
      url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      attribution: "Tiles &copy; Esri",
    },
    terrain: {
      url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
      attribution: "Map data &copy; OpenStreetMap contributors, SRTM | Map style &copy; OpenTopoMap",
      subdomains: "abc",
    },
  };
  const basemap = basemaps[baseMapMode] || basemaps.dark;

  if (baseMapLayer) map.removeLayer(baseMapLayer);
  baseMapLayer = L.tileLayer(basemap.url, {
    attribution: basemap.attribution,
    maxZoom: 19,
    subdomains: basemap.subdomains,
  }).addTo(map);
  map.getContainer().classList.toggle("map-night-theme", baseMapMode === "dark");
}

function closeZonePanel(event) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  const panel = $("zone-panel");
  if (!panel) return;
  panel.classList.remove("is-open");
  panel.setAttribute("aria-hidden", "true");
  setWorkflowStep(1);
}

// ---------------- MAP INIT ----------------
function initMap(center) {
  const el = document.getElementById("spatial-map");
  if (!el) return;
  if (typeof L === "undefined") {
    el.textContent = "Map tiles unavailable.";
    return;
  }
  if (mapInstance) return;
  try {
    map = L.map("spatial-map", { zoomControl: true }).setView(
      Array.isArray(center) ? center : [21.70, 79.80],
      Array.isArray(center) ? 15 : 9,
    );
    mapInstance = map;
    updateBasemap();

    const basemapControl = $("map-basemap-mode");
    if (basemapControl) {
      basemapControl.addEventListener("change", (event) => {
        baseMapMode = event.target.value;
        updateBasemap();
      });
    }

    const closeBtn = $("zone-panel-close");
    if (closeBtn) closeBtn.addEventListener("click", closeZonePanel);

    layers.spatial   = L.layerGroup().addTo(map);
    layers.spectral  = L.layerGroup().addTo(map);
    layers.telemetry = L.layerGroup().addTo(map);
    layers.aoi       = L.layerGroup().addTo(map);

    const bind = (id, layer) => {
      const control = $(id);
      if (!control) return;
      const setActiveState = () => {
        map.getContainer().classList.toggle("spectral-overlay-active", control.checked);
      };
      setActiveState();
      control.addEventListener("change", (e) => {
        if (e.target.checked) map.addLayer(layer); else map.removeLayer(layer);
        setActiveState();
      });
    };
    bind("toggle-space-layer", layers.spectral);
  } catch (_) {
    el.textContent = "Map could not be initialised.";
  }
}

// ---------------- AOI ----------------
async function loadAOI() {
  if (!map) return;
  const r = await fetch("/api/aoi_boundary");
  if (!r.ok) return;
  const data = await r.json();
  const geo = L.geoJSON(data.features, {
    style: { color: "#E879F9", weight: 3, opacity: 0.9 },
  }).addTo(layers.aoi);
  geo.bindTooltip(`${data.aoi_name} · ${data.area_ha} ha`);
  try { map.fitBounds(geo.getBounds(), { padding: [30, 30] }); } catch (e) {}
}

// ---------------- ZONES ----------------
async function loadZones() {
  if (!map) return;
  const r = await fetch("/api/zones");
  const data = await r.json();
  layers.spatial.clearLayers();
  layers.spectral.clearLayers();

  data.zones.forEach((z) => {
    const spatialColor = priorityColor(z.spatial_priority_band);
    const tier = confirmationTier(z.spectral_similarity);

    // SPATIAL: base pin
    const pin = L.circleMarker([z.latitude, z.longitude], {
      radius: 11, color: "#0B0E14", weight: 2,
      fillColor: spatialColor, fillOpacity: 0.95,
    });
    pin.bindTooltip(
      `<b>${z.name}</b><br>` +
      `Spatial: <b>${z.spatial_score}%</b> (${z.spatial_priority_band})<br>` +
      `Spectral: <b>${fmt(z.spectral_similarity, "%")}</b><br>` +
      `Fusion: <b>${z.final_exploration_score}%</b> → ${z.priority}<br>` +
      `<i>Click for full intel</i>`,
      { direction: "top", offset: [0, -8] }
    );
    pin.on("click", () => showZoneDetail(z.zone_id));
    pin.addTo(layers.spatial);

    // SPECTRAL: confirmation ring
    const ringStyle = {
      radius: 22, color: tier.color, weight: 3,
      fillColor: tier.color, fillOpacity: 0.08, opacity: 0.95,
    };
    if (tier.dash) ringStyle.dashArray = tier.dash;

    const ring = L.circleMarker([z.latitude, z.longitude], ringStyle);
    ring.on("add", () => {
      const el = ring.getElement();
      if (!el) return;
      el.classList.add("spectral-ring-vibration");
      if (tier.pulse) el.classList.add("spectral-ring-pulse");
    });
    ring.on("click", () => showZoneDetail(z.zone_id));
    ring.addTo(layers.spectral);

    if (z.spectral_similarity !== null && z.spectral_similarity !== undefined) {
      [0, 1].forEach((rippleIndex) => {
        const ripple = L.circleMarker([z.latitude, z.longitude], {
          radius: 22, color: tier.color, weight: 2,
          fillOpacity: 0, opacity: 0, interactive: false,
        });
        ripple.on("add", () => {
          const el = ripple.getElement();
          if (!el) return;
          el.classList.add("spectral-ripple");
          el.style.setProperty("--ripple-delay", `${rippleIndex * 0.55}s`);
        });
        ripple.addTo(layers.spectral);
      });
    }

    // SPECTRAL: floating badge above pin
    const badgeHtml = `
      <div class="spectral-badge" style="border-color:${tier.color};color:${tier.color};">
        <span class="spectral-badge-pct">${fmt(z.spectral_similarity, "%")}</span>
        <span class="spectral-badge-label">${tier.label}</span>
      </div>`;
    L.marker([z.latitude, z.longitude], {
      icon: L.divIcon({
        className: "spectral-badge-icon", html: badgeHtml,
        iconSize: [90, 32], iconAnchor: [45, 52],
      }),
      interactive: false,
    }).addTo(layers.spectral);
  });

  // Fresh load = we've completed step 1 (spatial candidates identified)
  setWorkflowStep(1);
}

// ---------------- ZONE DETAIL ----------------
async function showZoneDetail(zoneId) {
  const r = await fetch(`/api/zones/${zoneId}`);
  if (!r.ok) return;
  const z = await r.json();
  const tier = confirmationTier(z.spectral_similarity);
  const panel = $("zone-panel");
  if (!panel) return;
  const status = z.operational_status || "Unavailable";
  const water = z.water_depth_m === null || z.water_depth_m === undefined
    ? "Unavailable"
    : `${z.water_depth_m.toFixed(1)} m`;
  const equipment = z.pumps_active === null || z.pumps_active === undefined
    ? "Unavailable"
    : `${z.pumps_active} pump${z.pumps_active === 1 ? "" : "s"} active`;
  const scene = z.spectral_scene;

  panel.classList.add("is-open");
  panel.setAttribute("aria-hidden", "false");

  // HEADER
  $("zone-panel-title").textContent = z.name || z.zone_id;
  $("zone-panel-subtitle").textContent = z.zone_type || "";
  $("zp-zoneid").textContent = z.zone_id;
  const lat = z.latitude.toFixed(4), lon = z.longitude.toFixed(4);
  $("zp-coords").textContent = `${lat}°N · ${lon}°E`;

  $("zp-operational-status").textContent = status;
  $("zp-water").textContent = water;
  $("zp-equipment").textContent = equipment;
  $("zp-clearance").textContent = "Not supplied by backend";
  $("zp-ore").textContent = "No ore-grade measurement in prototype";

  // SCORE ROW
  $("zp-spatial").textContent = fmt(z.spatial_score, "%");
  $("zp-spatial-band").textContent = z.spatial_priority_band;
  $("zp-spatial-band").style.color = priorityColor(z.spatial_priority_band);
  $("zp-spatial-reason").textContent =
    `Spatial screening identifies this as a ${String(z.spatial_priority_band || "unavailable").toLowerCase()} prospectivity zone.`;

  if (z.spectral_similarity === null) {
    $("zp-spectral").textContent = "N/A";
    $("zp-spectral-band").textContent = "UNAVAILABLE";
    $("zp-spectral-band").style.color = "#64748B";
    $("zp-best-mineral").textContent = "";
  } else {
    const bestName = z.best_mineral_match
      ? z.best_mineral_match.charAt(0).toUpperCase() + z.best_mineral_match.slice(1)
      : "N/A";
    $("zp-spectral").textContent = fmt(z.spectral_similarity, "%");
    $("zp-spectral-band").textContent = tier.label;
    $("zp-spectral-band").style.color = tier.color;
    $("zp-best-mineral").textContent = `Best match: ${bestName}`;
  }

  $("zp-spectral-scene").textContent = scene
    ? `${scene.platform} · ${scene.date} · ${scene.scene_id}`
    : "Sentinel-2 scene metadata unavailable";

  $("zp-final").textContent = fmt(z.final_exploration_score, "%");
  $("zp-priority").textContent = z.priority;
  $("zp-priority").style.color = priorityColor(z.priority);
  $("zp-priority").style.borderColor = priorityColor(z.priority);

  // BAND FINGERPRINT (Sentinel-2 4 bands vs Pyrolusite reference)
  renderFingerprint(z.zone_reflectance);

  // WHY / ACTION
  $("zp-explanation").textContent = z.explanation;
  $("zp-action").textContent = z.recommended_action;

  // PROVENANCE
  const prov = provenanceChip(z.data_provenance);
  const provEl = $("zp-provenance");
  provEl.textContent = prov.text;
  provEl.className = "provenance-chip " + prov.cls;
  $("zp-scientific-note").textContent = z.scientific_note;

  // Advance workflow strip: 1 spatial done, 2 spectral rendered, 3 fusion computed.
  // 4 (Field Verification) only lights up when HIGH priority.
  setWorkflowStep(z.priority === "HIGH" ? 4 : 3);

  try { map.setView([z.latitude, z.longitude], 16, { animate: true }); } catch (e) {}
}

// Renders four horizontal bars per band, showing the zone reflectance vs pyrolusite reference.
function renderFingerprint(zoneReflectance) {
  const host = $("zp-fingerprint");
  host.innerHTML = "";
  if (!zoneReflectance) {
    host.innerHTML = '<div class="band-empty">Zone-level spectral data unavailable.</div>';
    return;
  }
  const bands = ["B04", "B08", "B11", "B12"];
  const labels = {
    B04: "B04 · Red (665 nm)",
    B08: "B08 · NIR (842 nm)",
    B11: "B11 · SWIR-1 (1610 nm)",
    B12: "B12 · SWIR-2 (2190 nm)",
  };
  // Common scale = max of both vectors, so bars are comparable
  const values = bands.map(b => zoneReflectance[b]);
  const refs = bands.map(b => PYROLUSITE_REF[b]);
  const maxVal = Math.max(...values, ...refs) * 1.05;

  bands.forEach((b) => {
    const zonePct = (zoneReflectance[b] / maxVal) * 100;
    const refPct  = (PYROLUSITE_REF[b]   / maxVal) * 100;
    const row = document.createElement("div");
    row.className = "band-row";
    row.innerHTML = `
      <div class="band-label">${labels[b]}</div>
      <div class="band-track">
        <div class="band-bar band-bar-zone" style="width:${zonePct}%"></div>
        <div class="band-bar band-bar-ref"  style="width:${refPct}%"></div>
      </div>
      <div class="band-values">
        <span class="band-val band-val-zone">${zoneReflectance[b].toFixed(3)}</span>
        <span class="band-val band-val-ref">${PYROLUSITE_REF[b].toFixed(3)}</span>
      </div>`;
    host.appendChild(row);
  });
}

// ---------------- CONTROLS / PREDICTIONS ----------------
function getControls() {
  return {
    rainfall_mm: parseFloat(document.getElementById("slider-rainfall").value),
    mtbf_hrs: parseFloat(document.getElementById("slider-mtbf").value),
    labor_drop_pct: parseFloat(document.getElementById("slider-labor").value),
    target_tonnage: parseInt(document.getElementById("input-target").value, 10),
    ore_grade: document.getElementById("ore-grade-mix").value || "STD",
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

// Single derived-state rule mirrored from the backend (items 4/5):
// predicted >= target -> GREEN TARGET EXCEEDED; >=0.9 -> AMBER ON TRACK; else RED.
function deriveBannerState(predicted, target, recovered, planExecuted, mitigationCount) {
  const pred = Number(predicted) || 0;
  const tgt = Number(target) || 0;
  const effective = pred + (Number(recovered) || 0);
  const ratio = tgt > 0 ? effective / tgt : 0;
  let tier, bannerClass, headline, simState;
  if (effective >= tgt) {
    tier = "target_exceeded";
    bannerClass = "ok";
    headline = "TARGET EXCEEDED — SURPLUS PROJECTED";
    simState = "OPTIMAL — TARGET SECURED";
  } else if (ratio >= 0.9) {
    tier = "on_track";
    bannerClass = "warn";
    headline = "ON TRACK — MINOR VARIANCE";
    simState = planExecuted || mitigationCount > 0 ? "MITIGATING" : "UNMITIGATED RISK";
  } else {
    tier = "shortfall";
    bannerClass = "danger";
    headline = "SHORTFALL ALERT";
    simState = planExecuted || mitigationCount > 0 ? "MITIGATING" : "UNMITIGATED RISK";
  }
  const gap = effective - tgt;
  const rate = 3808.49; // MN rate ₹/ton (kept in sync for the live preview only)
  if (gap < 0) {
    return { tier, bannerClass, headline, simState, gap, shortfall: Math.max(0, -gap),
      ledgerLabel: "Rupee Loss Ledger", ledgerAmount: -gap * rate, ledgerClass: "text-red" };
  }
  return { tier, bannerClass, headline, simState, gap, shortfall: 0,
    ledgerLabel: "Rupee Gain Ledger", ledgerAmount: gap * rate, ledgerClass: "text-green" };
}

function applyBannerState(state) {
  const hero = document.getElementById("hero-banner");
  if (hero) {
    hero.classList.remove("deficit-hero-card-ok", "deficit-hero-card-warn", "deficit-hero-card-danger");
    if (state.bannerClass) hero.classList.add(`deficit-hero-card-${state.bannerClass}`);
  }
  const label = document.getElementById("hero-shortfall-title");
  if (label) {
    label.textContent = state.headline +
      (state.shortfall > 0 ? `: ${fmtNum(state.shortfall)} MT DEFICIT PREDICTED` : "");
  }
  const ledgerLabel = document.getElementById("hero-loss-label");
  if (ledgerLabel) ledgerLabel.textContent = state.ledgerLabel;
  const ledgerVal = document.getElementById("hero-loss-val");
  if (ledgerVal) {
    ledgerVal.textContent = `₹${fmtNum(state.ledgerAmount / 1e7, 2)} Cr`;
    ledgerVal.className = `hero-sub-value ${state.ledgerClass}`;
  }
  const sim = document.getElementById("hero-sim-state");
  if (sim) sim.textContent = state.simState;
}

let lastBasePrediction = null; // { predicted, target, ore_grade } for live checkbox previews

function renderPrediction(data) {
  const prediction = data.prediction || {};
  const params = data.parameters || {};
  const shortfall = Number(prediction.shortfall_tons) || Number(prediction.shortfall_tonnage) || 0;
  const predicted = Number(prediction.predicted_output) || Number(prediction.predicted_tonnage) || 0;
  const target = Number(prediction.base_target) || Number(params.target_tonnage) || 0;

  lastBasePrediction = { predicted, target };

  const banner = prediction.banner
    ? {
        bannerClass: prediction.banner.banner_class,
        headline: prediction.banner.headline,
        shortfall: prediction.banner.remaining_shortfall_tonnes,
        ledgerLabel: prediction.banner.ledger_label,
        ledgerAmount: prediction.banner.ledger_amount_inr,
        ledgerClass: prediction.banner.ledger_class,
        simState: prediction.banner.simulation_state,
      }
    : deriveBannerState(predicted, target, 0, false, 0);
  applyBannerState(banner);

  document.getElementById("hero-output-val").textContent = fmtTons(predicted);
  document.getElementById("hero-target-val").textContent = fmtTons(target);

  planExecuted = !!data.plan_executed;
  updateExecuteButtons();
}

// Live feedback: toggling a recommendation checkbox feeds its MT recovery into
// the predicted output and cascades through the same derived-state rule (item 6).
function previewOptimizedBanner() {
  const checks = Array.from(
    document.querySelectorAll("#recommendations-container input.rec-checkbox:checked"),
  );
  const recovery = checks.reduce((sum, el) => sum + (Number(el.dataset.recoveryMt) || 0), 0);
  const latest = lastBasePrediction || { predicted: 0, target: 0 };
  if (!document.getElementById("hero-banner")) return;
  const state = deriveBannerState(
    latest.predicted,
    latest.target,
    recovery,
    planExecuted,
    checks.length,
  );
  applyBannerState(state);
}

// ---------------- PRESCRIPTIVE EXECUTION ----------------
function buildRecItem(opt) {
  const item = document.createElement("div");
  item.className = `rec-item rec-action ${opt.recommended ? "type-2" : "type-3"}`;

  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.className = "rec-checkbox";
  checkbox.dataset.action = opt.action_code || opt.action || "";
  checkbox.dataset.recoveryMt = String(opt.expected_recovery_tonnes || 0);
  checkbox.checked = opt.expected_recovery_tonnes > 0;
  checkbox.addEventListener("change", previewOptimizedBanner);
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

// ---------------- XAI ----------------
function renderXai(data) {
  const conf = Number(data.confidence_pct);
  document.getElementById("xai-conf-badge").textContent = isFinite(conf)
    ? `Synthetic Demo Confidence: ${conf.toFixed(1)}%`
    : "Confidence: Not available";

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

// ---------------- SPECTRAL ----------------
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

// ---------------- PIT GRID ----------------
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

// ---------------- TELEMETRY ----------------
async function loadTelemetry() {
  try {
    const data = await apiFetch("/api/telemetry");
    const site = document.getElementById("site-name");
    if (site && data.site) site.textContent = data.site;
    renderPitGrid(data.ore_pockets || []);
    const t = new Date(data.system_timestamp);
    const clock = document.getElementById("system-time");
    if (clock && !isNaN(t.getTime())) clock.textContent = t.toISOString().substr(11, 8) + " UTC";
    initMap(data.center);

    if (map && layers.telemetry) {
      layers.telemetry.clearLayers();
      (data.ore_pockets || []).forEach((p) => {
        const icon = L.divIcon({
          className: "telemetry-marker",
          html: `<div class="telemetry-dot">●</div>`,
          iconSize: [22, 22],
        });
        L.marker([p.lat, p.lon], { icon })
          .bindTooltip(`<b>${p.name}</b><br>Status: ${p.status}<br>Pumps active: ${p.pumps_active}`)
          .on("click", () => showZoneDetail(p.id))
          .addTo(layers.telemetry);
      });
    }
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
  const grade = document.getElementById("ore-grade-mix");
  if (grade) grade.addEventListener("change", () => refreshAll());
  const execute = document.getElementById("btn-execute-plan");
  const reset = document.getElementById("btn-reset-plan");
  if (execute) execute.addEventListener("click", onExecute);
  if (reset) reset.addEventListener("click", onReset);
}

// ---------------- INIT ----------------
async function init() {
  updateControlBadges(getControls());
  await Promise.allSettled([loadTelemetry(), loadSpectral()]);
  await Promise.allSettled([loadAOI(), loadZones()]);
  await refreshAll();
}

document.addEventListener("DOMContentLoaded", () => {
  document.addEventListener("click", (event) => {
    if (event.target.closest("#zone-panel-close")) closeZonePanel(event);
  }, true);
  updateClock();
  setInterval(updateClock, 1000);
  bindControls();
  init();
  setInterval(loadTelemetry, 5000);
});
