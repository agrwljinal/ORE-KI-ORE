"""Direct ROM-output prediction and the dashboard's downstream state rules."""
from __future__ import annotations

import pickle
from pathlib import Path

import constants as C

_MODEL_PATH = Path(__file__).resolve().parent.parent / "models" / "shortfall_regression_model.pkl"
_model = None
_model_error = None


def _load_model():
    global _model, _model_error
    if _model is None and _model_error is None:
        try:
            with open(_MODEL_PATH, "rb") as handle:
                bundle = pickle.load(handle)
            if bundle.get("target") != "actual_rom_tonnes":
                raise ValueError("model target must be actual_rom_tonnes; run scripts/train_shortfall_model.py")
            _model = bundle
        except Exception as exc:  # Never let a model-load issue take down the dashboard.
            _model_error = f"{type(exc).__name__}: {exc}"
    return _model


def labor_factor(attendance_deficit_pct: float) -> float:
    """Transparent, non-learned attendance scenario multiplier."""
    deficit = min(max(float(attendance_deficit_pct), 0.0), 50.0) / 100.0
    return max(0.0, 1.0 - 0.45 * deficit)


def ore_grade_factor(ore_grade: str) -> float:
    """Transparent, non-learned ore-grade scenario multiplier."""
    return float(C.ORE_GRADE_FACTORS.get(str(ore_grade).upper(), 1.0))


def _feature_values(rainfall_mm, soil_moisture_pct, equipment_downtime_hours,
                    blast_delay_minutes, mine_name, rain_3d_mm=None,
                    rain_7d_mm=None, soil_moisture_3d_avg=None,
                    soil_moisture_7d_avg=None):
    """Build the normalised ML feature vector from slider values.

    Only features present in the loaded model's ``feature_order`` are included
    in the returned ``values`` dict.  This keeps the function backward-compatible
    with v4 models (which used soil 3d/7d) while correctly supporting the v5
    model that dropped them and added ``rain_x_downtime``.
    """
    model = _load_model()
    if model is None:
        return None
    feature_order = model.get("feature_order", [])
    values: dict[str, float] = {}
    raw: dict[str, float] = {}

    def _add(key: str, val: float, raw_val: float | None = None):
        if key in feature_order:
            values[key] = val
            raw[key] = raw_val if raw_val is not None else val

    r = max(0.0, float(rainfall_mm))
    s = max(0.0, float(soil_moisture_pct))
    d = max(0.0, float(equipment_downtime_hours))
    b = max(0.0, float(blast_delay_minutes))

    _add("rainfall_mm", r, r)
    _add("rain_3d_mm", max(0.0, float(rain_3d_mm if rain_3d_mm is not None else rainfall_mm)))
    _add("rain_7d_mm", max(0.0, float(rain_7d_mm if rain_7d_mm is not None else rainfall_mm)))
    _add("soil_moisture_pct", s, s)
    _add("equipment_downtime_hours", d, d)
    _add("blast_delay_minutes", b, b)

    # v4 backward-compat (only if the loaded model uses them)
    _add("soil_moisture_3d_avg",
         max(0.0, float(soil_moisture_3d_avg if soil_moisture_3d_avg is not None else soil_moisture_pct)))
    _add("soil_moisture_7d_avg",
         max(0.0, float(soil_moisture_7d_avg if soil_moisture_7d_avg is not None else soil_moisture_pct)))

    # Engineered interaction: rain x downtime compounding (v5+)
    if "rain_x_downtime" in feature_order:
        values["rain_x_downtime"] = r * d
        raw["rain_x_downtime"] = r * d

    caps = model["numeric_feature_caps"]
    if model.get("feature_scaling") == "raw":
        vector = {name: value for name, value in values.items()}
    else:
        vector = {name: min(value / max(float(caps[name]), 1.0), 1.5) for name, value in values.items()}
    selected_mine = mine_name if mine_name in model["mine_list"] else model["baseline_mine"]
    for mine in model["mine_list"][1:]:
        vector[f"mine_{mine}"] = 1.0 if selected_mine == mine else 0.0
    return vector, raw, selected_mine


def predict_actual_rom_output(*, rainfall_mm, soil_moisture_pct, equipment_downtime_hours,
                              blast_delay_minutes, mine_name=None, rain_3d_mm=None,
                              rain_7d_mm=None, soil_moisture_3d_avg=None,
                              soil_moisture_7d_avg=None):
    """ML layer: predict actual_rom_tonnes only from production-dataset fields."""
    model = _load_model()
    result = _feature_values(rainfall_mm, soil_moisture_pct, equipment_downtime_hours,
                             blast_delay_minutes, mine_name, rain_3d_mm, rain_7d_mm,
                             soil_moisture_3d_avg, soil_moisture_7d_avg)
    if model is None or result is None:
        raise RuntimeError(f"Direct-output model unavailable: {_model_error or 'unknown error'}")
    vector, raw, selected_mine = result
    output = float(model["intercept"])
    for name in model["feature_order"]:
        output += float(model["coefficients"].get(name, 0.0)) * float(vector.get(name, 0.0))
    return max(0.0, output), raw, selected_mine, model


def predict_shortfall_with_model(base_target, rainfall_mm, soil_moisture_pct,
                                 equipment_downtime_hours, blast_delay_minutes,
                                 labor_drop_pct, mine_name=None, ore_grade="STD",
                                 rain_3d_mm=None, rain_7d_mm=None,
                                 soil_moisture_3d_avg=None, soil_moisture_7d_avg=None):
    """ML output x manual factors; target is used only for downstream shortfall."""
    ml_output, raw, selected_mine, model = predict_actual_rom_output(
        rainfall_mm=rainfall_mm, soil_moisture_pct=soil_moisture_pct,
        equipment_downtime_hours=equipment_downtime_hours,
        blast_delay_minutes=blast_delay_minutes, mine_name=mine_name,
        rain_3d_mm=rain_3d_mm, rain_7d_mm=rain_7d_mm,
        soil_moisture_3d_avg=soil_moisture_3d_avg,
        soil_moisture_7d_avg=soil_moisture_7d_avg,
    )
    attendance = labor_factor(labor_drop_pct)
    grade = ore_grade_factor(ore_grade)
    final_output = max(0.0, ml_output * attendance * grade)
    shortfall = max(0.0, float(base_target) - final_output)
    caps = model["numeric_feature_caps"]
    return {
        "ml_predicted_output": round(ml_output, 2),
        "predicted_tonnage": round(final_output, 2),
        "shortfall_tonnage": round(shortfall, 2),
        "model_used": model["model_type"], "mine_name": selected_mine,
        "ore_grade": str(ore_grade).upper(),
        "factors": {"labor": round(attendance, 4), "ore_grade": round(grade, 4)},
        "ml_features": raw, "metrics": model.get("metrics", {}),
        "honesty_notes": list(model.get("honesty_notes", [])),
        "penalties": {
            "rain": round(min(raw["rainfall_mm"] / max(caps["rainfall_mm"], 1), 1), 4),
            "equipment": round(min(raw["equipment_downtime_hours"] / max(caps["equipment_downtime_hours"], 1), 1), 4),
            "blast_delay": round(min(raw["blast_delay_minutes"] / max(caps["blast_delay_minutes"], 1), 1), 4),
        },
    }


def derive_banner_state(predicted_tonnage, target_tonnage, recovered_tonnage=0.0,
                        plan_executed=False, mitigation_counts=0):
    predicted_final = max(0.0, float(predicted_tonnage)) + max(0.0, float(recovered_tonnage))
    target = max(0.0, float(target_tonnage))
    ratio = predicted_final / target if target else 0.0
    if predicted_final >= target:
        tier, banner_class, headline, sim_state = "target_exceeded", "ok", C.TIER_TARGET_EXCEEDED_LABEL, C.SIM_STATE_LABELS["optimal"]
    elif ratio >= C.TIER_ON_TRACK_RATIO:
        tier, banner_class, headline = "on_track", "warn", C.TIER_ON_TRACK_LABEL
        sim_state = C.SIM_STATE_LABELS["mitigating"] if plan_executed or mitigation_counts else C.SIM_STATE_LABELS["unmitigated"]
    else:
        tier, banner_class, headline = "shortfall", "danger", C.TIER_SHORTFALL_LABEL
        sim_state = C.SIM_STATE_LABELS["mitigating"] if plan_executed or mitigation_counts else C.SIM_STATE_LABELS["unmitigated"]
    gap = predicted_final - target
    loss = gap < 0
    ledger_amount = abs(gap) * float(C.MN_COST_PER_TON_INR if loss else C.MN_PRICE_PER_TON_INR)
    return {"tier": tier, "banner_class": banner_class, "headline": headline, "simulation_state": sim_state,
            "ratio_pct": round(ratio * 100, 1), "gap_tonnes": round(gap, 2),
            "remaining_shortfall_tonnes": round(max(0, -gap), 2),
            "ledger_label": "Rupee Loss Ledger" if loss else "Rupee Gain Ledger",
            "ledger_amount_inr": round(ledger_amount, 2), "ledger_crores": round(ledger_amount / 1e7, 4),
            "ledger_class": "text-red" if loss else "text-green", "plan_executed": bool(plan_executed),
            "recovery_tonnes_applied": round(float(recovered_tonnage), 2)}


__all__ = ["predict_shortfall_with_model", "predict_actual_rom_output", "labor_factor", "ore_grade_factor", "derive_banner_state"]