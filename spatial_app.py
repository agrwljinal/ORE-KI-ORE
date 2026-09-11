"""Standalone launcher for the completed MOIL spatial module."""

import streamlit as st

from modules.spatial import render_reserve_map


st.set_page_config(
    page_title="MOIL GeoMine Intelligence",
    page_icon="🗺️",
    layout="wide",
)

render_reserve_map(center=[21.70, 79.80], ore_pockets=[])
