"""MOIL GeoMine Intelligence spatial module.

The repository is a Streamlit integration shell, so this module exposes the
original ``render_reserve_map`` entry point while keeping data loading,
prediction-provider selection, synthetic demo generation, map rendering and
exports in one replaceable boundary.

The supplied geology rows contain mine-level coordinate anchors.  They are
therefore used for distributions and charts, never as individual map points.
The only cell geometries generated here are explicitly labelled
``SYNTHETIC_SPATIAL_DEMO`` and are deterministic between runs.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Sequence, Tuple

try:  # Optional at import time so data adapters can be unit-tested headlessly.
    import folium
except ImportError:  # pragma: no cover - exercised only in minimal environments
    folium = None  # type: ignore[assignment]
import numpy as np
import pandas as pd
import streamlit as st


MODULE_DIR = Path(__file__).resolve().parent
REPO_DIR = MODULE_DIR.parent
DEFAULT_DATA_DIR = REPO_DIR / "data"
DATA_STATUS_DEMO = "SYNTHETIC_SPATIAL_DEMO"
DATA_STATUS_REAL = "REAL_MODEL"
CLASS_TOOLTIP = (
    "Experimental model-derived spectral transfer feature informed by elemental/spectral "
    "relationships demonstrated by Chandrayaan-2 CLASS. It does not represent direct "
    "Chandrayaan observations of the terrestrial mine."
)
TONNAGE_TOOLTIP = (
    "Indicative geometric tonnage proxy based on area × thickness × specific gravity. "
    "Not a certified geological reserve/resource estimate."
)


def _require_map_dependencies() -> Any:
    if folium is None:
        raise RuntimeError("folium is required for map rendering; install requirements.txt first")
    return folium


def _number(value: Any, default: Optional[float] = None) -> Optional[float]:
    """Convert a CSV value to a finite float without inventing a value."""

    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _first_present(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _resolve_data_dir(data_dir: Optional[os.PathLike[str] | str] = None) -> Path:
    """Find the extracted SIH dataset without coupling the UI to its location."""

    candidates: List[Path] = []
    if data_dir:
        candidates.append(Path(data_dir))
    env_dir = os.getenv("MOIL_DATA_DIR")
    if env_dir:
        candidates.append(Path(env_dir))
    candidates.extend(
        [
            DEFAULT_DATA_DIR,
            REPO_DIR / "demo_data",
            Path.cwd() / "data",
            Path.cwd() / "demo_data",
        ]
    )
    for candidate in candidates:
        if (candidate / "mine_dashboard_summary.csv").exists():
            return candidate
    return candidates[0] if candidates else DEFAULT_DATA_DIR


@dataclass
class DatasetBundle:
    """Schema-faithful data bundle used by the spatial dashboard."""

    summary: pd.DataFrame
    master: pd.DataFrame
    geology: pd.DataFrame
    production: pd.DataFrame
    geometry: Dict[str, Any]
    metadata: Dict[str, Any]
    sources: pd.DataFrame
    price_cost: pd.DataFrame
    data_dir: Path


def _read_csv(path: Path, required: bool = False) -> pd.DataFrame:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Required dataset file not found: {path}")
        return pd.DataFrame()
    return pd.read_csv(path)


def load_dataset(data_dir: Optional[os.PathLike[str] | str] = None) -> DatasetBundle:
    """Load the supplied files using their original field names.

    The adapter intentionally leaves synthetic/reference/derived columns
    untouched so that the UI can label them correctly.
    """

    root = _resolve_data_dir(data_dir)
    metadata: Dict[str, Any] = {}
    metadata_path = root / "metadata.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    geometry: Dict[str, Any] = {"type": "FeatureCollection", "features": []}
    geometry_path = root / "mine_map_geometry.geojson"
    if geometry_path.exists():
        geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
    return DatasetBundle(
        summary=_read_csv(root / "mine_dashboard_summary.csv"),
        master=_read_csv(root / "mine_master.csv"),
        geology=_read_csv(root / "processed_geology.csv"),
        production=_read_csv(root / "processed_production.csv"),
        geometry=geometry,
        metadata=metadata,
        sources=_read_csv(root / "source_registry.csv"),
        price_cost=_read_csv(root / "price_cost_reference.csv"),
        data_dir=root,
    )


@dataclass
class PredictionCell:
    """UI-neutral future-model prediction schema.

    ``geometry`` is GeoJSON Polygon geometry.  Confidence and uncertainty are
    nullable by design; no confidence is derived from probability or grade.
    """

    cell_id: str
    mine_name: str
    geometry: Dict[str, Any]
    predicted_mn_pct: Optional[float] = None
    base_mn_probability: Optional[float] = None
    class_mn_probability: Optional[float] = None
    class_transfer_score: Optional[float] = None
    confidence: Optional[float] = None
    uncertainty: Optional[float] = None
    predicted_grade_band: Optional[str] = None
    estimated_tonnage_proxy_t: Optional[float] = None
    ndvi: Optional[float] = None
    s2_b11_b12_ratio: Optional[float] = None
    aster_b4_b5_ratio: Optional[float] = None
    aster_b6_b7_ratio: Optional[float] = None
    aster_b8_b9_ratio: Optional[float] = None
    lithology: Optional[str] = None
    data_status: str = DATA_STATUS_DEMO
    model_version: Optional[str] = None
    prediction_timestamp: Optional[str] = None

    def class_delta(self) -> Optional[float]:
        return get_class_delta(self)

    def to_feature(self) -> Dict[str, Any]:
        properties = asdict(self)
        properties.pop("geometry", None)
        return {"type": "Feature", "geometry": self.geometry, "properties": properties}


def get_class_delta(cell: PredictionCell | Mapping[str, Any]) -> Optional[float]:
    """Return CLASS minus base probability only when both are available."""

    if isinstance(cell, PredictionCell):
        base = cell.base_mn_probability
        informed = cell.class_mn_probability
    else:
        base = _first_present(cell, "base_mn_probability", "baseMnProbability")
        informed = _first_present(cell, "class_mn_probability", "classMnProbability")
    base_value = _number(base)
    informed_value = _number(informed)
    if base_value is None or informed_value is None:
        return None
    return informed_value - base_value


class PredictionProvider(Protocol):
    """Provider contract consumed by the UI, independent of data source."""

    data_status: str

    def get_predictions(self, mine_name: str) -> List[PredictionCell]:
        ...


def _grade_band(grade: Optional[float]) -> Optional[str]:
    if grade is None:
        return None
    if grade < 25:
        return "BELOW_25"
    if grade < 35:
        return "25_TO_BELOW_35"
    if grade < 46:
        return "35_TO_BELOW_46"
    return "46_AND_ABOVE"


def _feature_center(feature: Mapping[str, Any]) -> Tuple[float, float]:
    geometry = feature.get("geometry") or {}
    coords = geometry.get("coordinates") or []
    if geometry.get("type") == "Point" and len(coords) >= 2:
        return float(coords[1]), float(coords[0])
    points: List[Tuple[float, float]] = []

    def visit(value: Any) -> None:
        if isinstance(value, (list, tuple)) and len(value) >= 2 and all(
            isinstance(item, (int, float)) for item in value[:2]
        ):
            points.append((float(value[1]), float(value[0])))
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child)

    visit(coords)
    if not points:
        return 21.75, 79.75
    return float(np.mean([point[0] for point in points])), float(np.mean([point[1] for point in points]))


def _mine_geometry(bundle: DatasetBundle, mine_name: str) -> Optional[Dict[str, Any]]:
    for feature in bundle.geometry.get("features", []):
        if feature.get("properties", {}).get("mine_name") == mine_name:
            return feature
    return None


def _polygon_bounds(feature: Optional[Mapping[str, Any]], summary_row: Mapping[str, Any]) -> Tuple[float, float, float, float]:
    """Return lat/lon bounds for a demo AOI, never for a legal lease claim."""

    if feature:
        geometry = feature.get("geometry") or {}
        coords = geometry.get("coordinates") or []
        points: List[Tuple[float, float]] = []

        def visit(value: Any) -> None:
            if isinstance(value, (list, tuple)) and len(value) >= 2 and all(
                isinstance(item, (int, float)) for item in value[:2]
            ):
                points.append((float(value[1]), float(value[0])))
            elif isinstance(value, (list, tuple)):
                for child in value:
                    visit(child)

        visit(coords)
        if points and geometry.get("type") != "Point":
            lats = [point[0] for point in points]
            lons = [point[1] for point in points]
            return min(lats), max(lats), min(lons), max(lons)

    lat = _number(summary_row.get("mine_latitude"), 21.75) or 21.75
    lon = _number(summary_row.get("mine_longitude"), 79.75) or 79.75
    # Demonstration extent around a mine anchor, never an assay/borehole claim.
    return lat - 0.004, lat + 0.004, lon - 0.004, lon + 0.004


class SyntheticPredictionProvider:
    """Deterministic, clearly-labelled spatial visualization demo provider."""

    data_status = DATA_STATUS_DEMO

    def __init__(self, bundle: DatasetBundle, rows: int = 80) -> None:
        self.bundle = bundle
        self.rows = max(60, min(rows, 120))

    def get_predictions(self, mine_name: str) -> List[PredictionCell]:
        summary_rows = self.bundle.summary[self.bundle.summary["mine_name"] == mine_name]
        if summary_rows.empty:
            return []
        summary = summary_rows.iloc[0].to_dict()
        geology = self.bundle.geology[self.bundle.geology["mine_name"] == mine_name].copy()
        feature = _mine_geometry(self.bundle, mine_name)
        min_lat, max_lat, min_lon, max_lon = _polygon_bounds(feature, summary)

        seed_bytes = hashlib.sha256(f"SIH26009:{mine_name}".encode("utf-8")).digest()
        seed = int.from_bytes(seed_bytes[:8], "big") % (2**32 - 1)
        rng = np.random.default_rng(seed)
        side = int(math.ceil(math.sqrt(self.rows)))
        lat_edges = np.linspace(min_lat, max_lat, side + 1)
        lon_edges = np.linspace(min_lon, max_lon, side + 1)

        grades = pd.to_numeric(geology.get("mn_pct", pd.Series(dtype=float)), errors="coerce").dropna()
        if grades.empty:
            mean_grade = _number(summary.get("synthetic_mean_mn_pct"), 30.0) or 30.0
            grade_low, grade_high = mean_grade - 8.0, mean_grade + 8.0
        else:
            mean_grade = float(grades.mean())
            grade_low, grade_high = float(grades.quantile(0.05)), float(grades.quantile(0.95))
        grade_low = max(0.0, grade_low)
        grade_high = max(grade_low + 0.5, grade_high)

        observed_lithologies = geology.get("lithology", pd.Series(dtype=str)).dropna().astype(str).tolist()
        observed_ndvi = pd.to_numeric(geology.get("ndvi", pd.Series(dtype=float)), errors="coerce").dropna()
        observed_s2 = pd.to_numeric(
            geology.get("s2_b11_b12_ratio", pd.Series(dtype=float)), errors="coerce"
        ).dropna()
        observed_a4 = pd.to_numeric(
            geology.get("aster_b4_b5_ratio", pd.Series(dtype=float)), errors="coerce"
        ).dropna()

        cells: List[PredictionCell] = []
        cluster_centres = [(0.28, 0.32), (0.70, 0.62), (0.52, 0.83)]
        for index in range(side * side):
            if len(cells) >= self.rows:
                break
            row, column = divmod(index, side)
            x = (column + 0.5) / side
            y = (row + 0.5) / side
            smooth_signal = sum(
                math.exp(-(((x - cx) ** 2 + (y - cy) ** 2) / (2 * spread**2)))
                for (cx, cy), spread in zip(cluster_centres, (0.18, 0.22, 0.15))
            )
            smooth_signal = min(1.0, smooth_signal / 1.15)
            noise = float(rng.normal(0, 0.035))
            grade = float(np.clip(mean_grade + (smooth_signal - 0.35) * (grade_high - grade_low) + noise * 8, grade_low, grade_high))
            base_probability = float(np.clip(0.20 + 0.65 * smooth_signal + rng.normal(0, 0.015), 0.02, 0.98))
            # These are intentionally demo-only values. They are not hidden as real CLASS output.
            class_probability = float(np.clip(base_probability + 0.08 * (smooth_signal - 0.45), 0.02, 0.98))
            area = (max_lon - min_lon) * 111_000 / side * (max_lat - min_lat) * 111_000 / side
            thickness = _number(geology.get("thickness_m", pd.Series(dtype=float)).median(), 1.0) or 1.0
            gravity = _number(geology.get("specific_gravity", pd.Series(dtype=float)).median(), 3.0) or 3.0
            cell_tonnage = max(0.0, area * thickness * gravity / 1_000_000)
            geometry = {
                "type": "Polygon",
                "coordinates": [
                    [
                        [lon_edges[column], lat_edges[row]],
                        [lon_edges[column + 1], lat_edges[row]],
                        [lon_edges[column + 1], lat_edges[row + 1]],
                        [lon_edges[column], lat_edges[row + 1]],
                        [lon_edges[column], lat_edges[row]],
                    ]
                ],
            }
            sample = geology.iloc[index % len(geology)] if not geology.empty else {}
            cells.append(
                PredictionCell(
                    cell_id=f"{mine_name[:3].upper()}_DEMO_{index + 1:03d}",
                    mine_name=mine_name,
                    geometry=geometry,
                    predicted_mn_pct=round(grade, 3),
                    base_mn_probability=round(base_probability, 4),
                    class_mn_probability=round(class_probability, 4),
                    class_transfer_score=round(class_probability - base_probability, 4),
                    predicted_grade_band=_grade_band(grade),
                    estimated_tonnage_proxy_t=round(cell_tonnage, 3),
                    ndvi=round(_number(sample.get("ndvi"), float(observed_ndvi.mean()) if not observed_ndvi.empty else 0.2) or 0.2, 4),
                    s2_b11_b12_ratio=round(_number(sample.get("s2_b11_b12_ratio"), float(observed_s2.mean()) if not observed_s2.empty else 1.0) or 1.0, 4),
                    aster_b4_b5_ratio=round(_number(sample.get("aster_b4_b5_ratio"), float(observed_a4.mean()) if not observed_a4.empty else 1.0) or 1.0, 4),
                    aster_b6_b7_ratio=round(_number(sample.get("aster_b6_b7_ratio"), 1.0) or 1.0, 4),
                    aster_b8_b9_ratio=round(_number(sample.get("aster_b8_b9_ratio"), 1.0) or 1.0, 4),
                    lithology=(observed_lithologies[index % len(observed_lithologies)] if observed_lithologies else None),
                    data_status=DATA_STATUS_DEMO,
                    model_version="demo-surface-v1",
                    prediction_timestamp=None,
                )
            )
        return cells


def _prediction_from_feature(feature: Mapping[str, Any]) -> PredictionCell:
    props = feature.get("properties") or {}
    return PredictionCell(
        cell_id=str(_first_present(props, "cell_id", "cellId") or "UNKNOWN"),
        mine_name=str(_first_present(props, "mine_name", "mineName") or ""),
        geometry=feature.get("geometry") or {},
        predicted_mn_pct=_number(_first_present(props, "predicted_mn_pct", "predictedMnPct")),
        base_mn_probability=_number(_first_present(props, "base_mn_probability", "baseMnProbability")),
        class_mn_probability=_number(_first_present(props, "class_mn_probability", "classMnProbability")),
        class_transfer_score=_number(_first_present(props, "class_transfer_score", "classTransferScore")),
        confidence=_number(props.get("confidence")),
        uncertainty=_number(props.get("uncertainty")),
        predicted_grade_band=_first_present(props, "predicted_grade_band", "predictedGradeBand"),
        estimated_tonnage_proxy_t=_number(_first_present(props, "estimated_tonnage_proxy_t", "estimatedTonnageProxyT")),
        ndvi=_number(props.get("ndvi")),
        s2_b11_b12_ratio=_number(_first_present(props, "s2_b11_b12_ratio", "s2B11B12Ratio")),
        aster_b4_b5_ratio=_number(_first_present(props, "aster_b4_b5_ratio", "asterB4B5Ratio")),
        aster_b6_b7_ratio=_number(_first_present(props, "aster_b6_b7_ratio", "asterB6B7Ratio")),
        aster_b8_b9_ratio=_number(_first_present(props, "aster_b8_b9_ratio", "asterB8B9Ratio")),
        lithology=props.get("lithology"),
        data_status=str(props.get("data_status") or DATA_STATUS_REAL),
        model_version=props.get("model_version"),
        prediction_timestamp=props.get("prediction_timestamp"),
    )


class GeoJSONPredictionProvider:
    """Read future real-model GeoJSON without changing the UI."""

    data_status = DATA_STATUS_REAL

    def __init__(self, path: os.PathLike[str] | str) -> None:
        self.path = Path(path)
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.features = payload.get("features", [])

    def get_predictions(self, mine_name: str) -> List[PredictionCell]:
        return [
            _prediction_from_feature(feature)
            for feature in self.features
            if (_first_present(feature.get("properties", {}), "mine_name", "mineName") == mine_name)
        ]


class PredictionService:
    """Select real GeoJSON when configured, otherwise use the demo provider."""

    def __init__(self, bundle: DatasetBundle, prediction_path: Optional[os.PathLike[str] | str] = None) -> None:
        configured = prediction_path or os.getenv("MOIL_PREDICTIONS_GEOJSON")
        default_path = bundle.data_dir / "model_predictions.geojson"
        candidate = Path(configured) if configured else default_path
        if candidate.exists():
            self.provider: PredictionProvider = GeoJSONPredictionProvider(candidate)
        else:
            self.provider = SyntheticPredictionProvider(bundle)

    @property
    def data_status(self) -> str:
        return self.provider.data_status

    def get_predictions(self, mine_name: str) -> List[PredictionCell]:
        return self.provider.get_predictions(mine_name)


def _minmax(series: pd.Series, inverse: bool = False) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce").fillna(0.0)
    lower, upper = float(numeric.min()), float(numeric.max())
    if math.isclose(lower, upper):
        result = pd.Series(0.5, index=numeric.index)
    else:
        result = (numeric - lower) / (upper - lower)
    return 1.0 - result if inverse else result


def calculate_mining_priority(
    summary: pd.DataFrame,
    production: Optional[pd.DataFrame] = None,
    weights: Optional[Mapping[str, float]] = None,
) -> pd.DataFrame:
    """Calculate a configurable operational priority score.

    This is decision support only.  It never changes prospectivity values and
    does not include confidence because the current dataset has no genuine
    confidence score.
    """

    if summary.empty:
        return pd.DataFrame(columns=["mine_name", "priority_score", "priority_band"])
    result = summary.copy()
    production = production if production is not None else pd.DataFrame()
    if not production.empty:
        grouped = production.groupby("mine_name", as_index=False).agg(
            mean_rainfall_mm=("rainfall_mm", "mean"),
            mean_shortfall_pct=("shortfall_pct", "mean"),
        )
        result = result.merge(grouped, on="mine_name", how="left")
    else:
        result["mean_rainfall_mm"] = 0.0
        result["mean_shortfall_pct"] = result.get("official_shortfall_pct", 0.0)

    default_weights = {
        "mn_potential": 0.28,
        "grade": 0.22,
        "production_urgency": 0.20,
        "economic_attractiveness": 0.18,
        "weather_feasibility": 0.12,
    }
    active = {key: max(0.0, float((weights or {}).get(key, value))) for key, value in default_weights.items()}
    total = sum(active.values()) or 1.0
    active = {key: value / total for key, value in active.items()}
    def column_or_zero(name: str) -> pd.Series:
        if name in result.columns:
            return result[name]
        return pd.Series(0.0, index=result.index)

    result["_mn_potential"] = _minmax(column_or_zero("synthetic_mean_mn_pct"))
    result["_grade"] = _minmax(column_or_zero("synthetic_mean_mn_pct"))
    result["_production_urgency"] = _minmax(column_or_zero("mean_shortfall_pct"))
    result["_economic_attractiveness"] = _minmax(column_or_zero("gross_margin_proxy_inr_per_t"))
    result["_weather_feasibility"] = _minmax(column_or_zero("mean_rainfall_mm"), inverse=True)
    result["priority_score"] = sum(active[key] * result[f"_{key}"] for key in active) * 100.0
    result["priority_band"] = pd.cut(
        result["priority_score"],
        bins=[-0.01, 25, 50, 75, 100.01],
        labels=["Low", "Moderate", "High", "Very High"],
    ).astype(str)
    return result


def _portfolio_feature_map(bundle: DatasetBundle) -> Dict[str, Dict[str, Any]]:
    return {
        feature.get("properties", {}).get("mine_name"): feature
        for feature in bundle.geometry.get("features", [])
        if feature.get("properties", {}).get("mine_name")
    }


def _metric_value(row: Mapping[str, Any], kpi: str) -> float:
    mapping = {
        "Mean Mn %": "synthetic_mean_mn_pct",
        "Production Shortfall %": "official_shortfall_pct",
        "Gross Margin Proxy / t": "gross_margin_proxy_inr_per_t",
        "Actual Production": "actual_rom_tonnes",
        "Reference Production": "reference_rom_tonnes",
    }
    return _number(row.get(mapping[kpi]), 0.0) or 0.0


def _portfolio_color(value: float, low: float, high: float) -> str:
    if math.isclose(low, high):
        ratio = 0.5
    else:
        ratio = max(0.0, min(1.0, (value - low) / (high - low)))
    # Blue -> amber -> red, useful for both positive and negative economics.
    if ratio < 0.5:
        return "#2563eb"
    if ratio < 0.75:
        return "#f59e0b"
    return "#dc2626"


def _map_center(bundle: DatasetBundle, fallback: Sequence[float]) -> Tuple[float, float]:
    points = []
    for feature in bundle.geometry.get("features", []):
        points.append(_feature_center(feature))
    if points:
        return float(np.mean([point[0] for point in points])), float(np.mean([point[1] for point in points]))
    return float(fallback[0]), float(fallback[1])


def _format_number(value: Any, suffix: str = "") -> str:
    numeric = _number(value)
    return f"{numeric:,.2f}{suffix}" if numeric is not None else "Not available"


def _render_portfolio_map(bundle: DatasetBundle, center: Sequence[float], kpi: str, selected_mine: Optional[str]) -> folium.Map:
    _require_map_dependencies()
    rows = bundle.summary.to_dict("records")
    values = [_metric_value(row, kpi) for row in rows] or [0.0]
    folium_map = folium.Map(location=_map_center(bundle, center), zoom_start=8, tiles="OpenStreetMap", control_scale=True)
    feature_map = _portfolio_feature_map(bundle)
    for row in rows:
        mine = str(row.get("mine_name", "Unknown"))
        feature = feature_map.get(mine)
        if not feature:
            continue
        properties = feature.get("properties", {})
        value = _metric_value(row, kpi)
        color = "#16a34a" if mine == selected_mine else _portfolio_color(value, min(values), max(values))
        geometry = feature.get("geometry", {})
        tooltip = (
            f"<b>{mine}</b><br>"
            f"{row.get('state', '')} · {row.get('district', '')}<br>"
            f"Mean synthetic Mn: {_format_number(row.get('synthetic_mean_mn_pct'), '%')}<br>"
            f"Reference Mn range: {_format_number(row.get('mn_grade_min_reference_pct'), '%')} – {_format_number(row.get('mn_grade_max_reference_pct'), '%')}<br>"
            f"Actual ROM: {_format_number(row.get('actual_rom_tonnes'), ' t')}<br>"
            f"Reference ROM: {_format_number(row.get('reference_rom_tonnes'), ' t')}<br>"
            f"Shortfall: {_format_number(row.get('official_shortfall_pct'), '%')}<br>"
            f"Indicative gross margin proxy: ₹{_format_number(row.get('gross_margin_proxy_inr_per_t'), '/t')}<br>"
            f"Coordinate quality: {properties.get('coordinate_quality', 'Not available')}"
        )
        if geometry.get("type") == "Point":
            lon, lat = geometry.get("coordinates", [None, None])
            if lat is None or lon is None:
                continue
            folium.CircleMarker(
                location=[lat, lon],
                radius=11 if mine == selected_mine else 8,
                color=color,
                fill=True,
                fill_color=color,
                fill_opacity=0.88,
                tooltip=folium.Tooltip(tooltip),
                popup=folium.Popup(tooltip, max_width=360),
            ).add_to(folium_map)
        else:
            folium.GeoJson(
                feature,
                name=f"{mine} contextual envelope",
                style_function=lambda _feature, fill=color, is_selected=(mine == selected_mine): {
                    "color": fill,
                    "weight": 4 if is_selected else 2,
                    "fillColor": fill,
                    "fillOpacity": 0.25,
                    "dashArray": "6 4",
                },
                tooltip=folium.Tooltip(tooltip),
            ).add_to(folium_map)
    folium.LayerControl(collapsed=True).add_to(folium_map)
    return folium_map


def _layer_value(cell: PredictionCell, layer: str) -> Optional[float]:
    if layer == "Base ML":
        return cell.base_mn_probability
    if layer == "CLASS-Informed":
        return cell.class_mn_probability
    if layer == "CLASS Impact Δ":
        return cell.class_delta()
    if layer == "Predicted Mn Grade":
        return cell.predicted_mn_pct
    if layer == "Estimated Tonnage Proxy":
        return cell.estimated_tonnage_proxy_t
    return None


def _cell_color(value: Optional[float], layer: str) -> str:
    if value is None:
        return "#94a3b8"
    if layer == "CLASS Impact Δ":
        return "#b91c1c" if value < -0.02 else "#15803d" if value > 0.02 else "#64748b"
    if layer in {"Base ML", "CLASS-Informed"}:
        return "#1d4ed8" if value < 0.4 else "#f59e0b" if value < 0.7 else "#b91c1c"
    return "#1d4ed8" if value < 25 else "#f59e0b" if value < 35 else "#b91c1c"


def _cell_tooltip(cell: PredictionCell, layer: str) -> str:
    delta = cell.class_delta()
    status = "DEMO SPATIAL VALUE" if cell.data_status == DATA_STATUS_DEMO else "REAL MODEL OUTPUT"
    return (
        f"<b>{cell.cell_id}</b><br>"
        f"{status}<br>"
        f"Predicted Mn grade: {_format_number(cell.predicted_mn_pct, '%')}<br>"
        f"Base ML probability: {_format_number(None if cell.base_mn_probability is None else cell.base_mn_probability * 100, '%')}<br>"
        f"CLASS-informed probability: {_format_number(None if cell.class_mn_probability is None else cell.class_mn_probability * 100, '%')}<br>"
        f"CLASS impact: {_format_number(None if delta is None else delta * 100, ' pp')}<br>"
        f"Estimated tonnage proxy: {_format_number(cell.estimated_tonnage_proxy_t, ' t')}<br>"
        f"Lithology: {cell.lithology or 'Not available'}<br>"
        f"Active layer: {layer}"
    )


def _render_prediction_map(bundle: DatasetBundle, cells: List[PredictionCell], layer: str, center: Sequence[float]) -> folium.Map:
    _require_map_dependencies()
    if cells:
        latitudes = []
        longitudes = []
        for cell in cells:
            coords = cell.geometry.get("coordinates", [])
            for ring in coords:
                for lon, lat in ring:
                    longitudes.append(lon)
                    latitudes.append(lat)
        map_center = (float(np.mean(latitudes)), float(np.mean(longitudes))) if latitudes else (center[0], center[1])
    else:
        map_center = (center[0], center[1])
    folium_map = folium.Map(location=map_center, zoom_start=14, tiles="OpenStreetMap", control_scale=True)
    for cell in cells:
        value = _layer_value(cell, layer)
        folium.GeoJson(
            cell.to_feature(),
            name=cell.cell_id,
            style_function=lambda _feature, fill=_cell_color(value, layer): {
                "color": fill,
                "weight": 1,
                "fillColor": fill,
                "fillOpacity": 0.64,
            },
            highlight_function=lambda _feature: {"weight": 3, "color": "#f8fafc", "fillOpacity": 0.82},
            tooltip=folium.Tooltip(_cell_tooltip(cell, layer)),
        ).add_to(folium_map)
    folium.LayerControl(collapsed=True).add_to(folium_map)
    return folium_map


def _render_analytics(bundle: DatasetBundle, mine_name: str) -> None:
    geology = bundle.geology[bundle.geology["mine_name"] == mine_name].copy()
    production = bundle.production[bundle.production["mine_name"] == mine_name].copy()
    if geology.empty and production.empty:
        st.info("No analytics rows are available for this mine.")
        return
    try:
        import plotly.express as px
    except ImportError:
        st.warning("Plotly is not installed; analytics charts are unavailable.")
        return

    st.caption("Geology records are used for descriptive distributions only; they are not individual map locations.")
    geology_tab, production_tab = st.tabs(["Geology analytics", "Production / weather"])
    with geology_tab:
        c1, c2 = st.columns(2)
        with c1:
            if "mn_pct" in geology:
                st.plotly_chart(px.histogram(geology, x="mn_pct", nbins=20, title="Mn % distribution"), use_container_width=True)
            if "grade_band" in geology:
                counts = geology["grade_band"].value_counts().rename_axis("grade_band").reset_index(name="records")
                st.plotly_chart(px.bar(counts, x="grade_band", y="records", title="Grade-band distribution"), use_container_width=True)
        with c2:
            if {"lithology", "mn_pct"}.issubset(geology.columns):
                by_lithology = geology.groupby("lithology", as_index=False)["mn_pct"].mean().sort_values("mn_pct", ascending=False)
                st.plotly_chart(px.bar(by_lithology, x="lithology", y="mn_pct", title="Mean Mn % by lithology"), use_container_width=True)
            if "thickness_m" in geology:
                st.plotly_chart(px.histogram(geology, x="thickness_m", nbins=20, title="Thickness distribution (m)"), use_container_width=True)
        ratio = st.selectbox("ASTER ratio", ["aster_b4_b5_ratio", "aster_b6_b7_ratio", "aster_b8_b9_ratio"], key=f"aster_{mine_name}")
        s2_ratio = "s2_b11_b12_ratio"
        scatter_cols = st.columns(2)
        with scatter_cols[0]:
            if {"mn_pct", "ndvi"}.issubset(geology.columns):
                st.plotly_chart(px.scatter(geology, x="ndvi", y="mn_pct", color="grade_band" if "grade_band" in geology else None, title="Mn % vs NDVI"), use_container_width=True)
        with scatter_cols[1]:
            if {"mn_pct", ratio}.issubset(geology.columns):
                st.plotly_chart(px.scatter(geology, x=ratio, y="mn_pct", title=f"Mn % vs {ratio}"), use_container_width=True)
        if {"mn_pct", s2_ratio}.issubset(geology.columns):
            st.plotly_chart(px.scatter(geology, x=s2_ratio, y="mn_pct", title="Mn % vs Sentinel-2 B11/B12 ratio"), use_container_width=True)
    with production_tab:
        if production.empty:
            st.info("No production records are available for this mine.")
            return
        production = production.copy()
        production["date"] = pd.to_datetime(production["date"], errors="coerce")
        timeline = st.columns(2)
        with timeline[0]:
            if {"date", "target_rom_tonnes", "actual_rom_tonnes"}.issubset(production.columns):
                st.plotly_chart(px.line(production, x="date", y=["target_rom_tonnes", "actual_rom_tonnes"], title="Target ROM vs Actual ROM"), use_container_width=True)
            if {"date", "shortfall_tonnes"}.issubset(production.columns):
                st.plotly_chart(px.line(production, x="date", y="shortfall_tonnes", title="Shortfall over time"), use_container_width=True)
        with timeline[1]:
            if {"date", "rainfall_mm"}.issubset(production.columns):
                st.plotly_chart(px.line(production, x="date", y="rainfall_mm", title="Rainfall over time"), use_container_width=True)
            if {"date", "equipment_downtime_hours", "blast_delay_minutes"}.issubset(production.columns):
                st.plotly_chart(px.line(production, x="date", y=["equipment_downtime_hours", "blast_delay_minutes"], title="Equipment downtime and blast delay"), use_container_width=True)
        scatter = st.columns(2)
        with scatter[0]:
            if {"rainfall_mm", "shortfall_tonnes"}.issubset(production.columns):
                st.plotly_chart(px.scatter(production, x="rainfall_mm", y="shortfall_tonnes", title="Rainfall vs shortfall"), use_container_width=True)
        with scatter[1]:
            if {"equipment_downtime_hours", "shortfall_tonnes"}.issubset(production.columns):
                st.plotly_chart(px.scatter(production, x="equipment_downtime_hours", y="shortfall_tonnes", title="Downtime vs shortfall"), use_container_width=True)


def _render_inspector(bundle: DatasetBundle, mine_name: str, cells: List[PredictionCell]) -> None:
    row_df = bundle.summary[bundle.summary["mine_name"] == mine_name]
    if row_df.empty:
        return
    row = row_df.iloc[0].to_dict()
    feature = _mine_geometry(bundle, mine_name) or {}
    props = feature.get("properties", {})
    st.subheader("Mine / cell inspector")
    st.caption(f"{mine_name} · {row.get('state', '')} · {row.get('district', '')}")
    st.warning(
        f"Coordinate quality: {props.get('coordinate_quality', row.get('coordinate_quality', 'Not available'))}. "
        f"{props.get('coordinate_note', row.get('coordinate_note', ''))}"
    )
    c1, c2, c3 = st.columns(3)
    c1.metric("Mean synthetic Mn %", _format_number(row.get("synthetic_mean_mn_pct"), "%"))
    c2.metric("Actual ROM", _format_number(row.get("actual_rom_tonnes"), " t"))
    c3.metric("Shortfall", _format_number(row.get("official_shortfall_pct"), "%"))
    with st.expander("Geology / production / economics metadata", expanded=False):
        st.dataframe(
            pd.DataFrame(
                [
                    {"Metric": "Reference Mn range", "Value": f"{_format_number(row.get('mn_grade_min_reference_pct'), '%')} – {_format_number(row.get('mn_grade_max_reference_pct'), '%')}", "Status": "Official/reference"},
                    {"Metric": "Median thickness", "Value": _format_number(row.get("synthetic_median_thickness_m"), " m"), "Status": "Synthetic"},
                    {"Metric": "Median specific gravity", "Value": _format_number(row.get("synthetic_median_specific_gravity")), "Status": "Synthetic"},
                    {"Metric": "Estimated Tonnage Proxy", "Value": _format_number(row.get("synthetic_median_tonnage_proxy_t"), " t"), "Status": "Derived proxy; do not sum"},
                    {"Metric": "Reference ROM", "Value": _format_number(row.get("reference_rom_tonnes"), " t"), "Status": "Official/reference"},
                    {"Metric": "Actual ROM", "Value": _format_number(row.get("actual_rom_tonnes"), " t"), "Status": "Official/reference"},
                    {"Metric": "Shortfall tonnes", "Value": _format_number(row.get("official_shortfall_tonnes"), " t"), "Status": "Official/reference"},
                    {"Metric": "Indicative gross margin proxy / t", "Value": f"₹{_format_number(row.get('gross_margin_proxy_inr_per_t'))}", "Status": "Derived proxy; not accounting profit"},
                ]
            ),
            hide_index=True,
            use_container_width=True,
        )
    if cells:
        selected_id = st.selectbox("Inspect prediction cell", [cell.cell_id for cell in cells], key=f"cell_{mine_name}")
        cell = next(cell for cell in cells if cell.cell_id == selected_id)
        st.info("DEMO SPATIAL VALUE — synthetic visualization cell; not an actual reserve location." if cell.data_status == DATA_STATUS_DEMO else "REAL MODEL OUTPUT")
        st.json(
            {
                "cell_id": cell.cell_id,
                "predicted_mn_pct": cell.predicted_mn_pct,
                "base_probability": cell.base_mn_probability,
                "class_informed_probability": cell.class_mn_probability,
                "class_impact": cell.class_delta(),
                "confidence": cell.confidence if cell.confidence is not None else "Not available",
                "estimated_tonnage_proxy_t": cell.estimated_tonnage_proxy_t,
                "ndvi": cell.ndvi,
                "s2_b11_b12_ratio": cell.s2_b11_b12_ratio,
                "aster_b4_b5_ratio": cell.aster_b4_b5_ratio,
                "lithology": cell.lithology,
                "model_version": cell.model_version,
            }
        )


def _render_exports(bundle: DatasetBundle, mine_name: str, cells: List[PredictionCell]) -> None:
    st.subheader("Export")
    summary = bundle.summary[bundle.summary["mine_name"] == mine_name]
    e1, e2, e3 = st.columns(3)
    with e1:
        st.download_button("Export filtered mine table CSV", bundle.summary.to_csv(index=False), "moil_mines.csv", "text/csv", key="export_mines")
    with e2:
        payload = {"type": "FeatureCollection", "features": [cell.to_feature() for cell in cells]}
        payload["data_status"] = DATA_STATUS_DEMO if any(cell.data_status == DATA_STATUS_DEMO for cell in cells) else DATA_STATUS_REAL
        st.download_button("Export displayed cells GeoJSON", json.dumps(payload, indent=2), f"{mine_name}_predictions.geojson", "application/geo+json", key=f"export_geojson_{mine_name}")
    with e3:
        st.download_button("Export mine analytics CSV", summary.to_csv(index=False), f"{mine_name}_analytics.csv", "text/csv", key=f"export_analytics_{mine_name}")


def render_reserve_map(center: Sequence[float], ore_pockets: Optional[List[Dict[str, Any]]] = None, highlight_index: int = 0) -> None:
    """Render the MOIL portfolio/detail GIS view in Streamlit.

    ``ore_pockets`` remains accepted for compatibility with the original team
    shell. When the supplied SIH dataset is present, the schema-faithful files
    take precedence over that legacy argument.
    """

    try:
        bundle = load_dataset()
    except (FileNotFoundError, json.JSONDecodeError, pd.errors.ParserError) as exc:
        st.error(f"Unable to load MOIL spatial dataset: {exc}")
        return
    if bundle.summary.empty:
        st.error("mine_dashboard_summary.csv is empty or missing. Set MOIL_DATA_DIR to the extracted dataset directory.")
        return

    st.markdown("### MOIL GeoMine Intelligence")
    st.caption("AI-Assisted Manganese Prospectivity & Decision Support")
    st.info("DATA STATUS: OFFICIAL / REFERENCE DATA + SYNTHETIC CALIBRATED DATA + DERIVED PROXY. Geological rows use mine anchors, not mapped sample locations.")

    with st.expander("Data status and limitations", expanded=False):
        st.markdown(
            "**Synthetic Spatial Demo** cells are deterministic visualization values and are **not actual reserve locations**. "
            "Public envelope polygons are contextual extents, not legal lease boundaries. "
            "Confidence: **Not available** until the ML provider supplies it."
        )
        for warning in bundle.metadata.get("warnings", []):
            st.warning(warning)

    mine_names = bundle.summary["mine_name"].astype(str).tolist()
    control_a, control_b, control_c = st.columns(3)
    with control_a:
        view = st.radio("View", ["Portfolio", "Mine detail"], horizontal=True, key="spatial_view")
    with control_b:
        selected_mine = st.selectbox("Mine", mine_names, index=min(highlight_index, len(mine_names) - 1), key="spatial_mine")
    with control_c:
        kpi = st.selectbox("Portfolio colour KPI", ["Mean Mn %", "Production Shortfall %", "Gross Margin Proxy / t", "Actual Production", "Reference Production"], key="spatial_kpi")

    service = PredictionService(bundle)
    cells: List[PredictionCell] = []
    try:
        if view == "Portfolio":
            map_obj = _render_portfolio_map(bundle, center, kpi, selected_mine)
            st.caption("Portfolio map: mine-level markers and contextual public envelopes. These are not reserve-cell maps.")
        else:
            cells = service.get_predictions(selected_mine)
            layer = st.selectbox("Mine detail layer", ["Base ML", "CLASS-Informed", "CLASS Impact Δ", "Predicted Mn Grade", "Estimated Tonnage Proxy"], key="spatial_layer")
            if service.data_status == DATA_STATUS_DEMO:
                st.error("SYNTHETIC SPATIAL DEMO — NOT ACTUAL RESERVE LOCATIONS")
                st.caption("CLASS Demo Layer is model-derived demonstration output; it does not represent direct Chandrayaan-2 observations of this mine.")
            else:
                st.success("REAL MODEL MODE — displaying the configured georeferenced prediction provider.")
            selected_row = bundle.summary[bundle.summary["mine_name"] == selected_mine].iloc[0]
            map_obj = _render_prediction_map(bundle, cells, layer, [_number(selected_row.get("mine_latitude"), center[0]) or center[0], _number(selected_row.get("mine_longitude"), center[1]) or center[1]])
            st.caption("Estimated Tonnage Proxy is an indicative geometric proxy and must not be summed into a mine reserve total.")
    except RuntimeError as exc:
        st.error(str(exc))
        return

    try:
        from streamlit_folium import st_folium

        st_folium(map_obj, height=640, use_container_width=True)
    except ImportError:
        st.warning("streamlit-folium is not installed; showing the map HTML fallback.")
        st.components.v1.html(map_obj.get_root().render(), height=640, scrolling=False)

    if view == "Mine detail":
        weights = {}
        with st.expander("Mining Priority — configurable decision-support score", expanded=False):
            st.caption("Priority score is not geological reserve probability. Production, economics and weather never alter geological prospectivity.")
            weight_cols = st.columns(5)
            labels = [
                ("mn_potential", "Mn potential", 28),
                ("grade", "Grade", 22),
                ("production_urgency", "Production urgency", 20),
                ("economic_attractiveness", "Economic attractiveness", 18),
                ("weather_feasibility", "Weather feasibility", 12),
            ]
            for column, (key, label, default) in zip(weight_cols, labels):
                with column:
                    weights[key] = st.slider(label, 0, 100, default, key=f"weight_{key}")
            priority = calculate_mining_priority(bundle.summary, bundle.production, weights)
            st.dataframe(priority[["mine_name", "priority_score", "priority_band"]].sort_values("priority_score", ascending=False), hide_index=True, use_container_width=True)
        _render_inspector(bundle, selected_mine, cells)
        _render_analytics(bundle, selected_mine)
        _render_exports(bundle, selected_mine, cells)

    with st.expander("Data provenance", expanded=False):
        if bundle.sources.empty:
            st.info("source_registry.csv is unavailable.")
        else:
            st.dataframe(bundle.sources, hide_index=True, use_container_width=True)


__all__ = [
    "DatasetBundle",
    "GeoJSONPredictionProvider",
    "PredictionCell",
    "PredictionProvider",
    "PredictionService",
    "SyntheticPredictionProvider",
    "calculate_mining_priority",
    "get_class_delta",
    "load_dataset",
    "render_reserve_map",
]
