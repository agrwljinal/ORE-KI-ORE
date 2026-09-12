# modules/xai.py
# OWNER: Team Lead / Member 6 (Feature 7: Driver Breakdown + Feature 8: Confidence Meter)
# 
# RESPONSIBILITIES:
# 1. Calculate Confidence Meter score combining volatility and spectral similarity.
# 2. Compute a simple driver breakdown across loss drivers.
# 3. Render Streamlit UI visuals (Confidence metric & driver breakdown chart).

import streamlit as st
import plotly.graph_objects as go

def model_confidence(rainfall_mm, mtbf_hrs, labor_drop_pct, spectral_similarity):
    """
    Computes overall confidence score (0.0 to 1.0).
    Formula:
        volatility = (rainfall_mm/300 + (400 - mtbf_hrs)/400 + labor_drop_pct/40) / 3
        confidence = 0.6 * (1 - volatility) + 0.4 * spectral_similarity
    """
    # Normalize inputs for volatility calculation
    rain_norm = min(rainfall_mm / 300.0, 1.0)
    mtbf_norm = max(0.0, (400.0 - mtbf_hrs) / 400.0)
    labor_norm = min(labor_drop_pct / 40.0, 1.0)
    
    volatility = (rain_norm + mtbf_norm + labor_norm) / 3.0
    
    # Bound confidence between 0.0 and 1.0
    confidence = 0.6 * (1.0 - volatility) + 0.4 * spectral_similarity
    return max(0.0, min(1.0, confidence))


def compute_driver_breakdown(penalties_dict, spectral_similarity):
    """
    Calculates simple percentage driver contributions to shortfall and recovery risk.
    Normalizes penalties and spectral uncertainty so they stay easy to read.
    """
    rain_p = penalties_dict.get("rain", 0.0)
    mtbf_p = penalties_dict.get("mtbf", 0.0)
    labor_p = penalties_dict.get("labor", 0.0)
    spectral_uncertainty = max(0.0, 1.0 - spectral_similarity) * 0.20

    total_weight = rain_p + mtbf_p + labor_p + spectral_uncertainty

    if total_weight == 0:
        return {
            "Rain and water in the pit": 25.0,
            "Truck and equipment delays": 25.0,
            "Labor and crew availability": 25.0,
            "Ore quality and material mix": 25.0
        }

    return {
        "Rain and water in the pit": round((rain_p / total_weight) * 100, 1),
        "Truck and equipment delays": round((mtbf_p / total_weight) * 100, 1),
        "Labor and crew availability": round((labor_p / total_weight) * 100, 1),
        "Ore quality and material mix": round((spectral_uncertainty / total_weight) * 100, 1)
    }


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