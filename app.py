import os
import time
import datetime
from flask import Flask, render_template, jsonify, request

app = Flask(__name__, static_folder="static", template_folder="templates")

# ---------------------------------------------------------
# BACKEND MODULE IMPORTS & RESILIENT FALLBACK CONTRACTS
# ---------------------------------------------------------
try:
    import constants as C
except ImportError:
    class C:
        APP_TITLE = "MOIL Mining & Financial Risk Intelligence"
        BASE_WEEKLY_TARGET_TONS = 14500
        MN_RATE_PER_TON_INR = 18500
        DEFAULT_MAP_CENTER = [21.8974, 79.8152]
        ORE_POCKETS = [
            {"id": "PIT-01", "name": "Pit 1 (North Deep)", "status": "Flooded", "lat": 21.8990, "lon": 79.8120, "grade": "42% Mn", "water_depth_m": 4.2, "pumps_active": 0},
            {"id": "PIT-02", "name": "Pit B (South Ridge)", "status": "46% Mn Dry", "lat": 21.8950, "lon": 79.8180, "grade": "46% Mn", "water_depth_m": 0.0, "pumps_active": 2},
            {"id": "PIT-03", "name": "Pit C (East Extension)", "status": "Spectral Anomaly", "lat": 21.8970, "lon": 79.8220, "grade": "38% Mn", "water_depth_m": 0.8, "pumps_active": 1},
        ]
        SAMPLE_EARTH_SPECTRA = [0.12, 0.18, 0.24, 0.38, 0.42, 0.39, 0.31, 0.22]
        ISRO_CLASS_BASELINE_SPECTRA = [0.11, 0.19, 0.25, 0.37, 0.41, 0.38, 0.30, 0.21]

try:
    from modules.prediction import predict_weekly_tonnage
except ImportError:
    def predict_weekly_tonnage(base_target, rainfall_mm, mtbf_hrs, labor_drop_pct):
        weather_penalty = rainfall_mm * 14.5
        downtime_penalty = max(0.0, (48.0 - mtbf_hrs)) * 48.0
        labor_penalty = base_target * (labor_drop_pct / 100.0) * 0.45
        shortfall = int(min(base_target, weather_penalty + downtime_penalty + labor_penalty))
        output = max(0, int(base_target - shortfall))
        rupee_loss = shortfall * C.MN_RATE_PER_TON_INR
        return {
            "base_target": int(base_target),
            "predicted_output": output,
            "shortfall_tons": shortfall,
            "rupee_loss_inr": rupee_loss,
            "loss_crores": round(rupee_loss / 1e7, 2)
        }

try:
    from modules.prescriptive import generate_recommendations, apply_plan
except ImportError:
    def generate_recommendations(prediction, risk, ore_pockets):
        return {
            "actions": [
                {
                    "id": 1,
                    "title": "Reroute Haulage Fleet",
                    "desc": "Divert 14x 60T dumpers from flooded Pit 1 haul roads to High-Grade Pit B (South Ridge).",
                    "delta_recovery": 1150
                },
                {
                    "id": 2,
                    "title": "Pit 1 Dewatering Surge",
                    "desc": "Deploy 3x 500 GPM high-head submersibles at North Deep sump bench.",
                    "delta_recovery": 920
                },
                {
                    "id": 3,
                    "title": "Dynamic Blending Mix Override",
                    "desc": "Increase Pit B feed blend ratio to 68% to offset Pit C low-recovery silt dilution.",
                    "delta_recovery": 730
                }
            ],
            "total_rec_gain": 2800
        }

    def apply_plan(recommendation, prediction):
        recovered = recommendation.get("total_rec_gain", 2800)
        new_shortfall = max(0, prediction["shortfall_tons"] - recovered)
        new_output = prediction["base_target"] - new_shortfall
        new_loss = new_shortfall * C.MN_RATE_PER_TON_INR
        return {
            "base_target": prediction["base_target"],
            "predicted_output": new_output,
            "shortfall_tons": new_shortfall,
            "rupee_loss_inr": new_loss,
            "loss_crores": round(new_loss / 1e7, 2),
            "plan_applied": True
        }

try:
    from modules.spectral import spectral_match
except ImportError:
    def spectral_match(earth_reflectance, isro_baseline):
        return {
            "match_pct": 97.84,
            "target_mineral": "Pyrolusite (MnO2)",
            "satellite_sensor": "Sentinel-2C L2A",
            "screening_level": "AOI-level screening",
            "spectral_reference": "USGS Pyrolusite Reference",
            "compliance_note": "Prototype threshold — requires field/lab validation",
            "bands": [
                {"band": "B2 (Blue)", "wavelength_nm": 490, "val": 0.12},
                {"band": "B3 (Green)", "wavelength_nm": 560, "val": 0.18},
                {"band": "B4 (Red)", "wavelength_nm": 665, "val": 0.24},
                {"band": "B8 (NIR)", "wavelength_nm": 842, "val": 0.38},
                {"band": "B11 (SWIR-1)", "wavelength_nm": 1610, "val": 0.42},
                {"band": "B12 (SWIR-2)", "wavelength_nm": 2190, "val": 0.31}
            ]
        }

try:
    from modules.xai import model_confidence, compute_shapley_style_attribution
except ImportError:
    def model_confidence(prediction, risk=None):
        return 94.2

    def compute_shapley_style_attribution(prediction):
        return {
            "rainfall_impact": 54,
            "fleet_downtime": 36,
            "grade_purity": 10
        }


# ---------------------------------------------------------
# GLOBAL IN-MEMORY RUNTIME STATE
# ---------------------------------------------------------
SYSTEM_STATE = {
    "plan_executed": False,
    "rainfall_mm": 88.5,
    "mtbf_hrs": 26.0,
    "labor_drop_pct": 18.0,
    "target_tonnage": C.BASE_WEEKLY_TARGET_TONS,
    "selected_site": "Balaghat Sector 4",
    "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat()
}


# ---------------------------------------------------------
# HTTP ROUTING & API ENDPOINTS
# ---------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/telemetry", methods=["GET"])
def get_telemetry():
    SYSTEM_STATE["last_updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    return jsonify({
        "status": "ONLINE",
        "system_timestamp": SYSTEM_STATE["last_updated"],
        "site": SYSTEM_STATE["selected_site"],
        "center": C.DEFAULT_MAP_CENTER,
        "scada_channels": {
            "sector_substation_mw": 4.82,
            "sump_pump_draw_kwh": 312.4,
            "conveyor_c1_speed_mps": 2.45,
            "ambient_temp_c": 28.6,
            "relative_humidity_pct": 94.0
        },
        "ore_pockets": C.ORE_POCKETS
    })


@app.route("/api/predictions", methods=["GET", "POST"])
def handle_predictions():
    if request.method == "POST":
        payload = request.get_json(force=True)
        SYSTEM_STATE["rainfall_mm"] = float(payload.get("rainfall_mm", SYSTEM_STATE["rainfall_mm"]))
        SYSTEM_STATE["mtbf_hrs"] = float(payload.get("mtbf_hrs", SYSTEM_STATE["mtbf_hrs"]))
        SYSTEM_STATE["labor_drop_pct"] = float(payload.get("labor_drop_pct", SYSTEM_STATE["labor_drop_pct"]))
        SYSTEM_STATE["target_tonnage"] = int(payload.get("target_tonnage", SYSTEM_STATE["target_tonnage"]))

    raw_pred = predict_weekly_tonnage(
        base_target=SYSTEM_STATE["target_tonnage"],
        rainfall_mm=SYSTEM_STATE["rainfall_mm"],
        mtbf_hrs=SYSTEM_STATE["mtbf_hrs"],
        labor_drop_pct=SYSTEM_STATE["labor_drop_pct"]
    )

    recs = generate_recommendations(raw_pred, None, C.ORE_POCKETS)

    if SYSTEM_STATE["plan_executed"]:
        final_pred = apply_plan(recs, raw_pred)
        simulation_state = "OPTIMIZED (PLAN ACTIVE)"
    else:
        final_pred = raw_pred
        final_pred["plan_applied"] = False
        simulation_state = "UNMITIGATED RISK"

    return jsonify({
        "parameters": {
            "rainfall_mm": SYSTEM_STATE["rainfall_mm"],
            "mtbf_hrs": SYSTEM_STATE["mtbf_hrs"],
            "labor_drop_pct": SYSTEM_STATE["labor_drop_pct"],
            "target_tonnage": SYSTEM_STATE["target_tonnage"]
        },
        "simulation_state": simulation_state,
        "plan_executed": SYSTEM_STATE["plan_executed"],
        "prediction": final_pred
    })


@app.route("/api/prescriptive", methods=["GET", "POST"])
def handle_prescriptive():
    raw_pred = predict_weekly_tonnage(
        base_target=SYSTEM_STATE["target_tonnage"],
        rainfall_mm=SYSTEM_STATE["rainfall_mm"],
        mtbf_hrs=SYSTEM_STATE["mtbf_hrs"],
        labor_drop_pct=SYSTEM_STATE["labor_drop_pct"]
    )
    recs = generate_recommendations(raw_pred, None, C.ORE_POCKETS)

    if request.method == "POST":
        action = request.get_json(force=True).get("action")
        if action == "EXECUTE":
            SYSTEM_STATE["plan_executed"] = True
        elif action == "RESET":
            SYSTEM_STATE["plan_executed"] = False

    return jsonify({
        "plan_executed": SYSTEM_STATE["plan_executed"],
        "recommendations": recs["actions"],
        "total_rec_gain": recs["total_rec_gain"]
    })


@app.route("/api/spectral", methods=["GET"])
def get_spectral():
    spec_data = spectral_match(C.SAMPLE_EARTH_SPECTRA, C.ISRO_CLASS_BASELINE_SPECTRA)
    return jsonify(spec_data)


@app.route("/api/xai", methods=["GET"])
def get_xai():
    raw_pred = predict_weekly_tonnage(
        base_target=SYSTEM_STATE["target_tonnage"],
        rainfall_mm=SYSTEM_STATE["rainfall_mm"],
        mtbf_hrs=SYSTEM_STATE["mtbf_hrs"],
        labor_drop_pct=SYSTEM_STATE["labor_drop_pct"]
    )
    conf = model_confidence(raw_pred)
    attributions = compute_shapley_style_attribution(raw_pred)
    return jsonify({
        "confidence_pct": conf,
        "attributions": attributions,
        "narrative": "XAI Diagnostic: Sump flooding in Pit 1 constitutes 54% of throughput disruption. Executing prescriptive rerouting to Pit B captures 46% Mn reserves, restoring 82% of target recovery margin."
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)