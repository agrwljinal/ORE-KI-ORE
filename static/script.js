// MOIL Command Center - integrated spatial + spectral map + ML operations dashboards.
// Merged frontend: zone-map command center (spatial/spectral/telemetry layers,
// per-zone inspector, workflow strip, basemap themes) + modern ML dashboards
// (prediction, prescriptive execution, XAI attribution, spectral chart, pit grid).

const API_BASE_URL = "";
const API_TIMEOUT_MS = 12000;

let planExecuted = false;
let lastExecutionResult = null;
let lastCustomerContracts = [];
let lastCustomers = [];
let availableMines = [];

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
// The front-end presentation pipeline always runs over the clearly-labelled
// SYNTHETIC_DEMO chips (no real raster is available), so the vegetation mask
// and surface renderer are always in demo mode.
let vegDemoMode = true;

// Space-tech spectral screening state (scan sweep + energy halos).
let screeningActive = false;
let haloMarkers = [];
let revealStopToken = 0;

// Latest loaded zones + marker registry for focusing/highlighting.
let zonesCache = [];
let zoneMarkers = {};

// Synthetic surface renderer state.
let surfaceMode = "raw";   // "raw" | "filtered"
let surfaceChip = null;    // { classes, size, mask }
let surfaceOverlay = null; // on-map image overlay showing the chip at zoom
let surfaceOverlayFrame = null;
let activeZone = null;     // last zone whose detail panel was opened
let activeZoneId = null;   // the actual zone_id captured at pin-click time
let surfaceScanning = false; // live NDVI sweep in progress (toggles locked)

// Pyrolusite reference vector (matches modules.spectral.PYROLUSITE_REFERENCE).
// Only used for visual comparison in the fingerprint bars.
const PYROLUSITE_REF = { B04: 0.05571, B08: 0.05838, B11: 0.09301, B12: 0.08208 };

const $ = (id) => document.getElementById(id);
const fmt = (n, s = "") => (n === null || n === undefined) ? "--" : `${n}${s}`;

const priorityStyle = (p) => {
  // Green = HIGH (most suitable for mining - matches user expectation).
  if (p === "HIGH")   return { color: "#4ADE80", glow: "rgba(74, 222, 128, 0.70)" };
  if (p === "MEDIUM") return { color: "#FBBF24", glow: "rgba(251, 191, 36, 0.65)" };
  return { color: "#F87171", glow: "rgba(248, 113, 113, 0.70)" }; // LOW
};
const priorityColor = (p) => priorityStyle(p).color;

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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
  if (raw === "SYNTHETIC_DEMO_CHIP_VEG_MASKED_PIXEL_MEANS" || raw === "SYNTHETIC_DEMO_CHIP")
    return { text: "SYNTHETIC DEMO DATA (simulated chip)", cls: "prov-demo" };
  if (raw.indexOf("SYNTHETIC") !== -1)
    return { text: "SYNTHETIC DEMO DATA (schema-faithful spectra)", cls: "prov-demo" };
  if (raw === "REAL_SPATIAL_SYNTHETIC_SPECTRAL")
    return { text: "MIXED (real spatial + synthetic spectral)", cls: "prov-mixed" };
  if (raw === "ZONE_LEVEL_SPECTRAL_UNAVAILABLE")
    return { text: "SPECTRAL DATA UNAVAILABLE FOR THIS ZONE", cls: "prov-unavail" };
  return { text: raw, cls: "prov-unknown" };
}

// Human-readable summary of the per-zone vegetation (NDVI) mask that ran
// BEFORE the B04/B08/B11/B12 means were extracted.
function vegetationMaskSummary(mask, demo = false) {
  if (!mask) return "Vegetation mask: unavailable.";
  if (mask.status === "APPLIED") {
    const src = demo ? "SYNTHETIC_DEMO chip" : "Per-pixel chip";
    if (!mask.scorable) {
      return `${src}: ${mask.surface_coverage_pct}% exposed surface remains -- too little to score, spectral result suppressed (no misleading score emitted).`;
    }
    return (
      `${src}: removed ${mask.vegetation_pixels_removed}/${mask.total_pixels} vegetated pixels ` +
      `(NDVI > ${mask.ndvi_threshold}), excluded ${mask.water_pixels_excluded} water/shadow pixels ` +
      `(NDVI < ${mask.ndvi_water_low_threshold ?? "-0.10"}), ` +
      `${mask.valid_pixels_remaining} valid pixels remain (${mask.surface_coverage_pct}% coverage).`
    );
  }
  return "Vegetation mask: not applied (no per-pixel Sentinel-2 data; unmasked synthetic vector used).";
}

// Full masking chain the demo mode highlights:
// Total pixels -> vegetation removed -> usable surface pixels -> spectral
// similarity -> final exploration priority.
function vegetationChain(mask, z, demo = false) {
  const id = $("zp-veg-chain");
  if (!id) return;
  if (!mask || mask.status !== "APPLIED") {
    id.textContent = demo || vegDemoMode
      ? "Chain unavailable: no pixel chip data."
      : "Enable Synthetic Demo · Veg-Masked Spectral to view the masking chain.";
    id.classList.toggle("veg-chain-strip-muted", true);
    return;
  }
  id.classList.remove("veg-chain-strip-muted");
  const total = fmt(mask.total_pixels, " px");
  const removed = fmt(mask.vegetation_pixels_removed, " px");
  const excludedWater = fmt(mask.water_pixels_excluded, " px");
  const valid = fmt(mask.valid_pixels_remaining, " px");
  const coverage = mask.surface_coverage_pct === null || mask.surface_coverage_pct === undefined
    ? "--"
    : `${mask.surface_coverage_pct}%`;
  const spectral = mask.scorable
    ? fmt(z.spectral_similarity, "%")
    : "N/A";
  const priority = (mask.scorable && z.priority) || "SPATIAL-ONLY";
  id.innerHTML =
    `<span>Total <b>${total}</b></span><i>→</i>` +
    `<span>veg <b>−${removed}</b></span><i>→</i>` +
    `<span>water <b>−${excludedWater}</b></span><i>→</i>` +
    `<span>usable <b>${valid}</b> (${coverage})</span><i>→</i>` +
    `<span>spectral <b>${spectral}</b></span><i>→</i>` +
    `<span>priority <b>${priority}</b></span>`;
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
      url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
      attribution: "&copy; OpenStreetMap contributors",
      subdomains: "abc",
      dark: true,
    },
    satellite: {
      url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      attribution: "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics",
    },
    terrain: {
      url: "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
      attribution: "&copy; OpenStreetMap contributors, SRTM | Map style &copy; OpenTopoMap",
      subdomains: "abc",
    },
  };
  const basemap = basemaps[baseMapMode] || basemaps.dark;

  if (baseMapLayer) map.removeLayer(baseMapLayer);
  baseMapLayer = L.tileLayer(basemap.url, {
    attribution: basemap.attribution,
    maxZoom: 19,
    subdomains: basemap.subdomains || "abc",
  }).addTo(map);
  map.getContainer().classList.toggle("map-dark-mode", !!basemap.dark);
  map.getContainer().classList.toggle("map-light-mode", !basemap.dark);
  if (basemap.dark) baseMapLayer.bringToBack();
}

function closeZonePanel(event, keepOverlay = false) {
  if (event) {
    event.preventDefault();
    event.stopPropagation();
  }
  const panel = $("zone-panel");
  if (!panel) return;
  // Accessibility: focus must never be marooned inside a subtree that is
  // about to become aria-hidden. Park it on the map container first.
  if (panel.contains(document.activeElement)) {
    const mapEl = document.getElementById("spatial-map");
    const target = mapEl ? mapEl : document.body;
    try { target.focus({ preventScroll: true }); } catch (_) { target.focus(); }
  }
  panel.classList.remove("is-open");
  panel.setAttribute("aria-hidden", "true");
  if (!keepOverlay) {
    removeSurfaceOverlay();
    setWorkflowStep(1);
  }
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
    map.invalidateSize();
    setTimeout(() => map.invalidateSize(), 400);
    window.addEventListener("resize", () => map.invalidateSize());

    const basemapControl = $("map-basemap-mode");
    if (basemapControl) {
      basemapControl.addEventListener("change", (event) => {
        baseMapMode = event.target.value;
        updateBasemap();
      });
    }

    const closeBtn = $("zone-panel-close");
    if (closeBtn) closeBtn.addEventListener("click", closeZonePanel);

    const surfaceBtn = $("btn-surface-filter");
    if (surfaceBtn) surfaceBtn.addEventListener("click", () => runSurfaceFilter());

    const rawViewBtn = $("btn-surface-raw");
    if (rawViewBtn) rawViewBtn.addEventListener("click", () => applySurfaceView("raw"));
    const filteredViewBtn = $("btn-surface-filtered");
    if (filteredViewBtn) filteredViewBtn.addEventListener("click", () => applySurfaceView("filtered"));

    layers.spatial   = L.layerGroup().addTo(map);
    layers.spectral  = L.layerGroup().addTo(map);
    layers.telemetry = L.layerGroup().addTo(map);
    layers.aoi       = L.layerGroup().addTo(map);

    initScreeningToggle();
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
async function loadZones(demo = false) {
  if (!map) return;
  const query = demo ? "?veg_demo=1" : "";
  const r = await fetch("/api/zones" + query);
  const data = await r.json();
  layers.spatial.clearLayers();
  layers.spectral.clearLayers();
  haloMarkers = [];
  screeningActive = false;
  zonesCache = data.zones;
  zoneMarkers = {};

  data.zones.forEach((z) => {
    const lat = z.latitude;
    const lon = z.longitude;
    const st = priorityStyle(z.spatial_priority_band);

    // Soft expanding halo ring behind the orb (subtle pulse).
    L.marker([lat, lon], {
      icon: L.divIcon({
        className: "zone-ring-icon",
        html: `<div class="zone-ring" style="width:60px;height:60px;"></div>`,
        iconSize: [60, 60],
        iconAnchor: [30, 30],
      }),
      interactive: false,
    }).addTo(layers.spatial);

    // Translucent glowing orb marker.
    const orb = L.marker([lat, lon], {
      icon: L.divIcon({
        className: "zone-orb-icon",
        html: `<div class="zone-orb" style="width:22px;height:22px;"></div>`,
        iconSize: [22, 22],
        iconAnchor: [11, 11],
      }),
      riseOnHover: true,
    });
    orb.on("add", () => {
      const el = orb.getElement();
      if (!el) return;
      const core = el.querySelector(".zone-orb");
      if (!core) return;
      core.style.setProperty("--zone-color", st.color);
      core.style.setProperty("--zone-glow", st.glow);
      core.title = z.name;
    });
    const vegLine = demo && z.vegetation_mask && z.vegetation_mask.status === "APPLIED"
      ? `<br>Veg −${z.vegetation_mask.vegetation_pixels_removed} · water −${z.vegetation_mask.water_pixels_excluded} · usable ${z.vegetation_mask.valid_pixels_remaining} px (${z.vegetation_mask.surface_coverage_pct}%)`
      : "";
    orb.bindTooltip(
      `<b>${z.name}</b><br>` +
      `Spatial: <b>${z.spatial_score}%</b> (${z.spatial_priority_band})<br>` +
      `Spectral: <b>${fmt(z.spectral_similarity, "%")}</b><br>` +
      `Fusion: <b>${z.final_exploration_score}%</b> → ${z.priority}<br>` +
      vegLine +
      `<i>Click for full intel</i>`,
      { direction: "top", offset: [0, -12] }
    );
    orb.on("click", () => onZoneSelect(z));
    orb.addTo(layers.spatial);

    zoneMarkers[z.zone_id] = { orb, st };
  });

  // Fresh load = spatial candidates identified.
  setWorkflowStep(1);
}

// Cinematic focus: smooth zoom into the zone and highlight its marker.
function focusZone(z) {
  try { map.flyTo([z.latitude, z.longitude], 18, { duration: 1.6 }); } catch (e) {}
  Object.entries(zoneMarkers).forEach(([id, m]) => {
    const core = m.orb.getElement() && m.orb.getElement().querySelector(".zone-orb");
    if (core) {
      core.classList.toggle("zone-selected", id === z.zone_id);
      core.classList.toggle("zone-dimmable", id !== z.zone_id);
    }
  });
  setWorkflowStep(1);
}

function onZoneSelect(z) {
  // Capture the ACTUAL zone_id the instant the pin is clicked, before any
  // async detail fetch. The NDVI button always sends exactly this id.
  activeZoneId = z.zone_id || null;
  const btn = $("btn-surface-filter");
  if (btn) btn.dataset.zoneId = activeZoneId || "";
  focusZone(z);
  showZoneDetail(z.zone_id);
}

// ---------------- ON-MAP SYNTHETIC SURFACE (zoomed satellite view) ----------------
function surfaceCanvasDataURL() {
  const canvas = $("zp-surface-canvas");
  if (!canvas) return null;
  try { return canvas.toDataURL("image/png"); } catch (e) { return null; }
}

function mountSurfaceOverlay(z) {
  if (!map || !surfaceChip) return;
  removeSurfaceOverlay();
  const url = surfaceCanvasDataURL();
  if (!url) return;
  const bounds = [
    [z.latitude - 0.0012, z.longitude - 0.0012],
    [z.latitude + 0.0012, z.longitude + 0.0012],
  ];
  surfaceOverlay = L.imageOverlay(url, bounds, {
    opacity: 0.92, interactive: false, className: "zone-surface-overlay",
  }).addTo(map);
  surfaceOverlayFrame = L.rectangle(bounds, {
    color: "#38BDF8", weight: 1, dashArray: "6 4",
    fill: false, interactive: false, opacity: 0.7,
  }).addTo(map);
}

function updateSurfaceOverlayFromCanvas() {
  if (!surfaceOverlay) return;
  const url = surfaceCanvasDataURL();
  if (url) surfaceOverlay.setUrl(url);
}

function removeSurfaceOverlay() {
  if (!map) return;
  if (surfaceOverlay) { map.removeLayer(surfaceOverlay); surfaceOverlay = null; }
  if (surfaceOverlayFrame) { map.removeLayer(surfaceOverlayFrame); surfaceOverlayFrame = null; }
}

// ----------------- SURFACE FILTER (SYNTHETIC DEMO) -----------------
function cellColor(cls, cleared, jitter) {
  if (cls === "water") {
    return cleared ? "rgba(56, 189, 248, 0.28)" : "rgba(34, 144, 214, 0.85)";
  }
  if (cls === "vegetation") {
    return cleared ? "rgba(9, 17, 29, 0.95)" : "rgba(34, 197, 94, 0.8)";
  }
  // exposed ore/soil/rock - dry warm palette with subtle texture
  const base = cleared ? [185, 160, 118] : [142, 112, 68];
  const j = (jitter % 3) - 1;
  const sh = Math.round(6 * j);
  return `rgba(${base[0] + sh}, ${base[1] + sh}, ${base[2] + sh}, 0.95)`;
}

function redrawSurface(scanYOrNull) {
  const canvas = $("zp-surface-canvas");
  if (!canvas || !surfaceChip) return;
  const { classes, size } = surfaceChip;
  const ctx = canvas.getContext("2d");
  if (!ctx) return;
  const px = canvas.width / size;
  const py = canvas.height / size;
  const active = surfaceMode === "filtered";
  ctx.fillStyle = "#060B14";
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  for (let i = 0; i < size; i++) {
    for (let j = 0; j < size; j++) {
      const cls = classes[i * size + j] || "exposed";
      const y = (i + 0.5) * py;
      const cleared = active && (scanYOrNull === null || y > scanYOrNull);
      // scan-band tint just behind the sweep line
      const isBand = scanYOrNull !== null && scanYOrNull !== undefined &&
        y > scanYOrNull - 10 && y < scanYOrNull + 1;
      ctx.fillStyle = cellColor(cls, cleared, (i * 7 + j * 13) % 3);
      ctx.fillRect(j * px + 1, i * py + 1, px - 2, py - 2);
      if (isBand && active) {
        ctx.fillStyle = "rgba(56, 189, 248, 0.22)";
        ctx.fillRect(j * px, i * py, px, py);
      }
    }
  }
}

function surfaceStatsFrom(mask) {
  $("zp-veg-pct").textContent = "--";
  $("zp-usable-pct").textContent = "--";
  $("zp-pixels").textContent = "--";
  if (!mask) return;
  const total = mask.total_pixels || 0;
  const veg = mask.vegetation_pixels_removed || 0;
  const pct = mask.surface_coverage_pct === null || mask.surface_coverage_pct === undefined
    ? 0 : mask.surface_coverage_pct;
  $("zp-veg-pct").textContent = total ? `${Math.round((veg / total) * 100)}%` : "--";
  $("zp-usable-pct").textContent = fmt(pct, "%");
  $("zp-pixels").textContent = fmt(total, " px");
}

function showNdviStats(chip) {
  const el = $("zp-ndvi-stats");
  if (!el) return;
  const s = chip && chip.mask && chip.mask.ndvi_statistics;
  el.innerHTML = s
    ? `<span class="ndvi-kicker">NDVI</span>` +
      `mean <b>${s.mean}</b> · median <b>${s.median}</b> · min <b>${s.min}</b> · max <b>${s.max}</b>`
    : `NDVI mean / median / min / max appear after the scan`;
}

function setViewToggle(mode) {
  ["raw", "filtered"].forEach((m) => {
    const el = m === "raw" ? $("btn-surface-raw") : $("btn-surface-filtered");
    if (!el) return;
    el.classList.toggle("active", surfaceMode === m);
    el.setAttribute("aria-pressed", surfaceMode === m ? "true" : "false");
  });
}

function compactSurfaceResult(mask) {
  const total = mask.total_pixels || 0;
  const veg = mask.vegetation_pixels_removed || 0;
  const vegPct = total ? Math.round((veg / total) * 100) : 0;
  const usable = (mask.surface_coverage_pct === null || mask.surface_coverage_pct === undefined)
    ? 0 : mask.surface_coverage_pct;
  const tail = mask.scorable ? "READY FOR SPECTRAL ANALYSIS" : "SPECTRAL SCORE WITHHELD";
  return `Vegetation removed: ${vegPct}% | Usable surface: ${fmt(usable, "%")} | Pixels analysed: ${fmt(total, "")} | ${tail}`;
}

function applySurfaceView(mode) {
  if (!surfaceChip || surfaceScanning || !$("zp-surface-canvas")) return;
  surfaceMode = mode;
  redrawSurface(null);
  updateSurfaceOverlayFromCanvas();
  setViewToggle(mode);
  const mask = surfaceChip.mask || {};
  const state = $("zp-surface-state");
  const status = $("zp-surface-status");
  if (surfaceMode === "filtered") {
    if (state) {
      state.textContent = mask.scorable ? "SURFACE FILTERED" : "SURFACE SUPPRESSED";
      state.className = "surface-state " + (mask.scorable ? "sf-filtered" : "sf-suppressed");
    }
    if (status) {
      status.textContent = compactSurfaceResult(mask);
      status.className = mask.scorable ? "sf-ready" : "sf-suppressed";
    }
  } else {
    if (state) { state.textContent = "RAW SURFACE"; state.className = "surface-state"; }
    if (status) {
      status.textContent = "Not screened — activate the NDVI Surface Filter";
      status.className = "";
    }
  }
}

async function runSurfaceFilter() {
  const btn = $("btn-surface-filter");
  const rawBtn = $("btn-surface-raw");
  const filtBtn = $("btn-surface-filtered");
  const state = $("zp-surface-state");
  const status = $("zp-surface-status");
  if (surfaceScanning) return;

  // The zone id is captured on the pin click (activeZoneId / btn.dataset).
  // activeZone (async detail payload) is only a fallback, never the primary
  // source, so the request always carries the exact clicked-pin id.
  const zoneId = activeZoneId
    || (btn && btn.dataset.zoneId) || null
    || (activeZone && activeZone.zone_id) || null;
  if (!zoneId) {
    setStatus("Select a zone first, then run the NDVI surface filter.", "warn");
    if (state) { state.textContent = "FALLBACK"; state.className = "surface-state sf-suppressed"; }
    return;
  }
  if (btn) btn.disabled = true;
  surfaceScanning = true;
  if (state) { state.textContent = "SCANNING…"; state.className = "surface-state sf-scanning"; }
  if (status) { status.textContent = "Requesting NDVI pipeline…"; status.className = ""; }

  // The click handler calls the backend NDVI pipeline directly (real XHR,
  // visible in DevTools Network). The UI only renders what the server returned.
  try {
    const resp = await fetch("/api/ndvi/filter", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ zone_id: zoneId }),
    });
    if (!resp.ok) {
      let message = `HTTP ${resp.status}`;
      try {
        const errorBody = await resp.json();
        if (errorBody && errorBody.error) message = errorBody.error;
      } catch (_) {}
      throw new Error(message);
    }
    const data = await resp.json();
    if (!data || data.total_pixels === undefined || !Array.isArray(data.classes)) {
      throw new Error("NDVI pipeline returned a malformed payload.");
    }

    // Adopt the server-computed chip + mask so every shown number reconciles
    // with the generated pixel grid (never client-side invention).
    surfaceMode = "filtered";
    surfaceChip = {
      classes: data.classes,
      ndvi: data.ndvi || [],
      size: data.chip_size || 20,
      mask: {
        status: data.status,
        applied: data.applied,
        total_pixels: data.total_pixels,
        vegetation_pixels_removed: data.vegetation_pixels_removed,
        water_pixels_excluded: data.water_pixels_excluded,
        valid_pixels_remaining: data.valid_pixels_remaining,
        surface_coverage_pct: data.surface_coverage_pct,
        ndvi_threshold: data.ndvi_threshold,
        ndvi_statistics: data.ndvi_statistics,
        scorable: data.scorable,
        reason: data.reason,
      },
    };
    showNdviStats(surfaceChip);

    // Success → hide the info card, fly into the pin and sweep the cleared
    // synthetic surface across the map.
    closeZonePanel(undefined, true);
    const zoneRef = (activeZone && activeZone.zone_id === zoneId)
      ? activeZone
      : (zonesCache.find((zc) => zc.zone_id === zoneId)) || null;
    if (map && zoneRef) {
      try { map.flyTo([zoneRef.latitude, zoneRef.longitude], 18.5, { duration: 1.8 }); }
      catch (e) {}
    }

    redrawSurface(0);
    if (!surfaceOverlay && zoneRef) mountSurfaceOverlay(zoneRef);
    updateSurfaceOverlayFromCanvas();

    const canvas = $("zp-surface-canvas");
    const height = canvas ? canvas.height : 200;
    const t0 = performance.now();
    const dur = 1500;
    await new Promise((resolve) => {
      const step = (now) => {
        const f = Math.min(1, (now - t0) / dur);
        redrawSurface(f * height);
        updateSurfaceOverlayFromCanvas();
        if (f < 1) requestAnimationFrame(step); else resolve();
      };
      requestAnimationFrame(step);
    });

    redrawSurface(null);
    updateSurfaceOverlayFromCanvas();
    const mask = surfaceChip.mask;
    surfaceStatsFrom(mask);
    if (state) {
      state.textContent = mask.scorable ? "SURFACE FILTERED" : "SURFACE SUPPRESSED";
      state.className = "surface-state " + (mask.scorable ? "sf-filtered" : "sf-suppressed");
    }
    if (status) {
      status.textContent = compactSurfaceResult(mask);
      status.className = mask.scorable ? "sf-ready" : "sf-suppressed";
    }
    setViewToggle("filtered");
    setWorkflowStep(mask.scorable ? 2 : 1);
    setStatus(
      `NDVI complete (${data.mode}): vegetation −${mask.vegetation_pixels_removed} px (${data.vegetation_pct}%) → usable ${fmt(mask.surface_coverage_pct, "%")}`,
      "ok"
    );
  } catch (err) {
    console.error("NDVI surface filter failed:", err);
    const message = (err && err.message) ? err.message : "unknown error";
    if (status) {
      status.textContent = `NDVI FAILED — ${message}`;
      status.className = "sf-suppressed";
    }
    if (state) { state.textContent = "FAILED"; state.className = "surface-state sf-suppressed"; }
    setStatus(`NDVI surface filter failed: ${message}`, "error");
  } finally {
    surfaceScanning = false;
    if (btn) btn.disabled = false;
    [rawBtn, filtBtn].forEach((b) => { if (b) b.disabled = false; });
  }
}

// ----------------- SPECTRAL SCREENING REVEAL -----------------
function initScreeningToggle() {
  const toggle = $("toggle-space-layer");
  if (!toggle) return;
  toggle.checked = false;
  toggle.addEventListener("change", () => {
    if (toggle.checked && !screeningActive) {
      runScreeningReveal();
    } else if (!toggle.checked) {
      if (screeningActive) dissolveScreening();
      else cancelReveal();
    }
  });
}

function sweepMap(direction) {
  const mapEl = $("spatial-map");
  if (!mapEl) return;
  let scan = $("scan-wave");
  if (!scan) {
    scan = document.createElement("div");
    scan.id = "scan-wave";
    scan.className = "scan-wave";
    mapEl.appendChild(scan);
  }
  scan.classList.remove("scan-reveal", "scan-retreat");
  void scan.offsetWidth; // restart the animation
  scan.classList.add(direction === "in" ? "scan-reveal" : "scan-retreat");
}

function scanTint(on) {
  const mapEl = $("spatial-map");
  if (!mapEl) return;
  let tint = $("scan-tint");
  if (!tint) {
    tint = document.createElement("div");
    tint.id = "scan-tint";
    tint.className = "scan-tint";
    mapEl.appendChild(tint);
  }
  if (on) {
    tint.hidden = false;
    requestAnimationFrame(() => tint.classList.add("tint-on"));
  } else {
    tint.classList.remove("tint-on");
    setTimeout(() => { tint.hidden = true; }, 800);
  }
}

// Beautiful translucent spectral layer that stays on the map while the
// screening layer is active (soft fade-in vignette + slow aurora shimmer).
function spectralGlow(on) {
  const mapEl = $("spatial-map");
  if (!mapEl) return;
  let g = $("spectral-glow");
  if (!g) {
    g = document.createElement("div");
    g.id = "spectral-glow";
    g.className = "spectral-glow";
    mapEl.appendChild(g);
  }
  if (on) {
    g.hidden = false;
    requestAnimationFrame(() => requestAnimationFrame(() => g.classList.add("glow-on")));
  } else {
    g.classList.remove("glow-on");
    setTimeout(() => { g.hidden = true; }, 1350);
  }
}

function cancelReveal() {
  revealStopToken++;
  scanTint(false);
  spectralGlow(false);
  haloMarkers.forEach((m) => {
    const el = m.getElement && m.getElement();
    const halo = el && el.querySelector(".energy-halo");
    if (halo) halo.classList.add("halo-out");
  });
  setTimeout(() => {
    layers.spectral.clearLayers();
    haloMarkers = [];
  }, 150);
}

async function runScreeningReveal() {
  const toggle = $("toggle-space-layer");
  if (!toggle || !map) return;
  const token = ++revealStopToken;
  // The translucent spectral layer fades in straight away so the map visibly
  // transforms the moment the toggle turns on (before the sweep passes).
  scanTint(true);
  spectralGlow(true);
  sweepMap("in");
  setStatus("Spectral screening: orbit scan sweep underway…", "info");
  // Halos pop in as the sweep wave passes each zone (sequential reveal).
  await delay(1200);
  if (token !== revealStopToken || !toggle.checked) { scanTint(false); return; }

  // Ranked by spectral similarity; suppressed zones (score withheld) come
  // last but still get a halo, so every zone's colour shows - including the
  // green LOW-priority zone that would otherwise silently disappear.
  const ranked = [...zonesCache].sort((a, b) => {
    const sa = (a.spectral_similarity === null || a.spectral_similarity === undefined) ? -1 : a.spectral_similarity;
    const sb = (b.spectral_similarity === null || b.spectral_similarity === undefined) ? -1 : b.spectral_similarity;
    return sb - sa;
  });

  for (const z of ranked) {
    if (token !== revealStopToken || !toggle.checked) { scanTint(false); return; }
    addEnergyHalo(z);
    await delay(430);
  }
  if (token !== revealStopToken) { scanTint(false); return; }
  screeningActive = true;
  scanTint(false);
  setStatus("Spectral screening complete: energy halos show zone-level similarity to Pyrolusite.", "ok");
  setWorkflowStep(2);
}

function addEnergyHalo(z) {
  const st = priorityStyle(z.spatial_priority_band);
  const sim = z.spectral_similarity;
  const withheld = sim === null || sim === undefined;
  const radius = 48 + Math.round(((sim || 0) / 100) * 56); // 48..104 px
  const haloSize = radius * 2;
  const coreSize = Math.round(haloSize * 0.34);
  const icon = L.divIcon({
    className: "energy-halo-icon",
    html:
      `<div class="energy-halo" style="width:${haloSize}px;height:${haloSize}px;">` +
      `<div class="energy-halo-core" style="width:${coreSize}px;height:${coreSize}px;"></div>` +
      `</div>` +
      `<div class="halo-label">` +
      `<span class="halo-kicker">Spectral Similarity</span>` +
      `<span class="halo-sim${withheld ? " withheld" : ""}">${withheld ? "SCORE WITHHELD" : fmt(sim, "%")}</span>` +
      `<span class="halo-mineral">Pyrolusite</span>` +
      `</div>`,
    iconSize: [haloSize, haloSize],
    iconAnchor: [radius, radius],
  });
  const m = L.marker([z.latitude, z.longitude], { icon, interactive: false, keyboard: false });
  m.on("add", () => {
    const el = m.getElement();
    if (!el) return;
    el.style.setProperty("--halo-color", st.color);
    el.style.setProperty("--halo-glow", st.glow);
    const halo = el.querySelector(".energy-halo");
    const core = el.querySelector(".energy-halo-core");
    const lbl = el.querySelector(".halo-label");
    if (!halo) return;
    requestAnimationFrame(() => {
      halo.classList.add("halo-in");
      if (core) core.classList.add("halo-core-in");
      if (lbl) lbl.classList.add("halo-label-in");
    });
  });
  m.addTo(layers.spectral);
  haloMarkers.push(m);
}

async function dissolveScreening() {
  const token = ++revealStopToken;
  scanTint(false);
  spectralGlow(false);
  haloMarkers.forEach((m) => {
    const el = m.getElement && m.getElement();
    const halo = el && el.querySelector(".energy-halo");
    if (halo) { halo.classList.remove("halo-in"); halo.classList.add("halo-out"); }
    const core = el && el.querySelector(".energy-halo-core");
    if (core) core.classList.remove("halo-core-in");
    const lbl = el && el.querySelector(".halo-label");
    if (lbl) lbl.classList.remove("halo-label-in");
  });
  sweepMap("out");
  setStatus("Spectral screening off: halos dissolving, clean spatial map restored.", "info");
  await delay(1250);
  layers.spectral.clearLayers();
  haloMarkers = [];
  screeningActive = false;
  setWorkflowStep(1);
}

// ---------------- ZONE DETAIL ----------------
async function showZoneDetail(zoneId) {
  // The surface-filter inspector always runs over the clearly-labelled
  // SYNTHETIC_DEMO chip so its numbers reconcile with the rendered canvas.
  const r = await fetch(`/api/zones/${zoneId}?veg_demo=1`);
  if (!r.ok) return;
  const z = await r.json();
  activeZone = z;
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
    $("zp-best-mineral").textContent = "Best Mineral Match: unavailable";
  } else {
    const bestName = z.best_mineral_match
      ? z.best_mineral_match.charAt(0).toUpperCase() + z.best_mineral_match.slice(1)
      : "N/A";
    $("zp-spectral").textContent = fmt(z.spectral_similarity, "%");
    $("zp-spectral-band").textContent = tier.label;
    $("zp-spectral-band").style.color = tier.color;
    $("zp-best-mineral").textContent = `Best Mineral Match: ${bestName}`;
  }

  const sceneTxt = scene
    ? `${scene.platform} · ${scene.date} · ${scene.scene_id}`
    : "Sentinel-2 scene metadata unavailable";
  $("zp-spectral-scene").textContent =
    `SYNTHETIC DEMO chip · ${z.demo_chip ? `${z.demo_chip.total_pixels} px · seed ${z.demo_chip.seed}` : "no chip meta"} · not a real scene`;

  $("zp-final").textContent = fmt(z.final_exploration_score, "%");
  $("zp-priority").textContent = z.priority;
  $("zp-priority").style.color = priorityColor(z.priority);
  $("zp-priority").style.borderColor = priorityColor(z.priority);

  // BAND FINGERPRINT (Sentinel-2 4 bands vs Pyrolusite reference)
  renderFingerprint(z.zone_reflectance);

  // WHY / ACTION
  $("zp-explanation").textContent = z.explanation;
  $("zp-action").textContent = "Recommended Action: " + (z.recommended_action || "Field sampling / assay verification");

  // PROVENANCE
  const prov = provenanceChip(z.data_provenance);
  const provEl = $("zp-provenance");
  provEl.textContent = prov.text;
  provEl.className = "provenance-chip " + prov.cls;
  $("zp-scientific-note").textContent = z.scientific_note;

  // VEGETATION (NDVI) MASK STATUS
  $("zp-veg-mask").textContent = vegetationMaskSummary(z.vegetation_mask, true);
  vegetationChain(z.vegetation_mask, z, true);

  // SYNTHETIC SURFACE RENDERER (RAW SURFACE)
  surfaceMode = "raw";
  surfaceChip = z.demo_chip
    ? {
        classes: z.demo_chip.classes || [],
        ndvi: z.demo_chip.ndvi || [],
        size: z.demo_chip.chip_size || 20,
        mask: z.vegetation_mask || {},
      }
    : null;
  if (surfaceChip && $("zp-surface-canvas")) {
    redrawSurface(null);
    surfaceStatsFrom(surfaceChip.mask);
    showNdviStats(surfaceChip);
    const state = $("zp-surface-state");
    const status = $("zp-surface-status");
    const btn = $("btn-surface-filter");
    const rawBtn = $("btn-surface-raw");
    const filtBtn = $("btn-surface-filtered");
    if (state) { state.textContent = "RAW SURFACE"; state.className = "surface-state"; }
    if (status) {
      if (!surfaceChip.mask.scorable && surfaceChip.mask.status === "APPLIED") {
        status.textContent = "Heavily vegetated — surface filter will be suppressed after NDVI";
        status.className = "sf-suppressed";
      } else {
        status.textContent = "Not screened — activate the NDVI Surface Filter";
        status.className = "";
      }
    }
    if (btn) btn.disabled = false;
    if (rawBtn) rawBtn.disabled = false;
    if (filtBtn) filtBtn.disabled = false;
    setViewToggle("raw");
  }
  // Show the synthetic satellite-like surface on the map at zoom.
  mountSurfaceOverlay(z);

  // Advance workflow strip: 1 spatial done, 2 spectral rendered, 3 fusion computed.
  // 4 (Field Verification) only lights up when HIGH priority.
  setWorkflowStep(z.priority === "HIGH" ? 4 : 3);
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
    soil_moisture_pct: parseFloat(document.getElementById("slider-soil-moisture").value),
    equipment_downtime_hours: parseFloat(document.getElementById("slider-downtime").value),
    blast_delay_minutes: parseFloat(document.getElementById("slider-blast-delay").value),
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
  set("val-soil-moisture", `${controls.soil_moisture_pct.toFixed(1)} %`);
  set("val-downtime", `${controls.equipment_downtime_hours.toFixed(1)} hrs`);
  set("val-blast-delay", `${Math.round(controls.blast_delay_minutes)} min`);
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

  if (prediction.customer_portfolio) {
    renderCustomerPortfolio(prediction.customer_portfolio);
  }

  planExecuted = !!data.plan_executed;
  updateExecuteButtons();
}

// ---------------- CUSTOMER CONTRACT COMMAND ----------------
function fmtInr(value, digits = 0) {
  const n = Number(value);
  return isFinite(n) ? `₹${fmtNum(n, digits)}` : "N/A";
}

function renderCustomerPortfolio(portfolio) {
  if (!portfolio) return;
  const scope = document.getElementById("customer-scope");
  if (scope) scope.textContent = `${portfolio.assigned_mine || "Selected mine"} · forecast as of ${portfolio.as_of_date || "today"}`;

  const summaryRows = document.getElementById("customer-summary-rows");
  if (summaryRows) {
    summaryRows.innerHTML = "";
    const values = [
      ["Contracted volume", fmtTons(portfolio.quantity_contracted_mt), ""],
      ["Expected delivery", fmtTons(portfolio.expected_delivery_mt), "ok"],
      ["Expected contract revenue", fmtInr(portfolio.expected_revenue_inr), "ok"],
      ["Delivery shortfall", fmtTons(portfolio.shortfall_mt), Number(portfolio.shortfall_mt) > 0 ? "danger" : "ok"],
      ["Delivery liability", fmtInr(portfolio.total_liability_inr), Number(portfolio.total_liability_inr) > 0 ? "danger" : "ok"],
    ];
    values.forEach(([label, value, tone]) => {
      const row = document.createElement("tr");
      const key = document.createElement("th");
      key.scope = "row";
      key.textContent = label;
      const amount = document.createElement("td");
      amount.className = tone;
      amount.textContent = value;
      row.appendChild(key);
      row.appendChild(amount);
      summaryRows.appendChild(row);
    });
  }
  const method = document.getElementById("customer-method");
  if (method && portfolio.method_note) method.textContent = portfolio.method_note;
  renderDeadlineTimeline(
    portfolio.contracts || [],
    Number(portfolio.forecast_daily_output_mt) || 0,
    portfolio.actual_output_regression || null,
  );

  const rows = document.getElementById("customer-contract-rows");
  if (!rows) return;
  rows.innerHTML = "";
  const contracts = portfolio.contracts || [];
  if (!contracts.length) {
    const empty = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 8;
    cell.textContent = "No active contracts are assigned to the selected mine.";
    empty.appendChild(cell);
    rows.appendChild(empty);
    return;
  }
  contracts.forEach((contract) => {
    const row = document.createElement("tr");
    const details = [
      `${contract.customer_name || "Customer"} · ${contract.contract_id || ""}`,
      contract.assigned_mine || "—",
      `${contract.delivery_deadline || "—"} (${Number(contract.days_to_deadline)}d)`,
      fmtTons(contract.quantity_contracted_mt),
      fmtTons(contract.expected_delivery_mt),
      fmtTons(contract.shortfall_mt),
      fmtInr((Number(contract.short_value_inr) || 0) + (Number(contract.penalty_liability_inr) || 0)),
    ];
    details.forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    });
    const statusCell = document.createElement("td");
    const status = document.createElement("span");
    const statusText = String(contract.delivery_status || "ON_TRACK");
    status.className = `contract-status ${statusText.toLowerCase().replace("_", "-")}`;
    status.textContent = statusText.replace("_", " ");
    statusCell.appendChild(status);
    row.appendChild(statusCell);
    rows.appendChild(row);
  });
}

function renderDeadlineTimeline(contracts, dailyForecast, actualRegression) {
  const timeline = document.getElementById("deadline-timeline");
  if (!timeline) return;
  timeline.innerHTML = "";
  renderDeadlineForecastChart(contracts, dailyForecast, actualRegression);
  if (!contracts.length) {
    timeline.textContent = "No active contracts are assigned to the selected mine.";
    return;
  }
  const track = document.createElement("div");
  track.className = "deadline-track";
  const now = document.createElement("div");
  now.className = "deadline-now";
  now.textContent = "TODAY";
  timeline.appendChild(track);
  timeline.appendChild(now);
  const maxDays = Math.max(1, ...contracts.map((contract) => Math.max(0, Number(contract.days_to_deadline) || 0)));
  contracts.forEach((contract, index) => {
    const days = Number(contract.days_to_deadline) || 0;
    const status = String(contract.delivery_status || "ON_TRACK").toLowerCase().replace("_", "-");
    const marker = document.createElement("div");
    marker.className = `deadline-marker ${status}`;
    const position = Math.min(91, Math.max(11, 11 + (Math.max(0, days) / maxDays) * 80 + (index % 2) * 1.5));
    marker.style.left = `${position}%`;
    const card = document.createElement("div");
    card.className = "deadline-marker-card";
    const title = document.createElement("div");
    title.className = "deadline-marker-title";
    title.textContent = `${contract.customer_name} · ${contract.contract_id}`;
    const meta = document.createElement("div");
    meta.className = "deadline-marker-meta";
    meta.textContent = `${contract.delivery_deadline} · ${days < 0 ? `${Math.abs(days)}d overdue` : `${days}d remaining`}`;
    const state = document.createElement("div");
    state.className = "deadline-marker-status";
    state.textContent = `${String(contract.delivery_status || "ON_TRACK").replace("_", " ")} · ${fmtTons(contract.shortfall_mt)} short`;
    card.appendChild(title);
    card.appendChild(meta);
    card.appendChild(state);
    marker.appendChild(card);
    timeline.appendChild(marker);
  });
}

function renderDeadlineForecastChart(contracts, dailyForecast, actualRegression) {
  const svg = document.getElementById("deadline-forecast-chart");
  if (!svg) return;
  svg.innerHTML = "";
  const NS = "http://www.w3.org/2000/svg";
  const make = (tag, attrs = {}, text = null) => {
    const node = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    if (text != null) node.textContent = text;
    return node;
  };
  if (!contracts.length) {
    svg.appendChild(make("text", { x: 280, y: 110, "text-anchor": "middle", fill: "#94A3B8", "font-size": 13 }, "No active contract deadlines to forecast."));
    return;
  }
  const ordered = [...contracts].sort((a, b) => Number(a.days_to_deadline) - Number(b.days_to_deadline));
  let committed = 0;
  const points = ordered.map((contract) => {
    committed += Number(contract.quantity_contracted_mt) || 0;
    const x = Math.max(0, Number(contract.days_to_deadline) || 0);
    return { x, committed, forecast: dailyForecast * (x + 1), contract };
  });
  const width = 560; const height = 220;
  const margin = { top: 18, right: 22, bottom: 42, left: 58 };
  const maxX = Math.max(1, ...points.map((point) => point.x));
  const regressionCeiling = (() => {
    if (!actualRegression) return 0;
    const slope = Number(actualRegression.slope_mt_per_day) || 0;
    const intercept = Number(actualRegression.intercept_mt) || 0;
    const origin = Number(actualRegression.origin_index) || 0;
    let total = 0;
    for (let offset = 0; offset <= Math.floor(maxX); offset += 1) total += Math.max(0, slope * (origin + offset) + intercept);
    return total;
  })();
  const maxY = Math.max(1, regressionCeiling, ...points.flatMap((point) => [point.committed, point.forecast])) * 1.12;
  const sx = (value) => margin.left + (value / maxX) * (width - margin.left - margin.right);
  const sy = (value) => height - margin.bottom - (value / maxY) * (height - margin.top - margin.bottom);

  [0, .25, .5, .75, 1].forEach((fraction) => {
    const value = maxY * fraction;
    const y = sy(value);
    svg.appendChild(make("line", { x1: margin.left, x2: width - margin.right, y1: y, y2: y, stroke: "#26334F", "stroke-width": 1 }));
    svg.appendChild(make("text", { x: margin.left - 8, y: y + 4, "text-anchor": "end", fill: "#94A3B8", "font-size": 10 }, `${fmtNum(value / 1000, 1)}k`));
  });
  svg.appendChild(make("line", { x1: margin.left, x2: width - margin.right, y1: height - margin.bottom, y2: height - margin.bottom, stroke: "#64748B", "stroke-width": 1 }));
  svg.appendChild(make("text", { x: margin.left, y: 12, fill: "#94A3B8", "font-size": 10 }, "CUMULATIVE TONNES (MT)"));
  svg.appendChild(make("text", { x: width - margin.right, y: height - 10, "text-anchor": "end", fill: "#94A3B8", "font-size": 10 }, "DAYS UNTIL CONTRACT DEADLINE"));

  const pathFor = (key) => points.map((point, index) => `${index ? "L" : "M"}${sx(point.x)},${sy(point[key])}`).join(" ");
  svg.appendChild(make("path", { d: pathFor("committed"), fill: "none", stroke: "#FBBF24", "stroke-width": 3, "stroke-linejoin": "round" }));
  svg.appendChild(make("path", { d: pathFor("forecast"), fill: "none", stroke: "#38BDF8", "stroke-width": 3, "stroke-linejoin": "round" }));

  const regressionCumulative = (days) => {
    if (!actualRegression) return 0;
    const slope = Number(actualRegression.slope_mt_per_day) || 0;
    const intercept = Number(actualRegression.intercept_mt) || 0;
    const origin = Number(actualRegression.origin_index) || 0;
    let total = 0;
    for (let offset = 0; offset <= Math.floor(days); offset += 1) total += Math.max(0, slope * (origin + offset) + intercept);
    return total;
  };
  if (actualRegression) {
    const trendStart = regressionCumulative(0);
    const trendEnd = regressionCumulative(maxX);
    svg.appendChild(make("path", { d: `M${sx(0)},${sy(trendStart)} L${sx(maxX)},${sy(trendEnd)}`, fill: "none", stroke: "#34D399", "stroke-width": 2, "stroke-dasharray": "6 5" }));
    svg.appendChild(make("text", { x: width - margin.right, y: 12, "text-anchor": "end", fill: "#34D399", "font-size": 10 }, `Actual ROM regression: y = ${Number(actualRegression.slope_mt_per_day).toFixed(2)}x + ${fmtNum(actualRegression.intercept_mt, 0)}`));
  }

  points.forEach((point) => {
    svg.appendChild(make("circle", { cx: sx(point.x), cy: sy(point.committed), r: 4, fill: "#FBBF24", stroke: "#0B1220", "stroke-width": 2 }));
    svg.appendChild(make("circle", { cx: sx(point.x), cy: sy(point.forecast), r: 4, fill: Number(point.contract.shortfall_mt) > 0 ? "#F87171" : "#38BDF8", stroke: "#0B1220", "stroke-width": 2 }));
    svg.appendChild(make("text", { x: sx(point.x), y: height - margin.bottom + 18, "text-anchor": "middle", fill: "#CBD5E1", "font-size": 10 }, `${point.x}d`));
  });
  const guide = make("line", { y1: margin.top, y2: height - margin.bottom, stroke: "#CBD5E1", "stroke-width": 1, "stroke-dasharray": "3 3", visibility: "hidden" });
  const tooltip = make("g", { visibility: "hidden" });
  const tooltipBox = make("rect", { width: 195, height: 52, rx: 4, fill: "#101A2C", stroke: "#38BDF8" });
  const tooltipTitle = make("text", { x: 8, y: 17, fill: "#F8FAFC", "font-size": 10, "font-weight": 800 });
  const tooltipValue = make("text", { x: 8, y: 35, fill: "#A8B6CA", "font-size": 10 });
  tooltip.appendChild(tooltipBox); tooltip.appendChild(tooltipTitle); tooltip.appendChild(tooltipValue);
  const overlay = make("rect", { x: margin.left, y: margin.top, width: width - margin.left - margin.right, height: height - margin.top - margin.bottom, fill: "transparent", "pointer-events": "all" });
  overlay.addEventListener("pointermove", (event) => {
    const rect = svg.getBoundingClientRect();
    const mouseX = ((event.clientX - rect.left) / rect.width) * width;
    const point = points.reduce((closest, candidate) => Math.abs(sx(candidate.x) - mouseX) < Math.abs(sx(closest.x) - mouseX) ? candidate : closest, points[0]);
    const x = sx(point.x); const y = Math.max(margin.top + 2, sy(Math.max(point.committed, point.forecast)) - 60);
    guide.setAttribute("x1", x); guide.setAttribute("x2", x); guide.setAttribute("visibility", "visible");
    tooltip.setAttribute("transform", `translate(${Math.min(width - 205, Math.max(margin.left, x - 90))} ${y})`); tooltip.setAttribute("visibility", "visible");
    tooltipTitle.textContent = `${point.contract.customer_name} · ${point.x}d`;
    tooltipValue.textContent = `Forecast ${fmtTons(point.forecast)} | Contract ${fmtTons(point.committed)}`;
  });
  overlay.addEventListener("pointerleave", () => { guide.setAttribute("visibility", "hidden"); tooltip.setAttribute("visibility", "hidden"); });
  svg.appendChild(guide); svg.appendChild(tooltip); svg.appendChild(overlay);
}

// The delivery graph deliberately uses daily ROM rather than cumulative contract
// volume: the yellow trace is the last observed production history, the blue
// trace is the model's forecast from TODAY onwards, and green is y = mx + c.
// This makes the regression line traceable to real production observations.
function renderDeadlineForecastChart(contracts, dailyForecast, actualRegression) {
  const svg = document.getElementById("deadline-forecast-chart");
  if (!svg) return;
  svg.innerHTML = "";
  const NS = "http://www.w3.org/2000/svg";
  const make = (tag, attrs = {}, text = null) => {
    const node = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, String(value)));
    if (text !== null) node.textContent = text;
    return node;
  };
  if (!contracts.length) {
    svg.appendChild(make("text", { x: 280, y: 110, "text-anchor": "middle", fill: "#94A3B8", "font-size": 13 }, "No active contract deadlines to forecast."));
    return;
  }

  const width = 560; const height = 220;
  const margin = { top: 24, right: 22, bottom: 42, left: 58 };
  const history = Array.isArray(actualRegression?.history) ? actualRegression.history : [];
  const observed = history.map((row, index) => ({ x: index, y: Number(row.actual_rom_tonnes) || 0, date: row.date || "Observed day" }));
  if (!observed.length && actualRegression) observed.push({ x: 0, y: Number(actualRegression.fitted_today_daily_mt) || 0, date: actualRegression.last_observed_date || "Today" });
  const todayIndex = Math.max(0, observed.length - 1);
  const maxDays = Math.max(1, ...contracts.map((contract) => Math.max(0, Number(contract.days_to_deadline) || 0)));
  const maxX = todayIndex + maxDays;
  const regressionValue = (index) => actualRegression
    ? Math.max(0, (Number(actualRegression.slope_mt_per_day) || 0) * index + (Number(actualRegression.intercept_mt) || 0))
    : Math.max(0, Number(dailyForecast) || 0);
  // Preserve the ML prediction at TODAY, then project it along the slope of
  // the fitted actual-production line. This avoids a misleading flat forecast
  // while keeping the model's current production estimate as the anchor.
  const forecastValue = (days) => Math.max(0, (Number(dailyForecast) || 0) + (regressionValue(todayIndex + days) - regressionValue(todayIndex)));
  const forecast = Array.from({ length: maxDays + 1 }, (_, days) => ({ x: todayIndex + days, y: forecastValue(days), days }));
  const regression = Array.from({ length: maxX + 1 }, (_, x) => ({ x, y: regressionValue(x) }));
  const deadlines = contracts.map((contract) => ({
    x: todayIndex + Math.max(0, Number(contract.days_to_deadline) || 0),
    y: forecastValue(Math.max(0, Number(contract.days_to_deadline) || 0)),
    contract,
  }));
  const maxY = Math.max(1, ...observed.map((point) => point.y), ...forecast.map((point) => point.y), ...regression.map((point) => point.y)) * 1.16;
  const sx = (x) => margin.left + (x / Math.max(1, maxX)) * (width - margin.left - margin.right);
  const sy = (y) => height - margin.bottom - (y / maxY) * (height - margin.top - margin.bottom);
  const pathFor = (points) => points.map((point, index) => `${index ? "L" : "M"}${sx(point.x)},${sy(point.y)}`).join(" ");

  [0, .25, .5, .75, 1].forEach((fraction) => {
    const value = maxY * fraction; const y = sy(value);
    svg.appendChild(make("line", { x1: margin.left, x2: width - margin.right, y1: y, y2: y, stroke: "#26334F", "stroke-width": 1 }));
    svg.appendChild(make("text", { x: margin.left - 8, y: y + 4, "text-anchor": "end", fill: "#94A3B8", "font-size": 10 }, `${fmtNum(value / 1000, 1)}k`));
  });
  svg.appendChild(make("line", { x1: margin.left, x2: width - margin.right, y1: height - margin.bottom, y2: height - margin.bottom, stroke: "#64748B", "stroke-width": 1 }));
  svg.appendChild(make("text", { x: margin.left, y: 13, fill: "#94A3B8", "font-size": 10 }, "DAILY ROM OUTPUT (MT)"));
  svg.appendChild(make("text", { x: width - margin.right, y: height - 10, "text-anchor": "end", fill: "#94A3B8", "font-size": 10 }, "OBSERVED ACTUALS → FORECAST DAYS"));
  if (observed.length > 1) svg.appendChild(make("path", { d: pathFor(observed), fill: "none", stroke: "#FBBF24", "stroke-width": 2.5, "stroke-linejoin": "round" }));
  svg.appendChild(make("path", { d: pathFor(forecast), fill: "none", stroke: "#38BDF8", "stroke-width": 3, "stroke-linejoin": "round" }));
  if (actualRegression) {
    svg.appendChild(make("path", { d: pathFor(regression), fill: "none", stroke: "#34D399", "stroke-width": 2, "stroke-dasharray": "6 5" }));
    svg.appendChild(make("text", { x: width - margin.right, y: 13, "text-anchor": "end", fill: "#34D399", "font-size": 10 }, `Trend: y = ${Number(actualRegression.slope_mt_per_day).toFixed(2)}x + ${fmtNum(actualRegression.intercept_mt, 0)}`));
  }
  observed.forEach((point) => svg.appendChild(make("circle", { cx: sx(point.x), cy: sy(point.y), r: 2.3, fill: "#FBBF24" })));
  const todayX = sx(todayIndex);
  svg.appendChild(make("line", { x1: todayX, x2: todayX, y1: margin.top, y2: height - margin.bottom, stroke: "#E2E8F0", "stroke-width": 1, "stroke-dasharray": "3 3" }));
  svg.appendChild(make("text", { x: todayX, y: height - margin.bottom + 17, "text-anchor": "middle", fill: "#E2E8F0", "font-size": 10, "font-weight": 800 }, "TODAY"));
  deadlines.forEach((point, index) => {
    const atRisk = Number(point.contract.shortfall_mt) > 0;
    svg.appendChild(make("circle", { cx: sx(point.x), cy: sy(point.y), r: 4.5, fill: atRisk ? "#F87171" : "#38BDF8", stroke: "#0B1220", "stroke-width": 2 }));
    if (index === 0 || point.x !== deadlines[index - 1].x) svg.appendChild(make("text", { x: sx(point.x), y: height - margin.bottom + 31, "text-anchor": "middle", fill: atRisk ? "#FCA5A5" : "#7DD3FC", "font-size": 9 }, `${point.contract.days_to_deadline}d`));
  });

  const guide = make("line", { y1: margin.top, y2: height - margin.bottom, stroke: "#CBD5E1", "stroke-width": 1, "stroke-dasharray": "3 3", visibility: "hidden" });
  const tooltip = make("g", { visibility: "hidden" });
  const tooltipBox = make("rect", { width: 225, height: 66, rx: 4, fill: "#101A2C", stroke: "#38BDF8" });
  const tooltipTitle = make("text", { x: 8, y: 17, fill: "#F8FAFC", "font-size": 10, "font-weight": 800 });
  const tooltipValue = make("text", { x: 8, y: 35, fill: "#A8B6CA", "font-size": 10 });
  const tooltipDetail = make("text", { x: 8, y: 51, fill: "#A8B6CA", "font-size": 10 });
  tooltip.appendChild(tooltipBox); tooltip.appendChild(tooltipTitle); tooltip.appendChild(tooltipValue); tooltip.appendChild(tooltipDetail);
  const overlay = make("rect", { x: margin.left, y: margin.top, width: width - margin.left - margin.right, height: height - margin.top - margin.bottom, fill: "transparent", "pointer-events": "all" });
  const showTooltip = (event) => {
    const rect = svg.getBoundingClientRect();
    const mouseX = ((event.clientX - rect.left) / rect.width) * width;
    const xIndex = Math.max(0, Math.min(maxX, Math.round(((mouseX - margin.left) / (width - margin.left - margin.right)) * maxX)));
    const x = sx(xIndex); const observedPoint = observed.find((point) => point.x === xIndex);
    const deadlineAtPoint = deadlines.filter((point) => point.x === xIndex);
    const output = observedPoint ? observedPoint.y : forecastValue(Math.max(0, xIndex - todayIndex));
    guide.setAttribute("x1", x); guide.setAttribute("x2", x); guide.setAttribute("visibility", "visible");
    tooltip.setAttribute("transform", `translate(${Math.min(width - 235, Math.max(margin.left, x - 106))} ${Math.max(margin.top + 2, sy(output) - 71)})`);
    tooltip.setAttribute("visibility", "visible");
    tooltipTitle.textContent = observedPoint ? `Actual · ${observedPoint.date}` : `Forecast · ${xIndex - todayIndex} day(s) after today`;
    tooltipValue.textContent = `${observedPoint ? "Actual" : "Model forecast"}: ${fmtTons(output)} · Trend: ${fmtTons(regressionValue(xIndex))}`;
    tooltipDetail.textContent = deadlineAtPoint.length ? deadlineAtPoint.map((point) => `${point.contract.contract_id}: ${fmtTons(point.contract.quantity_contracted_mt)}`).join(" · ") : "Hover a deadline marker for its commitment.";
  };
  overlay.addEventListener("pointermove", showTooltip);
  overlay.addEventListener("mousemove", showTooltip);
  overlay.addEventListener("pointerleave", () => { guide.setAttribute("visibility", "hidden"); tooltip.setAttribute("visibility", "hidden"); });
  svg.appendChild(guide); svg.appendChild(tooltip); svg.appendChild(overlay);
}

function renderCustomerDirectory(customers) {
  const rows = document.getElementById("customer-directory-rows");
  if (!rows) return;
  rows.innerHTML = "";
  if (!customers.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.textContent = "No customers have been added.";
    row.appendChild(cell);
    rows.appendChild(row);
    return;
  }
  customers.forEach((customer) => {
    const row = document.createElement("tr");
    [customer.customer_name, customer.assigned_mine || "—", customer.contact_person || "—", customer.contact_email || "—", customer.contact_phone || "—"].forEach((value) => {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.appendChild(cell);
    });
    rows.appendChild(row);
  });
}

function populateContractCustomerSelect(customers) {
  const select = document.getElementById("customer-contract-customer");
  if (!select) return;
  const selected = select.value;
  select.innerHTML = "";
  customers.forEach((customer) => {
    const option = document.createElement("option");
    option.value = customer.customer_name;
    option.textContent = customer.customer_name;
    select.appendChild(option);
  });
  if (selected) select.value = selected;
}

function populateMineSelects(mines) {
  ["customer-assigned-mine", "customer-mine"].forEach((id) => {
    const select = document.getElementById(id);
    if (!select) return;
    const selected = select.value || "Balaghat";
    select.innerHTML = "";
    mines.forEach((mine) => {
      const option = document.createElement("option");
      option.value = mine;
      option.textContent = mine;
      select.appendChild(option);
    });
    select.value = selected;
  });
}

async function loadCustomers() {
  try {
    const data = await apiFetch("/api/customers");
    lastCustomerContracts = data.contracts || [];
    lastCustomers = data.customers || [];
    availableMines = data.available_mines || ["Balaghat"];
    renderCustomerDirectory(lastCustomers);
    populateContractCustomerSelect(lastCustomers);
    populateMineSelects(availableMines);
    renderCustomerPortfolio(data.portfolio);
  } catch (err) {
    setStatus(err.message || "Customer contracts could not be loaded.", "error");
  }
}

async function addCustomer(event) {
  event.preventDefault();
  // currentTarget is cleared by the browser once an async event handler
  // yields. Capture the form before awaiting the POST so the UI can reset,
  // close and reload after a successful customer creation.
  const form = event.currentTarget;
  if (!form || !form.reportValidity()) return;
  const submit = form.querySelector("button[type='submit']");
  const payload = {
    entity: "customer",
    customer_name: document.getElementById("customer-name").value.trim(),
    contact_person: document.getElementById("customer-contact-person").value.trim(),
    contact_email: document.getElementById("customer-email").value.trim(),
    contact_phone: document.getElementById("customer-phone").value.trim(),
    assigned_mine: document.getElementById("customer-assigned-mine").value,
  };
  if (submit) submit.disabled = true;
  try {
    await apiFetch("/api/customers", { method: "POST", body: JSON.stringify(payload) });
    form.reset();
    document.getElementById("customer-modal").close();
    setStatus("Customer added to this demo session.", "ok");
    await loadCustomers();
  } catch (err) {
    setStatus(err.message || "Customer could not be added.", "error");
  } finally {
    if (submit) submit.disabled = false;
  }
}

async function addCustomerContract(event) {
  event.preventDefault();
  const form = event.currentTarget;
  if (!form || !form.reportValidity()) return;
  const submit = form.querySelector("button[type='submit']");
  const payload = {
    entity: "contract",
    customer_name: document.getElementById("customer-contract-customer").value,
    quantity_contracted_mt: Number(document.getElementById("customer-quantity").value),
    price_offered_per_mt: Number(document.getElementById("customer-price").value),
    delivery_deadline: document.getElementById("customer-deadline").value,
    penalty_type: document.getElementById("customer-penalty-type").value,
    penalty_value: Number(document.getElementById("customer-penalty-value").value),
    assigned_mine: document.getElementById("customer-mine").value.trim(),
  };
  if (submit) submit.disabled = true;
  try {
    await apiFetch("/api/customers", { method: "POST", body: JSON.stringify(payload) });
    form.reset();
    setDefaultCustomerDeadline();
    document.getElementById("contract-modal").close();
    setStatus("Customer contract added to this demo session.", "ok");
    await refreshAll(true);
    await loadCustomers();
  } catch (err) {
    setStatus(err.message || "Customer contract could not be added.", "error");
  } finally {
    if (submit) submit.disabled = false;
  }
}

function setDefaultCustomerDeadline() {
  const deadline = document.getElementById("customer-deadline");
  if (!deadline || deadline.value) return;
  const date = new Date();
  date.setDate(date.getDate() + 7);
  deadline.value = date.toISOString().slice(0, 10);
}

function switchCustomerTab(name) {
  const portfolio = name === "portfolio";
  document.getElementById("customer-portfolio-tab").hidden = !portfolio;
  document.getElementById("customer-management-tab").hidden = portfolio;
  document.querySelectorAll(".customer-tab").forEach((button) => {
    const active = button.dataset.customerTab === name;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
}

function bindCustomerCommand() {
  document.querySelectorAll(".customer-tab").forEach((button) => button.addEventListener("click", () => switchCustomerTab(button.dataset.customerTab)));
  document.getElementById("open-customer-modal")?.addEventListener("click", () => document.getElementById("customer-modal").showModal());
  document.getElementById("open-contract-modal")?.addEventListener("click", () => {
    if (!lastCustomers.length) {
      setStatus("Add a customer before creating a contract.", "warn");
      document.getElementById("customer-modal").showModal();
      return;
    }
    setDefaultCustomerDeadline();
    document.getElementById("contract-modal").showModal();
  });
  document.getElementById("customer-contract-customer")?.addEventListener("change", (event) => {
    const selected = lastCustomers.find((customer) => customer.customer_name === event.target.value);
    if (selected && selected.assigned_mine) document.getElementById("customer-mine").value = selected.assigned_mine;
  });
  document.querySelectorAll(".customer-modal .modal-close, .customer-modal [value='cancel']").forEach((button) => button.addEventListener("click", () => button.closest("dialog").close()));
  document.getElementById("customer-form")?.addEventListener("submit", addCustomer);
  document.getElementById("customer-contract-form")?.addEventListener("submit", addCustomerContract);
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
  const ids = ["slider-rainfall", "slider-soil-moisture", "slider-downtime", "slider-blast-delay", "slider-labor", "input-target"];
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
  setDefaultCustomerDeadline();
  updateControlBadges(getControls());
  await Promise.allSettled([loadTelemetry(), loadSpectral()]);
  await Promise.allSettled([loadAOI(), loadZones(true)]);
  await loadCustomers();
  await refreshAll();
}

document.addEventListener("DOMContentLoaded", () => {
  document.addEventListener("click", (event) => {
    if (event.target.closest("#zone-panel-close")) closeZonePanel(event);
  }, true);
  updateClock();
  setInterval(updateClock, 1000);
  bindControls();
  bindCustomerCommand();
  init();
  setInterval(loadTelemetry, 5000);
});
