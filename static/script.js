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

// Canonical operational-status colours. This is the ONE colour language used
// by the map orbs, zone cards and the map legend:
//   FLOODED = red, OPERATIONAL = green, SPECTRAL ANOMALY / risk = amber.
// Priority/prospectivity is carried as TEXT only so colours always mean the
// same thing on screen.
const STATUS_META = {
  FLOODED: { color: "#EF4444", glow: "rgba(239, 68, 68, 0.70)" },
  OPERATIONAL: { color: "#22C55E", glow: "rgba(34, 197, 94, 0.70)" },
  "SPECTRAL ANOMALY": { color: "#F59E0B", glow: "rgba(245, 158, 11, 0.70)" },
  "UNDER INVESTIGATION": { color: "#94A3B8", glow: "rgba(148, 163, 184, 0.65)" },
};
// Resolve a zone's canonical status (always prefer the machine field).
function zoneStatus(z) {
  if (!z) return "UNDER INVESTIGATION";
  const s = String(z.status || z.operational_status || "").toUpperCase();
  if (STATUS_META[s]) return s;
  if (/flood|inundat|submerg|waterlog/i.test(s)) return "FLOODED";
  if (/anomal/i.test(s)) return "SPECTRAL ANOMALY";
  if (/dry|active|operational|normal|open|ok|nominal|running/i.test(s)) return "OPERATIONAL";
  return "UNDER INVESTIGATION";
}
const statusStyle = (z) => STATUS_META[zoneStatus(z)] || STATUS_META["UNDER INVESTIGATION"];
const statusColor = (z) => statusStyle(z).color;

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
  hidePixelPopup();
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
    const st = statusStyle(z);

    // Soft expanding halo ring behind the orb (subtle pulse).
    L.marker([lat, lon], {
      icon: L.divIcon({
        className: "zone-ring-icon",
        html: `<div class="zone-ring" style="width:60px;height:60px;"></div>`,
        iconSize: [60, 60],
        iconAnchor: [30, 30],
      }),
      interactive: false,
    }).on("add", (ev) => {
      const ring = ev.target.getElement().querySelector(".zone-ring");
      if (ring) ring.style.setProperty("--zone-color", st.color);
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
    const status = zoneStatus(z);
    orb.bindTooltip(
      `<b>${z.name}</b><br>` +
      `Status: <b>${status}</b><br>` +
      `Spatial: <b>${z.spatial_score}%</b> (${z.spatial_priority_band})<br>` +
      `Spectral: <b>${fmt(z.spectral_similarity, "%")}</b><br>` +
      `Fusion: <b>${z.final_exploration_score}%</b> → ${z.priority}<br>` +
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
  if (expandMapOpen) {
    // Expanded map: a small floating card near the zone instead of the full
    // panel, so the expanded map stays usable.
    showExpandedZoneCard(z);
    return;
  }
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

// ---------------- COMPACT PIXEL READOUT ----------------
// Clicking an individual pixel on the synthetic surface shows a small popup
// with only that pixel's class + NDVI (never the giant zone panel).
let pixelPopupActive = false;

function showPixelPopup(clientX, clientY, info) {
  const pop = $("pixel-popup");
  if (!pop) return;
  const clsLabel = String(info.cls || "exposed").toUpperCase();
  const clsInfo = {
    WATER: { color: "#38BDF8", label: "Water surface" },
    VEGETATION: { color: "#34D399", label: "Vegetation" },
    EXPOSED: { color: "#E2E8F0", label: "Exposed surface" },
  }[clsLabel] || { color: "#E2E8F0", label: "Exposed surface" };
  const ndvi = isFinite(Number(info.ndvi)) ? Number(info.ndvi).toFixed(2) : "--";
  pop.innerHTML =
    `<div class="pixel-popup-head">PIXEL ${String(info.j).padStart(2, "0")},${String(info.i).padStart(2, "0")}</div>` +
    `<div class="pixel-popup-class" style="color:${clsInfo.color}">${clsLabel}</div>` +
    `<div class="pixel-popup-row"><span>Class</span><strong>${clsInfo.label}</strong></div>` +
    `<div class="pixel-popup-row"><span>NDVI</span><strong>${ndvi}</strong></div>` +
    `<div class="pixel-popup-foot">SYNTHETIC DEMO pixel</div>`;
  pop.style.left = Math.min(clientX + 12, window.innerWidth - 200) + "px";
  pop.style.top = Math.max(8, clientY - 8) + "px";
  pop.hidden = false;
  pixelPopupActive = true;
}

function hidePixelPopup() {
  const pop = $("pixel-popup");
  if (pop) pop.hidden = true;
  pixelPopupActive = false;
}

function initPixelPicking() {
  const canvas = $("zp-surface-canvas");
  if (!canvas || canvas.dataset.pickWired) return;
  canvas.dataset.pickWired = "1";
  canvas.addEventListener("click", (ev) => {
    if (!surfaceChip) return;
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const size = surfaceChip.size || 20;
    const fx = (ev.clientX - rect.left) / rect.width;
    const fy = (ev.clientY - rect.top) / rect.height;
    const j = Math.max(0, Math.min(size - 1, Math.floor(fx * size)));
    const i = Math.max(0, Math.min(size - 1, Math.floor(fy * size)));
    const idx = i * size + j;
    const classes = surfaceChip.classes || [];
    const ndvi = (surfaceChip.ndvi && surfaceChip.ndvi[idx]) || null;
    showPixelPopup(ev.clientX, ev.clientY, {
      i,
      j,
      cls: classes[idx] || "exposed",
      ndvi,
    });
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
    // Keep an open zone panel honest: spectral numbers appear/disappear with
    // the screening toggle instead of contradicting the toolbar.
    refreshToolbarNote();
    if (activeZone) {
      renderSpectralPanel(activeZone);
      renderFinalPanel(activeZone);
    }
  });
}

function refreshToolbarNote() {
  const note = $("spectral-toolbar-note");
  const toggle = $("toggle-space-layer");
  if (!note) return;
  if (toggle && toggle.checked && screeningActive) {
    note.textContent = "Screening active — halos show similarity to Pyrolusite. Click a zone to analyse it";
  } else if (toggle && toggle.checked) {
    note.textContent = "Screening in progress…";
  } else {
    note.textContent = "Screening off — zoom into a zone to analyse it";
  }
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
  refreshToolbarNote();
  if (activeZone) {
    renderSpectralPanel(activeZone);
    renderFinalPanel(activeZone);
  }
  setStatus("Spectral screening complete: energy halos show zone-level similarity to Pyrolusite.", "ok");
  setWorkflowStep(2);
}

function addEnergyHalo(z) {
  // Halo colour = spectral confirmation tier (HIGH/LIKELY/WEAK/MISMATCH/NO DATA),
  // so the screening layer never collides with the status-coloured orbs below.
  const tier = confirmationTier(z.spectral_similarity);
  const st = { color: tier.color, glow: tier.color };
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
  refreshToolbarNote();
  if (activeZone) {
    renderSpectralPanel(activeZone);
    renderFinalPanel(activeZone);
  }
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
  const panel = $("zone-panel");
  if (!panel) return;
  const status = zoneStatus(z);
  const water =
    z.water_depth_m === null || z.water_depth_m === undefined
      ? "--"
      : `${z.water_depth_m.toFixed(1)} m` +
        (z.pumps_active === null || z.pumps_active === undefined
          ? ""
          : " · " + z.pumps_active + " pump" + (z.pumps_active === 1 ? "" : "s"));
  const impact = z.production_impact || "--";
  const action = z.recommended_action || "Field sampling / assay validation";

  panel.classList.add("is-open");
  panel.setAttribute("aria-hidden", "false");

  // HEADER
  $("zone-panel-title").textContent = z.name || z.zone_id;
  $("zone-panel-subtitle").textContent = z.zone_type || "";
  $("zp-zoneid").textContent = z.zone_id;
  const lat = z.latitude.toFixed(4), lon = z.longitude.toFixed(4);
  $("zp-coords").textContent = `${lat}°N · ${lon}°E`;

  // OPERATIONAL STATUS (canonical values from the backend)
  const stEl = $("zp-operational-status");
  stEl.textContent = status;
  stEl.style.color = statusColor(z);
  const impEl = $("zp-production-impact");
  impEl.textContent = impact;
  impEl.style.color =
    impact === "HIGH" ? "#EF4444"
    : impact === "REVIEW" || impact === "MODERATE" || impact === "MEDIUM" ? "#F59E0B"
    : "#22C55E";
  $("zp-water").textContent = water;
  $("zp-recommended-action").textContent = action;

  // SPATIAL PROSPECTIVITY
  $("zp-spatial").textContent = fmt(z.spatial_score, "%");
  $("zp-spatial-band").textContent = z.spatial_priority_band;
  $("zp-spatial-reason").textContent =
    `Spatial screening identifies this as a ${String(z.spatial_priority_band || "unavailable").toLowerCase()} prospectivity zone.`;

  // SPECTRAL INTELLIGENCE — gated behind the screening toggle. No spectral
  // percentages are shown until Spectral Mineral Screening is enabled.
  renderSpectralPanel(z);

  // FINAL EXPLORATION PRIORITY — when screening is off this is spatial-only.
  renderFinalPanel(z);

  // BAND FINGERPRINT (Sentinel-2 4 bands vs Pyrolusite reference) is rendered
  // only when screening is active (renderSpectralPanel does it).

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
  // 4 (Field Verification) only lights up when fused priority is HIGH.
  const fusedPriority = screeningActive ? z.priority : null;
  setWorkflowStep(fusedPriority === "HIGH" ? 4 : screeningActive ? 3 : 3);
}

// Renders the Spectral Intelligence section honouring the screening-toggle gate.
// Also used to refresh an already-open panel when the toggle flips.
function renderSpectralPanel(z) {
  const lock = $("zp-spectral-lock");
  const body = $("zp-spectral-body");
  if (!lock || !body) return;
  if (!screeningActive) {
    lock.hidden = false;
    body.hidden = true;
    return;
  }
  lock.hidden = true;
  body.hidden = false;
  const tier = confirmationTier(z.spectral_similarity);
  const scene = z.spectral_scene;
  if (z.spectral_similarity === null) {
    $("zp-spectral").textContent = "N/A";
    $("zp-spectral-band").textContent = "SCORE WITHHELD";
    $("zp-spectral-band").style.color = "#64748B";
    $("zp-best-mineral").textContent = "Best Mineral Match: reference unavailable";
  } else {
    const bestName = z.best_mineral_match
      ? z.best_mineral_match.charAt(0).toUpperCase() + z.best_mineral_match.slice(1)
      : "N/A";
    $("zp-spectral").textContent = fmt(z.spectral_similarity, "%");
    $("zp-spectral-band").textContent = tier.label;
    $("zp-spectral-band").style.color = tier.color;
    $("zp-best-mineral").textContent = `Best Mineral Match: ${bestName}`;
  }
  $("zp-spectral-scene").textContent = scene
    ? `${scene.platform} · ${scene.date} · ${scene.scene_id} · tile ${scene.tile || "--"} · cloud ${scene.cloud_cover_pct != null ? scene.cloud_cover_pct + "%" : "--"}`
    : "Sentinel-2 L2A scene metadata unavailable";
  renderFingerprint(z.zone_reflectance);
  const prov = provenanceChip(z.data_provenance);
  const provEl = $("zp-provenance");
  provEl.textContent = prov.text;
  provEl.className = "provenance-chip " + prov.cls;
}

// Final priority: full spatial+spectral fusion when screening is on, otherwise
// an explicitly-labelled spatial-only estimate (no hidden spectral influence).
function renderFinalPanel(z) {
  $("zp-priority").style.color = "";
  $("zp-priority").style.borderColor = "";
  if (!screeningActive) {
    $("zp-final").textContent = fmt(z.spatial_score, "%");
    $("zp-priority").textContent = "SPATIAL-ONLY";
    $("zp-explanation").textContent =
      `Screening inactive — priority shown from spatial data alone. Enable Spectral Mineral Screening for the fused ${z.spatial_priority_band} evaluation.`;
    $("zp-action").textContent = "Recommended action: " + (z.recommended_action || "Field sampling / assay validation");
    return;
  }
  $("zp-final").textContent = fmt(z.final_exploration_score, "%");
  $("zp-priority").textContent = z.priority;
  $("zp-explanation").textContent = z.explanation;
  $("zp-action").textContent = "Recommended action: " + (z.recommended_action || "Field sampling / assay validation");
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
    title.textContent = `${isFinite(sim) ? (sim * 100).toFixed(2) : "--"}% ${(data.label || "SPECTRAL SIMILARITY").toUpperCase()} · AOI-LEVEL SCREENING`;
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
// Simulated borehole-style monitoring readings. Values derive from the real
// synthetic chip NDVI where available; everything else is clearly SIM-tagged.
function monitoringSim(zone, pocket) {
  const ndviArr =
    zone && zone.demo_chip && Array.isArray(zone.demo_chip.ndvi) && zone.demo_chip.ndvi.length
      ? zone.demo_chip.ndvi
      : null;
  let ndvi;
  if (ndviArr && ndviArr.length) {
    ndvi = (ndviArr.reduce((a, b) => a + b, 0) / ndviArr.length).toFixed(2);
  } else {
    const salt = zone && zone.zone_id ? zone.zone_id.charCodeAt(zone.zone_id.length - 1) : 0;
    ndvi = (0.10 + (salt % 4) * 0.05).toFixed(2);
  }
  const zoneW = zone && zone.water_depth_m != null ? Number(zone.water_depth_m) : null;
  const wd = pocket && pocket.water_depth_m != null ? Number(pocket.water_depth_m) : zoneW;
  const moisture = wd == null ? "LOW" : wd >= 3 ? "HIGH" : wd >= 0.5 ? "MODERATE" : "LOW";
  const rawStatus = String((pocket && pocket.status) || (zone && zone.operational_status) || "").toLowerCase();
  const isAnomaly = /anomal/i.test(rawStatus);
  const sim =
    zone && typeof zone.spectral_similarity === "number" ? zone.spectral_similarity : null;
  const simPct = sim !== null ? (sim > 1 ? sim : sim * 100) : null;
  let deviation;
  let confidence;
  if (isAnomaly || simPct === null) {
    deviation = "+23%";
    confidence = 81;
  } else {
    deviation = "+" + Math.max(0.1, 100 - simPct).toFixed(1) + "%";
    confidence = Math.round(simPct * 0.9);
  }
  return { ndvi, moisture, deviation, confidence };
}

function monitorTooltipHtml(idx, p, zone) {
  const sim = monitoringSim(zone, p);
  const title = p.name || (zone && zone.name) || `Monitoring Point ${idx + 1}`;
  return (
    `<div class="monitor-tip">` +
    `<div class="monitor-tip-head">` +
    `<span class="monitor-tip-title">Monitoring Point ${String(idx + 1).padStart(2, "0")}</span>` +
    `<span class="monitor-sim-tag">SIM</span>` +
    `</div>` +
    `<div class="monitor-tip-sub">${title}</div>` +
    `<div class="monitor-tip-meta">` +
    `<div><span>NDVI</span><strong>${sim.ndvi}</strong></div>` +
    `<div><span>Surface moisture</span><strong>${sim.moisture}</strong></div>` +
    `<div><span>Spectral deviation</span><strong>${sim.deviation}</strong></div>` +
    `<div><span>Risk confidence</span><strong>${sim.confidence}%</strong></div>` +
    `</div>` +
    `<div class="monitor-tip-note">Simulated demo reading — not a real satellite observation.</div>` +
    `</div>`
  );
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
    // Every value shown here comes from the canonical zone record exposed by
    // the backend (constants.CANDIDATE_ZONES -> /api/telemetry). The front end
    // never re-derives status/impact/action.
    const zone = (zonesCache || []).find((zz) => zz.zone_id === p.id);
    const status = zoneStatus(zone || { status: p.status });
    const st = STATUS_META[status] || STATUS_META["UNDER INVESTIGATION"];
    const wd = p.water_depth_m;
    const pumps = p.pumps_active;
    const impact = p.production_impact || (zone && zone.production_impact) || "LOW";
    const action = p.recommended_action || (zone && zone.recommended_action) || "Review & investigate";

    const box = document.createElement("div");
    box.className = "pit-box";

    const name = document.createElement("div");
    name.className = "pit-name";
    name.textContent = p.name || zone.name || p.id || "Pit";
    box.appendChild(name);

    const stEl = document.createElement("div");
    stEl.className = "pit-state";
    stEl.textContent = status;
    stEl.style.color = st.color;
    box.appendChild(stEl);

    const meta = document.createElement("div");
    meta.className = "pit-meta";

    let waterLine = "Water condition: --";
    if (wd != null) {
      waterLine = `Water condition: ${Number(wd).toFixed(1)} m`;
      if (pumps != null) waterLine += ` · ${pumps} pump${pumps === 1 ? "" : "s"}`;
    }
    meta.appendChild(metaRow(waterLine));
    meta.appendChild(metaRow(`Production impact: ${impact}`));
    box.appendChild(meta);

    const act = document.createElement("div");
    act.className = "pit-action";
    act.textContent = "→ " + action;
    box.appendChild(act);

    grid.appendChild(box);
  });
}

function metaRow(text) {
  const div = document.createElement("div");
  div.className = "pit-meta-row";
  div.textContent = text;
  return div;
}

// ---------------- EXPANDED MAP MODAL ----------------
let expandMapOpen = false;
let expandSavedView = null;
let expandOriginalParent = null;
let expandWired = false;

function openExpandedMap() {
  const modal = document.getElementById("map-expand-modal");
  const body = document.getElementById("map-modal-body");
  const wrap = document.querySelector(".map-frame-wrap");
  if (!modal || !body || !wrap || expandMapOpen) return;
  expandOriginalParent = wrap.parentElement;
  expandSavedView = map ? [map.getCenter(), map.getZoom()] : null;
  body.appendChild(wrap);
  modal.hidden = false;
  expandMapOpen = true;
  document.body.classList.add("map-expanded-open");
  const closeBtn = document.getElementById("btn-close-expanded-map");
  if (closeBtn) closeBtn.focus();
  if (map) {
    map.invalidateSize();
    requestAnimationFrame(() => map.invalidateSize());
    map.flyTo([21.845, 80.232], Math.max(map.getZoom(), 15), { duration: 0.9 });
  }
}

function closeExpandedMap() {
  const modal = document.getElementById("map-expand-modal");
  const wrap = document.querySelector(".map-frame-wrap");
  if (!modal || !wrap || !expandMapOpen) return;
  hideExpandedZoneCard();
  modal.hidden = true;
  if (expandOriginalParent && expandOriginalParent !== modal) {
    expandOriginalParent.appendChild(wrap);
  }
  expandMapOpen = false;
  document.body.classList.remove("map-expanded-open");
  if (map) {
    if (expandSavedView) map.setView(expandSavedView[0], expandSavedView[1], { animate: false });
    map.invalidateSize();
    requestAnimationFrame(() => map.invalidateSize());
  }
  const btn = document.getElementById("btn-expand-map");
  if (btn) btn.focus();
}

// ---------------- EXPANDED-MODE ZONE MINI CARD ----------------
// In expanded view, clicking a zone opens a small card anchored near the zone
// marker instead of covering the map with the full panel.
let expandedCardZone = null;
let expandedCardWired = false;

function showExpandedZoneCard(z) {
  const card = document.getElementById("expanded-zone-card");
  const body = document.getElementById("map-modal-body");
  if (!card || !body || !expandMapOpen) return;
  expandedCardZone = z;
  const status = zoneStatus(z);
  const st = STATUS_META[status] || STATUS_META["UNDER INVESTIGATION"];
  const water =
    z.water_depth_m == null ? "--" : `${Number(z.water_depth_m).toFixed(1)} m`;
  const impact = z.production_impact || "LOW";
  const action = z.recommended_action || "Review & investigate";
  const sim = z.spectral_similarity;
  const simLine =
    screeningActive && sim != null
      ? `<div class="ezc-row"><span>Spectral</span><strong>${fmt(sim, "%")} · ${confirmationTier(sim).label}</strong></div>`
      : "";
  card.innerHTML =
    `<div class="ezc-head">` +
    `<span class="ezc-status" style="color:${st.color}">${status}</span>` +
    `<button type="button" class="ezc-close" id="ezc-close" aria-label="Close zone card">×</button>` +
    `</div>` +
    `<div class="ezc-title">${z.name || z.zone_id}</div>` +
    `<div class="ezc-rows">` +
    `<div class="ezc-row"><span>Water condition</span><strong>${water}</strong></div>` +
    `<div class="ezc-row"><span>Production impact</span><strong>${impact}</strong></div>` +
    `<div class="ezc-row"><span>Recommended action</span><strong>${action}</strong></div>` +
    simLine +
    `</div>` +
    `<div class="ezc-foot">SYNTHETIC DEMO zone data</div>`;
  card.hidden = false;
  const closeBtn = document.getElementById("ezc-close");
  if (closeBtn) {
    closeBtn.onclick = (ev) => {
      ev.stopPropagation();
      hideExpandedZoneCard();
    };
  }
  wireExpandedCardReposition();
  positionExpandedZoneCard(z);
}

function hideExpandedZoneCard() {
  const card = document.getElementById("expanded-zone-card");
  if (card) card.hidden = true;
  expandedCardZone = null;
}

function positionExpandedZoneCard(z) {
  const card = document.getElementById("expanded-zone-card");
  const body = document.getElementById("map-modal-body");
  if (!card || !body || !map || card.hidden) return;
  const pt = map.latLngToContainerPoint([z.latitude, z.longitude]);
  const bw = body.clientWidth || 800;
  const bh = body.clientHeight || 600;
  const cw = card.offsetWidth || 264;
  const ch = card.offsetHeight || 130;
  let left = pt.x + 16;
  if (left + cw > bw - 8) left = Math.max(8, pt.x - cw - 16);
  let top = pt.y - ch / 2;
  top = Math.max(8, Math.min(top, bh - ch - 8));
  card.style.left = left + "px";
  card.style.top = top + "px";
}

function wireExpandedCardReposition() {
  if (expandedCardWired || !map) return;
  expandedCardWired = true;
  map.on("move zoom", () => {
    if (expandMapOpen && expandedCardZone) positionExpandedZoneCard(expandedCardZone);
  });
  map.on("click", (ev) => {
    if (!expandMapOpen) return;
    const el = ev.originalEvent && ev.originalEvent.target;
    if (el && el.closest && el.closest(".zone-orb, .energy-halo, .telemetry-dot")) return;
    hideExpandedZoneCard();
  });
}

function initExpandMap() {
  if (expandWired) return;
  expandWired = true;
  const btn = document.getElementById("btn-expand-map");
  const closeBtn = document.getElementById("btn-close-expanded-map");
  const modal = document.getElementById("map-expand-modal");
  if (btn) btn.addEventListener("click", openExpandedMap);
  if (closeBtn) closeBtn.addEventListener("click", closeExpandedMap);
  if (modal) {
    modal.addEventListener("click", (event) => {
      if (event.target.classList && event.target.classList.contains("map-modal-backdrop")) closeExpandedMap();
    });
    document.addEventListener("keydown", (event) => {
      if (expandMapOpen && event.key === "Escape") {
        event.stopPropagation();
        closeExpandedMap();
      }
    });
  }
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
      (data.ore_pockets || []).forEach((p, i) => {
        const zone = (zonesCache || []).find((zz) => zz.zone_id === p.id) || {};
        const icon = L.divIcon({
          className: "telemetry-marker",
          html: `<div class="telemetry-dot">●</div>`,
          iconSize: [22, 22],
        });
        L.marker([p.lat, p.lon], { icon, riseOnHover: true })
          .bindTooltip(monitorTooltipHtml(i, p, zone), {
            direction: "top",
            offset: [0, -10],
            className: "monitor-tooltip",
          })
          .on("click", () => {
            const zone = (zonesCache || []).find((zz) => zz.zone_id === p.id) || {
              zone_id: p.id,
              name: p.name,
              latitude: p.lat,
              longitude: p.lon,
            };
            onZoneSelect(zone);
          })
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
  updateControlBadges(getControls());
  await Promise.allSettled([loadTelemetry(), loadSpectral()]);
  await Promise.allSettled([loadAOI(), loadZones(true)]);
  await refreshAll();
}

document.addEventListener("DOMContentLoaded", () => {
  document.addEventListener("click", (event) => {
    if (event.target.closest("#zone-panel-close")) closeZonePanel(event);
  }, true);
  // Clicking anywhere except the pixel canvas / popup dismisses the readout.
  document.addEventListener("click", (event) => {
    const t = event.target;
    if (!t || !t.closest) return;
    if (t.closest("#pixel-popup") || t.closest("#zp-surface-canvas")) return;
    hidePixelPopup();
  }, true);
  initPixelPicking();
  updateClock();
  setInterval(updateClock, 1000);
  bindControls();
  initExpandMap();
  init();
  setInterval(loadTelemetry, 5000);
});
