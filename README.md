### Spectral Reference Data

The prototype uses a Pyrolusite reference spectrum from the
USGS Digital Spectral Library (splib05a), USGS Open-File Report 03-395.

Source:
https://pubs.usgs.gov/of/2003/ofr-03-395/ASCII/M/pyrolusite_hs138.5705.asc

The reference spectrum is used for spectral similarity comparison
with satellite-derived reflectance values.



# MOIL GeoMine Intelligence — spatial module

This repository is the team’s Streamlit integration shell for SIH26009. The
spatial module in [`modules/spatial.py`](modules/spatial.py) adds a
presentation-ready portfolio map, mine detail map, synthetic spatial demo
surface, geology/production analytics, data-status warnings, provenance and
exports while preserving the existing `render_reserve_map(center,
ore_pockets, highlight_index)` entry point.

The module was built against `sih26009_demo_data_v3.zip` and uses its original
field names. The extracted demo files are under `data/`:

- `mine_map_geometry.geojson` and `mine_master.csv` provide mine-level points
  and published envelope extents. Envelope polygons are contextual only, not
  legal lease boundaries.
- `mine_dashboard_summary.csv` provides mine-level synthetic, official and
  proxy metrics for the portfolio view.
- `processed_geology.csv` provides descriptive Mn-grade, lithology, thickness,
  Sentinel-2 and ASTER distributions. Its 500 rows are not spatial samples;
  their latitude/longitude values are mine anchors and are never plotted as
  assay points.
- `processed_production.csv` provides operational context for the separate
  production/weather panel.
- `price_cost_reference.csv`, `production_calibration.csv` and
  `source_registry.csv` provide economic/reference provenance.
- `metadata.json` is displayed as warnings in the UI and is not suppressed.

## Scientific and data-status boundaries

Prospectivity, CLASS-informed spectral transfer and mining priority are kept
as three separate concepts. Rainfall, shortfall and economics never alter a
geological probability. The CLASS layer is described as an experimental
model-derived transfer feature and never as direct Chandrayaan-2 observation
of a terrestrial mine.

The current provider is explicitly labelled `SYNTHETIC_SPATIAL_DEMO` and its
cells are deterministic visualization values, not reserve locations. The
`estimated_tonnage_proxy_t` field is shown as **Estimated Tonnage Proxy** and
is never summed into a reserve total. `profit_proxy_inr` is shown as an
**Indicative Gross Margin Proxy**, not accounting profit. Confidence and
uncertainty remain nullable; no confidence is derived from probability, grade
or CLASS impact.

## Running the Streamlit shell

```powershell
python -m pip install -r requirements.txt
streamlit run app.py
```

For the spatial work independently of the other unfinished team modules, run
the standalone launcher:

```powershell
python -m streamlit run spatial_app.py
```

The original repository still contains TODO shells for the other team
members’ modules. The spatial module itself can be imported and tested
without a Streamlit server:

```python
from modules.spatial import load_dataset, PredictionService

bundle = load_dataset()
cells = PredictionService(bundle).get_predictions("Balaghat")
```

Set `MOIL_DATA_DIR` when the data is kept outside the repository. Set
`MOIL_PREDICTIONS_GEOJSON` to switch from the deterministic provider to a
real georeferenced prediction file.

## Provider boundary for the ML team

The UI calls `PredictionService.get_predictions(mine_name)`. It selects
`GeoJSONPredictionProvider` when a configured file exists and otherwise uses
`SyntheticPredictionProvider`. No map or panel code needs to change when the
provider is replaced.

The future file must be a GeoJSON `FeatureCollection`. Each feature must have
Polygon geometry, `cell_id`, `mine_name`, and at least one of
`predicted_mn_pct` or `base_mn_probability`. Optional properties can include:

```json
{
  "cell_id": "BAL_001",
  "mine_name": "Balaghat",
  "predicted_mn_pct": 36.8,
  "base_mn_probability": 0.74,
  "class_mn_probability": 0.81,
  "class_transfer_score": 0.63,
  "confidence": null,
  "uncertainty": null,
  "predicted_grade_band": "35_TO_BELOW_46",
  "estimated_tonnage_proxy_t": null,
  "ndvi": 0.24,
  "s2_b11_b12_ratio": 1.18,
  "aster_b4_b5_ratio": 1.32,
  "aster_b6_b7_ratio": 1.11,
  "aster_b8_b9_ratio": 0.94,
  "lithology": "Gondite",
  "model_version": "v1",
  "prediction_timestamp": "2026-09-11T00:00:00Z"
}
```

If either probability is absent, CLASS impact is displayed as “Not
available”. If `confidence` is absent, the inspector displays “Confidence:
Not available”.

## Where the requested features live

- Map rendering, portfolio KPI colouring, mine detail layers, inspector,
  analytics and exports: `modules/spatial.py`.
- Current dataset loading: `load_dataset()` in `modules/spatial.py`.
- Synthetic cells: `SyntheticPredictionProvider.get_predictions()`.
- Base vs CLASS and CLASS impact: `PredictionCell.class_delta()`,
  `get_class_delta()` and the detail-layer selector.
- Mining Priority: `calculate_mining_priority()` with normalized configurable
  weights. It is a decision-support score, not a reserve probability.

The portfolio view uses reference/calibrated mine and production data. Geology
charts use the supplied synthetic processed geology rows descriptively.
Detailed prediction maps and cell exports use synthetic spatial demo values
until the ML GeoJSON provider is configured; this status is shown persistently
in the interface and in exported GeoJSON (`data_status`).
## Manganese location, grade and amount demo

Run the Folium/heatmap view with:

```powershell
python -m pip install -r requirements.txt
python -m streamlit run manganese_app.py
```

The map has three layers:

- **Prospectivity (%)**: the demo model's candidate-zone ranking.
- **Predicted Mn grade (%)**: the linked `processed_geology.csv` `mn_pct` value,
  displayed as a model-predicted/demo value.
- **Estimated tonnage proxy (t)**: the supplied
  `estimated_tonnage_proxy_t` field. It is a geometric proxy, not a certified
  reserve and must not be summed into a mine total.

The heatmap uses only the four configured candidate zones in
`constants.py`. It deliberately does not plot all 500 geology rows because
their coordinates are mine anchors rather than genuine assay coordinates.
For a real prediction, replace `predict_demo_zones()` with a provider that
returns georeferenced GeoJSON cells containing `geometry`, `cell_id`,
`mine_name`, and `predicted_mn_pct` and/or `base_mn_probability`.
