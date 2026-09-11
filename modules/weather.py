# modules/weather.py (or modules/ops.py)
# OWNER: Member 5 (Feature 3: Weather/Risk Engine + Feature 6: Rupee Loss Ledger Banner)
# 
# TODO FOR M5:
# Build risk input sliders, compute Operational Risk Index (ORI), and render the global Rupee Loss Ledger banner.
# 
# Required Exported Functions:
# 
# 1. def render_risk_sliders(st_obj):
#     """
#     Renders sidebar UI controls for Rainfall (mm), MTBF (hrs), and Labor Drop (%).
#     Returns dict:
#         {"rainfall_mm": float, "mtbf_hrs": float, "labor_drop_pct": float}
#     """
#     pass
# 
# 2. def risk_summary(rainfall_mm, mtbf_hrs, labor_drop_pct):
#     """
#     Computes Operational Risk Index:
#         ORI = 100 * (0.40 * rain_norm + 0.35 * mtbf_norm + 0.25 * labor_norm)
#     Returns dict:
#         {"ori": float}
#     """
#     pass
# 
# 3. def render_ledger_banner(shortfall_tonnage, recovered_tonnage, is_executed, rate_per_ton):
#     """
#     Calculates financial loss/savings and displays the header banner:
#         Revenue at Risk (₹ Cr) = shortfall_tonnage * rate_per_ton / 1e7
#         If is_executed is True: display "Revenue Saved" in green.
#         If False: display "Revenue at Risk" in red.
#     """
#     pass

# modules/weather.py
# OWNER: Member 5
# Feature 3: Weather/Risk Engine + Feature 6: Rupee Loss Ledger

import streamlit as st


def render_risk_sliders(st_obj):
    """
    Renders sidebar UI controls for Rainfall, MTBF, and Labor Drop.
    Returns dict with the selected values.
    """

    st_obj.subheader("⚠️ Operational Risk Inputs")

    rainfall_mm = st_obj.slider(
        "Rainfall (mm)",
        min_value=0.0,
        max_value=200.0,
        value=50.0,
        step=1.0
    )

    mtbf_hrs = st_obj.slider(
        "MTBF (hrs)",
        min_value=1.0,
        max_value=500.0,
        value=100.0,
        step=1.0
    )

    labor_drop_pct = st_obj.slider(
        "Labor Drop (%)",
        min_value=0.0,
        max_value=100.0,
        value=10.0,
        step=1.0
    )

    return {
        "rainfall_mm": rainfall_mm,
        "mtbf_hrs": mtbf_hrs,
        "labor_drop_pct": labor_drop_pct
    }


def risk_summary(rainfall_mm, mtbf_hrs, labor_drop_pct):
    """
    Computes Operational Risk Index (ORI).

    ORI = 100 * (
        0.40 * rain_norm +
        0.35 * mtbf_norm +
        0.25 * labor_norm
    )
    """

    # Higher rainfall = higher risk
    rain_norm = min(rainfall_mm / 200.0, 1.0)

    # Lower MTBF = higher risk
    mtbf_norm = 1.0 - min(mtbf_hrs / 500.0, 1.0)

    # Higher labor drop = higher risk
    labor_norm = min(labor_drop_pct / 100.0, 1.0)

    ori = 100 * (
        0.40 * rain_norm +
        0.35 * mtbf_norm +
        0.25 * labor_norm
    )

    return {
        "ori": float(ori)
    }


def render_ledger_banner(
    shortfall_tonnage,
    recovered_tonnage,
    is_executed,
    rate_per_ton
):
    """
    Displays Revenue at Risk / Revenue Saved banner.
    """

    revenue_at_risk = (
        shortfall_tonnage * rate_per_ton / 1e7
    )

    if is_executed:
        revenue_saved = (
            recovered_tonnage * rate_per_ton / 1e7
        )

        st.success(
            f"💰 Revenue Saved: ₹{revenue_saved:.2f} Cr"
        )

    else:
        st.error(
            f"⚠️ Revenue at Risk: ₹{revenue_at_risk:.2f} Cr"
        )