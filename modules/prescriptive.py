import os
import pickle
import time
from typing import Any, Dict, List, Optional, Tuple

import streamlit as st

# --------------------------------------------------------------------------
# MODEL ARTIFACT LOADING (load once, at runtime, never retrain here)
# --------------------------------------------------------------------------

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_MODEL_PATH = os.path.join(_THIS_DIR, "..", "models", "action_impact_model.pkl")

_model_bundle: Optional[Dict[str, Any]] = None
_model_load_error: Optional[str] = None
_model_load_attempted = False


def _get_model_bundle() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Lazily load the trained M4 action-impact model bundle exactly once.

    Returns (bundle_or_None, error_message_or_None). Never raises — callers
    must handle a None bundle by falling back gracefully.
    """
    global _model_bundle, _model_load_error, _model_load_attempted
    if _model_load_attempted:
        return _model_bundle, _model_load_error

    _model_load_attempted = True
    try:
        with open(_MODEL_PATH, "rb") as f:
            _model_bundle = pickle.load(f)
    except Exception as exc:  # noqa: BLE001 - deliberately broad, must never crash the app
        _model_bundle = None
        _model_load_error = f"{type(exc).__name__}: {exc}"
    return _model_bundle, _model_load_error


# --------------------------------------------------------------------------
# LOCAL PROTOTYPE CONFIGURATION
# (Not constants.py — this stays local to M4 as instructed. Anything here is
#  explicitly a hackathon prototype value, not an official MOIL figure.)
# --------------------------------------------------------------------------

# Reference grade/price bands supplied for the hackathon concept. Used only
# to attach illustrative "grade realization" context to the recommendation
# for the Customer Contract Ledger to optionally use — M4 never computes the
# final ₹ figure itself.
_PROTOTYPE_GRADE_PRICE_BANDS = [
    {"min_grade_pct": 44.0, "price_per_tonne_inr": 47333.0, "label": "High-grade (~46% Mn)"},
    {"min_grade_pct": 34.0, "price_per_tonne_inr": 32100.0, "label": "Medium/low-grade (35–38% Mn)"},
    {"min_grade_pct": 0.0, "price_per_tonne_inr": 32100.0, "label": "Low-grade (<35% Mn, using medium/low reference)"},
]

# Used only if ore_pockets is empty/unavailable, so the module still produces
# a coherent demo instead of crashing. Mirrors the worked example in the
# project brief (Pit 1 46% Mn -> Pit B 38% Mn).
_FALLBACK_SOURCE_POCKET = {"name": "Pit 1", "grade_pct": 46.0, "mine_name": None}
_FALLBACK_TARGET_POCKET = {"name": "Pit B", "grade_pct": 38.0, "mine_name": None}

# Demo-only time compression so a multi-hour dewatering timeline can be
# observed live during a hackathon demo without actually waiting hours.
# This does not represent real pump/equipment control in any way.
_DEMO_SECONDS_PER_SIMULATED_HOUR = 5.0

# Rainfall/downtime/labor thresholds used purely for rule-based candidate
# *selection* (which actions are operationally sensible). Magnitude of the
# benefit always comes from the ML model, never from these thresholds.
_RAIN_TRIGGER_MM = 25.0
_DOWNTIME_TRIGGER_HOURS = 8.0
_LABOR_DROP_TRIGGER_PCT = 15.0

# Central human-readable labels for the internal action codes. The codes
# below (REROUTE_FLEET, DEWATERING, ...) are consumed by the backend/model
# logic and must NEVER be replaced — only the wording shown to mine operators
# changes. Keep the wording action-oriented: WHAT THE WORKER SHOULD DO.
ACTION_DISPLAY_LABELS = {
    "REROUTE_FLEET": "Move dumpers to an alternate pit",
    "DEWATERING": "Pump water from the affected pit",
    "PREVENTIVE_MAINTENANCE": "Inspect and service equipment",
    "CONTINGENCY_LABOR": "Arrange additional workers",
    "BLENDING": "Blend ore with available stockpile",
}

# Human-readable rule/domain explanations backing each candidate action.
# These are STATIC operational heuristics written for mine operators — they
# are NOT produced by the ML model and must never be presented as if the ML
# model generated them. The ML model only estimates recovered tonnage.
ACTION_RULE_EXPLANATIONS = {
    "REROUTE_FLEET": "Fleet/logistics disruption makes alternative fleet routing useful.",
    "DEWATERING": "High rainfall/water accumulation makes water removal useful.",
    "PREVENTIVE_MAINTENANCE": "High equipment downtime indicates that restoring equipment availability may recover production.",
    "CONTINGENCY_LABOR": "Reduced labor availability makes additional temporary labor useful.",
    "BLENDING": "Available stockpile material can help compensate for reduced production while maintaining the required product quality.",
}

# Prototype per-action operating cost (₹ / action): median of the M4
# prototype action dataset. Hackathon reference figure ONLY — NOT an official
# MOIL cost. Used solely for the illustrative net-benefit estimate on the
# operator-selected plan.
ACTION_PROTOTYPE_COST_INR = {
    "REROUTE_FLEET": 6245.0,
    "DEWATERING": 9423.0,
    "PREVENTIVE_MAINTENANCE": 12898.0,
    "CONTINGENCY_LABOR": 3969.0,
    "BLENDING": 3576.0,
}

# State-transition constants for the sequential "selected plan" simulation.
# Explicit prototype assumptions ONLY — none of these were learned from real
# mine data. Every state value produced is clamped to a physically plausible
# range (floor of 0 / ceiling of 100% where applicable) so the simulation can
# never manufacture impossible values.
_DEWATERING_RAIN_REMAINING_FRACTION = 0.25       # rainfall severity drops to 25% after dewatering
_MAINTENANCE_DOWNTIME_REMAINING_FRACTION = 0.40  # downtime drops to 40% after maintenance
_REROUTE_DOWNTIME_REMAINING_FRACTION = 0.50      # fleet reroute halves the downtime penalty
_LABOR_DROP_REMAINING_FRACTION = 0.15            # extra labor cuts the labor shortfall to 15%
_IMPROVED_AVAILABILITY_FLOOR_PCT = 95.0          # serviced/rerouted assets >= 95% availability


# --------------------------------------------------------------------------
# STEP A — RECOVER APPROXIMATE RAW OPERATING SIGNALS
# --------------------------------------------------------------------------

def _derive_operating_signals(prediction: Optional[Dict[str, Any]],
                               risk: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Best-effort reconstruction of raw operating signals from whatever the
    prediction/risk dicts actually contain today, with every estimate
    labelled in `assumptions`.
    """
    prediction = prediction or {}
    risk = risk or {}
    penalties = prediction.get("penalties") or {}
    assumptions: List[str] = []

    def _first_present(*sources_and_keys):
        for source, key in sources_and_keys:
            if isinstance(source, dict) and source.get(key) is not None:
                return source.get(key)
        return None

    # --- Penalty convention detection ---
    # modules/prediction.py emits two DIFFERENT penalty conventions depending
    # on which backend actually produced the numbers:
    #   1) deterministic fallback (predict_weekly_tonnage):
    #        {"rain": min(mm/300,1)*0.35, "mtbf": max(0,(150-mtbf)/150)*0.30,
    #         "labor": (labor_drop_pct/100)*0.25}
    #   2) trained 13-feature ML model (predict_shortfall_with_model):
    #        {"rain": min(mm/100,1), "equipment": min(downtime_hrs/14,1),
    #         "labor": min(labor_drop_pct/100,1)}
    # Blindly inverting scale #2 with the #1 formulas produces nonsense
    # signals (e.g. 88.5 mm rainfall -> ~300 mm, or an 18% labor drop ->
    # ~72%). Detect the convention from the keys present and invert the
    # matching one.
    _uses_model_penalty_scale = "equipment" in penalties

    # --- Rainfall ---
    rainfall_mm = _first_present((risk, "rainfall_mm"), (prediction, "rainfall_mm"))
    if rainfall_mm is None:
        rain_penalty = penalties.get("rain")
        if rain_penalty is not None:
            if _uses_model_penalty_scale:
                # Model convention: rain_penalty = min(mm/100, 1); mm capped at 100.
                rainfall_mm = min(max(float(rain_penalty), 0.0), 1.0) * 100.0
                assumptions.append(
                    "rainfall_mm derived from prediction.penalties.rain via the inverse of the "
                    "ML-model rain penalty (min(mm/100, 1); the model caps mm at 100)."
                )
            else:
                # Fallback formula: rain_penalty = min(mm/300,1)*0.35
                rainfall_mm = min(float(rain_penalty) / 0.35, 1.0) * 300.0
                assumptions.append(
                    "rainfall_mm derived from prediction.penalties.rain via the inverse of the "
                    "documented rain-penalty formula (rain_penalty = min(mm/300,1)*0.35)."
                )
        else:
            rainfall_mm = 0.0
            assumptions.append("rainfall_mm unavailable from prediction/risk; defaulted to 0.0 mm.")

    # --- Equipment reliability / downtime ---
    mtbf_hrs = _first_present((risk, "mtbf_hrs"), (prediction, "mtbf_hrs"))
    # The current dashboard supplies downtime directly. Prefer that observed
    # value over reconstructing it from a model penalty, which can otherwise
    # preserve an earlier scenario after the controls have changed.
    equipment_downtime_hours = _first_present(
        (risk, "equipment_downtime_hours"),
        (prediction, "equipment_downtime_hours"),
    )
    if equipment_downtime_hours is not None and mtbf_hrs is None:
        mtbf_hrs = max(0.0, 150.0 - float(equipment_downtime_hours) * 12.5)
    if mtbf_hrs is None:
        if _uses_model_penalty_scale:
            equip_penalty = penalties.get("equipment")
            if equip_penalty is not None:
                # Model convention: equipment_downtime_hours = min(12, (150-mtbf)/150*12)
                # with the penalty normalised by /14. Invert both in one step and
                # clamp to the model's 12-hr downtime ceiling.
                equipment_downtime_hours = min(max(float(equip_penalty), 0.0), 1.0) * 14.0
                equipment_downtime_hours = min(equipment_downtime_hours, 12.0)
                mtbf_hrs = max(0.0, 150.0 - equipment_downtime_hours * 12.5)
                assumptions.append(
                    "equipment_downtime_hours and mtbf_hrs derived from prediction.penalties.equipment "
                    "via the inverse of the ML-model downtime penalty "
                    "(downtime = min(downtime/14, 1)*14, capped at the model's 12-hr ceiling)."
                )
        if equipment_downtime_hours is None:
            mtbf_penalty = penalties.get("mtbf")
            if mtbf_penalty is not None:
                # Fallback formula: mtbf_penalty = max(0,(150-mtbf)/150)*0.30
                mtbf_hrs = 150.0 - min(float(mtbf_penalty) / 0.30, 1.0) * 150.0
                assumptions.append(
                    "mtbf_hrs derived from prediction.penalties.mtbf via the inverse of the "
                    "documented MTBF-penalty formula."
                )
            else:
                mtbf_hrs = 150.0
                assumptions.append("mtbf_hrs unavailable; defaulted to a healthy 150 hrs (no downtime signal).")
    if equipment_downtime_hours is None:
        # No direct downtime telemetry exists in the current contract, so a
        # heuristic proxy scales the (reconstructed) MTBF penalty into a
        # 0-24 hr/day band.
        mtbf_penalty_val = max(0.0, (150.0 - float(mtbf_hrs)) / 150.0) * 0.30
        equipment_downtime_hours = max(0.0, min(1.0, mtbf_penalty_val / 0.30)) * 24.0
        assumptions.append(
            "equipment_downtime_hours is a heuristic proxy (0-24 hr/day) scaled from the MTBF "
            "penalty; no direct downtime telemetry is wired into the pipeline yet."
        )

    # --- Labor ---
    labor_drop_pct = _first_present((risk, "labor_drop_pct"), (prediction, "labor_drop_pct"))
    if labor_drop_pct is None:
        labor_penalty = penalties.get("labor")
        if labor_penalty is not None:
            if _uses_model_penalty_scale:
                # Model convention: labor_penalty = min(labor_drop_pct/100, 1)
                labor_drop_pct = min(max(float(labor_penalty), 0.0), 1.0) * 100.0
                assumptions.append(
                    "labor_drop_pct derived from prediction.penalties.labor via the inverse of the "
                    "ML-model labor penalty (min(labor_drop_pct/100, 1))."
                )
            else:
                # Fallback formula: labor_penalty = (labor_drop_pct/100)*0.25
                labor_drop_pct = min(float(labor_penalty) / 0.25, 1.0) * 100.0
                assumptions.append(
                    "labor_drop_pct derived from prediction.penalties.labor via the inverse of the "
                    "documented labor-penalty formula."
                )
        else:
            labor_drop_pct = 0.0
            assumptions.append("labor_drop_pct unavailable; defaulted to 0.0% (no labor signal).")

    # --- Production volumes ---
    predicted_tonnage = float(prediction.get("predicted_tonnage") or 0.0)
    shortfall_tonnage = float(prediction.get("shortfall_tonnage") or 0.0)
    if not prediction:
        assumptions.append(
            "prediction dict was empty/None (Shortfall Predictor module not yet implemented); "
            "predicted_tonnage and shortfall_tonnage defaulted to 0.0."
        )
    target_rom_tonnes = predicted_tonnage + shortfall_tonnage
    shortfall_pct = (shortfall_tonnage / target_rom_tonnes * 100.0) if target_rom_tonnes > 0 else 0.0

    return {
        "rainfall_mm": float(rainfall_mm),
        "mtbf_hrs": float(mtbf_hrs),
        "equipment_downtime_hours": float(equipment_downtime_hours),
        "labor_drop_pct": float(labor_drop_pct),
        "labor_available_pct_signal": max(0.0, 100.0 - float(labor_drop_pct)),
        "predicted_tonnage": predicted_tonnage,
        "shortfall_tonnage": shortfall_tonnage,
        "target_rom_tonnes": target_rom_tonnes,
        "shortfall_pct": shortfall_pct,
        "ori": float(risk.get("ori")) if isinstance(risk, dict) and risk.get("ori") is not None else None,
        "assumptions": assumptions,
    }


# --------------------------------------------------------------------------
# STEP B — NORMALIZE ORE POCKET / PIT INFORMATION
# --------------------------------------------------------------------------

def _normalize_pocket(raw: Any, index: int) -> Dict[str, Any]:
    """Accepts whatever shape the spatial/constants ore-pocket entry has
    (dict with varying key names is the common case) and returns a
    normalized {name, grade_pct, mine_name, raw} record.
    """
    if isinstance(raw, dict):
        name = (raw.get("name") or raw.get("pit_name") or raw.get("pocket_id")
                or raw.get("id") or raw.get("label") or f"Pit {index + 1}")
        grade = (raw.get("grade_pct") or raw.get("grade") or raw.get("mn_pct")
                 or raw.get("mn_grade_pct") or raw.get("predicted_mn_pct"))
        mine_name = raw.get("mine_name") or raw.get("mine")
        return {"name": str(name), "grade_pct": float(grade) if grade is not None else None,
                "mine_name": mine_name, "raw": raw}
    # Fallback for tuple/string/other shapes — keep it, but grade is unknown.
    return {"name": str(raw), "grade_pct": None, "mine_name": None, "raw": raw}


def _select_source_and_target(ore_pockets: Optional[List[Any]]) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], List[str]]:
    """Picks a "current/source" pocket and a lower-grade "alternate/target"
    pocket to model a reroute. Since the current contract does not flag
    which pocket is presently in production, the first entry is treated as
    the source (documented assumption) and the highest-grade *other* entry
    becomes the natural alternate-comparison target.
    """
    notes: List[str] = []
    pockets = [p for p in (ore_pockets or []) if p is not None]

    if not pockets:
        notes.append(
            "ore_pockets was empty/unavailable; using illustrative fallback pits "
            f"({_FALLBACK_SOURCE_POCKET['name']} {_FALLBACK_SOURCE_POCKET['grade_pct']}% Mn -> "
            f"{_FALLBACK_TARGET_POCKET['name']} {_FALLBACK_TARGET_POCKET['grade_pct']}% Mn) from the project brief."
        )
        return dict(_FALLBACK_SOURCE_POCKET), dict(_FALLBACK_TARGET_POCKET), notes

    normalized = [_normalize_pocket(p, i) for i, p in enumerate(pockets)]
    source = normalized[0]
    notes.append(
        f"source_pit assumed to be the first ore_pockets entry ('{source['name']}'); "
        "no explicit 'currently active pit' field exists in the current contract."
    )

    if source["grade_pct"] is None:
        source = dict(source)
        source["grade_pct"] = 38.0
        notes.append("source pit grade missing; defaulted to a mid-grade prototype value of 38% Mn.")

    others = normalized[1:]
    target = None
    if others:
        # Prefer the closest *lower*-grade alternate (typical flood-reroute
        # scenario); otherwise just take the next available pocket.
        lower_grade = [p for p in others if p["grade_pct"] is not None and p["grade_pct"] < source["grade_pct"]]
        target = max(lower_grade, key=lambda p: p["grade_pct"]) if lower_grade else others[0]
        if target["grade_pct"] is None:
            target = dict(target)
            target["grade_pct"] = 32.0
            notes.append("target pit grade missing; defaulted to a low/medium-grade prototype value of 32% Mn.")
    else:
        notes.append("only one ore pocket was available; no alternate pit exists to reroute production to.")

    return source, target, notes


def _grade_price_reference(grade_pct: Optional[float]) -> Dict[str, Any]:
    if grade_pct is None:
        band = _PROTOTYPE_GRADE_PRICE_BANDS[-1]
    else:
        band = next((b for b in _PROTOTYPE_GRADE_PRICE_BANDS if grade_pct >= b["min_grade_pct"]),
                     _PROTOTYPE_GRADE_PRICE_BANDS[-1])
    return {
        "price_per_tonne_inr": band["price_per_tonne_inr"],
        "band_label": band["label"],
        "is_official_price": False,
        "note": "Prototype/reference grade-price value supplied for the hackathon concept; not an official live MOIL price.",
    }


# --------------------------------------------------------------------------
# STEP C — RULE-BASED CANDIDATE ACTION SELECTION
# --------------------------------------------------------------------------

def _candidate_actions(signals: Dict[str, Any], has_alternate_pocket: bool,
                       source_pit: Optional[str] = None,
                       target_pit: Optional[str] = None) -> List[Dict[str, str]]:
    """Decides WHICH interventions are operationally sensible given current
    conditions. Magnitude of benefit is left entirely to the ML model in the
    next step — this function only ever answers "is this action relevant?".
    Reasons are written for mine operators (WHAT TO DO / WHY), not for the
    backend; `source_pit` / `target_pit` make the wording concrete.
    """
    candidates: List[Dict[str, str]] = []
    flooded = signals["rainfall_mm"] >= _RAIN_TRIGGER_MM

    if flooded:
        source = source_pit or "the affected pit"
        if has_alternate_pocket and target_pit:
            move_instruction = f"Move dumpers to {target_pit} to continue production."
        else:
            move_instruction = "Plan a fleet move to an alternate pit as soon as one is available."
        candidates.append({
            "action": "REROUTE_FLEET",
            "reason": f"Heavy rainfall (~{signals['rainfall_mm']:.0f} mm) may have made {source} "
                      f"unsafe for immediate operation. {move_instruction}",
        })
        candidates.append({
            "action": "DEWATERING",
            "reason": f"Standing water is likely at {source} after heavy rainfall. "
                      "Pump the water out so work at that pit can resume once it is cleared.",
        })

    if signals["equipment_downtime_hours"] >= _DOWNTIME_TRIGGER_HOURS:
        candidates.append({
            "action": "PREVENTIVE_MAINTENANCE",
            "reason": f"Machines have been unreliable lately (~{signals['equipment_downtime_hours']:.1f} hrs "
                      "of downtime expected). Inspect and service the equipment to reduce unplanned stoppages.",
        })

    if signals["labor_drop_pct"] >= _LABOR_DROP_TRIGGER_PCT:
        candidates.append({
            "action": "CONTINGENCY_LABOR",
            "reason": f"Crew availability is down ~{signals['labor_drop_pct']:.0f}%. "
                      "Arrange additional workers to keep the planned output on track.",
        })

    if has_alternate_pocket:
        candidates.append({
            "action": "BLENDING",
            "reason": "Lower-grade ore will be used while the main pit recovers. "
                      "Blend it with the available stockpile to keep the product grade stable.",
        })

    if not candidates:
        candidates.append({
            "action": "REROUTE_FLEET",
            "reason": "No urgent issue detected. A planned fleet move keeps production steady as a precaution.",
        })

    return candidates


# --------------------------------------------------------------------------
# STEP D — BUILD MODEL FEATURES AND GET THE ML TONNAGE ESTIMATE
# --------------------------------------------------------------------------

def _resource_percentile(bundle: Dict[str, Any], action: str, field: str, percentile: str, default: float) -> float:
    try:
        return float(bundle["resource_ranges"][action][field][percentile])
    except Exception:  # noqa: BLE001
        return default


def _resolve_mine_name(bundle: Dict[str, Any], signals: Dict[str, Any], source_pocket: Dict[str, Any]) -> Tuple[str, Optional[str]]:
    mine_list = bundle.get("mine_list") or []
    candidate = source_pocket.get("mine_name")
    if candidate and candidate in mine_list:
        return candidate, None
    if mine_list:
        return mine_list[0], (
            f"mine_name not resolvable from ore_pockets; defaulted to '{mine_list[0]}' "
            "(the model's first known mine) for the ML feature vector only."
        )
    return "", "mine_name unavailable and the model has no known mine list; one-hot mine columns left at 0."


def _build_feature_row(bundle: Dict[str, Any], action: str, mine_name: str, signals: Dict[str, Any],
                        percentile: str) -> Dict[str, float]:
    row = {col: 0.0 for col in bundle["feature_columns"]}

    row["target_rom_tonnes"] = signals["target_rom_tonnes"]
    row["actual_rom_tonnes"] = signals["predicted_tonnage"]
    row["shortfall_tonnes"] = signals["shortfall_tonnage"]
    row["shortfall_pct"] = signals["shortfall_pct"]
    row["equipment_downtime_hours"] = signals["equipment_downtime_hours"]
    row["downtime_3d_hours"] = signals["equipment_downtime_hours"] * 3.0  # proxy: no multi-day history available
    row["blast_delay_minutes"] = 0.0  # no signal available in current contract
    row["rainfall_mm"] = signals["rainfall_mm"]
    row["rain_3d_mm"] = signals["rainfall_mm"]  # proxy: no multi-day rainfall history available
    row["rain_7d_mm"] = signals["rainfall_mm"]  # proxy: no multi-day rainfall history available
    row["avg_mn_grade_pct"] = signals.get("avg_mn_grade_pct", 38.0)

    row["resources_allocated"] = _resource_percentile(bundle, action, "resources_allocated", percentile, 50.0)
    row["duration_hours"] = _resource_percentile(bundle, action, "duration_hours", percentile, 12.0)
    row["action_intensity"] = _resource_percentile(bundle, action, "action_intensity", percentile, 0.5)

    # Action-specific operating fields: only the field relevant to the chosen
    # action is populated (with derived signal preferred over the training
    # median where one exists); the rest stay at 0.0 as in training.
    if action == "PREVENTIVE_MAINTENANCE" or action == "REROUTE_FLEET":
        proxy = max(0.0, 100.0 - min(100.0, signals["equipment_downtime_hours"] / 24.0 * 100.0))
        row["available_equipment_pct"] = proxy if proxy > 0 else _resource_percentile(
            bundle, action, "available_equipment_pct", percentile, 80.0)
    if action == "DEWATERING":
        row["available_pumps"] = _resource_percentile(bundle, action, "available_pumps", percentile, 4.0)
    if action == "CONTINGENCY_LABOR":
        row["labor_available_pct"] = signals["labor_available_pct_signal"]
    if action == "BLENDING":
        # Prefer the live simulated stockpile level when one is present (the
        # sequential planner depletes it step by step); otherwise fall back to
        # the training-resource median like the original code did.
        if "stockpile_available_tonnes" in signals:
            stockpile_val = signals["stockpile_available_tonnes"]
        else:
            stockpile_val = _resource_percentile(bundle, action, "stockpile_available_tonnes", percentile, 200.0)
        row["stockpile_available_tonnes"] = stockpile_val
        row["quality_uplift_pct"] = _resource_percentile(bundle, action, "quality_uplift_pct", percentile, 3.5)

    action_col = f"action_{action}"
    if action_col in row:
        row[action_col] = 1.0
    mine_col = f"mine_name_{mine_name}" if mine_name else None
    if mine_col and mine_col in row:
        row[mine_col] = 1.0

    return row


_TRAINING_REFERENCE_TARGET_ROM = 537.0
_VOLUME_FEATURES = ("target_rom_tonnes", "actual_rom_tonnes", "shortfall_tonnes")


def _predict_recovered_tonnage(bundle: Optional[Dict[str, Any]], action: str, mine_name: str,
                                signals: Dict[str, Any], percentile: str) -> Tuple[Optional[float], Dict[str, float]]:
    if bundle is None:
        return None, {}
    try:
        import pandas as pd  # local import: only needed on the ML path
        row = _build_feature_row(bundle, action, mine_name, signals, percentile)

        # Volume normalization: the ML model was trained on daily-scale
        # production data (median target_rom_tonnes ≈ 537 t). When the live
        # operation runs at a different production scale, volume features are
        # normalized into the model's reference domain before prediction, and
        # the output is scaled back proportionally. This preserves the model's
        # learned recovery effectiveness (recovered / shortfall ≈ 5–9%)
        # while correctly converting to the live shortfall quantity.
        live_target = float(signals.get("target_rom_tonnes", 0.0))
        volume_ratio = 1.0
        if live_target > 0.0 and _TRAINING_REFERENCE_TARGET_ROM > 0.0:
            volume_ratio = live_target / _TRAINING_REFERENCE_TARGET_ROM
        row_for_model = dict(row)
        if abs(volume_ratio - 1.0) > 0.05:
            for vol_key in _VOLUME_FEATURES:
                if vol_key in row_for_model:
                    row_for_model[vol_key] = row_for_model[vol_key] / volume_ratio
            row_for_model["shortfall_pct"] = (
                row_for_model["shortfall_tonnes"] / row_for_model["target_rom_tonnes"] * 100.0
            ) if row_for_model.get("target_rom_tonnes", 0.0) > 0 else 0.0

        X = pd.DataFrame([row_for_model])[bundle["feature_columns"]]
        pred = float(bundle["tonnage_model"].predict(X)[0])
        if abs(volume_ratio - 1.0) > 0.05:
            pred = pred * volume_ratio
        return max(0.0, pred), row
    except Exception:  # noqa: BLE001 - model path must never crash the app
        return None, {}


def _fallback_heuristic_tonnage(action: str, shortfall_tonnage: float) -> float:
    """Used ONLY if the trained model artifact cannot be loaded/used. Crude,
    clearly-labelled rule-of-thumb so the app still degrades gracefully
    instead of crashing.
    """
    effectiveness = {
        "REROUTE_FLEET": 0.65,
        "DEWATERING": 0.55,
        "PREVENTIVE_MAINTENANCE": 0.35,
        "CONTINGENCY_LABOR": 0.30,
        "BLENDING": 0.25,
    }.get(action, 0.3)
    return max(0.0, shortfall_tonnage) * effectiveness


# --------------------------------------------------------------------------
# STEP E — EVALUATE EACH CANDIDATE ACTION AND RANK THEM
# --------------------------------------------------------------------------

def _best_recovery(bundle: Optional[Dict[str, Any]], mine_name: str,
                   signals: Dict[str, Any], action: str) -> Dict[str, Any]:
    """Runs the action through the three effort levels (conservative /
    standard / intensive) and picks the "best" supported estimate, mirroring
    the original ranking logic. Returns a dict with recovered_tonnage,
    resources_allocated, duration_hours, action_intensity, meets_shortfall,
    used_ml_model. Falls back to the rule-of-thumb only when the model
    artifact is unavailable.
    """
    shortfall = signals.get("shortfall_tonnage", 0.0)
    best: Optional[Dict[str, Any]] = None
    for percentile in ("p25", "median", "p75"):
        tonnage, feature_row = _predict_recovered_tonnage(bundle, action, mine_name, signals, percentile)
        if tonnage is None:
            tonnage = _fallback_heuristic_tonnage(action, shortfall)
            used_ml_model = False
        else:
            used_ml_model = True

        candidate_eval = {
            "recovered_tonnage": round(tonnage, 2),
            "resources_allocated": round(feature_row.get("resources_allocated", 0.0), 1),
            "duration_hours": round(feature_row.get("duration_hours", 0.0), 1),
            "action_intensity": round(feature_row.get("action_intensity", 0.0), 2),
            "meets_shortfall": tonnage >= shortfall * 0.9 if shortfall > 0 else True,
            "used_ml_model": used_ml_model,
        }
        if best is None:
            best = candidate_eval
        elif candidate_eval["meets_shortfall"] and not best["meets_shortfall"]:
            best = candidate_eval
        elif candidate_eval["meets_shortfall"] == best["meets_shortfall"] and (
                (candidate_eval["meets_shortfall"] and candidate_eval["resources_allocated"] < best["resources_allocated"])
                or (not candidate_eval["meets_shortfall"] and candidate_eval["recovered_tonnage"] > best["recovered_tonnage"])
        ):
            best = candidate_eval
    return best or {
        "recovered_tonnage": 0.0,
        "resources_allocated": 0.0,
        "duration_hours": 0.0,
        "action_intensity": 0.0,
        "meets_shortfall": shortfall <= 0,
        "used_ml_model": False,
    }


def _evaluate_candidate(bundle: Optional[Dict[str, Any]], model_error: Optional[str], candidate: Dict[str, str],
                         mine_name: str, mine_name_note: Optional[str], signals: Dict[str, Any]) -> Dict[str, Any]:
    action = candidate["action"]
    best = _best_recovery(bundle, mine_name, signals, action)

    notes = []
    if mine_name_note:
        notes.append(mine_name_note)
    if bundle is None and model_error:
        notes.append(f"ML model unavailable ({model_error}); used a rule-of-thumb fallback estimate instead.")

    return {
        "action": action,
        "reason": candidate["reason"],
        "recovered_tonnage": best["recovered_tonnage"],
        "resources_allocated": best["resources_allocated"],
        "duration_hours": best["duration_hours"],
        "action_intensity": best["action_intensity"],
        "meets_shortfall": best["meets_shortfall"],
        "used_ml_model": best["used_ml_model"],
        "notes": notes,
    }


# --------------------------------------------------------------------------
# PUBLIC API — 1. generate_recommendations
# --------------------------------------------------------------------------

def generate_recommendations(prediction: Optional[Dict[str, Any]], risk: Optional[Dict[str, Any]],
                              ore_pockets: Optional[List[Any]]) -> Dict[str, Any]:
    """Consumes prediction data, risk metrics and ore-pocket information and
    recommends corrective operational actions (fleet re-routing, dewatering,
    preventive maintenance, contingency labor, or blending).

    Returns a UI-friendly dict, including "recoverable_tonnage" (kept for
    compatibility with the existing app.py integration), a structured
    "recommended" action card, a ranked "alternatives" list, grade-aware
    context for the Customer Contract Ledger, and an "assumptions" list documenting
    every fallback used.
    """
    bundle, model_error = _get_model_bundle()

    signals = _derive_operating_signals(prediction, risk)
    source_pocket, target_pocket, pocket_notes = _select_source_and_target(ore_pockets)
    signals["avg_mn_grade_pct"] = source_pocket.get("grade_pct", 38.0)

    mine_name, mine_name_note = _resolve_mine_name(bundle or {"mine_list": []}, signals, source_pocket)

    candidates = _candidate_actions(
        signals,
        has_alternate_pocket=target_pocket is not None,
        source_pit=source_pocket.get("name"),
        target_pit=target_pocket.get("name") if target_pocket else None,
    )
    evaluated = [
        _evaluate_candidate(bundle, model_error, c, mine_name, mine_name_note, signals)
        for c in candidates
    ]

    # Rank: actions that cover the shortfall first, then by recovered tonnage.
    # IMPORTANT (Problem 3): DEWATERING's predicted tonnage only becomes real
    # once the background clearance timeline finishes — it is never an
    # "immediate" recovery. So whenever REROUTE_FLEET is also on the table
    # (the flood scenario), DEWATERING must not win the *primary,
    # immediately-executable* recommendation purely by having a bigger raw
    # ML number; it is instead reported as background/phased context below.
    has_reroute = any(e["action"] == "REROUTE_FLEET" for e in evaluated)
    has_dewatering = any(e["action"] == "DEWATERING" for e in evaluated)
    ranking_pool = [e for e in evaluated if not (has_reroute and has_dewatering and e["action"] == "DEWATERING")]
    ranking_pool.sort(key=lambda e: (not e["meets_shortfall"], -e["recovered_tonnage"]))
    primary = ranking_pool[0]
    alternatives = [e for e in evaluated if e is not primary]

    recovered_tonnage = primary["recovered_tonnage"]
    remaining_shortfall = max(0.0, signals["shortfall_tonnage"] - recovered_tonnage)

    # Background dewatering context (Problem 3): only attached when the
    # scenario actually involves flooding, regardless of whether DEWATERING
    # ended up being the *primary* recommended action.
    dewatering_candidate = next((e for e in evaluated if e["action"] == "DEWATERING"), None)
    background_dewatering = None
    if dewatering_candidate is not None and primary["action"] != "DEWATERING":
        background_dewatering = {
            "pit": source_pocket["name"],
            "phase": "FLOODED",
            "pump_status": "idle",
            "estimated_clearance_hours": dewatering_candidate["duration_hours"],
            "note": "Source pit stays unavailable until dewatering completes; execute the plan to begin the "
                    "background dewatering timeline.",
        }

    source_price = _grade_price_reference(source_pocket.get("grade_pct"))
    target_price = _grade_price_reference(target_pocket.get("grade_pct") if target_pocket else None)
    grade_delta_pct = None
    if target_pocket and source_pocket.get("grade_pct") is not None and target_pocket.get("grade_pct") is not None:
        grade_delta_pct = round(source_pocket["grade_pct"] - target_pocket["grade_pct"], 2)

    assumptions = list(signals["assumptions"]) + pocket_notes
    if mine_name_note:
        assumptions.append(mine_name_note)
    if bundle is None:
        assumptions.append(
            f"Trained model artifact could not be loaded ({model_error}); recommendations use a rule-of-thumb "
            "fallback and are lower-confidence."
        )

    # --- HITL options list: every operationally-sensible candidate action is
    # surfaced to the operator (AI recommends, the user decides). Each option
    # carries the ACTUAL ML estimate (never a hardcoded value) plus a
    # rule/domain reason. "recommended" is only a visual top-suggestion flag;
    # nothing is automatically committed.
    ordering = sorted(evaluated, key=lambda e: (not e["meets_shortfall"], -e["recovered_tonnage"]))
    options = [
        {
            "action": e["action"],
            "expected_recovery_tonnes": e["recovered_tonnage"],
            "reason": ACTION_RULE_EXPLANATIONS.get(e["action"], e["reason"]),
            "scenario_reason": e["reason"],
            "recommended": e["action"] == primary["action"],
            "resource_allocation_pct": e["resources_allocated"],
            "estimated_action_duration_hours": e["duration_hours"],
            "action_intensity": e["action_intensity"],
            "meets_shortfall": e["meets_shortfall"],
            "used_ml_model": e["used_ml_model"],
            "action_cost": ACTION_PROTOTYPE_COST_INR.get(e["action"], 0.0),
            "notes": list(e["notes"]) + [
                "Expected recovery is an ML-model estimate; the reason is a rule/domain explanation, not an ML claim.",
            ],
        }
        for e in ordering
    ]

    # --- Snapshot of the current scenario state that the operator-selected
    # plan simulator (calculate_selected_plan) will start from. This embeds
    # the derived operating signals so multi-action evaluation can update the
    # state step by step without re-deriving (and keeps the ML feature vector
    # consistent across steps).
    initial_state = {
        "mine_name": mine_name,
        "source_pocket": {
            "name": source_pocket["name"],
            "grade_pct": source_pocket.get("grade_pct"),
            "mine_name": source_pocket.get("mine_name"),
        },
        "target_pocket": (
            {"name": target_pocket["name"], "grade_pct": target_pocket.get("grade_pct"),
             "mine_name": target_pocket.get("mine_name")}
            if target_pocket else None
        ),
        "price_per_tonne_inr": float(source_price["price_per_tonne_inr"]),
        "stockpile_available_tonnes": (
            _resource_percentile(bundle, "BLENDING", "stockpile_available_tonnes", "median", 200.0)
            if bundle is not None else 200.0
        ),
        # All rows below are the derived operating signals the ML feature
        # builder reads (rainfall, downtime, labor, volumes, grades, ...).
        **{k: v for k, v in signals.items()},
    }

    recommendation = {
        # --- Top-level compatibility field expected by the existing app.py ---
        "recoverable_tonnage": recovered_tonnage,

        # --- Primary recommended action (for the action card) ---
        "recommended_action": primary["action"],
        "reason": primary["reason"],
        "expected_recovery_tonnes": recovered_tonnage,
        "remaining_shortfall_tonnes": round(remaining_shortfall, 2),
        "resource_allocation_pct": primary["resources_allocated"],
        "estimated_action_duration_hours": primary["duration_hours"],
        "action_intensity": primary["action_intensity"],
        "recovery_status": "DELAYED_UNTIL_DEWATERING_COMPLETE" if primary["action"] == "DEWATERING" else "PENDING_EXECUTION",
        "is_simulated": True,

        # --- Grade / pit context (for the Customer Contract Ledger downstream) ---
        "source_pit": source_pocket["name"],
        "source_grade_pct": source_pocket.get("grade_pct"),
        "target_pit": target_pocket["name"] if target_pocket else None,
        "target_grade_pct": target_pocket.get("grade_pct") if target_pocket else None,
        "grade_delta_pct": grade_delta_pct,
        "for_ledger": {
            "source_pit": source_pocket["name"],
            "source_grade_pct": source_pocket.get("grade_pct"),
            "target_pit": target_pocket["name"] if target_pocket else None,
            "target_grade_pct": target_pocket.get("grade_pct") if target_pocket else None,
            "recovered_tonnage": recovered_tonnage,
            "remaining_shortfall_tonnage": round(remaining_shortfall, 2),
            "source_price_reference_inr_per_tonne": source_price,
            "target_price_reference_inr_per_tonne": target_price,
            "note": "M4 supplies volume + grade context only. Final ₹ Revenue-at-Risk / Revenue-Saved "
                    "calculation is owned by the Customer Contract Ledger "
                    "(modules/customers.py / _customer_portfolio() in app.py).",
        },

        # --- Phased recovery context (Problem 3) ---
        "immediate_action": {"action": primary["action"], "effect": "Applied immediately on Execute Plan."},
        "background_dewatering": background_dewatering,

        # --- Model / confidence info (kept simple for the UI) ---
        "model_info": {
            "model_type": (bundle or {}).get("tonnage_model_type", "unavailable"),
            "avg_prediction_error_tonnes": (bundle or {}).get("metrics", {}).get("tonnage_model", {}).get("MAE"),
            "r2": (bundle or {}).get("metrics", {}).get("tonnage_model", {}).get("R2"),
            "used_ml_model": primary["used_ml_model"],
            "trained_on_synthetic_data": (bundle or {}).get("dataset_is_synthetic", True),
        },

        # --- Ranked alternatives for transparency ---
        "alternatives": [
            {
                "action": e["action"],
                "reason": e["reason"],
                "expected_recovery_tonnes": e["recovered_tonnage"],
                "resource_allocation_pct": e["resources_allocated"],
                "estimated_action_duration_hours": e["duration_hours"],
                "used_ml_model": e["used_ml_model"],
            }
            for e in alternatives
        ],

        # --- HITL: candidate options for the operator to choose from ---
        "plan_type": "ai_recommendation",
        "options": options,

        # --- Internal snapshot consumed by calculate_selected_plan() to run
        # the operator-selected plan simulation (documented, not user-facing).
        "scenario_state": initial_state,

        "assumptions": assumptions,
    }
    return recommendation


# --------------------------------------------------------------------------
# PUBLIC API — 2. calculate_selected_plan
# (operator-chosen actions, evaluated sequentially against an updated state)
# --------------------------------------------------------------------------

def _normalize_selected_actions(selected_actions: Optional[List[str]]) -> List[str]:
    """Returns a clean, deduplicated, ordered list of valid action codes from
    whatever the caller passed (checkboxes, multiselect, or a bare string).
    Unknown/empty entries are dropped. The original order is preserved because
    order defines the evaluation/execution sequence.
    """
    valid = set(ACTION_RULE_EXPLANATIONS)
    out: List[str] = []
    for entry in (selected_actions or []):
        if not isinstance(entry, str):
            continue
        code = entry.strip().upper()
        if code in valid and code not in out:
            out.append(code)
    return out


def _apply_action_state_change(state: Dict[str, Any], action: str,
                               recovered_tonnes: float) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Simulates the influence of executing `action` on the operating state
    dict that will be used to evaluate the NEXT selected action in the
    operator's plan.

    IMPORTANT: these are explicit prototype assumptions for the sequential
    plan simulation. They were NOT learned from real mine data and are NOT a
    claim about learned action-to-action causal effects. Every mutated field
    is clamped to a physically plausible range (floor 0 / ceiling 100%) so the
    simulation can never produce impossible values.

    Returns (updated_state_copy, changes) where `changes` records the
    before/after value (and a one-line rationale) for each touched field so
    the plan output stays transparent to the operator.
    """
    s = dict(state)
    changes: Dict[str, Any] = {}

    def _record(field: str, before: float, after: float, note: str) -> None:
        changes[field] = {
            "before": round(float(before), 3),
            "after": round(float(after), 3),
            "note": note,
        }

    if action == "DEWATERING":
        # Reduce water/rain-related severity and improve the effective
        # operating condition (rain-driven disruption drops). Never negative.
        before = float(s.get("rainfall_mm", 0.0))
        after = max(0.0, before * _DEWATERING_RAIN_REMAINING_FRACTION)
        s["rainfall_mm"] = after
        _record(
            "rainfall_mm", before, after,
            "Dewatering reduces standing-water/rain-derived severity; the effective operating "
            "condition improves because rainfall-related disruption drops. Prototype assumption only.",
        )

    elif action == "PREVENTIVE_MAINTENANCE":
        # Reduce equipment downtime (never below 0) and restore availability.
        before = float(s.get("equipment_downtime_hours", 0.0))
        after = max(0.0, before * _MAINTENANCE_DOWNTIME_REMAINING_FRACTION)
        s["equipment_downtime_hours"] = after
        s["downtime_3d_hours"] = after * 3.0
        _record(
            "equipment_downtime_hours", before, after,
            "Preventive maintenance reduces expected equipment downtime; floor is 0 hours (never negative).",
        )
        avail_before = float(s.get("available_equipment_pct", 0.0))
        s["available_equipment_pct"] = min(100.0, max(avail_before, _IMPROVED_AVAILABILITY_FLOOR_PCT))
        _record(
            "available_equipment_pct", avail_before, s["available_equipment_pct"],
            "Serviced equipment is restored to at least 95% availability; capped at 100%.",
        )

    elif action == "REROUTE_FLEET":
        # Improve effective fleet/transport availability (the reroute avoids
        # the disrupted route); never produce negative downtime values.
        before = float(s.get("equipment_downtime_hours", 0.0))
        after = max(0.0, before * _REROUTE_DOWNTIME_REMAINING_FRACTION)
        s["equipment_downtime_hours"] = after
        s["downtime_3d_hours"] = after * 3.0
        _record(
            "equipment_downtime_hours", before, after,
            "Fleet rerouting improves effective fleet/transport availability, lowering the disruption penalty.",
        )
        avail_before = float(s.get("available_equipment_pct", 0.0))
        s["available_equipment_pct"] = min(100.0, max(avail_before, _IMPROVED_AVAILABILITY_FLOOR_PCT))
        _record(
            "available_equipment_pct", avail_before, s["available_equipment_pct"],
            "Effective transport availability improves; capped at 100%.",
        )

    elif action == "CONTINGENCY_LABOR":
        # Improve labor availability; labor_drop_pct can never go below 0.
        before = float(s.get("labor_drop_pct", 0.0))
        after = max(0.0, before * _LABOR_DROP_REMAINING_FRACTION)
        s["labor_drop_pct"] = after
        s["labor_available_pct_signal"] = max(0.0, min(100.0, 100.0 - after))
        _record(
            "labor_drop_pct", before, after,
            "Additional temporary labor reduces the labor shortfall; floor is 0% (never negative).",
        )
        _record(
            "labor_available_pct_signal", 100.0 - before, s["labor_available_pct_signal"],
            "Labor availability recomputed from the reduced labor drop; capped at 100%.",
        )

    elif action == "BLENDING":
        # Consume available stockpile; never allow it below zero. Prototype
        # 1:1 assumption: each tonne of blending-derived recovery consumes
        # one tonne of stockpile (a simplified approximation for the demo).
        before = float(s.get("stockpile_available_tonnes", 0.0))
        after = max(0.0, before - float(recovered_tonnes or 0.0))
        s["stockpile_available_tonnes"] = after
        _record(
            "stockpile_available_tonnes", before, after,
            "Blending consumes available stockpile; floor is 0 tonnes (never negative). "
            "Prototype 1:1 assumption, not a learned relationship.",
        )

    # Keep shortfall bookkeeping coherent at every step.
    s["shortfall_tonnage"] = max(0.0, float(s.get("shortfall_tonnage", 0.0)))
    target = float(s.get("target_rom_tonnes", 0.0))
    s["shortfall_pct"] = (s["shortfall_tonnage"] / target * 100.0) if target > 0 else 0.0
    return s, changes


def calculate_selected_plan(plan_recommendation: Dict[str, Any],
                            prediction: Optional[Dict[str, Any]],
                            selected_actions: Optional[List[str]],
                            risk: Optional[Dict[str, Any]] = None,
                            ore_pockets: Optional[List[Any]] = None) -> Dict[str, Any]:
    """Evaluates the OPERATOR-CHOSEN action(s) sequentially and returns the
    expected outcome of that selected plan.

    Flow (AI recommends → user decides → system simulates):
      * Start from the current scenario state (captured by
        generate_recommendations() under "scenario_state"; if absent it is
        re-derived from `prediction`/`risk`/`ore_pockets`).
      * For each selected action (in user order):
          - ask the ML model for the expected recovery against the CURRENT
            state, cap it at the current remaining shortfall (never negative,
            never more than what is left),
          - subtract it from the remaining shortfall,
          - apply the documented prototype state update so the NEXT action is
            evaluated against the improved (plausibly-constrained) state.
      * Independent ML predictions are deliberately NOT blindly summed — each
        action is evaluated after the previous one's state change, and the AI
        never adds actions the operator did not choose.

    Returns (always pure compute — no Streamlit calls, safe for app.py to use
    inside a checkbox/multiselect UI):
        {"plan_type": "operator_selected", "recommended_action": ...,
         "selected_actions": [...], "action_sequence": [...],
         "total_expected_recovery_tonnes": ..., "final_remaining_shortfall": ...,
         "total_action_cost": ..., "revenue_saved": ..., "net_benefit": ...,
         "recoverable_tonnage": ..., "expected_recovery_tonnes": ...,
         "remaining_shortfall": ..., "is_simulated": True, "assumptions": [...]}
    """
    plan_recommendation = plan_recommendation or {}
    prediction = prediction or {}
    selected = _normalize_selected_actions(selected_actions)

    bundle, model_error = _get_model_bundle()

    # --- Recover the starting state (prefer the snapshot the recommender
    # embedded; fall back to re-deriving when a homemade dict is passed). ---
    state_source = plan_recommendation.get("scenario_state")
    if not state_source:
        # Minimal fallback state construction (no scenario_state was stored).
        risk = risk or {}
        signals = _derive_operating_signals(prediction, risk)
        source_pocket, target_pocket, _ = _select_source_and_target(list(ore_pockets or []))
        signals["avg_mn_grade_pct"] = source_pocket.get("grade_pct", 38.0)
        mine_name, _ = _resolve_mine_name(bundle or {"mine_list": []}, signals, source_pocket)
        state_source = {
            "mine_name": mine_name,
            "source_pocket": source_pocket,
            "target_pocket": target_pocket,
            "price_per_tonne_inr": float(_grade_price_reference(source_pocket.get("grade_pct"))["price_per_tonne_inr"]),
            "stockpile_available_tonnes": (
                _resource_percentile(bundle, "BLENDING", "stockpile_available_tonnes", "median", 200.0)
                if bundle is not None else 200.0
            ),
            **dict(signals),
        }

    state = dict(state_source)
    options_lookup = {
        (o.get("action") or "").upper(): o for o in (plan_recommendation.get("options") or [])
    }

    # --- Initial figures ---
    shortfall = max(0.0, float(state.get("shortfall_tonnage") or prediction.get("shortfall_tonnage") or 0.0))
    target = max(0.0, float(state.get("target_rom_tonnes") or (float(prediction.get("predicted_tonnage") or 0.0) + shortfall)))
    state.setdefault("target_rom_tonnes", target)
    mine_name = str(state.get("mine_name") or "")
    price_per_tonne = float(state.get("price_per_tonne_inr") or 0.0)
    source_pocket = state.get("source_pocket") or {}

    # --- Sequential evaluation of the operator's chosen actions ---
    action_sequence: List[Dict[str, Any]] = []
    remaining = shortfall
    for idx, action in enumerate(selected, start=1):
        remaining = max(0.0, remaining)
        state["shortfall_tonnage"] = remaining
        target = max(0.0, float(state.get("target_rom_tonnes") or 0.0))
        state["shortfall_pct"] = (remaining / target * 100.0) if target > 0 else 0.0

        # 1) ML estimate against the CURRENT (possibly updated) state.
        best = _best_recovery(bundle, mine_name, state, action)
        recovery = max(0.0, float(best.get("recovered_tonnage") or 0.0))

        # 2) Recovery safety: never exceed the remaining shortfall.
        recovery = min(recovery, remaining)

        # 3) Apply estimated recovery, then update the state for the next step.
        remaining = max(0.0, remaining - recovery)
        state, state_changes = _apply_action_state_change(state, action, recovery)

        option = options_lookup.get(action, {})
        reason = option.get("reason") or ACTION_RULE_EXPLANATIONS.get(action, action)
        scenario_reason = option.get("scenario_reason") or reason

        # DEWATERING is not an immediate recovery (Problem 3): it clears in
        # the background. The plan still records the estimate; apply_plan()
        # decides how much counts as "recovered now" via the phase timeline.
        status = "DELAYED_UNTIL_DEWATERING_COMPLETE" if action == "DEWATERING" else "IMMEDIATE"

        action_sequence.append({
            "step": idx,
            "action": action,
            "expected_recovery_tonnes": round(recovery, 2),
            "remaining_shortfall": round(remaining, 2),
            "reason": reason,
            "scenario_reason": scenario_reason,
            "state_changes": state_changes,
            "used_ml_model": best.get("used_ml_model", False),
            "resources_allocated": best.get("resources_allocated", 0.0),
            "duration_hours": best.get("duration_hours", 0.0),
            "action_intensity": best.get("action_intensity", 0.0),
            "recovery_status": status,
            "action_cost": ACTION_PROTOTYPE_COST_INR.get(action, 0.0),
        })

    total_recovery = sum(round(s["expected_recovery_tonnes"], 2) for s in action_sequence)
    total_cost = sum(float(s.get("action_cost") or 0.0) for s in action_sequence)
    # Revenue uses the module's existing prototype grade-price reference
    # (source-pit band). No new/made-up market price is introduced here; the
    # Customer Contract Ledger (modules/customers.py / _customer_portfolio()
    # in app.py) owns the final ₹ figure.
    revenue_saved = total_recovery * price_per_tonne
    net_benefit = revenue_saved - total_cost

    assumptions = [
        "selected-actions evaluated sequentially, each against the state left by the previous action; "
        "independent ML predictions are intentionally NOT blindly summed.",
        "state updates between actions are explicit prototype assumptions (DEWATERING lowers rain severity, "
        "PREVENTIVE_MAINTENANCE/REROUTE_FLEET lower downtime, CONTINGENCY_LABOR lowers the labor drop, "
        "BLENDING consumes stockpile) — they are NOT learned causal effects.",
        "every state value is clamped (floor 0 / ceiling 100%) and any predicted recovery above the current "
        "remaining shortfall is capped at that shortfall.",
        "action costs are prototype medians from the M4 prototype dataset, not official MOIL figures.",
        "revenue_saved = total expected recovery x prototype grade-price reference for the source pit; "
        "final ₹ reckoning belongs to the Customer Contract Ledger.",
    ]
    if bundle is None and model_error:
        assumptions.append(f"ML model unavailable ({model_error}); recovery used the rule-of-thumb fallback.")

    return {
        "plan_type": "operator_selected",
        "recommended_action": plan_recommendation.get("recommended_action"),
        "selected_actions": list(selected),
        "action_sequence": action_sequence,
        "total_expected_recovery_tonnes": round(total_recovery, 2),
        "final_remaining_shortfall": round(remaining, 2),
        "total_action_cost": round(total_cost, 2),
        "revenue_saved": round(revenue_saved, 2),
        "net_benefit": round(net_benefit, 2),
        # Backward-compatible top-level aliases (app.py / ledgers read these).
        "recoverable_tonnage": round(total_recovery, 2),
        "expected_recovery_tonnes": round(total_recovery, 2),
        "remaining_shortfall": round(remaining, 2),
        "shortfall_tonnage": shortfall,
        "source_pit": (source_pocket or {}).get("name"),
        "source_grade_pct": (source_pocket or {}).get("grade_pct"),
        "is_simulated": True,
        "assumptions": assumptions,
    }


# --------------------------------------------------------------------------
# PUBLIC API — 3. apply_plan  ("Execute Plan" interaction + phased state)
# --------------------------------------------------------------------------

def _human_action_label(recommendation: Dict[str, Any]) -> str:
    """Human-readable, pit-aware label for the recommended action. Derived
    from ACTION_DISPLAY_LABELS, with the concrete source/target pit injected
    for pit-specific actions. Internal action codes are never replaced — only
    the wording shown to operators changes.
    """
    action_code = recommendation.get("recommended_action")
    label = ACTION_DISPLAY_LABELS.get(action_code, action_code)
    if action_code == "REROUTE_FLEET":
        target = recommendation.get("target_pit")
        if target:
            label = f"Move dumpers to {target}"
    elif action_code == "DEWATERING":
        source = recommendation.get("source_pit")
        if source:
            label = f"Pump water from {source}"
    return label


def _render_action_card(recommendation: Dict[str, Any]) -> None:
    st.markdown("#### 🤖 AI-Optimized Plan")

    # Main headline uses the human-readable action label, with the concrete
    # pit names for the pit-specific actions (internal codes stay unchanged).
    headline = _human_action_label(recommendation)
    st.markdown(f"**{headline}**")
    st.caption(recommendation["reason"])

    c1, c2 = st.columns(2)
    c1.metric("Expected Recovery", f"+{recommendation['expected_recovery_tonnes']:.1f} t")
    c2.metric("Remaining Shortfall", f"{recommendation['remaining_shortfall_tonnes']:.1f} t")

    c3, c4 = st.columns(2)
    src_grade = recommendation.get("source_grade_pct")
    tgt_grade = recommendation.get("target_grade_pct")
    c3.metric("Source Grade", f"{src_grade:.0f}% Mn" if src_grade is not None else "N/A")
    c4.metric("Target Grade", f"{tgt_grade:.0f}% Mn" if tgt_grade is not None else "N/A")

    st.caption(
        f"Resource allocation: {recommendation['resource_allocation_pct']:.0f}% · "
        f"Duration: {recommendation['estimated_action_duration_hours']:.1f} hrs"
    )

    bg = recommendation.get("background_dewatering")
    if bg:
        st.caption(
            f"The fleet move happens immediately. Water removal at {bg['pit']} starts once "
            f"the plan is executed — estimated clearance: {bg['estimated_clearance_hours']:.1f} hours"
        )

    if recommendation.get("alternatives"):
        with st.expander("Other actions under consideration"):
            for alt in recommendation["alternatives"]:
                label = ACTION_DISPLAY_LABELS.get(alt["action"], alt["action"])
                st.markdown(
                    f"- **{label}** — +{alt['expected_recovery_tonnes']:.1f} t. {alt['reason']}"
                )

    # Technical validation detail is kept available for engineers/judges but
    # kept out of the main worker-facing recommendation.
    with st.expander("Model / technical details (for engineers)"):
        mi = recommendation.get("model_info") or {}
        if mi.get("used_ml_model"):
            st.caption(
                f"Estimate produced by the trained model ({mi.get('model_type', 'unavailable')})."
            )
        else:
            st.caption("Model unavailable — estimate came from a rule-of-thumb fallback.")
        if mi.get("avg_prediction_error_tonnes") is not None:
            st.caption(
                f"Average prediction error: ~{mi['avg_prediction_error_tonnes']:.2f} tonnes "
                f"(R²: {mi.get('r2', 0):.2f}) — prototype model trained/evaluated on synthetic intervention outcomes."
            )

    if recommendation.get("assumptions"):
        with st.expander("Prototype assumptions used for this recommendation"):
            for note in recommendation["assumptions"]:
                st.caption(f"• {note}")


def _dewatering_phase(elapsed_sim_hours: float, clearance_hours: float) -> Tuple[str, float]:
    if clearance_hours <= 0:
        return "CLEARED", 0.0
    remaining = max(0.0, clearance_hours - elapsed_sim_hours)
    if elapsed_sim_hours <= 0:
        return "FLOODED", clearance_hours
    if remaining <= 0:
        return "CLEARED", 0.0
    return "DEWATERING_IN_PROGRESS", remaining


def apply_plan(recommendation: Dict[str, Any], prediction: Optional[Dict[str, Any]],
               selected_actions: Optional[List[str]] = None) -> Dict[str, Any]:
    """Renders the 'Execute Plan' action card + button and tracks a simulated
    execution state. Demo-safe prototype ONLY — it never controls any real
    equipment or external system.

    Supported plans:
      * selected_actions given (HITL multiselect) -> simulates the operator's
        chosen actions sequentially (via calculate_selected_plan), honoring
        the dewatering background timeline for DEWATERING steps.
      * selected_actions omitted (existing two-arg call) -> defaults to the
        AI's single top suggestion, exactly like the previous behaviour.

    Returns dict (compatible with the original contract, extended):
        {"execute_clicked": bool, "plan_executed": bool,
         "executed_actions": [...], "per_action_recovery": [...],
         "recovered_tonnage": float, "remaining_shortfall": float,
         "dewatering_status": dict | None, "plan": dict | None,
         "is_simulated": True}
    """
    recommendation = recommendation or {}
    prediction = prediction or {}
    shortfall_tonnage = float(prediction.get("shortfall_tonnage") or 0.0)

    selected = _normalize_selected_actions(selected_actions)
    if not selected:
        # Backward-compatible default: if the UI did not ask for specific
        # actions, fall back to the AI top suggestion (highlight only).
        top = recommendation.get("recommended_action")
        if top:
            selected = [top]

    _render_action_card(recommendation)

    if "m4_plan_executed" not in st.session_state:
        st.session_state["m4_plan_executed"] = False
        st.session_state["m4_execution_start_ts"] = None
        st.session_state["m4_execution_snapshot"] = None
        st.session_state["m4_selected_actions"] = []

    execute_clicked = False
    button_label = "🔁 Reset Simulation" if st.session_state["m4_plan_executed"] else "▶ Execute Plan"
    if st.button(button_label, key="m4_execute_plan_button"):
        if st.session_state["m4_plan_executed"]:
            st.session_state["m4_plan_executed"] = False
            st.session_state["m4_execution_start_ts"] = None
            st.session_state["m4_execution_snapshot"] = None
            st.session_state["m4_selected_actions"] = []
        else:
            st.session_state["m4_plan_executed"] = True
            st.session_state["m4_execution_start_ts"] = time.time()
            st.session_state["m4_execution_snapshot"] = recommendation
            st.session_state["m4_selected_actions"] = list(selected)
            execute_clicked = True

    plan_executed = st.session_state["m4_plan_executed"]
    snapshot = st.session_state["m4_execution_snapshot"] or recommendation
    executed_selection = st.session_state.get("m4_selected_actions") or selected

    dewatering_status = None
    bg = snapshot.get("background_dewatering") if plan_executed else recommendation.get("background_dewatering")
    if bg:
        clearance_hours = float(bg.get("estimated_clearance_hours", 0.0))
        if plan_executed and st.session_state["m4_execution_start_ts"]:
            elapsed_real_s = time.time() - st.session_state["m4_execution_start_ts"]
            elapsed_sim_hours = elapsed_real_s / _DEMO_SECONDS_PER_SIMULATED_HOUR
            phase, remaining_hours = _dewatering_phase(elapsed_sim_hours, clearance_hours)
        else:
            phase, remaining_hours = "FLOODED", clearance_hours
        dewatering_status = {
            "pit": bg["pit"],
            "phase": phase,
            "pump_status": "active" if phase == "DEWATERING_IN_PROGRESS" else ("idle" if phase == "FLOODED" else "complete"),
            "estimated_clearance_hours_remaining": round(remaining_hours, 2),
            "is_simulated": True,
            "note": "Simulated using elapsed time compression for demo purposes; no real equipment is controlled.",
        }

    computed_plan: Optional[Dict[str, Any]] = None
    per_action_recovery: List[Dict[str, Any]] = []
    executed_actions: List[str] = []
    recovered_tonnage = 0.0

    if plan_executed:
        # Evaluate the operator-selected plan against the captured scenario
        # state (guarded: the plan calculator is compute-only and should never
        # crash the app; on any unexpected failure we fall back to the legacy
        # single-action behaviour).
        try:
            computed_plan = calculate_selected_plan(
                snapshot, prediction, executed_selection,
            )
        except Exception:  # noqa: BLE001
            computed_plan = None

        if computed_plan and computed_plan.get("action_sequence"):
            sequence = computed_plan["action_sequence"]
            executed_actions = [s["action"] for s in sequence]
            dewatering_pending = (
                dewatering_status is not None
                and any(s["action"] == "DEWATERING" for s in sequence)
                and dewatering_status["phase"] != "CLEARED"
            )
            # DEWATERING recovery is only counted once the simulated phase
            # reaches CLEARED; every other selected action counts immediately.
            for step in sequence:
                if step["action"] == "DEWATERING" and dewatering_pending:
                    continue
                recovered_tonnage += float(step["expected_recovery_tonnes"])
                per_action_recovery.append({
                    "action": step["action"],
                    "expected_recovery_tonnes": round(float(step["expected_recovery_tonnes"]), 2),
                    "remaining_shortfall": step["remaining_shortfall"],
                    "reason": step.get("scenario_reason") or step.get("reason"),
                    "action_cost": step.get("action_cost", 0.0),
                    "recovery_status": step.get("recovery_status"),
                    "used_ml_model": step.get("used_ml_model", False),
                })
            recovered_tonnage = max(0.0, min(recovered_tonnage, shortfall_tonnage))

            labels = ", ".join(ACTION_DISPLAY_LABELS.get(a, a) for a in executed_actions) or "—"
            st.success(f"Operator-selected plan executed (simulated): {labels}.")
            if dewatering_pending:
                st.caption("Dewatering recovery will be counted after the simulated clearance completes.")
            elif recovered_tonnage > 0:
                st.caption(
                    f"+{recovered_tonnage:.1f} t expected recovery across {len(sequence)} action(s). "
                    f"Total plan recovery: +{computed_plan['total_expected_recovery_tonnes']:.1f} t."
                )
        else:
            # Legacy single-action fallback (defensive).
            recommended_action = snapshot.get("recommended_action")
            dewatering_pending = (
                recommended_action == "DEWATERING"
                and dewatering_status is not None
                and dewatering_status["phase"] != "CLEARED"
            )
            recovered_tonnage = 0.0 if dewatering_pending else float(snapshot.get("expected_recovery_tonnes", 0.0))
            recovered_tonnage = max(0.0, min(recovered_tonnage, shortfall_tonnage))
            executed_actions = [recommended_action] if recommended_action else []
            per_action_recovery = [{
                "action": recommended_action,
                "expected_recovery_tonnes": round(recovered_tonnage, 2),
                "remaining_shortfall": round(shortfall_tonnage - recovered_tonnage, 2),
                "reason": snapshot.get("reason"), "action_cost": 0.0,
                "used_ml_model": (snapshot.get("model_info") or {}).get("used_ml_model", False),
            }]
            st.success(f"Plan executed (simulated): {_human_action_label(snapshot)}.")
            if dewatering_pending:
                st.caption("Recovery will be counted after dewatering is complete.")
            elif recovered_tonnage > 0:
                st.caption(f"+{recovered_tonnage:.1f} t expected recovery")

        if dewatering_status:
            pit = dewatering_status["pit"]
            phase = dewatering_status["phase"]
            remaining_hrs = dewatering_status["estimated_clearance_hours_remaining"]
            if phase == "DEWATERING_IN_PROGRESS":
                message = f"Dewatering in progress at {pit} — {remaining_hrs:.1f} hours remaining"
            elif phase == "CLEARED":
                message = f"{pit} cleared — operations can resume"
            else:  # FLOODED
                message = f"{pit} is flooded — waiting for dewatering"
            st.info(message)
            st.caption(
                f"Demo time compression: {_DEMO_SECONDS_PER_SIMULATED_HOUR:g} seconds = 1 simulated hour"
            )

    remaining_shortfall = max(0.0, shortfall_tonnage - recovered_tonnage)

    return {
        "execute_clicked": execute_clicked,
        "plan_executed": plan_executed,
        "executed_actions": executed_actions,
        "per_action_recovery": per_action_recovery,
        "recovered_tonnage": round(recovered_tonnage, 2),
        "remaining_shortfall": round(remaining_shortfall, 2),
        "dewatering_status": dewatering_status,
        "plan": computed_plan,
        "is_simulated": True,
    }  
