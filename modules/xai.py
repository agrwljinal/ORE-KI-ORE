# modules/xai.py
# OWNER: Team Lead / Member 6 (Feature 7: Driver Breakdown + Feature 8: Confidence Meter)
# 
# RESPONSIBILITIES:
# 1. Calculate Confidence Meter score combining volatility and spectral similarity.
# 2. Compute a simple driver breakdown across loss drivers.
# 3. Render Streamlit UI visuals (Confidence metric & driver breakdown chart).

import streamlit as st
import plotly.graph_objects as go

def model_confidence(rainfall_mm, mtbf_hrs, labor_drop_pct, spectral_similarity,
                     soil_moisture_pct=0.0, blast_delay_minutes=0.0, ore_grade="STD"):
    """
    Computes overall confidence score (0.0 to 1.0).
    Formula expands the original rainfall / downtime / labor volatility into a
    full operational mix including soil moisture, blast drag, and grade signal.
    """
    rain_norm = min(float(rainfall_mm) / 300.0, 1.0)
    mtbf_norm = max(0.0, (400.0 - float(mtbf_hrs)) / 400.0)
    labor_norm = min(float(labor_drop_pct) / 40.0, 1.0)
    soil_norm = min(float(soil_moisture_pct) / 60.0, 1.0)
    blast_norm = min(float(blast_delay_minutes) / 180.0, 1.0)
    ore_norm = 0.0 if str(ore_grade).upper() == "STD" else 0.15

    volatility = (rain_norm + mtbf_norm + labor_norm + soil_norm + blast_norm + ore_norm) / 6.0
    confidence = 0.6 * (1.0 - volatility) + 0.4 * float(spectral_similarity)
    return max(0.0, min(1.0, confidence))


def compute_driver_breakdown(penalties_dict, spectral_similarity=0.9784):
    """
    Calculates percentage driver contributions using every visible scenario
    slider input and the spectral similarity uncertainty. Values are returned
    in a stable order and normalized so the six factors always sum to 100%.
    """
    if isinstance(penalties_dict, dict) and "penalties" in penalties_dict:
        penalties_dict = penalties_dict.get("penalties", {})

    if not isinstance(penalties_dict, dict):
        penalties_dict = {}

    rain_p = float(penalties_dict.get("rain", penalties_dict.get("rainfall", 0.0)) or 0.0)
    soil_p = float(penalties_dict.get("soil_moisture", penalties_dict.get("soil", 0.0)) or 0.0)
    equipment_p = float(penalties_dict.get("equipment", penalties_dict.get("mtbf", 0.0)) or 0.0)
    blast_p = float(penalties_dict.get("blast_delay", 0.0) or 0.0)
    labor_p = float(penalties_dict.get("labor", 0.0) or 0.0)
    ore_quality_p = float(penalties_dict.get("ore_quality", 0.0) or 0.0)
    spectral_uncertainty = max(0.0, 1.0 - float(spectral_similarity)) * 0.20

    weights = {
        "Rainfall": rain_p,
        "Soil Moisture": soil_p,
        "Equipment Downtime": equipment_p,
        "Blast Delay": blast_p,
        "Labor Drop": labor_p,
        "Ore Quality": ore_quality_p + spectral_uncertainty,
    }

    total_weight = sum(max(0.0, v) for v in weights.values())
    if total_weight == 0:
        return {
            "Rainfall": 25.0,
            "Soil Moisture": 25.0,
            "Equipment Downtime": 25.0,
            "Blast Delay": 25.0,
            "Labor Drop": 0.0,
            "Ore Quality": 0.0,
        }

    normalized = {}
    for key, source in weights.items():
        normalized[key] = round(max(0.0, min(100.0, (float(source) / total_weight) * 100.0)), 1)

    # Force the sum to exactly 100% at the output boundary because UI progress
    # bars should render a full story and the diagnostic narrative should be
    # internally consistent in every live refresh.
    raw_total = sum(normalized.values())
    if raw_total:
        delta = 100.0 - raw_total
        if normalized:
            first_key = next(iter(normalized))
            normalized[first_key] = round(normalized[first_key] + delta, 1)
    return normalized


compute_shapley_style_attribution = compute_driver_breakdown


def render_xai_charts(attribution_dict, confidence_score):
    """
    Renders the Confidence Meter and Shapley-style Attribution Bar Chart in Streamlit.
    """
    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("Model Confidence Meter")
        st.metric(
            label="Confidence Index",
            value=f"{confidence_score * 100:.1f}%",
            delta="High Reliability" if confidence_score >= 0.75 else "Moderate Risk"
        )
        st.progress(confidence_score)

    with col2:
        st.subheader("Main Shortfall Drivers")
        
        drivers = list(attribution_dict.keys())
        percentages = list(attribution_dict.values())

        fig = go.Figure(go.Bar(
            x=percentages,
            y=drivers,
            orientation='h',
            marker=dict(color=['#EF553B', '#FF6692', '#FFA15A', '#19D3F3']),
            text=[f"{val}%" for val in percentages],
            textposition='auto'
        ))

        fig.update_layout(
            xaxis_title="Contribution Share (%)",
            yaxis_title="Driver",
            height=300,
            margin=dict(l=20, r=20, t=30, b=20)
        )

        st.plotly_chart(fig, use_container_width=True)