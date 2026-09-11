# modules/weather.py (or modules/ops.py)
# OWNER: Member 5 (Feature 3: Weather/Risk Engine + Feature 6: Rupee Loss Ledger Banner)

# This file must export exactly 3 functions, matching what app.py already calls:
#   render_risk_sliders(st_obj)   -> dict
#   risk_summary(rainfall_mm, mtbf_hrs, labor_drop_pct) -> dict (key must be "ori")
#   render_ledger_banner(shortfall_tonnage, recovered_tonnage, is_executed, rate_per_ton)

import streamlit as st  # needed here because render_ledger_banner is not passed an st object


def render_risk_sliders(st_obj):
    """
    Renders the 3 operational-risk sliders.
    NOTE: app.py calls this as ops.render_risk_sliders(st.sidebar) — so st_obj
    here is actually st.sidebar, a container, not the top-level st module.
    Returns a dict with the exact keys app.py expects.
    """
    st_obj.subheader("⚠️ Operational Risk Inputs")

    rainfall_mm = st_obj.slider(
        "Rainfall (mm, next 7 days)",
        min_value=0.0,
        max_value=200.0,
        value=50.0,
        step=1.0,
        help="Higher rainfall floods haul roads and halts open-cast blasting.",
    )
    mtbf_hrs = st_obj.slider(
        "Machinery MTBF (hrs)",
        min_value=1.0,
        max_value=500.0,
        value=100.0,
        step=1.0,
        help="Mean Time Between Failures — lower value means more frequent breakdowns.",
    )
    labor_drop_pct = st_obj.slider(
        "Labor calendar drop (%)",
        min_value=0.0,
        max_value=100.0,
        value=10.0,
        step=1.0,
        help="Workforce unavailability from festivals, local holidays, or strikes.",
    )

    return {
        "rainfall_mm": rainfall_mm,
        "mtbf_hrs": mtbf_hrs,
        "labor_drop_pct": labor_drop_pct,
    }


def risk_summary(rainfall_mm, mtbf_hrs, labor_drop_pct):
    """
    Computes the Operational Risk Index (ORI), a single 0-100 score.

    ORI = 100 * (0.40*rain_norm + 0.35*mtbf_norm + 0.25*labor_norm)

    IMPORTANT: app.py reads ori_results['ori'] directly — the key must be
    exactly "ori" (not "operational_risk_index") or the sidebar metric will
    throw a KeyError.
    """
    rain_norm = min(rainfall_mm / 200.0, 1.0)          # more rain = more risk
    mtbf_norm = 1.0 - min(mtbf_hrs / 500.0, 1.0)       # lower MTBF = more risk
    labor_norm = min(labor_drop_pct / 100.0, 1.0)      # more labor drop = more risk

    ori = 100 * (0.40 * rain_norm + 0.35 * mtbf_norm + 0.25 * labor_norm)

    return {"ori": float(round(ori, 1))}


def render_ledger_banner(shortfall_tonnage, recovered_tonnage, is_executed, rate_per_ton):
    """
    Renders the global Rupee Loss Ledger banner (Feature 6).

    Revenue at Risk (Cr) = shortfall_tonnage * rate_per_ton / 1e7
    If a corrective plan has been executed, show Revenue Saved (green) instead
    of Revenue at Risk (red).

    app.py doesn't use a return value here — it just calls this for its
    side effect (drawing the banner) — but we return the numbers too so you
    can unit-test this function without Streamlit running.
    """
    revenue_at_risk_cr = shortfall_tonnage * rate_per_ton / 1e7

    if is_executed:
        revenue_saved_cr = recovered_tonnage * rate_per_ton / 1e7
        st.success(f"💰 Revenue Saved: ₹{revenue_saved_cr:.2f} Cr")
        return {"status": "saved", "revenue_saved_cr": round(revenue_saved_cr, 2)}
    else:
        st.error(f"⚠️ Revenue at Risk: ₹{revenue_at_risk_cr:.2f} Cr")
        return {"status": "at_risk", "revenue_at_risk_cr": round(revenue_at_risk_cr, 2)}


# --- Standalone test (run: python modules/weather.py) — no Streamlit needed for this part ---
if __name__ == "__main__":
    test_cases = [
        (50, 200, 10),
        (150, 80, 30),
        (190, 20, 70),
    ]
    print("risk_summary() checks:")
    for rainfall, mtbf, labor in test_cases:
        result = risk_summary(rainfall, mtbf, labor)
        print(f"  rainfall={rainfall}, mtbf={mtbf}, labor_drop={labor}% -> {result}")
