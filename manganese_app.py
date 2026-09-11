"""Run the Folium manganese prediction/heatmap demo."""

import streamlit as st

from modules.manganese import render_manganese_map

st.set_page_config(page_title="MOIL Manganese Prospectivity", layout="wide")
render_manganese_map()
