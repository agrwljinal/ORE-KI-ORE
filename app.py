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
    from modules.prescriptive import (
        generate_recommendations,
        calculate_selected_plan,
        ACTION_DISPLAY_LABELS,
    )
except ImportError:
    ACTION_DISPLAY_LABELS = {
        "REROUTE_FLEET": "Move dumpers to an alternate pit",
        "DEWATERING": "Pump water from the affected pit",
        "PREVENTIVE_MAINTENANCE": "Inspect and service equipment",
        "CONTINGENCY_LABOR": "Arrange additional workers",
        "BLENDING": "Blend ore with available stockpile",
    }

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

    def calculate_selected_plan(plan_recommendation, prediction, selected_actions=None,
                                risk=None, ore_pockets=None):
        rec = plan_recommendation or {}
        actions = rec.get("actions") or []
        shortfall = max(0.0, float((prediction or {}).get("shortfall_tons", 0.0) or 0.0))
        if not actions:
            total = 0.0
        elif not selected_actions:
            total = float(rec.get("total_rec_gain", 0.0) or 0.0)
        else:
            wanted = {str(s).lower() for s in selected_actions}
            total = sum(
                float(a.get("delta_recovery", 0.0) or 0.0)
                for a in actions
                if str(a.get("id")) in {s for s in selected_actions}
                or str(a.get("title", "")).lower() in wanted
            )
        total = min(total, shortfall)
        remaining = max(0.0, shortfall - total)
        rate = float(getattr(C, "MN_RATE_PER_TON_INR", 0.0))
        top = max(actions, key=lambda a: a.get("delta_recovery", 0)) if actions else None
        sequence = []
        if actions:
            sequence.append({
                "step": 1,
                "action": (top or {}).get("title") or "OPTIMIZATION_PLAN",
                "expected_recovery_tonnes": round(total, 2),
                "remaining_shortfall": round(remaining, 2),
                "reason": "Best available corrective action (demo fallback mode).",
                "action_cost": 0.0,
                "used_ml_model": False,
            })
        return {
            "plan_type": "operator_selected",
            "recommended_action": (top or {}).get("title"),
            "selected_actions": list(selected_actions or []),
            "action_sequence": sequence,
            "total_expected_recovery_tonnes": round(total, 2),
            "final_remaining_shortfall": round(remaining, 2),
            "total_action_cost": 0.0,
            "revenue_saved": round(total * rate, 2),
            "net_benefit": round(total * rate, 2),
            "recoverable_tonnage": round(total, 2),
            "expected_recovery_tonnes": round(total, 2),
            "remaining_shortfall": round(remaining, 2),
            "shortfall_tonnage": shortfall,
            "is_simulated": True,
            "assumptions": ["Falling back to demo action data (prescriptive module unavailable)."],
        }

try:
    from modules.spectral import build_bharveli_aoi_result
except ImportError:
    def build_bharveli_aoi_result():
        return {
            "similarity": 0.9784,
            "status": "computed",
            "label": "Pyrolusite Spectral Similarity",
            "aoi_name": "Bharveli-Awalajhari",
            "spectral_potential": "HIGH",
            "interpretation": "Pyrolusite (MnO2) spectral match. Prototype threshold — requires field/lab validation.",
            "scene": {"satellite_sensor": "Sentinel-2C L2A", "date": "2026-01-07"},
            "wavelengths_um": {"B04": 0.665, "B08": 0.842, "B11": 1.61, "B12": 2.19},
            "live_reflectance": {"B04": 0.21, "B08": 0.34, "B11": 0.34, "B12": 0.27},
            "reference_reflectance": {"B04": 0.06, "B08": 0.06, "B11": 0.09, "B12": 0.08},
        }

try:
    from modules.xai import model_confidence, compute_shapley_style_attribution
except ImportError:
    def model_confidence(rainfall_mm=0.0, mtbf_hrs=150.0, labor_drop_pct=0.0, spectral_similarity=0.9784):
        return 0.942

    def compute_shapley_style_attribution(penalties_dict=None, spectral_similarity=0.9784):
        return {
            "Rainfall Impact": 54.0,
            "Equipment MTBF Failure": 36.0,
            "Labor Drop": 8.0,
            "Spectral Variance": 2.0,
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
    "selected_actions": None,
    "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat()
}


# ---------------------------------------------------------
# API RESPONSE SHAPING (real module output -> frontend JSON)
# ---------------------------------------------------------
def _prediction_view(raw_pred, base_target, plan_result=None):
    base_target = float(base_target)
    predicted = float(raw_pred.get("predicted_tonnage") or raw_pred.get("predicted_output") or 0.0)
    shortfall = float(raw_pred.get("shortfall_tonnage") or raw_pred.get("shortfall_tons") or 0.0)
    plan_applied = plan_result is not None
    if plan_result is not None:
        recovery = min(float(plan_result.get("total_expected_recovery_tonnes") or 0.0), shortfall)
        predicted = min(base_target, predicted + recovery)
        shortfall = max(0.0, base_target - predicted)
    rupee_loss = round(shortfall * float(C.MN_RATE_PER_TON_INR), 2)
    return {
        "base_target": round(base_target, 2),
        "predicted_tonnage": round(predicted, 2),
        "shortfall_tonnage": round(shortfall, 2),
        "predicted_output": round(predicted, 2),
        "shortfall_tons": round(shortfall, 2),
        "penalties": raw_pred.get("penalties") or {},
        "rupee_loss_inr": rupee_loss,
        "loss_crores": round(rupee_loss / 1e7, 2),
        "plan_applied": plan_applied,
    }


def _current_params():
    return {
        "rainfall_mm": SYSTEM_STATE["rainfall_mm"],
        "mtbf_hrs": SYSTEM_STATE["mtbf_hrs"],
        "labor_drop_pct": SYSTEM_STATE["labor_drop_pct"],
        "target_tonnage": SYSTEM_STATE["target_tonnage"],
    }


def _shape_recommendation(recs):
    recs = recs or {}
    options = []
    if "actions" in recs:
        for idx, a in enumerate(recs["actions"] or []):
            options.append({
                "action": str(a.get("title") or a.get("id") or f"Action {idx + 1}"),
                "action_code": None,
                "action_label": str(a.get("title") or "Corrective action"),
                "expected_recovery_tonnes": float(a.get("delta_recovery") or 0.0),
                "reason": str(a.get("desc") or ""),
                "scenario_reason": str(a.get("desc") or ""),
                "recommended": idx == 0,
                "resource_allocation_pct": None,
                "estimated_action_duration_hours": None,
                "action_intensity": None,
                "meets_shortfall": None,
                "used_ml_model": False,
                "action_cost": 0.0,
            })
        return {
            "recommended_action": options[0]["action"] if options else None,
            "action_label": options[0]["action_label"] if options else None,
            "reason": options[0]["reason"] if options else "",
            "expected_recovery_tonnes": float(recs.get("total_rec_gain") or 0.0),
            "recoverable_tonnage": float(recs.get("total_rec_gain") or 0.0),
            "remaining_shortfall_tonnes": None,
            "source_pit": None,
            "source_grade_pct": None,
            "target_pit": None,
            "target_grade_pct": None,
            "resource_allocation_pct": None,
            "estimated_action_duration_hours": None,
            "action_intensity": None,
            "recovery_status": "PENDING_EXECUTION",
            "background_dewatering": None,
            "used_ml_model": False,
            "model_type": "unknown",
            "options": options,
            "assumptions": ["Falling back to demo recommendation data (prescriptive module unavailable)."],
        }
    action_code = recs.get("recommended_action")
    for o in recs.get("options") or []:
        code = o.get("action")
        options.append({
            "action": code,
            "action_code": code,
            "action_label": ACTION_DISPLAY_LABELS.get(code, code),
            "expected_recovery_tonnes": float(o.get("expected_recovery_tonnes") or 0.0),
            "reason": o.get("reason"),
            "scenario_reason": o.get("scenario_reason") or o.get("reason"),
            "recommended": bool(o.get("recommended")),
            "resource_allocation_pct": o.get("resource_allocation_pct"),
            "estimated_action_duration_hours": o.get("estimated_action_duration_hours"),
            "action_intensity": o.get("action_intensity"),
            "meets_shortfall": o.get("meets_shortfall"),
            "used_ml_model": bool(o.get("used_ml_model")),
            "action_cost": o.get("action_cost"),
        })
    mi = recs.get("model_info") or {}
    return {
        "recommended_action": action_code,
        "action_label": ACTION_DISPLAY_LABELS.get(action_code, action_code),
        "reason": recs.get("reason"),
        "expected_recovery_tonnes": recs.get("expected_recovery_tonnes"),
        "recoverable_tonnage": recs.get("recoverable_tonnage"),
        "remaining_shortfall_tonnes": recs.get("remaining_shortfall_tonnes"),
        "source_pit": recs.get("source_pit"),
        "source_grade_pct": recs.get("source_grade_pct"),
        "target_pit": recs.get("target_pit"),
        "target_grade_pct": recs.get("target_grade_pct"),
        "resource_allocation_pct": recs.get("resource_allocation_pct"),
        "estimated_action_duration_hours": recs.get("estimated_action_duration_hours"),
        "action_intensity": recs.get("action_intensity"),
        "recovery_status": recs.get("recovery_status"),
        "background_dewatering": recs.get("background_dewatering"),
        "used_ml_model": bool(mi.get("used_ml_model")),
        "model_type": mi.get("model_type"),
        "trained_on_synthetic_data": mi.get("trained_on_synthetic_data"),
        "options": options,
        "assumptions": recs.get("assumptions") or [],
    }


def _shape_execution(plan_result, shortfall_tonnes=None):
    if not plan_result:
        return None
    action_sequence = []
    for step in plan_result.get("action_sequence") or []:
        s = dict(step)
        s["action_label"] = ACTION_DISPLAY_LABELS.get(s.get("action"), s.get("action"))
        action_sequence.append(s)
    if shortfall_tonnes is None:
        shortfall_tonnes = float(plan_result.get("shortfall_tonnage") or 0.0)
    shortfall_tonnes = round(shortfall_tonnes, 2)
    total = float(plan_result.get("total_expected_recovery_tonnes") or 0.0)
    recovered = round(min(total, shortfall_tonnes), 2)
    return {
        "selected_actions": plan_result.get("selected_actions") or [],
        "action_sequence": action_sequence,
        "per_action_recovery": action_sequence,
        "total_expected_recovery_tonnes": plan_result.get("total_expected_recovery_tonnes"),
        "final_remaining_shortfall": round(max(0.0, shortfall_tonnes - recovered), 2),
        "recovered_tonnage": recovered,
        "remaining_shortfall": round(max(0.0, shortfall_tonnes - recovered), 2),
        "shortfall_tonnage": shortfall_tonnes,
        "total_action_cost": plan_result.get("total_action_cost"),
        "revenue_saved": plan_result.get("revenue_saved"),
        "net_benefit": plan_result.get("net_benefit"),
        "is_simulated": plan_result.get("is_simulated"),
    }


def _simulation_state():
    return "OPTIMIZED (PLAN ACTIVE)" if SYSTEM_STATE["plan_executed"] else "UNMITIGATED RISK"


def _build_prescriptive_response(raw_pred, recs, plan_result=None):
    shaped = _shape_recommendation(recs)
    shortfall = round(float(raw_pred.get("shortfall_tonnage") or raw_pred.get("shortfall_tons") or 0.0), 2)
    execution = _shape_execution(plan_result, shortfall) if plan_result is not None else None
    recovered = 0.0
    remaining = shortfall
    total_rec_gain = shaped.get("recoverable_tonnage") or shaped.get("expected_recovery_tonnes") or 0.0
    if execution:
        recovered = round(min(float(execution.get("total_expected_recovery_tonnes") or 0.0), shortfall), 2)
        remaining = round(max(0.0, shortfall - recovered), 2)
        total_rec_gain = recovered
    response = {
        "status": "success",
        "plan_executed": SYSTEM_STATE["plan_executed"],
        "simulation_state": _simulation_state(),
        "parameters": _current_params(),
        "recommendation": shaped,
        "shortfall_tonnage": shortfall,
        "recovered_tonnage": recovered,
        "remaining_shortfall": remaining,
        "total_rec_gain": total_rec_gain,
    }
    if execution:
        response["execution"] = execution
    return response


# ---------------------------------------------------------
# HTTP ROUTING & API ENDPOINTS
# ---------------------------------------------------------
@app.after_request
def add_cors_headers(response):
    origin = os.environ.get("MOIL_CORS_ORIGIN")
    if origin:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


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
        "center": list(C.DEFAULT_MAP_CENTER),
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
        payload = request.get_json(silent=True)
        if request.get_data(as_text=True).strip() and payload is None:
            return jsonify({"status": "error", "message": "Malformed JSON body."}), 400
        payload = payload or {}
        try:
            if not isinstance(payload, dict):
                return jsonify({"status": "error", "message": "Invalid JSON body: an object is required."}), 400
            SYSTEM_STATE["rainfall_mm"] = float(payload.get("rainfall_mm", SYSTEM_STATE["rainfall_mm"]))
            SYSTEM_STATE["mtbf_hrs"] = float(payload.get("mtbf_hrs", SYSTEM_STATE["mtbf_hrs"]))
            SYSTEM_STATE["labor_drop_pct"] = float(payload.get("labor_drop_pct", SYSTEM_STATE["labor_drop_pct"]))
            SYSTEM_STATE["target_tonnage"] = int(payload.get("target_tonnage", SYSTEM_STATE["target_tonnage"]))
        except (TypeError, ValueError):
            return jsonify({
                "status": "error",
                "message": "Invalid parameter value. Rainfall, MTBF (hrs), labor (%) and target must be numeric.",
            }), 400

    raw_pred = predict_weekly_tonnage(
        base_target=SYSTEM_STATE["target_tonnage"],
        rainfall_mm=SYSTEM_STATE["rainfall_mm"],
        mtbf_hrs=SYSTEM_STATE["mtbf_hrs"],
        labor_drop_pct=SYSTEM_STATE["labor_drop_pct"]
    )

    plan_result = None
    if SYSTEM_STATE["plan_executed"]:
        recs = generate_recommendations(raw_pred, None, C.ORE_POCKETS)
        selected = SYSTEM_STATE.get("selected_actions")
        if not selected:
            top_action = recs.get("recommended_action")
            selected = [top_action] if top_action else None
        if selected:
            try:
                plan_result = calculate_selected_plan(recs, raw_pred, selected)
            except Exception:  # noqa: BLE001 - plan engine must never take the API down
                plan_result = None

    return jsonify({
        "status": "success",
        "parameters": _current_params(),
        "simulation_state": _simulation_state(),
        "plan_executed": SYSTEM_STATE["plan_executed"],
        "prediction": _prediction_view(raw_pred, SYSTEM_STATE["target_tonnage"], plan_result),
        "plan": _shape_execution(plan_result)
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
        body = request.get_json(silent=True)
        if request.get_data(as_text=True).strip() and body is None:
            return jsonify({"status": "error", "message": "Malformed JSON body."}), 400
        body = body or {}
        try:
            if not isinstance(body, dict):
                return jsonify({"status": "error", "message": "Invalid JSON body: an object is required."}), 400
            action = str(body.get("action") or "").upper()
        except AttributeError:
            return jsonify({"status": "error", "message": "Invalid JSON body: an object is required."}), 400

        if action == "EXECUTE":
            selected_raw = body.get("selected_actions")
            if selected_raw is not None and not isinstance(selected_raw, (list, tuple)):
                return jsonify({
                    "status": "error",
                    "message": "Invalid 'selected_actions': expected a list of action codes.",
                }), 400
            valid_codes = set(ACTION_DISPLAY_LABELS)
            if selected_raw:
                unknown = [str(s) for s in selected_raw if str(s).strip().upper() not in valid_codes]
                if unknown:
                    return jsonify({
                        "status": "error",
                        "message": f"Unknown action code(s): {', '.join(unknown)}. "
                                   f"Supported: {', '.join(sorted(valid_codes))}.",
                    }), 400
                SYSTEM_STATE["selected_actions"] = [str(s).strip().upper() for s in selected_raw]
            else:
                SYSTEM_STATE["selected_actions"] = None
            SYSTEM_STATE["plan_executed"] = True
            selected = SYSTEM_STATE["selected_actions"]
            if not selected:
                top_action = recs.get("recommended_action")
                selected = [top_action] if top_action else None
            plan_result = calculate_selected_plan(recs, raw_pred, selected)
            payload = _build_prescriptive_response(raw_pred, recs, plan_result)
        elif action == "RESET":
            SYSTEM_STATE["plan_executed"] = False
            SYSTEM_STATE["selected_actions"] = None
            payload = _build_prescriptive_response(raw_pred, recs)
        else:
            return jsonify({
                "status": "error",
                "message": f"Unknown action '{action}'. Supported actions: EXECUTE, RESET.",
            }), 400
    else:
        plan_result = None
        if SYSTEM_STATE["plan_executed"]:
            selected = SYSTEM_STATE.get("selected_actions")
            if not selected:
                top_action = recs.get("recommended_action")
                selected = [top_action] if top_action else None
            if selected:
                try:
                    plan_result = calculate_selected_plan(recs, raw_pred, selected)
                except Exception:  # noqa: BLE001 - plan engine must never take the API down
                    plan_result = None
        payload = _build_prescriptive_response(raw_pred, recs, plan_result)

    return jsonify(payload)


@app.route("/api/spectral", methods=["GET"])
def get_spectral():
    try:
        return jsonify(build_bharveli_aoi_result())
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "error", "message": f"Spectral module unavailable: {type(exc).__name__}"}), 503


@app.route("/api/xai", methods=["GET"])
def get_xai():
    spectral_sim = 0.9784
    try:
        spectral_sim = float(build_bharveli_aoi_result().get("similarity") or spectral_sim)
    except Exception:  # noqa: BLE001
        pass

    raw_pred = predict_weekly_tonnage(
        base_target=SYSTEM_STATE["target_tonnage"],
        rainfall_mm=SYSTEM_STATE["rainfall_mm"],
        mtbf_hrs=SYSTEM_STATE["mtbf_hrs"],
        labor_drop_pct=SYSTEM_STATE["labor_drop_pct"]
    )
    penalties = raw_pred.get("penalties") or {}

    try:
        conf = float(model_confidence(
            SYSTEM_STATE["rainfall_mm"], SYSTEM_STATE["mtbf_hrs"],
            SYSTEM_STATE["labor_drop_pct"], spectral_sim,
        ))
    except TypeError:
        conf = float(model_confidence(raw_pred))
    try:
        attributions = compute_shapley_style_attribution(penalties, spectral_sim)
    except TypeError:
        attributions = compute_shapley_style_attribution(raw_pred)

    drivers = sorted(attributions.items(), key=lambda kv: str(kv[1]))
    top_label, top_pct = drivers[-1] if drivers else ("Unknown driver", 0.0)
    predicted = float(raw_pred.get("predicted_tonnage") or raw_pred.get("predicted_output") or 0.0)
    shortfall = float(raw_pred.get("shortfall_tonnage") or raw_pred.get("shortfall_tons") or 0.0)

    return jsonify({
        "status": "success",
        "confidence_pct": round(conf * 100.0, 1),
        "attributions": attributions,
        "predicted_tonnage": round(predicted, 2),
        "shortfall_tonnage": round(shortfall, 2),
        "narrative": (
            f"XAI Diagnostic: '{top_label}' accounts for ~{float(top_pct):.0f}% of the current "
            f"{round(shortfall):,} t shortfall against {round(predicted):,} t predicted output. "
            "Review the prescriptive actions on the left and execute the plan to begin recovery."
        ),
    })

# ---------------------------------------------------------
# WEATHER API ENDPOINT
# ---------------------------------------------------------
try:
    from modules.weather import WeatherModule
    weather_module = WeatherModule()
except ImportError:
    weather_module = None

@app.route("/api/weather", methods=["GET", "POST"])
def get_weather():
    city = request.args.get("city", "Delhi")
    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        city = payload.get("city", city)

    if weather_module:
        # Fetch live weather data using your WeatherModule
        result = weather_module.service.fetch_live_weather(city)
        return jsonify(result)
    
    # Mock fallback if modules/weather.py is missing
    return jsonify({
        "success": True,
        "city": city,
        "temp": 28.5,
        "humidity": 65,
        "condition": "Haze (Fallback Mode)",
        "wind_speed": 3.1
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
