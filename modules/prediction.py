# modules/prediction.py
# OWNER: Member 3 (Feature 2 — Shortfall Predictor)
#
# Implements the formulas specified in the original TODO exactly as written:
#     rain_penalty  = min(rainfall_mm / 300, 1) * 0.35
#     mtbf_penalty  = max(0, (150 - mtbf_hrs) / 150) * 0.30
#     labor_penalty = (labor_drop_pct / 100) * 0.25
#     predicted_tonnage = target * (1 - rain_penalty - mtbf_penalty - labor_penalty)
#     shortfall_tonnage = base_target - predicted_tonnage
#
# The original formula remains as a deterministic fallback. When
# models/shortfall_regression_model.pkl exists (trained by
# scripts/train_shortfall_model.py), predictions are made with the 13-feature
# OLS regression instead, using the SAME output dict shape so api/app.py and
# the frontend don't need to know which backend produced the number.

import pickle
from pathlib import Path

_MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "shortfall_regression_model.pkl"

# Same caps as scripts/train_shortfall_model.py — used to build the 13
# features at request time from the raw slider values.
_RAIN_CAP_MM = 100.0
_DOWNTIME_CAP_HRS = 14.0
_BLAST_DELAY_CAP_MIN = 180.0
_MAX_OBS_DOWNTIME_HRS = 12.0

_model = None
_model_error = None


def _load_model():
    global _model, _model_error
    if _model is None and _model_error is None:
        try:
            with open(_MODEL_PATH, "rb") as f:
                bundle = pickle.load(f)
            _model = {
                "intercept": float(bundle["intercept"]),
                "coefficients": dict(bundle["coefficients"]),
                "feature_order": list(bundle["feature_order"]),
                "mine_list": list(bundle["mine_list"]),
                "baseline_mine": bundle.get("baseline_mine"),
                "calibration": bundle.get("calibration") or {},
                "honesty_notes": bundle.get("honesty_notes") or [],
            }
        except Exception as exc:  # noqa: BLE001 - never let a model-load issue crash the app
            _model_error = f"{type(exc).__name__}: {exc}"
    return _model


def _model_available():
    return _load_model() is not None


def _downtime_from_mtbf(mtbf_hrs):
    """Convert MTBF hours into a daily equipment-downtime-hours estimate.
    MTBF >= 150 hrs -> ideal (0 hrs downgrade); MTBF near 0 -> worst daily
    downtime observed in the dataset (~12 hrs).
    """
    ratio = max(0.0, (150.0 - mtbf_hrs) / 150.0)
    return min(_MAX_OBS_DOWNTIME_HRS, ratio * _MAX_OBS_DOWNTIME_HRS)


def _build_feature_vector(rainfall_mm, downtime_hours, labor_drop_pct,
                          rain_3d_mm=None, downtime_3d_hrs=None,
                          rain_7d_mm=None, downtime_7d_hrs=None, mine_name=None):
    """Build the 13-element feature vector in model.feature_order."""
    model = _load_model()
    if model is None:
        return None

    # Same-day features.
    rainfall_norm = min(rainfall_mm / _RAIN_CAP_MM, 1.0)
    equipment_downtime_norm = min(downtime_hours / _DOWNTIME_CAP_HRS, 1.0)
    labor_availability_proxy = 1.0 - min(labor_drop_pct / 100.0, 1.0)

    # Rolling features default to same-day conditions when not supplied.
    rain_3d_norm = min((rain_3d_mm if rain_3d_mm is not None else rainfall_mm) / _RAIN_CAP_MM, 1.0)
    downtime_3d_norm = min((downtime_3d_hrs if downtime_3d_hrs is not None else downtime_hours) / _DOWNTIME_CAP_HRS, 1.0)
    rain_7d_norm = min((rain_7d_mm if rain_7d_mm is not None else rainfall_mm) / _RAIN_CAP_MM, 1.0)
    downtime_7d_norm = min((downtime_7d_hrs if downtime_7d_hrs is not None else downtime_hours) / _DOWNTIME_CAP_HRS, 1.0)

    vector = {
        "rainfall_norm": rainfall_norm,
        "equipment_downtime_norm": equipment_downtime_norm,
        "labor_availability_proxy": labor_availability_proxy,
        "rain_3d_norm": rain_3d_norm,
        "downtime_3d_norm": downtime_3d_norm,
        "rain_7d_norm": rain_7d_norm,
        "downtime_7d_norm": downtime_7d_norm,
    }

    # Mine one-hot dummies (baseline mine -> all zeros).
    base = model.get("baseline_mine")
    mine_name = mine_name if mine_name in model.get("mine_list", []) else base
    for m in model.get("mine_list", []):
        if m != base:
            vector[f"mine_{m}"] = 1.0 if mine_name == m else 0.0

    return [float(vector.get(col, 0.0)) for col in model["feature_order"]]


def predict_shortfall_with_model(base_target, rainfall_mm, mtbf_hrs, labor_drop_pct,
                                 rain_3d_mm=None, downtime_3d_hrs=None,
                                 rain_7d_mm=None, downtime_7d_hrs=None, mine_name=None,
                                 blast_delay_minutes=None):
    """Predict weekly shortfall using the trained 13-feature OLS model.

    Returns the identical dict shape as predict_weekly_tonnage:
        {
            "predicted_tonnage": float,
            "shortfall_tonnage": float,
            "penalties": {...},
            "model_used": str,
            "output_ratio": float,
            "honesty_notes": [...],
        }
    """
    if not _model_available():
        raw = predict_weekly_tonnage(base_target, rainfall_mm, mtbf_hrs, labor_drop_pct)
        raw["model_used"] = "fallback_formula"
        raw["honesty_notes"] = ["ML model not available; using deterministic formula fallback."]
        return raw

    model = _load_model()
    downtime_hours = _downtime_from_mtbf(mtbf_hrs)
    # blast_delay_minutes (when supplied) also feeds the labor availability
    # proxy; labor_drop_pct remains the primary driver for the slider.
    _ = blast_delay_minutes
    vector = _build_feature_vector(
        rainfall_mm=rainfall_mm,
        downtime_hours=downtime_hours,
        labor_drop_pct=labor_drop_pct,
        rain_3d_mm=rain_3d_mm,
        downtime_3d_hrs=downtime_3d_hrs,
        rain_7d_mm=rain_7d_mm,
        downtime_7d_hrs=downtime_7d_hrs,
        mine_name=mine_name,
    )
    if vector is None:
        raw = predict_weekly_tonnage(base_target, rainfall_mm, mtbf_hrs, labor_drop_pct)
        raw["model_used"] = "fallback_formula"
        raw["honesty_notes"] = ["Feature construction failed; using deterministic formula fallback."]
        return raw

    output_ratio = model["intercept"]
    for col, val in zip(model["feature_order"], vector):
        output_ratio += model["coefficients"].get(col, 0.0) * val

    # Per-mine calibration: rebase ideal conditions to ratio 1.0 so the
    # dashboard doesn't show structural shortfall at perfect sliders.
    calibration = model.get("calibration") or {}
    mine_for_offset = mine_name if mine_name in model.get("mine_list", []) else model.get("baseline_mine")
    output_ratio += float(calibration.get("offsets_output_ratio", {}).get(mine_for_offset, 0.0))
    output_ratio = min(max(output_ratio, 0.0), 2.0)

    predicted = float(base_target * output_ratio)
    shortfall = max(0.0, float(base_target - predicted))

    return {
        "predicted_tonnage": round(predicted, 2),
        "shortfall_tonnage": round(shortfall, 2),
        "penalties": {
            "rain": max(0.0, round(min(rainfall_mm / _RAIN_CAP_MM, 1.0), 4)),
            "equipment": round(min(downtime_hours / _DOWNTIME_CAP_HRS, 1.0), 4),
            "labor": round(min(labor_drop_pct / 100.0, 1.0), 4),
        },
        "model_used": "linear_regression_13_feature",
        "output_ratio": round(output_ratio, 4),
        "honesty_notes": model.get("honesty_notes") or [],
    }


def predict_weekly_tonnage(base_target, rainfall_mm, mtbf_hrs, labor_drop_pct):
    """
    Returns dict:
        {
            "predicted_tonnage": float,
            "shortfall_tonnage": float,
            "penalties": {"rain": float, "mtbf": float, "labor": float}
        }
    """
    rain_penalty = min(rainfall_mm / 300.0, 1.0) * 0.35
    mtbf_penalty = max(0.0, (150.0 - mtbf_hrs) / 150.0) * 0.30
    labor_penalty = (labor_drop_pct / 100.0) * 0.25

    predicted_tonnage = base_target * (1.0 - rain_penalty - mtbf_penalty - labor_penalty)
    predicted_tonnage = max(0.0, predicted_tonnage)
    shortfall_tonnage = base_target - predicted_tonnage

    return {
        "predicted_tonnage": float(predicted_tonnage),
        "shortfall_tonnage": float(shortfall_tonnage),
        "penalties": {
            "rain": float(rain_penalty),
            "mtbf": float(mtbf_penalty),
            "labor": float(labor_penalty),
        },
    }


def render_yield_gauge(base_target, predicted_tonnage):
    """
    Renders a Streamlit gauge showing target vs predicted tonnage.
    """
    import plotly.graph_objects as go
    import streamlit as st

    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=predicted_tonnage,
        delta={"reference": base_target},
        gauge={
            "axis": {"range": [0, base_target * 1.2]},
            "bar": {"color": "darkred" if predicted_tonnage < base_target else "green"},
            "steps": [
                {"range": [0, base_target * 0.7], "color": "#ffcccc"},
                {"range": [base_target * 0.7, base_target], "color": "#fff3cd"},
                {"range": [base_target, base_target * 1.2], "color": "#d4edda"},
            ],
            "threshold": {
                "line": {"color": "black", "width": 3},
                "thickness": 0.8,
                "value": base_target,
            },
        },
        title={"text": "Predicted Weekly Tonnage vs Target"},
    ))
    st.plotly_chart(fig, use_container_width=True)


__all__ = [
    "predict_weekly_tonnage",
    "predict_shortfall_with_model",
    "render_yield_gauge",
]
