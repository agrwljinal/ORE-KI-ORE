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

import constants as C

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


def _factor_weights():
    """Multiplicative factor weights tied to the trained model.

    The 13-feature OLS fits ``output_ratio = intercept + Σ coef_i · feature_i``
    where each feature is normalised to [0, 1] (labor_availability_proxy is
    1 - labor_norm). Writing it multiplicatively:

        outputRatio ≈ rainfallFactor · uptimeFactor · laborFactor

    with ``factor = 1 + coef · feature_norm`` reproduces the model's marginal
    responses (each coef is small, so the linear and product forms agree to
    first order). We therefore use |model coef| as the factor weights:
    ideal conditions -> every factor = 1.0 -> predicted = fleet capacity.
    """
    model = _load_model()
    if model is not None:
        coef = model.get("coefficients") or {}
        w_rain = abs(float(coef.get("rainfall_norm", 0.0)))
        w_dt = abs(float(coef.get("equipment_downtime_norm", 0.0)))
        w_lab = abs(float(coef.get("labor_availability_proxy", 0.0)))
        if w_rain or w_dt or w_lab:
            return {
                "rain": max(w_rain, 0.01),
                "uptime": max(w_dt, 0.01),
                "labor": max(w_lab, 0.01),
                "source": "trained_ols_coefficients",
            }
    return {
        "rain": 0.25,
        "uptime": 0.20,
        "labor": 0.15,
        "source": "fallback_constants",
    }


def predict_shortfall_with_model(base_target, rainfall_mm, mtbf_hrs, labor_drop_pct,
                                 rain_3d_mm=None, downtime_3d_hrs=None,
                                 rain_7d_mm=None, downtime_7d_hrs=None, mine_name=None,
                                 blast_delay_minutes=None, ore_grade="STD",
                                 fleet_capacity_baseline=None):
    """Predict weekly shortfall with the multiplicative factor model.

    Decoupled formula:
        predictedOutput = fleetCapacityBaseline × rainfallFactor × uptimeFactor
                          × laborFactor × oreGradeFactor
        shortfall       = max(0, monthlyTarget − predictedOutput)

    `monthlyTarget` (base_target) appears only in the comparison, never inside
    the output formula. Factor weights are the trained model's own OLS
    coefficients (fallback constants when the model is missing) so ideal
    conditions always produce predictedOutput == fleetCapacityBaseline.

    Returns the identical dict shape as predict_weekly_tonnage plus factor
    metadata:
        {
            "predicted_tonnage", "shortfall_tonnage", "penalties",
            "model_used", "output_ratio", "honesty_notes",
            "fleet_capacity_baseline", "ore_grade",
            "factors": {"rainfall", "uptime", "labor", "ore_grade"},
        }
    """
    weights = _factor_weights()
    dtype = weights["source"]

    downtime_hours = _downtime_from_mtbf(mtbf_hrs)

    rain_norm = min(max(rainfall_mm, 0.0) / _RAIN_CAP_MM, 1.0)
    downtime_norm = min(max(downtime_hours, 0.0) / _DOWNTIME_CAP_HRS, 1.0)
    labor_norm = min(max(labor_drop_pct, 0.0) / 100.0, 1.0)

    rainfall_factor = 1.0 - weights["rain"] * rain_norm
    uptime_factor = 1.0 - weights["uptime"] * downtime_norm
    labor_factor = 1.0 - weights["labor"] * labor_norm

    grade_factor = float(C.ORE_GRADE_FACTORS.get(ore_grade, 1.0))

    baseline = fleet_capacity_baseline if fleet_capacity_baseline else float(C.FLEET_CAPACITY_BASELINE_TONS)

    predicted = baseline * rainfall_factor * uptime_factor * labor_factor * grade_factor
    predicted = max(0.0, float(predicted))
    shortfall = max(0.0, float(base_target) - predicted)

    model = _load_model() or {}
    notes = list(model.get("honesty_notes") or [])
    notes.append(
        "Predicted output uses the decoupled multiplicative factor model: "
        "predicted = fleetCapacityBaseline × rainfall × uptime × labor × oreGrade. "
        f"Factor weights sourced from {dtype}. Monthly target is only used for the "
        "shortfall/ledger comparison."
    )

    return {
        "predicted_tonnage": round(predicted, 2),
        "shortfall_tonnage": round(shortfall, 2),
        "penalties": {
            "rain": round(1.0 - rainfall_factor, 4),
            "equipment": round(1.0 - uptime_factor, 4),
            "labor": round(1.0 - labor_factor, 4),
        },
        "model_used": f"multiplicative_factor_{dtype}",
        "output_ratio": round(predicted / baseline if baseline else 0.0, 4),
        "honesty_notes": notes,
        "fleet_capacity_baseline": round(float(baseline), 2),
        "ore_grade": ore_grade,
        "factors": {
            "rainfall": round(rainfall_factor, 4),
            "uptime": round(uptime_factor, 4),
            "labor": round(labor_factor, 4),
            "ore_grade": round(grade_factor, 4),
        },
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


def derive_banner_state(predicted_tonnage, target_tonnage, recovered_tonnage=0.0,
                        plan_executed=False, mitigation_counts=0):
    """Single derived-state rule (item 4) driving headline, colour, simulation
    state, and the rupee ledger from ONE live (post-recovery) predicted output.

    Tier grid:
        predicted >= target            -> GREEN  "TARGET EXCEEDED — SURPLUS PROJECTED"
        predicted >= 0.90 * target     -> AMBER  "ON TRACK — MINOR VARIANCE"
        predicted <  0.90 * target     -> RED    "SHORTFALL ALERT"

    Simulation state ladder on the same tiers:
        UNMITIGATED RISK -> MITIGATING -> OPTIMAL / TARGET SECURED

    Rupee ledger (item 5) derived from the same live gap:
        loss = (target - predicted) * MN_COST_PER_TON_INR   (red)
        gain = (predicted - target) * MN_PRICE_PER_TON_INR  (green)
    """
    predicted = max(0.0, float(predicted_tonnage))
    target = max(0.0, float(target_tonnage))
    predicted_final = predicted + max(0.0, float(recovered_tonnage))
    ratio = (predicted_final / target) if target > 0 else 0.0
    extra = max(0, int(mitigation_counts))

    if predicted_final >= target:
        tier = "target_exceeded"
        banner_class = "ok"
        headline = C.TIER_TARGET_EXCEEDED_LABEL
        sim_state = C.SIM_STATE_LABELS["optimal"]
    elif ratio >= C.TIER_ON_TRACK_RATIO:
        tier = "on_track"
        banner_class = "warn"
        headline = C.TIER_ON_TRACK_LABEL
        sim_state = C.SIM_STATE_LABELS["mitigating"] if plan_executed or extra > 0 else C.SIM_STATE_LABELS["unmitigated"]
    else:
        tier = "shortfall"
        banner_class = "danger"
        headline = C.TIER_SHORTFALL_LABEL
        sim_state = C.SIM_STATE_LABELS["mitigating"] if plan_executed or extra > 0 else C.SIM_STATE_LABELS["unmitigated"]

    gap_tonnes = predicted_final - target
    if gap_tonnes < 0:
        ledger_amount_inr = (-gap_tonnes) * float(C.MN_COST_PER_TON_INR)
        ledger_label = "Rupee Loss Ledger"
        ledger_class = "text-red"
    else:
        ledger_amount_inr = gap_tonnes * float(C.MN_PRICE_PER_TON_INR)
        ledger_label = "Rupee Gain Ledger"
        ledger_class = "text-green"

    return {
        "tier": tier,
        "banner_class": banner_class,
        "headline": headline,
        "simulation_state": sim_state,
        "ratio_pct": round(ratio * 100.0, 1),
        "gap_tonnes": round(gap_tonnes, 2),
        "remaining_shortfall_tonnes": round(max(0.0, -gap_tonnes), 2),
        "ledger_label": ledger_label,
        "ledger_amount_inr": round(ledger_amount_inr, 2),
        "ledger_crores": round(ledger_amount_inr / 1e7, 4),
        "ledger_class": ledger_class,
        "plan_executed": bool(plan_executed),
        "recovery_tonnes_applied": round(float(recovered_tonnage), 2),
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
    "derive_banner_state",
    "render_yield_gauge",
]
