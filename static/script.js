// MOIL Command Center - integrated spatial + spectral map + fusion.
// Every value shown comes from the backend (no hardcoded scores in the UI).

let map;
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
    return { label: "CONFIRMED", color: "#10B981", pulse: true,  dash: null };
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
    return { text: "MIXED (real spatial · synthetic spectral)", cls: "prov-mixed" };
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
  map = L.map("spatial-map", { zoomControl: true }).setView(center, 15);
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
}

// ---------------- AOI ----------------
async function loadAOI() {
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
  const status = z.operational_status || "Unavailable";
  const water = z.water_depth_m === null || z.water_depth_m === undefined
    ? "Unavailable"
    : `${z.water_depth_m.toFixed(1)} m`;
  const equipment = z.pumps_active === null || z.pumps_active === undefined
    ? "Unavailable"
    : `${z.pumps_active} pump${z.pumps_active === 1 ? "" : "s"} active`;
  const scene = z.spectral_scene;

  if (!panel) return;
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

// ---------------- TELEMETRY ----------------
async function loadTelemetry() {
  const r = await fetch("/api/telemetry");
  const data = await r.json();
  layers.telemetry.clearLayers();
  data.ore_pockets.forEach((p) => {
    const icon = L.divIcon({
      className: "telemetry-marker",
      html: `<div class="telemetry-dot">⛏</div>`,
      iconSize: [22, 22],
    });
    L.marker([p.lat, p.lon], { icon })
      .bindTooltip(`<b>${p.name}</b><br>Status: ${p.status}<br>Pumps active: ${p.pumps_active}`)
      .on("click", () => showZoneDetail(p.id))
      .addTo(layers.telemetry);
  });
  const t = new Date(data.system_timestamp);
  $("system-time").textContent = t.toISOString().substr(11, 8) + " UTC";
}

// ---------------- AOI SPECTRAL ----------------
async function loadAOISpectral() {
  const r = await fetch("/api/spectral");
  const s = await r.json();
  $("aoi-similarity-value").textContent = s.similarity_pct + "%";
  $("aoi-tag-platform").textContent = s.scene.platform;
  $("aoi-tag-date").textContent = "07 Jan 2026";
  $("aoi-tag-tile").textContent = "Tile " + s.scene.tile;
  $("aoi-tag-area").textContent = s.aoi_area_ha + " ha AOI";
  $("aoi-scope-note").textContent = s.scope_note;
}

// ---------------- PREDICTIONS / RECS / XAI ----------------
async function refreshPredictions() {
  const payload = {
    rainfall_mm: parseFloat($("slider-rainfall").value),
    mtbf_hrs: parseFloat($("slider-mtbf").value),
    labor_drop_pct: parseFloat($("slider-labor").value),
    target_tonnage: parseInt($("input-target").value, 10),
  };
  const r = await fetch("/api/predictions", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const d = await r.json();
  const p = d.prediction;
  $("hero-shortfall-title").textContent =
    `SHORTFALL ALERT: ${p.shortfall_tons.toLocaleString()} MT DEFICIT PREDICTED`;
  $("hero-loss-val").textContent = `₹${p.loss_crores} Cr`;
  $("hero-output-val").textContent = `${p.predicted_output.toLocaleString()} MT`;
  $("hero-target-val").textContent = `${p.base_target.toLocaleString()} MT`;
  $("hero-sim-state").textContent = d.simulation_state;
  await loadRecommendations();
}

async function loadRecommendations() {
  const r = await fetch("/api/prescriptive");
  const d = await r.json();
  const container = $("recommendations-container");
  container.innerHTML = "";
  d.recommendations.forEach((rec, i) => {
    const el = document.createElement("div");
    el.className = `rec-item type-${(i % 3) + 1}`;
    el.innerHTML = `
      <div class="rec-content">
        <div class="rec-title">${rec.title}</div>
        <div class="rec-desc">${rec.desc}</div>
      </div>
      <div class="rec-gain">+${rec.delta_recovery.toLocaleString()} MT</div>`;
    container.appendChild(el);
  });
}

async function loadXAI() {
  const r = await fetch("/api/xai");
  const d = await r.json();
  $("xai-conf-badge").textContent = `Model Conf: ${d.confidence_pct}%`;
  $("xai-narrative-text").textContent = d.narrative;
}

async function planAction(action) {
  await fetch("/api/prescriptive", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  await refreshPredictions();
}

// ---------------- CONTROL WIRING ----------------
function wireControls() {
  const sync = (id, elId, suffix) => {
    const s = $(id);
    s.addEventListener("input", () => { $(elId).textContent = s.value + suffix; });
    s.addEventListener("change", refreshPredictions);
  };
  sync("slider-rainfall", "val-rainfall", " mm");
  sync("slider-mtbf",     "val-mtbf",     " hrs");
  sync("slider-labor",    "val-labor",    " %");
  $("input-target").addEventListener("change", () => {
    $("val-target-label").textContent = parseInt($("input-target").value, 10).toLocaleString() + " MT";
    refreshPredictions();
  });
  $("btn-execute-plan").addEventListener("click", () => planAction("EXECUTE"));
  $("btn-reset-plan").addEventListener("click", () => planAction("RESET"));

  const closeBtn = $("zone-panel-close");
  if (closeBtn) {
    closeBtn.removeEventListener("click", closeZonePanel);
    closeBtn.addEventListener("click", closeZonePanel);
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  document.addEventListener("click", (event) => {
    if (event.target.closest("#zone-panel-close")) closeZonePanel(event);
  }, true);
  const telemetryR = await fetch("/api/telemetry");
  const telemetry = await telemetryR.json();
  initMap(telemetry.center);
  await Promise.all([
    loadAOI(), loadZones(), loadTelemetry(),
    loadAOISpectral(), refreshPredictions(), loadXAI(),
  ]);
  wireControls();
  setInterval(loadTelemetry, 5000);
});