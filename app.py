import os
import time
import datetime
import math
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
    from modules.prediction import (
        predict_weekly_tonnage,
        predict_shortfall_with_model,
        derive_banner_state,
    )
except ImportError:
    predict_shortfall_with_model = None

    def derive_banner_state(predicted_tonnage, target_tonnage, recovered_tonnage=0.0,
                            plan_executed=False, mitigation_counts=0):
        predicted = max(0.0, float(predicted_tonnage))
        target = max(0.0, float(target_tonnage))
        predicted_final = predicted + max(0.0, float(recovered_tonnage))
        ratio = (predicted_final / target) if target > 0 else 0.0
        if predicted_final >= target:
            tier, banner_class = "target_exceeded", "ok"
            headline = "TARGET EXCEEDED — SURPLUS PROJECTED"
            sim_state = "OPTIMAL — TARGET SECURED"
        elif ratio >= 0.90:
            tier, banner_class = "on_track", "warn"
            headline = "ON TRACK — MINOR VARIANCE"
            sim_state = "UNMITIGATED RISK"
        else:
            tier, banner_class = "shortfall", "danger"
            headline = "SHORTFALL ALERT"
            sim_state = "UNMITIGATED RISK"
        gap_tonnes = predicted_final - target
        if gap_tonnes < 0:
            ledger_amount_inr, ledger_label, ledger_class = (
                (-gap_tonnes) * C.MN_RATE_PER_TON_INR, "Rupee Loss Ledger", "text-red")
        else:
            ledger_amount_inr, ledger_label, ledger_class = (
                gap_tonnes * C.MN_RATE_PER_TON_INR, "Rupee Gain Ledger", "text-green")
        return {
            "tier": tier, "banner_class": banner_class, "headline": headline,
            "simulation_state": sim_state, "ratio_pct": round(ratio * 100.0, 1),
            "gap_tonnes": round(gap_tonnes, 2),
            "remaining_shortfall_tonnes": round(max(0.0, -gap_tonnes), 2),
            "ledger_label": ledger_label,
            "ledger_amount_inr": round(ledger_amount_inr, 2),
            "ledger_crores": round(ledger_amount_inr / 1e7, 4),
            "ledger_class": ledger_class,
            "plan_executed": bool(plan_executed),
            "recovery_tonnes_applied": round(float(recovered_tonnage), 2),
        }

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

# The spectral module owns both the AOI-level result and the new per-zone
# scoring pipeline (used by the /api/zones endpoints).
try:
    from modules import spectral as spectral_module
    from modules import fusion as fusion_module
    from modules.spectral import get_zone_reflectance
    from modules.spectral import MINERAL_REFERENCES
    ZONE_ENGINE_AVAILABLE = True
except Exception:  # noqa: BLE001 - zone endpoints 503 instead of crashing the app
    spectral_module = None
    fusion_module = None
    get_zone_reflectance = None
    MINERAL_REFERENCES = []
    ZONE_ENGINE_AVAILABLE = False

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
    "ore_grade": "STD",
    "selected_site": "Balaghat Sector 4",
    "mine_name": None,
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
    recovered = 0.0
    if plan_result is not None:
        recovered = min(float(plan_result.get("total_expected_recovery_tonnes") or 0.0), shortfall)
        predicted = min(base_target, predicted + recovered)
        shortfall = max(0.0, base_target - predicted)

    # Single derived-state rule (items 4 & 5): banner, sim-state and the rupee
    # ledger all come from ONE live predicted-output comparison.
    state = derive_banner_state(
        predicted,
        base_target,
        recovered_tonnage=recovered,
        plan_executed=plan_applied,
        mitigation_counts=1 if plan_applied else 0,
    )
    rupee_loss = round(shortfall * float(C.MN_COST_PER_TON_INR), 2)
    return {
        "base_target": round(base_target, 2),
        "predicted_tonnage": round(predicted, 2),
        "shortfall_tonnage": round(shortfall, 2),
        "predicted_output": round(predicted, 2),
        "shortfall_tons": round(shortfall, 2),
        "penalties": raw_pred.get("penalties") or {},
        "factors": raw_pred.get("factors") or {},
        "model_used": raw_pred.get("model_used"),
        "ore_grade": raw_pred.get("ore_grade", SYSTEM_STATE.get("ore_grade", "STD")),
        "fleet_capacity_baseline": raw_pred.get("fleet_capacity_baseline"),
        "honesty_notes": raw_pred.get("honesty_notes") or [],
        "rupee_loss_inr": rupee_loss,
        "loss_crores": round(rupee_loss / 1e7, 2),
        "banner": state,
        "simulation_state": state["simulation_state"],
        "headline": state["headline"],
        "banner_class": state["banner_class"],
        "ledger_label": state["ledger_label"],
        "ledger_amount_inr": state["ledger_amount_inr"],
        "ledger_crores": state["ledger_crores"],
        "ledger_class": state["ledger_class"],
        "plan_applied": plan_applied,
    }


def _current_params():
    return {
        "rainfall_mm": SYSTEM_STATE["rainfall_mm"],
        "mtbf_hrs": SYSTEM_STATE["mtbf_hrs"],
        "labor_drop_pct": SYSTEM_STATE["labor_drop_pct"],
        "target_tonnage": SYSTEM_STATE["target_tonnage"],
        "ore_grade": SYSTEM_STATE.get("ore_grade", "STD"),
        "fleet_capacity_baseline": float(getattr(C, "FLEET_CAPACITY_BASELINE_TONS", 14500.0)),
    }


def _make_prediction():
    """Predict shortfall using the multiplicative factor model (trained-model
    weights when available; deterministic constants otherwise)."""
    if predict_shortfall_with_model is not None:
        return predict_shortfall_with_model(
            base_target=SYSTEM_STATE["target_tonnage"],
            rainfall_mm=SYSTEM_STATE["rainfall_mm"],
            mtbf_hrs=SYSTEM_STATE["mtbf_hrs"],
            labor_drop_pct=SYSTEM_STATE["labor_drop_pct"],
            mine_name=SYSTEM_STATE.get("mine_name") or None,
            ore_grade=SYSTEM_STATE.get("ore_grade", "STD"),
            fleet_capacity_baseline=float(getattr(C, "FLEET_CAPACITY_BASELINE_TONS", 14500.0)),
        )
    return predict_weekly_tonnage(
        base_target=SYSTEM_STATE["target_tonnage"],
        rainfall_mm=SYSTEM_STATE["rainfall_mm"],
        mtbf_hrs=SYSTEM_STATE["mtbf_hrs"],
        labor_drop_pct=SYSTEM_STATE["labor_drop_pct"]
    )


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
    """Derive the simulation-state label from the CURRENT live predicted output
    vs target (item 4). Falls back to a plan-executed marker when no prediction
    state is available."""
    raw = _make_prediction()
    predicted = float(raw.get("predicted_tonnage") or raw.get("predicted_output") or 0.0)
    state = derive_banner_state(
        predicted,
        SYSTEM_STATE["target_tonnage"],
        plan_executed=SYSTEM_STATE["plan_executed"],
        mitigation_counts=1 if SYSTEM_STATE["plan_executed"] else 0,
    )
    return state["simulation_state"]


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
    # Single derived-state rule for the prescriptive panel too (item 4/5).
    state = derive_banner_state(
        float(raw_pred.get("predicted_tonnage") or raw_pred.get("predicted_output") or 0.0),
        SYSTEM_STATE["target_tonnage"],
        recovered_tonnage=recovered,
        plan_executed=SYSTEM_STATE["plan_executed"],
        mitigation_counts=1 if SYSTEM_STATE["plan_executed"] else 0,
    )
    response = {
        "status": "success",
        "plan_executed": SYSTEM_STATE["plan_executed"],
        "simulation_state": state["simulation_state"],
        "parameters": _current_params(),
        "recommendation": shaped,
        "shortfall_tonnage": shortfall,
        "recovered_tonnage": recovered,
        "remaining_shortfall": remaining,
        "total_rec_gain": total_rec_gain,
        "banner": state,
        "headline": state["headline"],
        "banner_class": state["banner_class"],
        "ledger_label": state["ledger_label"],
        "ledger_amount_inr": state["ledger_amount_inr"],
        "ledger_crores": state["ledger_crores"],
        "ledger_class": state["ledger_class"],
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


def _telemetry_markers_from_zones():
    """Build the legacy 'ore_pockets' payload shape from the new zone list.

    Preserves the existing frontend contract (id/name/lat/lon/status/
    water_depth_m/pumps_active) without inventing '46% Mn' grade labels.
    Coordinates + operational status are labelled SYNTHETIC_DEMO_ZONE_DATA
    so no viewer can mistake them for satellite-detected ore locations.
    """
    zones = getattr(C, "CANDIDATE_ZONES", [])
    synthetic_tag = getattr(C, "SYNTHETIC_ZONE_TAG", "SYNTHETIC_DEMO_ZONE_DATA")
    return [
        {
            "id": zone["zone_id"],
            "name": zone["name"],
            "status": zone.get("operational_status", "Unknown"),
            "lat": zone["latitude"],
            "lon": zone["longitude"],
            "water_depth_m": zone.get("water_depth_m", 0.0),
            "pumps_active": zone.get("pumps_active", 0),
            "data_provenance": zone.get("spatial_provenance", synthetic_tag),
        }
        for zone in zones
    ]


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
        # Legacy contract preserved; underlying data now comes from
        # constants.CANDIDATE_ZONES so telemetry and the new spectral fusion
        # panel refer to the same zones.
        "ore_pockets": _telemetry_markers_from_zones(),
        "data_provenance_note": (
            "Operational markers derive from SYNTHETIC_DEMO_ZONE_DATA. "
            "Coordinates are inside the real 76.409-ha Bharveli-Awalajhari AOI "
            "but are not claimed as satellite-detected ore locations."
        ),
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
            rainfall = float(payload.get("rainfall_mm", SYSTEM_STATE["rainfall_mm"]))
            mtbf = float(payload.get("mtbf_hrs", SYSTEM_STATE["mtbf_hrs"]))
            labor = float(payload.get("labor_drop_pct", SYSTEM_STATE["labor_drop_pct"]))
            target = float(payload.get("target_tonnage", SYSTEM_STATE["target_tonnage"]))
            if not all(math.isfinite(value) for value in (rainfall, mtbf, labor, target)):
                raise ValueError("non-finite values are not allowed")
            if rainfall < 0:
                raise ValueError("rainfall must be non-negative")
            if mtbf < 5:
                mtbf = 5.0  # item 7: MTBF floored at 5 hrs
            if labor < 0 or labor > 50:
                labor = max(0.0, min(50.0, labor))  # item 7: labor deficit capped at 50%
            if target <= 0:
                raise ValueError("target must be positive")
            ore_grade = str(payload.get("ore_grade", SYSTEM_STATE.get("ore_grade", "STD"))).upper()
            if ore_grade not in getattr(C, "ORE_GRADE_FACTORS", {"STD": 1.0}):
                raise ValueError(f"unknown ore grade '{ore_grade}'")
            SYSTEM_STATE["rainfall_mm"] = rainfall
            SYSTEM_STATE["mtbf_hrs"] = mtbf
            SYSTEM_STATE["labor_drop_pct"] = labor
            SYSTEM_STATE["target_tonnage"] = target
            SYSTEM_STATE["ore_grade"] = ore_grade
            SYSTEM_STATE["mine_name"] = payload.get("mine_name", SYSTEM_STATE.get("mine_name")) or None
        except (TypeError, ValueError):
            return jsonify({
                "status": "error",
                "message": "Invalid parameter value. Rainfall, MTBF (hrs), labor (%) and target must be numeric.",
            }), 400

    raw_pred = _make_prediction()

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

    view = _prediction_view(raw_pred, SYSTEM_STATE["target_tonnage"], plan_result)
    return jsonify({
        "status": "success",
        "parameters": _current_params(),
        "simulation_state": view["simulation_state"],
        "plan_executed": SYSTEM_STATE["plan_executed"],
        "prediction": view,
        "plan": _shape_execution(plan_result)
    })


@app.route("/api/prescriptive", methods=["GET", "POST"])
def handle_prescriptive():
    raw_pred = _make_prediction()
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
    """AOI-level spectral screening result (combined contract).

    This is the ONE mine-wide 97.84% Pyrolusite similarity figure derived
    from the supplied Sentinel-2C L2A scene mean vs the USGS Pyrolusite
    reference. It is NOT the score for any individual pit or candidate
    zone. Per-zone scores live at /api/zones.

    The payload serves both the legacy frontend contract (similarity_pct /
    scene.platform / scene.tile / aoi_area_ha / scope_note) and the newer
    frontend contract (similarity as a fraction, label, scene.satellite_sensor,
    spectral_potential, interpretation, wavelengths_um, live_reflectance,
    reference_reflectance).
    """
    try:
        aoi = build_bharveli_aoi_result()
    except Exception as exc:  # noqa: BLE001
        return jsonify({"status": "error", "message": f"Spectral module unavailable: {type(exc).__name__}"}), 503

    similarity = float(aoi.get("similarity", 0.9784))
    scene = dict(aoi.get("scene") or {})
    scene.setdefault("satellite_sensor", scene.get("platform") or "Sentinel-2C L2A")

    return jsonify({
        "status": aoi.get("status", "computed"),
        "level": "AOI",
        "label": aoi.get("label", "Pyrolusite Spectral Similarity"),
        "similarity": similarity,
        "similarity_pct": round(similarity * 100.0, 2),
        "spectral_potential": aoi.get("spectral_potential"),
        "aoi_name": aoi.get("aoi_name"),
        "aoi_area_ha": aoi.get("aoi_area_ha"),
        "interpretation": aoi.get("interpretation"),
        "scene": scene,
        "wavelengths_um": aoi.get("wavelengths_um") or {},
        "live_reflectance": aoi.get("live_reflectance") or {},
        "reference_reflectance": aoi.get("reference_reflectance") or {},
        "overlap_points": aoi.get("overlap_points"),
        "reference_source": "USGS Digital Spectral Library (splib05a) - Pyrolusite",
        "compliance_note": "Prototype threshold - requires field/lab validation",
        "scope_note": (
            "AOI-level screening result over the 76.409-ha Bharveli-Awalajhari AOI. "
            "Do not interpret as a per-pit or per-zone spectral score. "
            "See /api/zones for per-zone spatial + spectral fusion."
        ),
        "context_note": (
            "Chandrayaan-2 CLASS demonstrated space-based Mn mapping principles "
            "used here as inspiration; Earth imagery in this pipeline is from "
            "Sentinel-2C L2A, not Chandrayaan-2."
        ),
    })


# ---------------------------------------------------------
# ZONE FUSION ENDPOINTS (spatial + spectral -> exploration priority)
# ---------------------------------------------------------

def _spectral_similarity_scorer(zone_reflectance, reference_reflectance):
    """Thin adapter so fusion.evaluate_zone stays decoupled from the
    spectral module's internal API shape."""
    return spectral_module.spectral_match(zone_reflectance, reference_reflectance)["similarity"]


def _evaluate_zone(zone_config):
    """Compute the full per-zone spatial + spectral fusion result."""
    reflectance, spectral_provenance = get_zone_reflectance(
        zone_config.get("linked_geology_record_id"),
    )
    evaluation = fusion_module.evaluate_zone(
        zone_id=zone_config["zone_id"],
        latitude=zone_config["latitude"],
        longitude=zone_config["longitude"],
        spatial_score=zone_config["spatial_score"],
        zone_reflectance=reflectance,
        mineral_references=MINERAL_REFERENCES,
        spectral_scorer=_spectral_similarity_scorer,
        weights=C.FUSION_WEIGHTS,
        spatial_provenance=zone_config.get("spatial_provenance", C.SYNTHETIC_ZONE_TAG),
        spectral_provenance=spectral_provenance,
    )
    payload = evaluation.to_dict()
    payload.update({
        "name": zone_config["name"],
        "zone_type": zone_config.get("zone_type"),
        "operational_status": zone_config.get("operational_status"),
        "zone_reflectance": reflectance,
        "zone_reflectance_provenance": spectral_provenance,
        "spectral_scene": spectral_module.SCENE_METADATA.copy(),
        "spatial_priority_band": fusion_module.priority_band(
            zone_config["spatial_score"]
        ),
        "fusion_rule": C.FUSION_PROTOTYPE_LABEL,
    })
    return payload


@app.route("/api/zones", methods=["GET"])
def list_zones():
    """Return every candidate zone with its per-zone fusion result."""
    if not ZONE_ENGINE_AVAILABLE:
        return jsonify({"error": "Zone fusion engine unavailable."}), 503
    zones = [_evaluate_zone(zone) for zone in C.CANDIDATE_ZONES]
    return jsonify({
        "zones": zones,
        "count": len(zones),
        "fusion_rule": C.FUSION_PROTOTYPE_LABEL,
        "weights": C.FUSION_WEIGHTS,
        "aoi_name": "MOIL Bharveli-Awalajhari Mine AOI",
        "data_provenance_note": (
            "Zone coordinates and spatial scores are SYNTHETIC_DEMO_ZONE_DATA "
            "used to demonstrate the end-to-end pipeline. Zone-level spectral "
            "vectors are drawn from processed_geology.csv (SYNTHETIC_SCHEMA_FAITHFUL). "
            "The architecture accepts real Sentinel-2 per-pixel extraction "
            "without frontend contract changes."
        ),
    })


@app.route("/api/aoi_boundary", methods=["GET"])
def get_aoi_boundary():
    """Return the real 76.409-ha MOIL AOI boundary as GeoJSON features."""
    if not ZONE_ENGINE_AVAILABLE:
        return jsonify({"error": "AOI boundary engine unavailable.", "features": []}), 503
    try:
        features = spectral_module.load_aoi_kml()
    except (FileNotFoundError, ValueError) as exc:
        return jsonify({"error": str(exc), "features": []}), 500
    return jsonify({
        "type": "FeatureCollection",
        "features": features,
        "aoi_name": "MOIL Bharveli-Awalajhari Mine AOI",
        "area_ha": 76.409,
        "source": "Supplied 76.409-ha KML boundary",
    })


@app.route("/api/zones/<zone_id>", methods=["GET"])
def get_zone(zone_id):
    """Return the full per-zone fusion result for a single zone (map click)."""
    if not ZONE_ENGINE_AVAILABLE:
        return jsonify({"error": "Zone fusion engine unavailable."}), 503
    for zone in C.CANDIDATE_ZONES:
        if zone["zone_id"] == zone_id:
            payload = _evaluate_zone(zone)
            # Add references used, for transparency on the detail panel.
            payload["mineral_references_used"] = [
                {
                    "mineral_id": m.mineral_id,
                    "display_name": m.display_name,
                    "provenance": m.provenance,
                }
                for m in MINERAL_REFERENCES
            ]
            return jsonify(payload)
    return jsonify({"error": f"Unknown zone_id: {zone_id}"}), 404


@app.route("/api/xai", methods=["GET"])
def get_xai():
    spectral_sim = 0.9784
    try:
        spectral_sim = float(build_bharveli_aoi_result().get("similarity") or spectral_sim)
    except Exception:  # noqa: BLE001
        pass

    raw_pred = _make_prediction()
    penalties = dict(raw_pred.get("penalties") or {})
    # Harmonize the ML model's penalty key ("equipment") with the key the XAI
    # engine expects ("mtbf") so attributions stay meaningful on both paths.
    if "equipment" in penalties and "mtbf" not in penalties:
        penalties["mtbf"] = penalties.pop("equipment")

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
        "confidence_pct": None,
        "confidence_status": "NOT_AVAILABLE",
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
