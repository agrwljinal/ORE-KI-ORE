# modules/prediction.py
# OWNER: Member 3 (Feature 2 — Shortfall Predictor)
#
# Calculates predicted weekly tonnage based on operational penalties
# (rainfall, equipment MTBF, and labor drop) applied against a base target.


def predict_weekly_tonnage(base_target, rainfall_mm, mtbf_hrs, labor_drop_pct):
    """
    Formulas:
        rain_penalty  = min(rainfall_mm / 300, 1) * 0.35
        mtbf_penalty  = max(0, (150 - mtbf_hrs) / 150) * 0.30
        labor_penalty = (labor_drop_pct / 100) * 0.25
        predicted_tonnage = target * (1 - rain_penalty - mtbf_penalty - labor_penalty)
        shortfall_tonnage = base_target - predicted_tonnage

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

    total_penalty = rain_penalty + mtbf_penalty + labor_penalty
    predicted_tonnage = base_target * (1.0 - total_penalty)
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


__all__ = ["predict_weekly_tonnage"]
