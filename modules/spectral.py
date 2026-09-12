# Reference spectral data:
# Pyrolusite HS138.3B W1R1Bb AREF
# USGS Digital Spectral Library (splib05a)
# USGS Open-File Report 03-395
# Source:
# https://pubs.usgs.gov/of/2003/ofr-03-395/ASCII/M/pyrolusite_hs138.5705.asc




"""Bharveli-Awalajhari AOI spectral-potential overlay."""

from __future__ import annotations

import math
import random
import statistics
import xml.etree.ElementTree as element_tree
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import plotly.graph_objects as go
    import streamlit as st
except ImportError:  # Keep numerical/domain functions testable without UI packages.
    go = None  # type: ignore[assignment]
    st = None  # type: ignore[assignment]

try:
    import folium
except ImportError:  # pragma: no cover
    folium = None  # type: ignore[assignment]

try:  # shapely is only needed for the CASE A boundary expansion (/api/aoi_boundary)
    from shapely.geometry import Point as _ShapelyPoint
    from shapely.geometry import Polygon as _ShapelyPolygon
    from shapely.ops import unary_union as _shapely_unary_union
    _HAS_SHAPELY = True
except Exception:  # pragma: no cover - guarded so the app still runs without it
    _ShapelyPoint = None  # type: ignore[assignment]
    _ShapelyPolygon = None  # type: ignore[assignment]
    _shapely_unary_union = None  # type: ignore[assignment]
    _HAS_SHAPELY = False


REPO_DIR = Path(__file__).resolve().parents[1]
DEFAULT_AOI_KML = REPO_DIR / "data" / "07_Aug_2019_1659504705RGLR1LRProjectSite.kml"
AOI_NAME = "MOIL Bharveli-Awalajhari Mine AOI"
AOI_AREA_HA = 76.409
OVERLAY_BASEMAP = "OpenStreetMap"
SENTINEL_WAVELENGTHS_UM = {"B04": 0.665, "B08": 0.842, "B11": 1.610, "B12": 2.190}
SCENE_REFLECTANCE = {"B04": 0.20696, "B08": 0.33599, "B11": 0.33861, "B12": 0.27219}
PYROLUSITE_REFERENCE = {"B04": 0.05571, "B08": 0.05838, "B11": 0.09301, "B12": 0.08208}
SCENE_METADATA = {
    "platform": "Sentinel-2C L2A",
    "scene_id": "S2C_MSIL2A_20260107T051211_R019_T44QMK",
    "date": "2026-01-07",
    "tile": "T44QMK",
    "cloud_cover_pct": 0.000319,
}


def command_header_html() -> str:
    """Return non-indented HTML so Streamlit does not render it as Markdown code."""

    return (
        "<div class='command-header'>"
        "<div class='command-title'>⛏️ MOIL Mining Command Center</div>"
        "<div class='command-subtitle'>Bharveli-Awalajhari · Spatial intelligence and spectral verification</div>"
        "</div>"
    )


def _as_vector(values: Mapping[str, float] | Sequence[float]) -> list[float]:
    if isinstance(values, Mapping):
        return [float(values[band]) for band in SENTINEL_WAVELENGTHS_UM if band in values]
    return [float(value) for value in values]


def spectral_match(
    earth_reflectance: Mapping[str, float] | Sequence[float],
    isro_baseline: Mapping[str, float] | Sequence[float],
) -> dict[str, Any]:
    """Compute normalized cosine similarity for two nonzero equal-length vectors."""

    earth, baseline = _as_vector(earth_reflectance), _as_vector(isro_baseline)
    if len(earth) != len(baseline) or not earth:
        raise ValueError("Spectral vectors must be non-empty and the same length.")
    earth_norm = math.sqrt(sum(value * value for value in earth))
    baseline_norm = math.sqrt(sum(value * value for value in baseline))
    if math.isclose(earth_norm, 0.0) or math.isclose(baseline_norm, 0.0):
        raise ValueError("Spectral vectors must not be zero vectors.")
    similarity = max(-1.0, min(1.0, sum(a * b for a, b in zip(earth, baseline)) / (earth_norm * baseline_norm)))
    return {"similarity": similarity, "status": "Confirmed" if similarity >= 0.90 else "Unconfirmed", "overlap_points": len(earth)}


def build_bharveli_aoi_result() -> dict[str, Any]:
    """Return the supplied AOI-level result, never a manganese grade estimate."""

    match = spectral_match(SCENE_REFLECTANCE, PYROLUSITE_REFERENCE)
    return {
        **match,
        "label": "Pyrolusite Spectral Similarity",
        "aoi_name": AOI_NAME,
        "aoi_area_ha": AOI_AREA_HA,
        "spectral_potential": "HIGH" if match["similarity"] >= 0.90 else "REVIEW",
        "scene": SCENE_METADATA.copy(),
        "live_reflectance": SCENE_REFLECTANCE.copy(),
        "reference_reflectance": PYROLUSITE_REFERENCE.copy(),
        "wavelengths_um": SENTINEL_WAVELENGTHS_UM.copy(),
        "interpretation": "High pyrolusite spectral potential over the supplied AOI based on four Sentinel-2 band means and a USGS reference vector. This is not Mn concentration, ore grade, reserve estimation, or a pixel-level mineral map.",
        "vegetation_mask": {
            "status": "NOT_APPLIED",
            "level": "AOI_LEVEL_PROTOTYPE",
            "note": (
                "AOI-level result computed from scene band means; per-pixel NDVI "
                "vegetation masking is applied only at the per-zone / per-chip "
                "level (see /api/zones). Scene means are not pixel statistics."
            ),
        },
    }


def load_aoi_kml(path: Path | str = DEFAULT_AOI_KML) -> list[dict[str, Any]]:
    """Load KML linework exactly; this routine never creates coordinates."""

    root = element_tree.parse(path).getroot()
    namespace = {"kml": "http://www.opengis.net/kml/2.2"}
    features = []
    for index, placemark in enumerate(root.findall(".//kml:Placemark", namespace), start=1):
        node = placemark.find(".//kml:LineString/kml:coordinates", namespace)
        if node is None or not node.text:
            continue
        coordinates = []
        for triplet in node.text.split():
            longitude, latitude, *_ = triplet.split(",")
            coordinates.append([float(longitude), float(latitude)])
        if len(coordinates) >= 2:
            features.append({"type": "Feature", "properties": {"name": "76.409 Ha", "boundary_part": index, "source": "Supplied 76.409-ha KML boundary"}, "geometry": {"type": "LineString", "coordinates": coordinates}})
    if not features:
        raise ValueError(f"No LineString features found in supplied KML: {path}")
    return features


def expand_lease_boundary(
    features: Sequence[Mapping[str, Any]] | None = None,
    markers: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """CASE A fix: expand the lease boundary just enough to enclose the markers.

    The supplied KML linework is the authoritative lease outline. It is
    buffered outward by ``radius`` where ``radius`` is the smallest single
    value that leaves every demo-zone marker strictly inside the resulting
    polygon (max outward marker-to-boundary distance + a 0.5% / min ~1 m
    safety margin). The expanded polygon therefore hugs the original lease
    shape instead of being blown up arbitrarily.

    Zone/zone markers are never touched: their coordinates are read-only
    inputs that decide ``radius``; the returned geometry is the boundary only.

    Requires shapely. If shapely is unavailable the original LineString
    features are returned unchanged and ``buffered`` is False so the caller
    can degrade gracefully (and the demo markers will be outside the raw
    linework until shapely is installed).
    """
    if features is None:
        features = load_aoi_kml()
    if markers is None:
        try:
            from constants import CANDIDATE_ZONES

            markers = CANDIDATE_ZONES
        except Exception:  # pragma: no cover - constants always importable in app
            markers = []

    original_coords = [
        feature["geometry"]["coordinates"] for feature in features
    ]

    if not _HAS_SHAPELY:
        return {
            "features": list(features),
            "buffered": False,
            "buffer_radius_m": 0.0,
            "markers_contained": [],
            "note": (
                "CASE A boundary expansion requires shapely; it is not "
                "installed, so the raw lease linework is served unchanged."
            ),
        }

    # Build the lease polygon(s) from the supplied linework. Every part is
    # closed implicitly and cleaned (buffer 0 fixes bow-tie/self-overlap
    # fragments typical of double-traced KML parcels); malformed parts are
    # skipped rather than poisoning the union.
    base_parts = []
    for coords in original_coords:
        if len(coords) < 3:
            continue
        try:
            polygon = _ShapelyPolygon(coords).buffer(0.0)
            if not polygon.is_empty:
                base_parts.append(polygon)
        except Exception:  # noqa: BLE001 - malformed stroke; skip this part
            continue
    if not base_parts:
        return {
            "features": list(features),
            "buffered": False,
            "buffer_radius_m": 0.0,
            "markers_contained": [],
            "note": "No valid lease polygon could be built from the KML linework.",
        }
    lease = _shapely_unary_union(base_parts)

    # Smallest uniform buffer that strictly encloses every marker.
    radius_deg = 0.0
    per_marker = []
    point_of = _ShapelyPoint
    for marker in markers:
        point = point_of(float(marker["longitude"]), float(marker["latitude"]))
        distance = lease.distance(point) if not lease.contains(point) else 0.0
        per_marker.append({"zone_id": marker.get("zone_id"), "distance_deg": round(distance, 7)})
        radius_deg = max(radius_deg, distance)
    safety_deg = max(radius_deg * 0.005, 1e-5)  # ~1 m at these latitudes
    buffer_radius_deg = radius_deg + safety_deg

    expanded = lease.buffer(buffer_radius_deg, quad_segs=48)

    # Export the expanded lease as Polygon GeoJSON features (rounded to 6 dp
    # for tidy payloads). Holes from the fragmentary double-traced linework
    # are resolved backward to The exterior ring only - the drawer (Leaflet)
    # cannot fill concave fragments; we serve filled lease polygons instead.
    features_out = []
    geom_parts = (
        [expanded]
        if expanded.geom_type in ("Polygon", "MultiPolygon")
        else [g for g in expanded.geoms if g.geom_type in ("Polygon", "MultiPolygon")]
    )
    for part in geom_parts:
        polygons = [part] if part.geom_type == "Polygon" else list(part.geoms)
        for polygon in polygons:
            exterior = [
                [round(lon, 6), round(lat, 6)]
                for lon, lat in polygon.exterior.coords
            ]
            features_out.append({
                "type": "Feature",
                "properties": {
                    "name": "76.409 Ha",
                    "boundary_part": "buffered",
                    "source": "Supplied 76.409-ha KML boundary (CASE A: buffered to enclose demo zone markers)",
                },
                "geometry": {"type": "Polygon", "coordinates": [exterior]},
            })

    centroid_lat, centroid_lon = _boundary_center(features)
    metres_per_deg = (
        110574.0 + 111320.0 * math.cos(math.radians(centroid_lat))
    ) / 2.0
    contained = []
    for marker in markers:
        point = point_of(float(marker["longitude"]), float(marker["latitude"]))
        contained.append({
            "zone_id": marker.get("zone_id"),
            "contained": bool(expanded.contains(point) or expanded.covers(point)),
            "distance_m": 0.0,
            "original_latitude": marker.get("latitude"),
            "original_longitude": marker.get("longitude"),
            "pipeline_unchanged": True,
        })
    for entry, record in zip(contained, per_marker):
        entry["distance_m"] = round(record["distance_deg"] * metres_per_deg, 1)

    return {
        "features": features_out,
        "buffered": True,
        "buffer_radius_deg": round(buffer_radius_deg, 7),
        "buffer_radius_m": round(buffer_radius_deg * metres_per_deg, 1),
        "radius_safety_m": round(safety_deg * metres_per_deg, 1),
        "lease_area_ha": AOI_AREA_HA,
        "markers_contained": contained,
        "note": (
            "CASE A: the authoritative lease outline was buffered outward by "
            "the minimum radius that encloses all four demo-zone markers. Zone "
            "marker coordinates are unchanged; only the boundary geometry was "
            "expanded."
        ),
    }


def _boundary_center(features: Sequence[Mapping[str, Any]]) -> tuple[float, float]:
    coordinates = [point for feature in features for point in feature["geometry"]["coordinates"]]
    return (
        sum(point[1] for point in coordinates) / len(coordinates),
        sum(point[0] for point in coordinates) / len(coordinates),
    )


def _popup_html(result: Mapping[str, Any]) -> str:
    return f"""<div style='background:#0b1328;color:#eef5ff;padding:14px 16px;border:1px solid #e879f9;border-radius:10px;min-width:300px;font-family:Arial,sans-serif'>
<b style='color:#d8b4fe'>🛰️ ISRO-adapted spectral analysis</b><br><span style='color:#9ca3af'>{result['aoi_name']} · {result['aoi_area_ha']} ha</span><hr>
<b>Pyrolusite spectral similarity:</b> <span style='color:#e879f9'>{result['similarity'] * 100:.2f}%</span><br><b>Reference:</b> USGS pyrolusite vector<br><b>Scene:</b> Sentinel-2C L2A · 7 Jan 2026 · T44QMK
<div style='color:#34d399;margin-top:10px;font-weight:700'>HIGH SPECTRAL POTENTIAL · REVIEW WITH ASSAY</div><div style='color:#94a3b8;margin-top:8px;font-size:11px'>AOI mean result only; not manganese concentration or ore grade.</div></div>"""


def build_aoi_overlay_map(result: Mapping[str, Any], overlay_enabled: bool = True) -> Any:
    """Build a map from the actual KML linework, without synthetic pit geometry."""

    if folium is None:
        raise RuntimeError("folium is required for map rendering; install requirements.txt first")
    features = load_aoi_kml()
    map_object = folium.Map(location=_boundary_center(features), zoom_start=16, tiles=OVERLAY_BASEMAP, control_scale=True)
    color = "#e879f9" if overlay_enabled else "#38bdf8"
    for feature in features:
        folium.GeoJson(feature, name="SWIR spectral-potential boundary" if overlay_enabled else "MOIL AOI boundary", style_function=lambda _feature, active_color=color: {"color": active_color, "weight": 4, "opacity": 0.95}, tooltip=folium.Tooltip(f"{result['label']}: {result['similarity'] * 100:.2f}% · AOI-level result"), popup=folium.Popup(_popup_html(result), max_width=370)).add_to(map_object)
    all_coordinates = [point for feature in features for point in feature["geometry"]["coordinates"]]
    map_object.fit_bounds([[min(point[1] for point in all_coordinates), min(point[0] for point in all_coordinates)], [max(point[1] for point in all_coordinates), max(point[0] for point in all_coordinates)]])
    folium.LayerControl(collapsed=True).add_to(map_object)
    return map_object


def render_spectral_chart(spec_results: Mapping[str, Any]) -> None:
    """Render only the four supplied sensor samples; no continuous spectrum is inferred."""

    if go is None or st is None:
        raise RuntimeError("plotly and streamlit are required for chart rendering; install requirements.txt first")
    bands = list(SENTINEL_WAVELENGTHS_UM)
    wavelengths = spec_results.get("wavelengths_um", SENTINEL_WAVELENGTHS_UM)
    live = spec_results.get("live_reflectance", SCENE_REFLECTANCE)
    reference = spec_results.get("reference_reflectance", PYROLUSITE_REFERENCE)
    figure = go.Figure()
    figure.add_trace(go.Scatter(x=[wavelengths[band] for band in bands], y=[reference[band] * 100 for band in bands], mode="lines+markers", name="USGS pyrolusite reference", line={"color": "#22d3ee", "width": 3}))
    figure.add_trace(go.Scatter(x=[wavelengths[band] for band in bands], y=[live[band] * 100 for band in bands], mode="lines+markers", name="AOI mean Sentinel-2 scan", line={"color": "#e879f9", "width": 3, "dash": "dash"}))
    figure.update_layout(template="plotly_dark", height=330, margin={"l": 20, "r": 20, "t": 38, "b": 20}, title="Sentinel-2 band comparison - AOI mean vs USGS pyrolusite", xaxis={"title": "Wavelength (μm)", "range": [0.6, 2.5], "tickvals": [0.665, 0.842, 1.61, 2.19], "ticktext": bands}, yaxis={"title": "Reflectance (%)", "rangemode": "tozero"}, legend={"orientation": "h", "y": 1.15}, paper_bgcolor="#0b1020", plot_bgcolor="#0b1020")
    st.plotly_chart(figure, use_container_width=True, key="bharveli_spectral_curve")
    st.caption("Lines connect four sensor-band samples for readability; they do not imply a continuous hyperspectral scan or a 2.30 μm measurement.")


def render_aoi_spectral_overlay() -> None:
    """Render the command-center AOI overlay with explicit scientific limitations."""

    if st is None:
        raise RuntimeError("streamlit is required for overlay rendering; install requirements.txt first")
    result = build_bharveli_aoi_result()
    st.markdown("""<style>.spectral-shell{background:linear-gradient(115deg,#0a1020,#111a35);border:1px solid #334155;border-radius:16px;padding:18px 20px;margin-bottom:14px;box-shadow:0 0 34px rgba(168,85,247,.12)}.spectral-label{color:#c4b5fd;font-size:.82rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase}.spectral-value{color:#f0abfc;font-size:2rem;font-weight:800;margin:.1rem 0}.spectral-note{color:#a5b4cf;font-size:.92rem}</style>""", unsafe_allow_html=True)
    st.markdown(f"""<div class='spectral-shell'><div class='spectral-label'>🛰️ ISRO / Chandrayaan-inspired spectral overlay</div><div class='spectral-value'>{result['label']} - {result['similarity'] * 100:.2f}%</div><div class='spectral-note'>{AOI_NAME} · Supplied 76.409-ha boundary · Sentinel-2C L2A, 7 Jan 2026 · T44QMK · Cloud {SCENE_METADATA['cloud_cover_pct']}%</div></div>""", unsafe_allow_html=True)
    control, metric = st.columns([1, 1])
    with control:
        mode = st.radio("Map layer", ["RGB base map only", "SWIR spectral overlay"], horizontal=True, key="bharveli_layer")
    with metric:
        st.metric("Spectral potential", result["spectral_potential"], "AOI-level screening")
    try:
        from streamlit_folium import st_folium
        st_folium(build_aoi_overlay_map(result, mode == "SWIR spectral overlay"), height=490, use_container_width=True, key="bharveli_aoi_map")
    except ImportError:
        st.warning("streamlit-folium is unavailable; map interaction cannot be displayed in this environment.")
    left, right = st.columns([1.15, 1])
    with left:
        render_spectral_chart(result)
    with right:
        st.subheader("Spectral review")
        st.markdown(f"**Pyrolusite spectral similarity:** `{result['similarity'] * 100:.2f}%`")
        st.caption("Prototype threshold — requires field/lab validation.")
        st.markdown("**Reference vector:** USGS Digital Spectral Library - pyrolusite")
        st.markdown("**Noise context:** AOI mean reflectance; no per-pixel vegetation or moisture mask was supplied.")
        st.success("HIGH SPECTRAL POTENTIAL - validate with field assay before dispatch decisions")
        st.caption(result["interpretation"])


# ===========================================================================
# Per-zone spectral extraction + mineral-reference registry
# ---------------------------------------------------------------------------
# Additive to the original AOI-level implementation above.
# Nothing above this line has changed - existing tests keep passing.
# ===========================================================================

from dataclasses import dataclass
from typing import Dict, List, Optional

try:
    import pandas as _pd  # optional at import time
except ImportError:  # pragma: no cover
    _pd = None  # type: ignore[assignment]


@dataclass(frozen=True)
class MineralReference:
    """Reference spectrum for one mineral, resampled to Sentinel-2 bands."""
    mineral_id: str
    display_name: str
    reflectance: Dict[str, float]
    provenance: str
    source_url: Optional[str] = None


# Mineral reference registry.
# ---------------------------------------------------------------------------
# Only Pyrolusite is shipped with real, provenanced spectral values (USGS
# splib05a - already used by the AOI overlay above). Additional references
# can be added by appending here IF AND ONLY IF real reference data is
# obtained. We do NOT invent spectral values just to populate the UI.
MINERAL_REFERENCES: List[MineralReference] = [
    MineralReference(
        mineral_id="pyrolusite",
        display_name="Pyrolusite",
        reflectance=dict(PYROLUSITE_REFERENCE),
        provenance="USGS Digital Spectral Library (splib05a), USGS Open-File Report 03-395",
        source_url="https://pubs.usgs.gov/of/2003/ofr-03-395/ASCII/M/pyrolusite_hs138.5705.asc",
    ),
]


def score_zone_against_references(
    zone_reflectance: Mapping[str, float],
    references: Sequence[MineralReference] = MINERAL_REFERENCES,
) -> Dict[str, float]:
    """Return {mineral_id: similarity_pct} for every reference, using the
    same cosine-similarity routine the AOI overlay uses."""
    scores: Dict[str, float] = {}
    for reference in references:
        try:
            result = spectral_match(zone_reflectance, reference.reflectance)
        except ValueError:
            continue
        scores[reference.mineral_id] = round(result["similarity"] * 100.0, 2)
    return scores


# ---------------------------------------------------------------------------
# Zone-level reflectance extraction with honest provenance labelling
# ---------------------------------------------------------------------------
# TRUTH TABLE FOR PROVENANCE
#
# We do not currently have a way to pull per-pixel Sentinel-2 reflectance
# for arbitrary lat/lon out of a Sentinel-2 cube in this repo. Doing so
# would need e.g. sentinelhub / rasterio / a downloaded L2A safe file.
#
# What we DO have in processed_geology.csv is per-record synthetic-but-
# schema-faithful values for s2_b4/s2_b8/s2_b11/s2_b12 (data_status:
# SYNTHETIC_SCHEMA_FAITHFUL). Each candidate zone in constants.py is
# linked to one of these rows via ``linked_geology_record_id``.
#
# So the honest label for zone-level reflectance in this prototype is
# SYNTHETIC (schema-faithful) - NOT REAL_SATELLITE. The architecture is
# ready to receive real per-pixel extraction later without any frontend
# contract change.

ZONE_REFLECTANCE_PROVENANCE_SYNTHETIC = "SYNTHETIC_SCHEMA_FAITHFUL_FROM_PROCESSED_GEOLOGY"
ZONE_REFLECTANCE_PROVENANCE_UNAVAILABLE = "ZONE_LEVEL_SPECTRAL_UNAVAILABLE"

_ZONE_REFLECTANCE_CACHE: Optional[Dict[str, Dict[str, float]]] = None


def _load_processed_geology_reflectance() -> Dict[str, Dict[str, float]]:
    """Read processed_geology.csv once and return record_id -> {B04,B08,B11,B12}."""
    global _ZONE_REFLECTANCE_CACHE
    if _ZONE_REFLECTANCE_CACHE is not None:
        return _ZONE_REFLECTANCE_CACHE
    result: Dict[str, Dict[str, float]] = {}
    if _pd is None:
        _ZONE_REFLECTANCE_CACHE = result
        return result
    path = REPO_DIR / "data" / "processed_geology.csv"
    if not path.exists():
        _ZONE_REFLECTANCE_CACHE = result
        return result
    try:
        frame = _pd.read_csv(path, usecols=["record_id", "s2_b4", "s2_b8", "s2_b11", "s2_b12"])
    except (ValueError, KeyError):  # pragma: no cover
        _ZONE_REFLECTANCE_CACHE = result
        return result
    for row in frame.itertuples(index=False):
        try:
            result[str(row.record_id)] = {
                "B04": float(row.s2_b4),
                "B08": float(row.s2_b8),
                "B11": float(row.s2_b11),
                "B12": float(row.s2_b12),
            }
        except (ValueError, TypeError):  # pragma: no cover
            continue
    _ZONE_REFLECTANCE_CACHE = result
    return result


def get_zone_reflectance(
    linked_geology_record_id: Optional[str],
) -> tuple[Optional[Dict[str, float]], str]:
    """Return (reflectance_dict_or_None, provenance_label).

    * If the linked geology record exists in processed_geology.csv, returns
      that row's synthetic-schema-faithful (B04, B08, B11, B12) reflectance
      with a SYNTHETIC provenance label.
    * If we have no way to extract a zone-level vector, returns (None, ...)
      with an UNAVAILABLE provenance label. The fusion layer will then
      down-weight to spatial-only, honestly.
    """
    if not linked_geology_record_id:
        return None, ZONE_REFLECTANCE_PROVENANCE_UNAVAILABLE
    reflectance = _load_processed_geology_reflectance().get(str(linked_geology_record_id))
    if not reflectance:
        return None, ZONE_REFLECTANCE_PROVENANCE_UNAVAILABLE
    return reflectance, ZONE_REFLECTANCE_PROVENANCE_SYNTHETIC


# ===========================================================================
# Vegetation (NDVI) masking for the per-zone spectral pipeline
# ---------------------------------------------------------------------------
# A Sentinel-2 pixel can mix vegetation with exposed rock/soil. Green
# vegetation strongly absorbs in the red (B04) and strongly reflects in the
# near-infrared (B08), so:
#
#     NDVI = (B08 - B04) / (B08 + B04)
#
# Pixels with NDVI > threshold are treated as vegetation-dominated and are
# EXCLUDED *before* the mineral-similarity vector (means of B04/B08/B11/B12)
# is built. This keeps a green canopy from distorting the mineral fingerprint.
#
# Honesty contract (see point 6 in the change request):
#   * The mask is applied to REAL per-pixel reflectance arrays supplied by a
#     zone chip provider. The default provider returns None (no raster data in
#     this repo), so the API reports UNAVAILABLE instead of inventing pixel
#     statistics.
#   * When only a zone-average vector exists (the SYNTHETIC_SCHEMA_FAITHFUL
#     rows from processed_geology.csv), the zone is still scored from the
#     unmasked vector but the payload explicitly states that the vegetation
#     mask was NOT applied.
# ===========================================================================

NDVI_VEGETATION_THRESHOLD = 0.30
NDVI_WATER_LOW_THRESHOLD = -0.10
MIN_VALID_PIXELS_FOR_SCORE = 64
MIN_SURFACE_COVERAGE_PCT = 15.0

VEG_MASK_APPLIED = "APPLIED"
VEG_MASK_UNAVAILABLE = "UNAVAILABLE"
VEG_MASK_NO_PIXEL_DATA = "NO_PIXEL_DATA"


@dataclass(frozen=True)
class NdviMaskConfig:
    """Configurable NDVI vegetation mask thresholds.

    Pixels with NDVI in the "exposed" window ``[ndvi_water_low_threshold,
    ndvi_threshold]`` are the usable surface pool. Vegetation (NDVI above
    ``ndvi_threshold``) is excluded before spectral scoring, and water/shadows
    (NDVI below ``ndvi_water_low_threshold``) are also excluded - neither
    represents exposed ore surface.
    """

    ndvi_threshold: float = NDVI_VEGETATION_THRESHOLD
    ndvi_water_low_threshold: float = NDVI_WATER_LOW_THRESHOLD
    min_valid_pixels: int = MIN_VALID_PIXELS_FOR_SCORE
    min_surface_coverage_pct: float = MIN_SURFACE_COVERAGE_PCT


@dataclass
class NdviMaskResult:
    """Result of applying the vegetation mask to one zone's pixel chip."""

    status: str = VEG_MASK_UNAVAILABLE
    applied: bool = False
    total_pixels: Optional[int] = None
    vegetation_pixels_removed: Optional[int] = None
    water_pixels_excluded: Optional[int] = None
    valid_pixels_remaining: Optional[int] = None
    surface_coverage_pct: Optional[float] = None
    ndvi_threshold: Optional[float] = None
    ndvi_statistics: Optional[Dict[str, float]] = None
    mean_reflectance: Optional[Dict[str, float]] = None
    scorable: bool = False
    reason: Optional[str] = None

    def quality(self) -> Dict[str, Any]:
        """Simple quality information: totals, removed, excluded, remaining, coverage."""
        return {
            "total_pixels": self.total_pixels,
            "vegetation_pixels_removed": self.vegetation_pixels_removed,
            "water_pixels_excluded": self.water_pixels_excluded,
            "valid_pixels_remaining": self.valid_pixels_remaining,
            "surface_coverage_pct": self.surface_coverage_pct,
            "ndvi_statistics": self.ndvi_statistics,
        }


def compute_ndvi(
    b04_pixels: Sequence[float],
    b08_pixels: Sequence[float],
) -> List[Optional[float]]:
    """Per-pixel NDVI = (B08 - B04) / (B08 + B04).

    Returns one value per pixel, aligned to the shorter input. Pixels with a
    zero/undefined denominator, or a missing/non-finite band value, yield
    ``None`` (the caller treats those as non-scoring pixels) - we never
    fabricate an NDVI for them.
    """
    if b04_pixels is None or b08_pixels is None:
        return []
    reds = list(b04_pixels)
    nirs = list(b08_pixels)
    length = min(len(reds), len(nirs))
    values: List[Optional[float]] = []
    for index in range(length):
        try:
            red = float(reds[index])
            nir = float(nirs[index])
        except (TypeError, ValueError):
            values.append(None)
            continue
        denominator = red + nir
        if not (math.isfinite(red) and math.isfinite(nir)) or math.isclose(denominator, 0.0):
            values.append(None)
            continue
        values.append((nir - red) / denominator)
    return values


def summary_ndvi_statistics(
    ndvi_values: Sequence[Optional[float]],
) -> Optional[Dict[str, float]]:
    """Summary statistics over all finite per-pixel NDVI values.

    Returns mean / min / max / median rounded to 6dp, or ``None`` when the
    chip has no measurable NDVI. These numbers come straight from the pixel
    grid (B04/B08 per pixel) - the UI renders them, it never invents them.
    """
    finite = [float(value) for value in ndvi_values if value is not None]
    if not finite:
        return None
    median: float = statistics.median(finite)
    return {
        "mean": round(sum(finite) / len(finite), 6),
        "min": round(min(finite), 6),
        "max": round(max(finite), 6),
        "median": round(median, 6),
    }


def apply_ndvi_mask(
    band_pixels: Mapping[str, Sequence[float]],
    config: Optional[NdviMaskConfig] = None,
) -> NdviMaskResult:
    """Apply the vegetation (NDVI) mask to per-pixel Sentinel-2 bands.

    ``band_pixels`` maps band name to one reflectance value per pixel, e.g.
    ``{"B04": [...], "B08": [...], "B11": [...], "B12": [...]}``.

    Returns an :class:`NdviMaskResult` whose ``quality()`` reports:

    * total pixels
    * vegetation pixels removed (NDVI > threshold)
    * water pixels excluded (NDVI < water low threshold)
    * valid (exposed) pixels remaining
    * surface coverage % (valid / total)

    ``mean_reflectance`` holds the B04/B08/B11/B12 means computed over the
    VALID (exposed) pixels only, ready for ``spectral_match`` /
    ``score_zone_against_references``. If too few valid pixels remain (below
    ``min_valid_pixels`` or ``min_surface_coverage_pct``) ``scorable`` is
    False and ``mean_reflectance`` is None, so no misleading spectral score
    can be produced.
    """
    if not band_pixels:
        return NdviMaskResult(
            status=VEG_MASK_UNAVAILABLE,
            reason="No per-pixel Sentinel-2 reflectance supplied.",
        )
    cfg = config or NdviMaskConfig()
    b04 = band_pixels.get("B04")
    b08 = band_pixels.get("B08")
    if b04 is None or b08 is None:
        return NdviMaskResult(
            status=VEG_MASK_UNAVAILABLE,
            reason="NDVI requires B04 and B08 channels; they were not supplied.",
        )
    reds = list(b04)
    nirs = list(b08)
    total = max(len(reds), len(nirs))
    if total == 0:
        return NdviMaskResult(status=VEG_MASK_UNAVAILABLE, reason="Empty pixel set supplied.", total_pixels=0)

    ndvi_values = compute_ndvi(reds, nirs)
    ndvi_statistics = summary_ndvi_statistics(ndvi_values)
    valid = 0
    vegetation_removed = 0
    water_excluded = 0
    band_sums: Dict[str, float] = {band: 0.0 for band in SENTINEL_WAVELENGTHS_UM}
    for index in range(total):
        pixel_ndvi = ndvi_values[index] if index < len(ndvi_values) else None
        if pixel_ndvi is not None and pixel_ndvi > cfg.ndvi_threshold:
            vegetation_removed += 1
            continue
        if pixel_ndvi is not None and pixel_ndvi < cfg.ndvi_water_low_threshold:
            # Water / shadow: low NDVI, not exposed ore surface.
            water_excluded += 1
            continue
        valid += 1
        for band in SENTINEL_WAVELENGTHS_UM:
            values = band_pixels.get(band)
            if values is None:
                continue
            value = values[index] if index < len(values) else None
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value):
                band_sums[band] = band_sums[band] + value

    coverage = (valid / total * 100.0) if total else 0.0
    scorable = valid >= cfg.min_valid_pixels and coverage >= cfg.min_surface_coverage_pct

    mean_reflectance: Optional[Dict[str, float]] = None
    if scorable and valid > 0:
        mean_reflectance = {
            band: round(band_sums[band] / valid, 6)
            for band in SENTINEL_WAVELENGTHS_UM
            if any(band_pixels.get(band))
        }
        for band in SENTINEL_WAVELENGTHS_UM:
            if band not in mean_reflectance:
                scorable = False
                mean_reflectance = None
                break

    if scorable:
        reason = None
    elif valid == 0:
        reason = (
            f"All {total} pixels were classified as vegetation (NDVI > {cfg.ndvi_threshold}) "
            f"or water/shadow (NDVI < {cfg.ndvi_water_low_threshold}); no exposed surface "
            "remains, so no spectral score is produced."
        )
    else:
        reason = (
            f"Only {valid} of {total} pixels remain after NDVI masking "
            f"(surface coverage {coverage:.1f}%), below the {cfg.min_valid_pixels}-pixel / "
            f"{cfg.min_surface_coverage_pct}% thresholds; no spectral score is produced."
        )

    return NdviMaskResult(
        status=VEG_MASK_APPLIED,
        applied=True,
        total_pixels=total,
        vegetation_pixels_removed=vegetation_removed,
        water_pixels_excluded=water_excluded,
        valid_pixels_remaining=valid,
        surface_coverage_pct=round(coverage, 2),
        ndvi_threshold=cfg.ndvi_threshold,
        ndvi_statistics=ndvi_statistics,
        mean_reflectance=mean_reflectance,
        scorable=scorable,
        reason=reason,
    )


# Seam where a real Sentinel-2 chip extractor would plug in. Signature:
#   callable(zone_config: Mapping[str, Any]) -> Optional[Mapping[str, Sequence[float]]]
ZONE_PIXEL_DATA_PROVIDER: Optional[Any] = None


def get_zone_pixel_data(
    zone_config: Optional[Mapping[str, Any]],
) -> Optional[Mapping[str, Sequence[float]]]:
    """Return per-pixel Sentinel-2 reflectance for a zone, or None.

    Defaults to None until a real chip provider is configured, so the API can
    honestly report the vegetation mask as unavailable instead of inventing
    pixel statistics (see change request point 6).
    """
    provider = ZONE_PIXEL_DATA_PROVIDER
    if provider is None or not zone_config:
        return None
    return provider(zone_config)


def zone_vegetation_mask(
    zone_config: Optional[Mapping[str, Any]] = None,
    band_pixels: Optional[Mapping[str, Sequence[float]]] = None,
    config: Optional[NdviMaskConfig] = None,
) -> NdviMaskResult:
    """Resolve the vegetation-mask state for one zone.

    * Real per-pixel bands supplied -> apply the NDVI mask and return real
      quality information.
    * Otherwise -> a ``NO_PIXEL_DATA`` result describes why the mask was not
      applied; callers keep scoring from the unmasked (synthetic) vector and
      label it honestly.
    """
    if band_pixels is None and zone_config is not None:
        band_pixels = get_zone_pixel_data(zone_config)
    if not band_pixels:
        return NdviMaskResult(
            status=VEG_MASK_NO_PIXEL_DATA,
            reason=(
                "No per-pixel Sentinel-2 reflectance is available for this zone; "
                "the vegetation (NDVI) mask was not applied. A zone-average vector "
                "cannot produce pixel-level vegetation statistics without fabrication."
            ),
        )
    return apply_ndvi_mask(band_pixels, config)


# ===========================================================================
# Synthetic-demo pixel-chip mode (explicitly SYNTHETIC_DEMO)
# ---------------------------------------------------------------------------
# Lets the map VISIBLY exercise the vegetation-masking pipeline using clearly
# labelled simulated pixel chips. Every field these functions produce is
# either deterministic noise or a spectral shape descriptor; nothing here is a
# Sentinel-2 observation. All outputs carry provenance = SYNTHETIC_DEMO_CHIP
# and dataset = SYNTHETIC_DEMO so consumers can never mistake them for real
# satellite measurements. The AOI-level 97.84% result stays a separate
# AOI_LEVEL_PROTOTYPE and is untouched by this mode.
# ===========================================================================

SIMULATED_CHIP_DATASET = "SYNTHETIC_DEMO"
SIMULATED_CHIP_PROVENANCE = "SYNTHETIC_DEMO_CHIP"
SIMULATED_CHIP_ZONE_REFLECTANCE_PROVENANCE = "SYNTHETIC_DEMO_CHIP_VEG_MASKED_PIXEL_MEANS"

# Spectral shape used for vegetation pixels (green canopy: strong NIR, weak red).
_VEGETATION_PIXEL_RANGES = {"B04": (0.040, 0.080), "B08": (0.350, 0.550), "B11": (0.180, 0.300), "B12": (0.100, 0.200)}
# Water pixels sit far below the NDVI water threshold (B08 << B04).
_WATER_PIXEL_RANGES = {"B04": (0.045, 0.095), "B11": (0.010, 0.045), "B12": (0.005, 0.020)}


def simulate_zone_chip(zone_config: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Build a deterministic SYNTHETIC_DEMO pixel chip for one zone.

    Each candidate zone in constants.py may carry a ``demo_chip`` block::

        "demo_chip": {"size": 20, "seed": 1101,
                      "vegetation_fraction": 0.08, "water_fraction": 0.06}

    The chip is ``size x size`` pixels (default 20 -> 400). Every pixel is
    assigned a class with the seeded PRNG:

    * vegetation   (``vegetation_fraction``): green-canopy shape, NDVI ~0.6-0.86
      - removed by the NDVI mask.
    * water        (``water_fraction``): water/shadow shape (B08 << B04), NDVI
      ~ -0.60..-0.25 - excluded by the mask as not exposed surface.
    * exposed      (remainder): dry ore/rock surface anchored on the zone's
      synthetic base reflectance with B08 kept close to B04 so NDVI stays in
      the usable exposed window - these pixels survive the mask and feed the
      spectral mean.

    Returns a fully-labelled payload: ``{"dataset", "provenance", "seed",
    "chip_size", "total_pixels", "vegetation_fraction", "water_fraction",
    "ndvi_threshold", "ndvi_water_low_threshold", "classes": [per-pixel class],
    "bands": {B04/B08/B11/B12: [...]}}``. ``classes`` drives the client-side
    synthetic surface renderer; ``bands`` feeds ``apply_ndvi_mask`` exactly
    like a would-be Sentinel-2 chip would.
    """
    cfg = dict((zone_config or {}).get("demo_chip") or {})
    chip_size = int(cfg.get("size", 20))
    if chip_size < 1:
        chip_size = 20
    vegetation_fraction = max(0.0, min(1.0, float(cfg.get("vegetation_fraction", 0.30))))
    water_fraction = max(0.0, float(cfg.get("water_fraction", 0.06)))
    # Guarantee a small exposed-surface remnant even for heavily vegetated zones.
    water_fraction = min(water_fraction, max(0.0, 1.0 - vegetation_fraction - 0.05))
    exposed_fraction = 1.0 - vegetation_fraction - water_fraction
    zone_id = str((zone_config or {}).get("zone_id") or "")
    record_id = str((zone_config or {}).get("linked_geology_record_id") or "")
    seed = int(cfg.get("seed", 1000 + sum(ord(char) for char in zone_id + record_id)))
    config = NdviMaskConfig()

    base = _load_processed_geology_reflectance().get(record_id) or SCENE_REFLECTANCE
    rng = random.Random(seed)
    total = chip_size * chip_size
    bands: Dict[str, List[float]] = {band: [] for band in SENTINEL_WAVELENGTHS_UM}
    classes: List[str] = []

    for _ in range(total):
        roll = rng.random()
        if roll < vegetation_fraction:
            classes.append("vegetation")
            for band, (low, high) in _VEGETATION_PIXEL_RANGES.items():
                bands[band].append(round(rng.uniform(low, high), 6))
        elif roll < vegetation_fraction + water_fraction:
            classes.append("water")
            b4 = rng.uniform(*_WATER_PIXEL_RANGES["B04"])
            b8 = b4 * rng.uniform(0.25, 0.60)  # NDVI stays well below the water threshold
            bands["B04"].append(round(b4, 6))
            bands["B08"].append(round(b8, 6))
            bands["B11"].append(round(rng.uniform(*_WATER_PIXEL_RANGES["B11"]), 6))
            bands["B12"].append(round(rng.uniform(*_WATER_PIXEL_RANGES["B12"]), 6))
        else:
            classes.append("exposed")
            b4 = base["B04"] * rng.uniform(0.92, 1.08)
            b8 = b4 * rng.uniform(0.98, 1.20)  # keeps exposed-surface NDVI in the usable window
            bands["B04"].append(round(b4, 6))
            bands["B08"].append(round(b8, 6))
            bands["B11"].append(round(base["B11"] * rng.uniform(0.92, 1.08), 6))
            bands["B12"].append(round(base["B12"] * rng.uniform(0.92, 1.08), 6))

    # Per-pixel NDVI recomputed from the generated grid exactly as the mask
    # will do. Drives the client-side NDVI heatmap behind the scan sweep.
    chip_ndvi: List[Optional[float]] = compute_ndvi(bands["B04"], bands["B08"])

    return {
        "dataset": SIMULATED_CHIP_DATASET,
        "provenance": SIMULATED_CHIP_PROVENANCE,
        "note": (
            "Simulated pixel chip used to demonstrate the NDVI vegetation-masking "
            "pipeline and the synthetic surface renderer in the UI. This is NOT a "
            "Sentinel-2 observation and contains no real satellite pixels."
        ),
        "seed": seed,
        "chip_size": chip_size,
        "total_pixels": total,
        "vegetation_fraction": round(vegetation_fraction, 4),
        "water_fraction": round(water_fraction, 4),
        "exposed_fraction": round(exposed_fraction, 4),
        "ndvi_threshold": config.ndvi_threshold,
        "ndvi_water_low_threshold": config.ndvi_water_low_threshold,
        "classes": classes,
        "ndvi": chip_ndvi,
        "bands": bands,
    }


__all__ = [
    "build_aoi_overlay_map",
    "build_bharveli_aoi_result",
    "command_header_html",
    "load_aoi_kml",
    "render_aoi_spectral_overlay",
    "render_spectral_chart",
    "spectral_match",
    # Per-zone additions:
    "MineralReference",
    "MINERAL_REFERENCES",
    "score_zone_against_references",
    "get_zone_reflectance",
    "ZONE_REFLECTANCE_PROVENANCE_SYNTHETIC",
    "ZONE_REFLECTANCE_PROVENANCE_UNAVAILABLE",
    # Vegetation (NDVI) masking additions:
    "NDVI_VEGETATION_THRESHOLD",
    "NDVI_WATER_LOW_THRESHOLD",
    "MIN_VALID_PIXELS_FOR_SCORE",
    "MIN_SURFACE_COVERAGE_PCT",
    "VEG_MASK_APPLIED",
    "VEG_MASK_UNAVAILABLE",
    "VEG_MASK_NO_PIXEL_DATA",
    "NdviMaskConfig",
    "NdviMaskResult",
    "compute_ndvi",
    "summary_ndvi_statistics",
    "apply_ndvi_mask",
    "ZONE_PIXEL_DATA_PROVIDER",
    "get_zone_pixel_data",
    "zone_vegetation_mask",
    # Synthetic-demo pixel-chip mode:
    "SIMULATED_CHIP_DATASET",
    "SIMULATED_CHIP_PROVENANCE",
    "SIMULATED_CHIP_ZONE_REFLECTANCE_PROVENANCE",
    "simulate_zone_chip",
]
