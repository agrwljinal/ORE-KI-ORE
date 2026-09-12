import os
import time
import datetime
import math
import csv
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

try:
    from modules.customers import CustomerContractStore, calculate_customer_portfolio
    CUSTOMER_ENGINE_AVAILABLE = True
except ImportError:
    CustomerContractStore = None
    calculate_customer_portfolio = None
    CUSTOMER_ENGINE_AVAILABLE = False


# ---------------------------------------------------------
# GLOBAL IN-MEMORY RUNTIME STATE
# ---------------------------------------------------------
SYSTEM_STATE = {
    "plan_executed": False,
    "rainfall_mm": 88.5,
    "soil_moisture_pct": 38.0,
    "equipment_downtime_hours": 6.0,
    "blast_delay_minutes": 45.0,
    "labor_drop_pct": 18.0,
    "target_tonnage": C.BASE_WEEKLY_TARGET_TONS,
    "ore_grade": "STD",
    "selected_site": "Balaghat Sector 4",
    "mine_name": "Balaghat",
    "selected_actions": None,
    "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat()
}

# Customer contracts are intentionally in memory for this demo, just like the
# existing simulation state.  The seed file supplies a transparent, editable
# commercial scenario; a production deployment should use an authenticated
# contract-management system instead.
customer_store = CustomerContractStore() if CUSTOMER_ENGINE_AVAILABLE else None


def _customer_mine_options():
    """Expose the same mine names used by the spatial map's portfolio data."""
    path = os.path.join(os.path.dirname(__file__), "data", "mine_dashboard_summary.csv")
    try:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            return sorted({str(row.get("mine_name") or "").strip() for row in csv.DictReader(handle) if row.get("mine_name")})
    except OSError:
        return ["Balaghat"]


def _customer_portfolio(predicted_tonnage):
    if not customer_store or calculate_customer_portfolio is None:
        return None
    return calculate_customer_portfolio(
        customer_store.list(),
        predicted_daily_tonnage=predicted_tonnage,
        assigned_mine=SYSTEM_STATE.get("mine_name") or "Balaghat",
    )


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
    customer_portfolio = _customer_portfolio(predicted)
    # Customer contracts are now the commercial source of truth.  When a
    # contract portfolio exists, the hero ledger reports forecast delivery
    # liability: short-delivery contract value + the agreed penalty clause.
    if customer_portfolio and customer_portfolio["active_contract_count"]:
        state = dict(state)
        liability = float(customer_portfolio["total_liability_inr"])
        state.update({
            "ledger_label": "Contract Delivery Liability",
            "ledger_amount_inr": round(liability, 2),
            "ledger_crores": round(liability / 1e7, 4),
            "ledger_class": "text-red" if liability > 0 else "text-green",
        })
    rupee_loss = round(
        float(customer_portfolio["total_liability_inr"])
        if customer_portfolio and customer_portfolio["active_contract_count"]
        else shortfall * float(C.MN_COST_PER_TON_INR),
        2,
    )
    return {
        "base_target": round(base_target, 2),
        "predicted_tonnage": round(predicted, 2),
        "shortfall_tonnage": round(shortfall, 2),
        "predicted_output": round(predicted, 2),
        "shortfall_tons": round(shortfall, 2),
        "ml_predicted_output": raw_pred.get("ml_predicted_output"),
        "ml_features": raw_pred.get("ml_features") or {},
        "model_metrics": raw_pred.get("metrics") or {},
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
        "customer_portfolio": customer_portfolio,
        "plan_applied": plan_applied,
    }


def _current_params():
    return {
        "rainfall_mm": SYSTEM_STATE["rainfall_mm"],
        "soil_moisture_pct": SYSTEM_STATE["soil_moisture_pct"],
        "equipment_downtime_hours": SYSTEM_STATE["equipment_downtime_hours"],
        "blast_delay_minutes": SYSTEM_STATE["blast_delay_minutes"],
        "labor_drop_pct": SYSTEM_STATE["labor_drop_pct"],
        "target_tonnage": SYSTEM_STATE["target_tonnage"],
        "ore_grade": SYSTEM_STATE.get("ore_grade", "STD"),
        "mine_name": SYSTEM_STATE.get("mine_name"),
    }


def _prescriptive_risk_inputs():
    """Pass the current operator controls directly to the action engine.

    This avoids deriving action triggers from a prediction model's penalty
    fields, which are a lossy representation of the live scenario.
    """
    return {
        "rainfall_mm": SYSTEM_STATE["rainfall_mm"],
        "equipment_downtime_hours": SYSTEM_STATE["equipment_downtime_hours"],
        "labor_drop_pct": SYSTEM_STATE["labor_drop_pct"],
    }


def _make_prediction():
    """ML direct output, then manual labor/grade factors, then shortfall."""
    if predict_shortfall_with_model is None:
        raise RuntimeError("Direct actual-ROM model is unavailable")
    return predict_shortfall_with_model(
        base_target=SYSTEM_STATE["target_tonnage"],
        rainfall_mm=SYSTEM_STATE["rainfall_mm"],
        soil_moisture_pct=SYSTEM_STATE["soil_moisture_pct"],
        equipment_downtime_hours=SYSTEM_STATE["equipment_downtime_hours"],
        blast_delay_minutes=SYSTEM_STATE["blast_delay_minutes"],
        labor_drop_pct=SYSTEM_STATE["labor_drop_pct"],
        mine_name=SYSTEM_STATE.get("mine_name") or None,
        ore_grade=SYSTEM_STATE.get("ore_grade", "STD"),
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
    base_output = float(raw_pred.get("predicted_tonnage") or raw_pred.get("predicted_output") or 0.0)
    customer_before = _customer_portfolio(base_output)
    customer_after = _customer_portfolio(base_output + recovered)
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
        "customer_portfolio": customer_after,
        "customer_impact": {
            "liability_avoided_inr": round(
                max(0.0, float((customer_before or {}).get("total_liability_inr") or 0.0)
                    - float((customer_after or {}).get("total_liability_inr") or 0.0)),
                2,
            ),
            "additional_expected_revenue_inr": round(
                max(0.0, float((customer_after or {}).get("expected_revenue_inr") or 0.0)
                    - float((customer_before or {}).get("expected_revenue_inr") or 0.0)),
                2,
            ),
        },
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
    # Keep the HTML + our own JS/CSS cache-free so the app always runs the
    # latest build (stale script.js has repeatedly masked deployed changes).
    if request.path.startswith("/static/") or request.path == "/":
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
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
            "status": zone.get("status", zone.get("operational_status", "Unknown")),
            "production_impact": zone.get("production_impact", "LOW"),
            "recommended_action": zone.get("recommended_action", "Review & investigate"),
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
            soil_moisture = float(payload.get("soil_moisture_pct", SYSTEM_STATE["soil_moisture_pct"]))
            downtime = float(payload.get("equipment_downtime_hours", SYSTEM_STATE["equipment_downtime_hours"]))
            blast_delay = float(payload.get("blast_delay_minutes", SYSTEM_STATE["blast_delay_minutes"]))
            labor = float(payload.get("labor_drop_pct", SYSTEM_STATE["labor_drop_pct"]))
            target = float(payload.get("target_tonnage", SYSTEM_STATE["target_tonnage"]))
            if not all(math.isfinite(value) for value in (rainfall, soil_moisture, downtime, blast_delay, labor, target)):
                raise ValueError("non-finite values are not allowed")
            if rainfall < 0 or soil_moisture < 0 or downtime < 0 or blast_delay < 0:
                raise ValueError("ML input values must be non-negative")
            if labor < 0 or labor > 50:
                labor = max(0.0, min(50.0, labor))  # item 7: labor deficit capped at 50%
            if target <= 0:
                raise ValueError("target must be positive")
            ore_grade = str(payload.get("ore_grade", SYSTEM_STATE.get("ore_grade", "STD"))).upper()
            if ore_grade not in getattr(C, "ORE_GRADE_FACTORS", {"STD": 1.0}):
                raise ValueError(f"unknown ore grade '{ore_grade}'")
            scenario_changed = any((
                rainfall != SYSTEM_STATE["rainfall_mm"],
                soil_moisture != SYSTEM_STATE["soil_moisture_pct"],
                downtime != SYSTEM_STATE["equipment_downtime_hours"],
                blast_delay != SYSTEM_STATE["blast_delay_minutes"],
                labor != SYSTEM_STATE["labor_drop_pct"],
                target != SYSTEM_STATE["target_tonnage"],
                ore_grade != SYSTEM_STATE.get("ore_grade", "STD"),
            ))
            SYSTEM_STATE["rainfall_mm"] = rainfall
            SYSTEM_STATE["soil_moisture_pct"] = soil_moisture
            SYSTEM_STATE["equipment_downtime_hours"] = downtime
            SYSTEM_STATE["blast_delay_minutes"] = blast_delay
            SYSTEM_STATE["labor_drop_pct"] = labor
            SYSTEM_STATE["target_tonnage"] = target
            SYSTEM_STATE["ore_grade"] = ore_grade
            SYSTEM_STATE["mine_name"] = payload.get("mine_name", SYSTEM_STATE.get("mine_name")) or None
            # An executed plan belongs to the scenario it was evaluated for.
            # Never re-run its old selections after any operator input changes.
            if scenario_changed:
                SYSTEM_STATE["plan_executed"] = False
                SYSTEM_STATE["selected_actions"] = None
        except (TypeError, ValueError):
            return jsonify({
                "status": "error",
                "message": "Invalid parameter value. Rainfall, soil moisture, downtime, blast delay, labor and target must be numeric.",
            }), 400

    raw_pred = _make_prediction()

    plan_result = None
    if SYSTEM_STATE["plan_executed"]:
        recs = generate_recommendations(raw_pred, _prescriptive_risk_inputs(), C.ORE_POCKETS)
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
    recs = generate_recommendations(raw_pred, _prescriptive_risk_inputs(), C.ORE_POCKETS)

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


@app.route("/api/customers", methods=["GET", "POST"])
def handle_customers():
    """Read or add customer contracts for the current demo session.

    Contract terms are deliberately the source for the customer-liability
    ledger.  POST additions are runtime-only, matching SYSTEM_STATE.
    """
    if not customer_store:
        return jsonify({"status": "error", "message": "Customer contract engine unavailable."}), 503

    if request.method == "POST":
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"status": "error", "message": "A customer contract JSON object is required."}), 400
        try:
            entity = str(payload.get("entity") or "contract").lower()
            if entity == "customer":
                added = customer_store.add_customer(payload)
                created_kind = "customer"
            elif entity == "contract":
                added = customer_store.add(payload)
                created_kind = "contract"
            else:
                raise ValueError("entity must be customer or contract.")
        except ValueError as exc:
            return jsonify({"status": "error", "message": str(exc)}), 400
        status_code = 201
    else:
        added = None
        status_code = 200

    raw_pred = _make_prediction()
    predicted = float(raw_pred.get("predicted_tonnage") or raw_pred.get("predicted_output") or 0.0)
    portfolio = _customer_portfolio(predicted)
    response = {
        "status": "success",
        "contracts": [contract.to_dict() for contract in customer_store.list()],
        "customers": customer_store.list_customers(),
        "available_mines": _customer_mine_options(),
        "portfolio": portfolio,
        "persistence_note": "Demo contracts added here remain available until the Flask server restarts.",
    }
    if added:
        response[f"created_{created_kind}"] = added.to_dict() if hasattr(added, "to_dict") else added
    return jsonify(response), status_code


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
        "vegetation_mask": aoi.get("vegetation_mask"),
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


def _evaluate_zone(zone_config, use_demo_chip=False):
    """Compute the full per-zone spatial + spectral fusion result.

    When real per-pixel Sentinel-2 bands are available for a zone (via
    spectral.ZONE_PIXEL_DATA_PROVIDER), the vegetation (NDVI) mask is applied
    BEFORE the B04/B08/B11/B12 means are extracted and scored, and quality
    information (total pixels, vegetation removed, valid remaining, surface
    coverage %) is returned. If the mask leaves too few valid pixels, no
    misleading spectral score is produced. If no pixel data exists for the
    zone, the unmasked synthetic vector is scored and the payload states that
    the mask was NOT applied (nothing is fabricated).

    With ``use_demo_chip=True`` a clearly-labelled SYNTHETIC_DEMO pixel chip is
    simulated per zone (see spectral.simulate_zone_chip) so the map visibly
    exercises the NDVI masking pipeline. Those pixels are NOT real Sentinel-2
    observations; every payload field is labelled SYNTHETIC_DEMO.
    """
    reflectance, spectral_provenance = get_zone_reflectance(
        zone_config.get("linked_geology_record_id"),
    )
    veg_mask = getattr(spectral_module, "zone_vegetation_mask", None)
    chip = None
    mask_result = None
    if use_demo_chip:
        chipper = getattr(spectral_module, "simulate_zone_chip", None)
        chip = chipper(zone_config) if chipper else None
        if chip and veg_mask:
            mask_result = veg_mask(zone_config, band_pixels=chip.get("bands"))
    else:
        mask_result = veg_mask(zone_config) if veg_mask else None

    if use_demo_chip:
        # Data source is always the SYNTHETIC_DEMO chip, scorable or not.
        spectral_provenance = (
            getattr(spectral_module, "SIMULATED_CHIP_ZONE_REFLECTANCE_PROVENANCE",
                    "SYNTHETIC_DEMO_CHIP_VEG_MASKED_PIXEL_MEANS")
        )
    elif mask_result is not None and mask_result.applied and mask_result.scorable:
        spectral_provenance = "VEGETATION_MASKED_PIXEL_MEANS"

    if mask_result is not None and mask_result.applied and mask_result.scorable:
        reflectance = mask_result.mean_reflectance
    elif mask_result is not None and mask_result.applied and not mask_result.scorable:
        reflectance = None  # too few valid pixels: do not produce a score
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
        "status": zone_config.get("status", zone_config.get("operational_status", "UNDER INVESTIGATION")),
        "production_impact": zone_config.get("production_impact", "LOW"),
        "recommended_action": zone_config.get("recommended_action", "Review & investigate"),
        "operational_status": zone_config.get("operational_status"),
        "water_depth_m": zone_config.get("water_depth_m"),
        "pumps_active": zone_config.get("pumps_active"),
        "zone_reflectance": reflectance,
        "zone_reflectance_provenance": spectral_provenance,
        "vegetation_mask": _zone_vegetation_mask_payload(mask_result),
        "spectral_scene": spectral_module.SCENE_METADATA.copy(),
        "spatial_priority_band": fusion_module.priority_band(
            zone_config["spatial_score"]
        ),
        "fusion_rule": C.FUSION_PROTOTYPE_LABEL,
    })
    if use_demo_chip:
        payload["data_source"] = {
            "dataset": getattr(spectral_module, "SIMULATED_CHIP_DATASET", "SYNTHETIC_DEMO"),
            "provenance": getattr(spectral_module, "SIMULATED_CHIP_PROVENANCE", "SYNTHETIC_DEMO_CHIP"),
            "note": (
                "This zone's spectral result comes from a simulated pixel chip "
                "(SYNTHETIC_DEMO) running the NDVI vegetation-masking pipeline. "
                "It is NOT a real Sentinel-2 observation. The real AOI-level "
                "97.84% result remains a separate AOI_LEVEL_PROTOTYPE."
            ),
        }
        if chip:
            payload["demo_chip"] = {
                key: value
                for key, value in chip.items()
                if key != "bands"
            }
    return payload


def _zone_vegetation_mask_payload(mask_result):
    """Serializable vegetation-mask summary for a zone payload."""
    if mask_result is None:
        return {
            "status": getattr(spectral_module, "VEG_MASK_UNAVAILABLE", "UNAVAILABLE"),
            "applied": False,
            "total_pixels": None,
            "vegetation_pixels_removed": None,
            "valid_pixels_remaining": None,
            "surface_coverage_pct": None,
            "ndvi_threshold": None,
            "scorable": False,
            "reason": "Vegetation mask engine unavailable.",
        }
    return {
        "status": mask_result.status,
        "applied": mask_result.applied,
        "total_pixels": mask_result.total_pixels,
        "vegetation_pixels_removed": mask_result.vegetation_pixels_removed,
        "water_pixels_excluded": mask_result.water_pixels_excluded,
        "valid_pixels_remaining": mask_result.valid_pixels_remaining,
        "surface_coverage_pct": mask_result.surface_coverage_pct,
        "ndvi_threshold": mask_result.ndvi_threshold,
        "ndvi_statistics": mask_result.ndvi_statistics,
        "scorable": mask_result.scorable,
        "reason": mask_result.reason,
    }


@app.route("/api/zones", methods=["GET"])
def list_zones():
    """Return every candidate zone with its per-zone fusion result.

    ``?veg_demo=1`` switches each zone to the clearly-labelled SYNTHETIC_DEMO
    pixel-chip mode so the map visibly demonstrates the vegetation-masking
    pipeline. Synthetic chip results are never presented as real Sentinel-2
    observations; the AOI-level 97.84% result stays an AOI_LEVEL_PROTOTYPE.
    """
    if not ZONE_ENGINE_AVAILABLE:
        return jsonify({"error": "Zone fusion engine unavailable."}), 503
    use_demo_chip = request.args.get("veg_demo", "").lower() in ("1", "true", "yes")
    zones = [_evaluate_zone(zone, use_demo_chip=use_demo_chip) for zone in C.CANDIDATE_ZONES]
    payload = {
        "zones": zones,
        "count": len(zones),
        "fusion_rule": C.FUSION_PROTOTYPE_LABEL,
        "weights": C.FUSION_WEIGHTS,
        "aoi_name": "MOIL Bharveli-Awalajhari Mine AOI",
        "demo_mode": use_demo_chip,
        "data_provenance_note": (
            "Zone coordinates and spatial scores are SYNTHETIC_DEMO_ZONE_DATA "
            "used to demonstrate the end-to-end pipeline. Zone-level spectral "
            "vectors are drawn from processed_geology.csv (SYNTHETIC_SCHEMA_FAITHFUL). "
            "Per-zone vegetation (NDVI) masking is applied only when per-pixel "
            "bands are supplied (real provider or veg_demo=1 SYNTHETIC_DEMO chips); "
            "until then each zone reports VEG_MASK NO_PIXEL_DATA and the mask is "
            "honestly reported as not applied. The architecture accepts real "
            "Sentinel-2 per-pixel extraction without frontend contract changes."
        ),
    }
    if use_demo_chip:
        payload["dataset"] = getattr(spectral_module, "SIMULATED_CHIP_DATASET", "SYNTHETIC_DEMO")
        payload["demo_note"] = (
            "Vegetation-masking demo mode is ON. Each zone uses a simulated, "
            "deterministic SYNTHETIC_DEMO pixel chip (not real Sentinel-2 pixels) "
            "labelled per zone via data_source / demo_chip. Do not interpret these "
            "scores as real satellite observations."
        )
    return jsonify(payload)


@app.route("/api/aoi_boundary", methods=["GET"])
def get_aoi_boundary():
    """Return the 76.409-ha MOIL AOI boundary as GeoJSON features.

    CASE A fix: the supplied KML lease outline is buffered outward by the
    smallest radius that encloses all four demo-zone markers, so the drawn
    boundary now encloses the markers. Only the boundary geometry changes;
    zone markers keep their original coordinates (see markers_contained).
    The raw KML linework stays available via spectral.load_aoi_kml.
    """
    if not ZONE_ENGINE_AVAILABLE:
        return jsonify({"error": "AOI boundary engine unavailable.", "features": []}), 503
    try:
        expansion = spectral_module.expand_lease_boundary()
    except (FileNotFoundError, ValueError) as exc:
        return jsonify({"error": str(exc), "features": []}), 500
    return jsonify({
        "type": "FeatureCollection",
        "features": expansion["features"],
        "aoi_name": "MOIL Bharveli-Awalajhari Mine AOI",
        "area_ha": 76.409,
        "boundary_mode": (
            "expanded" if expansion.get("buffered") else "raw_kml_linework"
        ),
        "buffer_radius_m": expansion.get("buffer_radius_m", 0.0),
        "markers_contained": expansion.get("markers_contained", []),
        "note": expansion.get("note", ""),
    })


@app.route("/api/zones/<zone_id>", methods=["GET"])
def get_zone(zone_id):
    """Return the full per-zone fusion result for a single zone (map click).

    Honors ``?veg_demo=1`` exactly like /api/zones so the zone inspector can
    show the SYNTHETIC_DEMO vegetation-masking chain.
    """
    if not ZONE_ENGINE_AVAILABLE:
        return jsonify({"error": "Zone fusion engine unavailable."}), 503
    use_demo_chip = request.args.get("veg_demo", "").lower() in ("1", "true", "yes")
    for zone in C.CANDIDATE_ZONES:
        if zone["zone_id"] == zone_id:
            payload = _evaluate_zone(zone, use_demo_chip=use_demo_chip)
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


@app.route("/api/ndvi/filter", methods=["POST"])
def run_ndvi_filter():
    """Run the NDVI vegetation-filter pipeline for one zone and return results.

    The frontend triggers this with a real XHR when the user clicks
    RUN NDVI SURFACE FILTER, so the numbers the UI renders come from an actual
    server-side computation - never client-side fakes.

    Resolution order (honest contracts):
      1. If ``spectral.ZONE_PIXEL_DATA_PROVIDER`` is configured, real per-pixel
         Sentinel-2 bands are used (``mode`` = ``REAL_SENTINEL_2``).
      2. Otherwise the endpoint generates a clearly-labelled SYNTHETIC_DEMO
         pixel chip (B04/B08/B11/B12 per pixel), recomputes NDVI from the
         bands, applies the vegetation mask and returns the full result
         (``mode`` = ``SYNTHETIC_DEMO``). It is never presented as real
         satellite data.

    Body: ``{"zone_id": "ZONE_A"}``.
    """
    if not ZONE_ENGINE_AVAILABLE:
        return jsonify({"error": "Zone fusion engine unavailable."}), 503
    body = request.get_json(silent=True) or {}
    zone_id = str((body.get("zone_id") or "").strip())
    if not zone_id:
        return jsonify({"error": "zone_id is required."}), 400

    zone = next(
        (z for z in C.CANDIDATE_ZONES if z["zone_id"] == zone_id),
        None,
    )
    if zone is None:
        return jsonify({"error": f"Unknown zone_id: {zone_id}"}), 404

    # 1) Pixel source: real provider if configured, else the SYNTHETIC_DEMO chip.
    bands = spectral_module.get_zone_pixel_data(zone) if hasattr(spectral_module, "get_zone_pixel_data") else None
    mode = "REAL_SENTINEL_2"
    chip = None
    if not bands:
        chip = getattr(spectral_module, "simulate_zone_chip", None)(zone) if hasattr(spectral_module, "simulate_zone_chip") else None
        if not chip or not chip.get("bands"):
            return jsonify({
                "error": "No pixel source available (neither a real chip provider nor the SYNTHETIC_DEMO simulator)."
            }), 503
        bands = chip["bands"]
        mode = "SYNTHETIC_DEMO"

    # 2) NDVI is computed from B04/B08 inside the mask engine (never faked).
    mask = spectral_module.zone_vegetation_mask(zone, band_pixels=bands)
    total = mask.total_pixels or 0
    veg = mask.vegetation_pixels_removed or 0
    payload = {
        "zone_id": zone["zone_id"],
        "mode": mode,
        "applied": mask.applied,
        "status": mask.status,
        "seed": (chip or {}).get("seed"),
        "chip_size": (chip or {}).get("chip_size"),
        "total_pixels": total,
        "vegetation_pixels_removed": veg,
        "water_pixels_excluded": mask.water_pixels_excluded,
        "valid_pixels_remaining": mask.valid_pixels_remaining,
        "vegetation_pct": round((veg / total) * 100) if total else 0,
        "surface_coverage_pct": mask.surface_coverage_pct,
        "usable_surface_pct": mask.surface_coverage_pct,
        "ndvi_threshold": mask.ndvi_threshold,
        "ndvi_water_low_threshold": getattr(spectral_module, "NDVI_WATER_LOW_THRESHOLD", -0.10),
        "ndvi_statistics": mask.ndvi_statistics,
        "scorable": mask.scorable,
        "reason": mask.reason,
        "classes": (chip or {}).get("classes") or [],
        "ndvi": (chip or {}).get("ndvi") or [],
        "bands": bands,
        "dataset": getattr(spectral_module, "SIMULATED_CHIP_DATASET", "SYNTHETIC_DEMO"),
        "provenance": getattr(spectral_module, "SIMULATED_CHIP_PROVENANCE", "SYNTHETIC_DEMO_CHIP"),
    }
    if mode == "SYNTHETIC_DEMO":
        payload["note"] = (
            "SYNTHETIC_DEMO pixel chip generated server-side on this request: "
            "NDVI was computed from the chip's B04/B08 and the vegetation mask "
            "was applied to produce these statistics. This is NOT a real "
            "Sentinel-2 observation."
        )
    return jsonify(payload)


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
    from modules.weather import (
        WeatherModule,
        compute_weather_scenario,
        weather_impact_tonnes,
        classify_rain_level,
        BALAGHAT_AOI_LAT,
        BALAGHAT_AOI_LON,
        BALAGHAT_SITE_LABEL,
        CITY_FALLBACK,
    )
    weather_module = WeatherModule(lat=BALAGHAT_AOI_LAT, lon=BALAGHAT_AOI_LON)
except ImportError:
    weather_module = None
    compute_weather_scenario = None
    weather_impact_tonnes = None
    classify_rain_level = None

@app.route("/api/weather", methods=["GET", "POST"])
def get_weather():
    payload = request.get_json(silent=True) or {}
    city = (request.args.get("city") or payload.get("city") or CITY_FALLBACK).strip() or CITY_FALLBACK

    live = None
    live_status = "unavailable"
    live_error = None
    if weather_module and compute_weather_scenario is not None:
        # Live feed is always scoped to the Balaghat Sector 4 AOI coordinates so
        # the demo shows the weather at the mine, not at a generic city centroid.
        fetched = weather_module.service.fetch_live_weather(city,
                                                            lat=BALAGHAT_AOI_LAT,
                                                            lon=BALAGHAT_AOI_LON)
        if fetched and fetched.get("success"):
            live = fetched
            live_status = "ok"
        else:
            live_status = "missing_key" if (fetched or {}).get("error", "").startswith("Missing") else "failed"
            live_error = (fetched or {}).get("error", "Live weather unavailable.")

        # Live rainfall is the operator's primary weather input, fetched at the
        # exact mine AOI by summing the next 24h of 3-hourly forecast steps.
        rainfall = weather_module.service.fetch_rainfall_24h(lat=BALAGHAT_AOI_LAT,
                                                             lon=BALAGHAT_AOI_LON)
        api_rain_mm = None
        if rainfall and rainfall.get("success"):
            api_rain_mm = max(0.0, float(rainfall.get("rainfall_mm_24h") or 0.0))
            live["rainfall_mm_24h"] = api_rain_mm
            live["rain_level"] = classify_rain_level(api_rain_mm) if classify_rain_level is not None else "n/a"
        else:
            rainfall = None

    scenario = compute_weather_scenario(
        rainfall_mm=SYSTEM_STATE["rainfall_mm"],
        soil_moisture_pct=SYSTEM_STATE["soil_moisture_pct"],
        equipment_downtime_hours=SYSTEM_STATE["equipment_downtime_hours"],
        blast_delay_minutes=SYSTEM_STATE["blast_delay_minutes"],
        live=live,
    )
    impact = None
    if weather_impact_tonnes is not None:
        try:
            impact = weather_impact_tonnes(
                scenario,
                rainfall_mm=SYSTEM_STATE["rainfall_mm"],
                soil_moisture_pct=SYSTEM_STATE["soil_moisture_pct"],
                equipment_downtime_hours=SYSTEM_STATE["equipment_downtime_hours"],
                blast_delay_minutes=SYSTEM_STATE["blast_delay_minutes"],
                labor_drop_pct=SYSTEM_STATE["labor_drop_pct"],
                target_tonnage=SYSTEM_STATE["target_tonnage"],
                mine_name=SYSTEM_STATE.get("mine_name") or None,
                ore_grade=SYSTEM_STATE.get("ore_grade", "STD"),
            )
        except Exception:  # noqa: BLE001 - weather path must never take the API down
            impact = None

    base_response = {
        "status": "success",
        "city": city,
        "site": {
            "name": "Balaghat",
            "label": BALAGHAT_SITE_LABEL,
            "lat": BALAGHAT_AOI_LAT,
            "lon": BALAGHAT_AOI_LON,
        },
        "live": live,
        "live_status": live_status,
        "live_error": live_error,
        "rainfall": rainfall,
        "weather_scenario": scenario.to_dict(),
        "weather_impact": impact,
        "prediction": None,
    }

    if request.method == "POST":
        # Auto-sync action: translate current weather into the model inputs the
        # frozen prediction module consumes, so the baseline dashboard visibly
        # shifts. Rainfall comes straight from the API (the slider is locked to
        # it); soil/downtime/blast get the weather-translated effective values.
        # Only applied when live data actually arrived; the prediction module and
        # its model are never modified.
        if live is None:
            base_response.update({"status": "error", "message": "Live weather unavailable; nothing applied."})
            return jsonify(base_response), 200
        SYSTEM_STATE["rainfall_mm"] = api_rain_mm if api_rain_mm is not None else scenario.effective_rainfall_mm
        SYSTEM_STATE["soil_moisture_pct"] = scenario.effective_soil_moisture_pct
        SYSTEM_STATE["equipment_downtime_hours"] = scenario.effective_downtime_hours
        SYSTEM_STATE["blast_delay_minutes"] = scenario.effective_blast_delay_minutes
        SYSTEM_STATE["plan_executed"] = False
        SYSTEM_STATE["selected_actions"] = None
        try:
            raw_pred = _make_prediction()
            view = _prediction_view(raw_pred, SYSTEM_STATE["target_tonnage"], None)
        except Exception:  # noqa: BLE001 - model availability must not break weather sync
            view = None
        base_response.update({"prediction": view, "parameters": _current_params()})

    return jsonify(base_response)

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=5001,
        debug=True,
        use_reloader=False
    )
