# MOIL AOI Spectral Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Display the supplied Bharveli-Awalajhari 76.409-ha KML boundary with an AOI-level Sentinel-2/USGS pyrolusite spectral-potential overlay and curve comparison.

**Architecture:** `modules/spectral.py` owns scientifically constrained spectral calculations, KML parsing, and the Streamlit/Folium spectral view. It uses the supplied four-band scene mean and USGS reference vector as an AOI-level result, never as Mn concentration or a pixel-level grade map. `app.py` delegates its spectral tab to that module.

**Tech Stack:** Python, Streamlit, NumPy, Plotly, Folium, ElementTree, unittest.

**Spec:** User-provided implementation requirements in this task.

## Global Constraints

- Use `data/07_Aug_2019_1659504705RGLR1LRProjectSite.kml` unchanged as the real Bharveli-Awalajhari AOI boundary source.
- Do not reference Pit B or fabricate sample coordinates.
- Show 97.84% only as Pyrolusite Spectral Similarity; never as Mn percentage, concentration, reserve, or grade.
- Identify the scene as Sentinel-2C L2A, 2026-01-07, T44QMK, cloud cover 0.000319%.
- Make the overlay AOI-level because only AOI mean reflectance is supplied.

---

### Task 1: Spectral result domain model and calculations

**Files:**
- Create: `tests/test_spectral.py`
- Modify: `modules/spectral.py`

**Interfaces:**
- Produces: `build_bharveli_aoi_result() -> dict` and `spectral_match(earth_reflectance, isro_baseline) -> dict`.

- [ ] Write failing tests that assert the supplied Sentinel-2 and USGS vectors yield a 0.9784 similarity and that the result wording is spectral potential, not Mn grade.
- [ ] Run `python -m unittest tests.test_spectral -v` and confirm failure because the functions are missing.
- [ ] Implement normalised cosine similarity, immutable supplied scene metadata, and a result builder with appropriate scientific labels.
- [ ] Re-run `python -m unittest tests.test_spectral -v` and confirm passing tests.

### Task 2: Actual-KML boundary adapter

**Files:**
- Modify: `tests/test_spectral.py`
- Modify: `modules/spectral.py`

**Interfaces:**
- Produces: `load_aoi_kml(path: Path | str = DEFAULT_AOI_KML) -> list[dict]`, with GeoJSON LineString/Polygon geometries sourced directly from KML coordinates.

- [ ] Write a failing test requiring all 13 KML placemarks to load and requiring their coordinates to remain in the Bharveli-Awalajhari coordinate range.
- [ ] Run the focused unit test and confirm failure because the KML adapter is missing.
- [ ] Implement namespace-safe KML parsing that converts closed lines to polygons and preserves non-closed lines as line geometry; do not construct any new coordinates.
- [ ] Re-run the focused unit test and confirm passing tests.

### Task 3: Dark-mode AOI overlay and spectral curve UI

**Files:**
- Modify: `modules/spectral.py`
- Modify: `app.py`

**Interfaces:**
- Produces: `render_aoi_spectral_overlay() -> None`.

- [ ] Write a failing unit test for the UI-neutral overlay payload: exact scene metadata, 97.84% similarity, and AOI-level status.
- [ ] Run the focused unit test and confirm failure before the payload implementation.
- [ ] Render a Streamlit RGB/SWIR mode switch, exact KML outlines, violet AOI spectral-potential styling, an honest click-equivalent analysis card, and the cyan/dashed-violet comparison chart.
- [ ] Replace the legacy generic spectral tab content in `app.py` with `render_aoi_spectral_overlay()`.
- [ ] Run all unit tests, compile all edited Python modules, and launch Streamlit headlessly for a smoke test.
