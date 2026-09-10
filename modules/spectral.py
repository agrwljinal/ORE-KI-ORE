"""Bharveli-Awalajhari AOI spectral-potential overlay."""

from __future__ import annotations

import math
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
        st.markdown("**Reference vector:** USGS Digital Spectral Library - pyrolusite")
        st.markdown("**Noise context:** AOI mean reflectance; no per-pixel vegetation or moisture mask was supplied.")
        st.success("HIGH SPECTRAL POTENTIAL - validate with field assay before dispatch decisions")
        st.caption(result["interpretation"])


__all__ = ["build_aoi_overlay_map", "build_bharveli_aoi_result", "command_header_html", "load_aoi_kml", "render_aoi_spectral_overlay", "render_spectral_chart", "spectral_match"]
