"""Standalone launcher for the Bharveli-Awalajhari spectral command view."""

import streamlit as st

from modules.spectral import command_header_html, render_aoi_spectral_overlay


st.set_page_config(page_title="MOIL Spectral Command Center", page_icon="🛰️", layout="wide")
st.markdown(
    "<style>.stApp{background:#060b18;color:#e5edf9}.command-header{padding:1.15rem 1.5rem;border:1px solid #26334f;border-radius:16px;background:#0d1529;margin-bottom:1rem}.command-title{color:#f8fafc;font-size:1.45rem;font-weight:800;letter-spacing:.02em}.command-subtitle{color:#9aa9c4;margin-top:.2rem}</style>",
    unsafe_allow_html=True,
)
st.markdown(command_header_html(), unsafe_allow_html=True)
render_aoi_spectral_overlay()
