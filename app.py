# app.py
# Central Integration Shell & UI Controller for MOIL-GeoMind Platform

import streamlit as st
import constants as C

# Import individual member modules
import modules.spatial as spatial
import modules.prediction as prediction
import modules.prescriptive as prescriptive
import modules.spectral as spectral
import modules.weather as ops  # Operational risk & Rupee Loss Ledger
import modules.xai as xai       # Member 6: XAI & Confidence Engine

# --- PAGE CONFIGURATION ---
st.set_page_config(
    page_title=C.APP_TITLE,
    page_icon="⛏️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- SESSION STATE INITIALIZATION ---
if "execution_state" not in st.session_state:
    st.session_state["execution_state"] = False

# --- HEADER & TITLE ---
st.title(f"⛏️ {C.APP_TITLE}")
st.caption("AI-Heuristic Decision Support System for MOIL Ore Target Identification & Production Safeguarding")

# --- SIDEBAR: GLOBAL OPERATIONAL CONTROLS (Member 5 UI) ---
st.sidebar.header("🕹️ Field Risk Parameters")
risk_inputs = ops.render_risk_sliders(st.sidebar)

# Compute Operational Risk Index (ORI)
ori_results = ops.risk_summary(
    risk_inputs["rainfall_mm"],
    risk_inputs["mtbf_hrs"],
    risk_inputs["labor_drop_pct"]
)

# Sidebar Risk Readout
st.sidebar.markdown("---")
st.sidebar.metric(
    label="Operational Risk Index (ORI)",
    value=f"{ori_results['ori']:.1f} / 100",
    delta="High Risk" if ori_results['ori'] > 50 else "Normal",
    delta_color="inverse"
)

# --- PIPELINE COMPUTATIONS ---
# 1. Shortfall Prediction (Member 3)
pred_results = prediction.predict_weekly_tonnage(
    base_target=C.BASE_WEEKLY_TARGET_TONS,
    rainfall_mm=risk_inputs["rainfall_mm"],
    mtbf_hrs=risk_inputs["mtbf_hrs"],
    labor_drop_pct=risk_inputs["labor_drop_pct"]
)

# 2. Spectral Matching (Member 2)
# Simulating reflectance input from target 0 for baseline execution
spec_results = spectral.spectral_match(
    earth_reflectance=C.SAMPLE_EARTH_SPECTRA,
    isro_baseline=C.ISRO_CLASS_BASELINE_SPECTRA
)

# 3. Prescriptive Planning (Member 4)
plan_recommendation = prescriptive.generate_recommendations(
    prediction=pred_results,
    risk=ori_results,
    ore_pockets=C.ORE_POCKETS
)

# Check execution button state from Member 4 module
if st.session_state["execution_state"]:
    recovered_tonnage = plan_recommendation.get("recoverable_tonnage", 0.0)
else:
    recovered_tonnage = 0.0

# 4. Global Rupee Loss Ledger Banner (Member 5 & Lead)
st.markdown("---")
ops.render_ledger_banner(
    shortfall_tonnage=pred_results["shortfall_tonnage"],
    recovered_tonnage=recovered_tonnage,
    is_executed=st.session_state["execution_state"],
    rate_per_ton=C.MN_RATE_PER_TON_INR
)
st.markdown("---")

# --- MAIN TABBED INTERFACE ---
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🗺️ Spatial Reserve Map",
    "📊 Shortfall Predictor",
    "🔬 Chandrayaan-2 Spectral Layer",
    "📋 Prescriptive Engine",
    "🤖 XAI & Confidence Meter"
])

# --- TAB 1: Spatial Reserve Map (Member 1) ---
with tab1:
    st.header("Candidate Ore Pockets & Geological Reserves")
    spatial.render_reserve_map(
        center=C.DEFAULT_MAP_CENTER,
        ore_pockets=C.ORE_POCKETS,
        highlight_index=0
    )

# --- TAB 2: Shortfall Predictor (Member 3) ---
with tab2:
    st.header("Weekly Production & Tonnage Forecast")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("Base Target", f"{C.BASE_WEEKLY_TARGET_TONS:,.0f} Tons")
        st.metric("Predicted Output", f"{pred_results['predicted_tonnage']:,.1f} Tons")
    with col2:
        st.metric("Forecasted Shortfall", f"{pred_results['shortfall_tonnage']:,.1f} Tons", delta_color="inverse")

# --- TAB 3: Chandrayaan-2 Spectral Layer (Member 2) ---
with tab3:
    spectral.render_aoi_spectral_overlay()

# --- TAB 4: Prescriptive Planning Engine (Member 4) ---
with tab4:
    st.header("Actionable Mitigation & Resource Shift Plan")
    st.json(plan_recommendation)
    
    execution_status = prescriptive.apply_plan(plan_recommendation, pred_results)
    if execution_status.get("execute_clicked"):
        st.session_state["execution_state"] = True
        st.rerun()

# --- TAB 5: XAI & Confidence Meter (Member 6 / Team Lead) ---
with tab5:
    st.header("Explainable AI (XAI) & Model Reliability")
    
    # Calculate confidence score
    conf_score = xai.model_confidence(
        rainfall_mm=risk_inputs["rainfall_mm"],
        mtbf_hrs=risk_inputs["mtbf_hrs"],
        labor_drop_pct=risk_inputs["labor_drop_pct"],
        spectral_similarity=spec_results["similarity"]
    )

    # Compute Shapley-style attribution driver breakdown
    attribution = xai.compute_shapley_style_attribution(
        penalties_dict=pred_results["penalties"],
        spectral_similarity=spec_results["similarity"]
    )

    # Render Member 6 visuals
    xai.render_xai_charts(attribution, conf_score)
