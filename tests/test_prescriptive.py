"""
Temporary manual test for M4 Prescriptive Planning Engine.

This tests:
1. Heavy rainfall / flood scenario
2. Normal weather scenario
3. Equipment downtime scenario
4. Labor shortage scenario
5. Execute Plan + simulated dewatering timeline

This file does NOT modify production code, models, or datasets.
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
import streamlit as st

from modules.prescriptive import (
    generate_recommendations,
    apply_plan,
)


def run_scenario(title, prediction, risk, ore_pockets):
    st.markdown("---")
    st.header(title)

    recommendation = generate_recommendations(
        prediction,
        risk,
        ore_pockets
    )

    st.subheader("Recommended Action")

    st.success(
        recommendation.get(
            "recommended_action",
            "No recommendation"
        )
    )

    st.write(
        "**Reason:**",
        recommendation.get("reason", "N/A")
    )

    col1, col2 = st.columns(2)

    with col1:
        st.metric(
            "Expected Recovery",
            f"+{recommendation.get('expected_recovery_tonnes', 0):.2f} tonnes"
        )

    with col2:
        st.metric(
            "Remaining Shortfall",
            f"{recommendation.get('remaining_shortfall_tonnes', 0):.2f} tonnes"
        )

    st.write(
        "**Source Pit:**",
        recommendation.get("source_pit")
    )

    st.write(
        "**Source Grade:**",
        f"{recommendation.get('source_grade_pct')}% Mn"
    )

    st.write(
        "**Alternate Pit:**",
        recommendation.get("target_pit")
    )

    st.write(
        "**Alternate Grade:**",
        (
            f"{recommendation.get('target_grade_pct')}% Mn"
            if recommendation.get("target_grade_pct") is not None
            else "N/A"
        )
    )

    # Dewatering information
    dewatering = recommendation.get("background_dewatering")

    if dewatering:
        st.info(
            "Dewatering: "
            f"{dewatering['pit']} requires water removal. "
            f"Estimated clearance time: "
            f"{dewatering['estimated_clearance_hours']:.1f} hours."
        )

    # Alternatives
    alternatives = recommendation.get("alternatives", [])

    if alternatives:
        st.subheader("Other Actions Considered")

        for alternative in alternatives:
            st.write(
                f"• {alternative['action']} — "
                f"Expected recovery: "
                f"+{alternative['expected_recovery_tonnes']:.2f} tonnes"
            )

    # Model information
    model_info = recommendation.get("model_info", {})

    with st.expander("Technical Model Information"):
        st.write(
            "ML model used:",
            model_info.get("used_ml_model")
        )

        st.write(
            "Model:",
            model_info.get("model_type")
        )

        st.write(
            "MAE:",
            model_info.get("avg_prediction_error_tonnes")
        )

        st.write(
            "R²:",
            model_info.get("r2")
        )

    return recommendation


# ============================================================
# TEST DATA
# ============================================================

ore_pockets = [
    {
        "name": "Pit 1",
        "grade_pct": 46.0,
        "mine_name": None
    },
    {
        "name": "Pit B",
        "grade_pct": 38.0,
        "mine_name": None
    }
]


# ============================================================
# SCENARIO 1 — HEAVY RAINFALL / FLOOD
# ============================================================

prediction_1 = {
    "predicted_tonnage": 850.0,
    "shortfall_tonnage": 150.0,
    "penalties": {
        "rain": 0.35,
        "mtbf": 0.05,
        "labor": 0.05,
    },
}

risk_1 = {
    "ori": 80.0,
    "rainfall_mm": 45.0,
    "mtbf_hrs": 125.0,
    "labor_drop_pct": 20.0,
}


# ============================================================
# SCENARIO 2 — NORMAL WEATHER
# ============================================================

prediction_2 = {
    "predicted_tonnage": 950.0,
    "shortfall_tonnage": 50.0,
    "penalties": {
        "rain": 0.0,
        "mtbf": 0.0,
        "labor": 0.0,
    },
}

risk_2 = {
    "ori": 5.0,
    "rainfall_mm": 0.0,
    "mtbf_hrs": 150.0,
    "labor_drop_pct": 0.0,
}


# ============================================================
# SCENARIO 3 — EQUIPMENT DOWNTIME
# ============================================================

prediction_3 = {
    "predicted_tonnage": 820.0,
    "shortfall_tonnage": 180.0,
    "penalties": {
        "rain": 0.0,
        "mtbf": 0.20,
        "labor": 0.0,
    },
}

risk_3 = {
    "ori": 30.0,
    "rainfall_mm": 0.0,
    "mtbf_hrs": 50.0,
    "labor_drop_pct": 0.0,
}


# ============================================================
# SCENARIO 4 — LABOR SHORTAGE
# ============================================================

prediction_4 = {
    "predicted_tonnage": 825.0,
    "shortfall_tonnage": 175.0,
    "penalties": {
        "rain": 0.0,
        "mtbf": 0.0,
        "labor": 0.20,
    },
}

risk_4 = {
    "ori": 25.0,
    "rainfall_mm": 0.0,
    "mtbf_hrs": 150.0,
    "labor_drop_pct": 80.0,
}


# ============================================================
# RUN TESTS
# ============================================================

st.title("⛏️ M4 Prescriptive Planning Engine")

st.caption(
    "Temporary backend validation — "
    "no production code, ML models, or datasets are modified."
)

rec1 = run_scenario(
    "🌧️ Scenario 1 — Heavy Rainfall / Flood",
    prediction_1,
    risk_1,
    ore_pockets
)

rec2 = run_scenario(
    "☀️ Scenario 2 — Normal Weather",
    prediction_2,
    risk_2,
    ore_pockets
)

rec3 = run_scenario(
    "🔧 Scenario 3 — Equipment Downtime",
    prediction_3,
    risk_3,
    ore_pockets
)

rec4 = run_scenario(
    "👷 Scenario 4 — Labor Shortage",
    prediction_4,
    risk_4,
    ore_pockets
)


# ============================================================
# EXECUTION TEST
# ============================================================

st.markdown("---")
st.header("▶ Execute Plan Test")

st.caption(
    "Uses Scenario 1 recommendation to test apply_plan()."
)

result = apply_plan(
    rec1,
    prediction_1
)

st.subheader("Execution Result")

st.write(
    "**Execute clicked:**",
    result["execute_clicked"]
)

st.write(
    "**Plan executed:**",
    result["plan_executed"]
)

st.write(
    "**Recovered tonnage:**",
    f"{result['recovered_tonnage']:.2f} tonnes"
)

st.write(
    "**Remaining shortfall:**",
    f"{result['remaining_shortfall']:.2f} tonnes"
)

if result.get("dewatering_status"):

    st.subheader("💧 Dewatering Status")

    status = result["dewatering_status"]

    st.write(
        "**Pit:**",
        status["pit"]
    )

    st.write(
        "**Phase:**",
        status["phase"]
    )

    st.write(
        "**Pump status:**",
        status["pump_status"]
    )

    st.write(
        "**Estimated hours remaining:**",
        status["estimated_clearance_hours_remaining"]
    )

    st.caption(status["note"])


st.markdown("---")
st.success("M4 testing completed.")