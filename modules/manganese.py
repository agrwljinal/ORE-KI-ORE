"""Schema-faithful manganese location/grade/tonnage demo map.

The supplied processed geology rows have mine-anchor coordinates, not sample
coordinates. This module therefore joins each configured synthetic candidate
zone to one geology record for a deterministic UI demo. It never plots the
500 geology rows as if they were spatial observations.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

import folium
import pandas as pd
import streamlit as st
from folium.plugins import HeatMap
from branca.colormap import LinearColormap

try:
    import constants as C
except ImportError:  # pragma: no cover
    C = None


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
DEMO_STATUS = "SYNTHETIC_SPATIAL_DEMO"


def load_processed_geology(path: Optional[Path] = None) -> pd.DataFrame:
    """Load processed geology using the supplied schema without renaming fields."""

    return pd.read_csv(path or DATA_DIR / "processed_geology.csv")


def predict_demo_zones(
    geology: Optional[pd.DataFrame] = None,
    zones: Optional[Iterable[Mapping[str, Any]]] = None,
) -> pd.DataFrame:
    """Create deterministic demo predictions for configured candidate zones.

    ``predicted_mn_pct`` and ``estimated_tonnage_proxy_t`` are sourced from
    the linked processed geology records. They are demo/reference values, not
    validated spatial reserve estimates.
    """

    geology = geology if geology is not None else load_processed_geology()
    zones = list(zones if zones is not None else getattr(C, "CANDIDATE_ZONES", []))
    if not zones:
        return pd.DataFrame()
    geology = geology.copy()
    geology["record_id"] = geology["record_id"].astype(str)
    by_id = geology.set_index("record_id", drop=False)
    rows: List[Dict[str, Any]] = []
    for zone in zones:
        record_id = str(zone.get("linked_geology_record_id", ""))
        record = by_id.loc[record_id].to_dict() if record_id in by_id.index else {}
        grade = pd.to_numeric(record.get("mn_pct"), errors="coerce")
        tonnage = pd.to_numeric(record.get("estimated_tonnage_proxy_t"), errors="coerce")
        rows.append(
            {
                "zone_id": zone.get("zone_id"),
                "name": zone.get("name"),
                "latitude": float(zone.get("latitude")),
                "longitude": float(zone.get("longitude")),
                "prospectivity_pct": float(zone.get("spatial_score", 0.0)),
                "predicted_mn_pct": None if pd.isna(grade) else float(grade),
                "estimated_tonnage_proxy_t": None if pd.isna(tonnage) else float(tonnage),
                "grade_band": record.get("grade_band"),
                "lithology": record.get("lithology"),
                "linked_record_id": record_id,
                "data_status": DEMO_STATUS,
                "coordinate_note": "Synthetic candidate-zone coordinate; not a borehole/sample location.",
            }
        )
    return pd.DataFrame(rows)


def _value(row: Mapping[str, Any], layer: str) -> float:
    if layer == "Predicted Mn grade (%)":
        return float(row.get("predicted_mn_pct") or 0.0)
    if layer == "Estimated tonnage proxy (t)":
        return float(row.get("estimated_tonnage_proxy_t") or 0.0)
    return float(row.get("prospectivity_pct") or 0.0)


def render_manganese_map() -> None:
    """Render a Folium heatmap and inspectable candidate-zone table."""

    predictions = predict_demo_zones()
    st.title("Manganese Location, Grade & Tonnage Demo")
    st.error(
        "SYNTHETIC SPATIAL DEMO — candidate-zone coordinates are not actual assay or reserve locations. "
        "Replace this provider with georeferenced ML cells before operational use."
    )
    if predictions.empty:
        st.warning("No candidate zones are configured.")
        return

    layer = st.selectbox(
        "Heatmap layer",
        ["Prospectivity (%)", "Predicted Mn grade (%)", "Estimated tonnage proxy (t)"],
    )
    radius = st.slider("Heatmap radius", 8, 40, 22)
    center = [predictions["latitude"].mean(), predictions["longitude"].mean()]
    fmap = folium.Map(location=center, zoom_start=14, tiles="OpenStreetMap", control_scale=True)
    values = [_value(row, layer) for row in predictions.to_dict("records")]
    colormap = LinearColormap(["#2563eb", "#f59e0b", "#dc2626"], vmin=min(values), vmax=max(values) or 1.0)
    colormap.caption = layer
    colormap.add_to(fmap)

    heat_points = [
        [float(row["latitude"]), float(row["longitude"]), _value(row, layer)]
        for row in predictions.to_dict("records")
    ]
    HeatMap(heat_points, radius=radius, blur=18, min_opacity=0.35, max_zoom=17).add_to(fmap)
    for row in predictions.to_dict("records"):
        value = _value(row, layer)
        tooltip = (
            f"<b>{row['name']}</b><br>"
            f"Prospectivity: {row['prospectivity_pct']:.1f}%<br>"
            f"Model-predicted Mn grade: {row['predicted_mn_pct'] if row['predicted_mn_pct'] is not None else 'Not available'}%<br>"
            f"Estimated Tonnage Proxy: {row['estimated_tonnage_proxy_t'] if row['estimated_tonnage_proxy_t'] is not None else 'Not available'} t<br>"
            f"Active layer value: {value:.2f}<br>"
            f"Status: {DEMO_STATUS}<br>{row['coordinate_note']}"
        )
        folium.CircleMarker(
            [row["latitude"], row["longitude"]],
            radius=8,
            color=colormap(value),
            fill=True,
            fill_color=colormap(value),
            fill_opacity=0.9,
            tooltip=folium.Tooltip(tooltip),
        ).add_to(fmap)

    from streamlit_folium import st_folium

    st_folium(fmap, height=650, use_container_width=True)
    st.caption(
        "The heatmap answers where the current demo provider ranks candidate zones. "
        "It does not establish a certified reserve or sum observation tonnage proxies."
    )
    st.dataframe(
        predictions[[
            "zone_id", "name", "prospectivity_pct", "predicted_mn_pct",
            "estimated_tonnage_proxy_t", "grade_band", "lithology", "data_status",
        ]],
        hide_index=True,
        use_container_width=True,
    )


__all__ = ["DEMO_STATUS", "load_processed_geology", "predict_demo_zones", "render_manganese_map"]
