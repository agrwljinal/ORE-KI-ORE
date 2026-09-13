# ORE की ओर

A Flask and Streamlit-powered mining intelligence dashboard for monitoring manganese production risk, spatial prospectivity, spectral similarity, operational constraints, and customer delivery exposure across the MOIL Bharveli-Awalajhari mining scenario.

This project transforms operational, geological, production, weather, spectral, and customer contract datasets into a command-center web experience that models shortfall, calculates projected production, estimates commercial liabilities, and recommends response actions for mine planning teams.

## Key Features

- Interactive Flask dashboard and API for operational and financial risk simulation.
- REST endpoints for telemetry, predictions, prescriptive recommendations, customer contracts, and spectral intelligence.
- Spatial portfolio map and mine-level analytics using synthetic demo geospatial data and data provenance notes.
- Spectral AOI scoring and per-zone fusion scoring based on Sentinel-2 reflectance behavior and USGS pyrolusite spectral references.
- Manganese prospectivity and Folium/Streamlit heatmap workflows for demo spatial exploration.
- Customer contract and delivery liability forecasting tied to active contracts and predicted output.
- Prescriptive recommendation engine with selectable mitigation actions and recovery gain simulation.

## Tech Stack / Built With

- Python 3
- Flask for the primary dashboard and API backend
- Streamlit for independent spatial, spectral, and manganese demo launchers
- HTML, CSS, JavaScript, Leaflet, and Plotly for the web and visualization experience
- Pandas, NumPy, scikit-learn, Shapely, Folium, and Streamlit-Folium for geospatial and analytics workflows
- CSV, GeoJSON, KML, and JSON project datasets stored in the `data/` directory

## Prerequisites

Before running the project locally, make sure the following are available:

- Python 3.9+ (recommended Python 3.10 or newer)
- `pip` package manager
- Git for cloning the repository
- Optional: a local virtual environment or Conda environment
- Optional: `.env`-compatible shell environment for custom data paths or CORS configuration

## Installation & Setup

Clone the repository:

```bash
git clone <repository-url>
cd SIH
```

Create and activate a virtual environment:

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Install the required Python packages:

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Run the Flask dashboard locally:

```bash
python -m flask --app app.py run --debug
```

Then visit the application in your browser:

```text
http://127.0.0.1:5000/
```

The repository also includes standalone Streamlit entry points:

```bash
python -m streamlit run spatial_app.py
python -m streamlit run spectral_app.py
python -m streamlit run manganese_app.py
```

## Environment Variables

This codebase supports a few optional environment or shell variables that can be used to point the app at alternate data sources or local development origins.

```env
MOIL_DATA_DIR=/path/to/data
MOIL_PREDICTIONS_GEOJSON=/path/to/prediction.geojson
MOIL_CORS_ORIGIN=http://127.0.0.1:5000
```

Variable descriptions:

- `MOIL_DATA_DIR`: Overrides the repository data directory when datasets are stored outside the repo.
- `MOIL_PREDICTIONS_GEOJSON`: Points to an external GeoJSON prediction provider for production-style map prediction data.
- `MOIL_CORS_ORIGIN`: Sets the access-control origin header returned by the Flask API layer.

## Project Structure

```text
SIH/
+-- app.py                     # Flask app entry point and main API/UI routes
+-- constants.py              # Shared constants, model assumptions, and reference values
+-- requirements.txt          # Python dependencies
+-- static/                   # CSS, JavaScript, and frontend asset files
+-- templates/                # Flask HTML template files
+-- data/                     # Data files such as CSV, GeoJSON, JSON, and KML assets
+-- modules/                  # Functional modules for spatial, spectral, fusion, weather, etc.
+-- spatial_app.py            # Streamlit spatial demo launcher
+-- spectral_app.py           # Streamlit spectral launcher
+-- manganese_app.py          # Streamlit manganese prospectivity launcher
+-- tests/                    # Regression and API/module test suite
+-- docs/                     # Additional project documentation and sample materials
```

## Usage & Examples

The main Flask service exposes several endpoints for telemetry, predictions, planning, customer contracts, and spectral scoring:

```http
GET /api/telemetry
GET /api/predictions
POST /api/predictions
GET /api/prescriptive
POST /api/prescriptive
GET /api/customers
POST /api/customers
GET /api/spectral
```

Example prediction call:

```bash
curl http://127.0.0.1:5000/api/predictions
```

Example prescriptive execution:

```bash
curl -X POST http://127.0.0.1:5000/api/prescriptive \
  -H "Content-Type: application/json" \
  -d '{"action":"EXECUTE","selected_actions":["REROUTE_FLEET","DEWATERING"]}'
```

Example customer contract POST:

```bash
curl -X POST http://127.0.0.1:5000/api/customers \
  -H "Content-Type: application/json" \
  -d '{"entity":"contract","mine_name":"Balaghat","customer_name":"Demo Customer","quantity_mt":500,"price_per_mt":5000,"deadline":"2026-09-30","penalty_type":"INR_PER_MT","penalty_value":2500}'
```

## Contributing

Contributions are welcome.

1. Fork the repository and create a feature branch.
2. Add or update tests for algorithm, API, or data workflow changes.
3. Keep dataset provenance, synthetic/demo labels, and risk-model assumptions transparent in your changes.
4. Submit a pull request with a concise explanation of the update and validation performed.

## License

This repository does not currently declare a license. If you intend to redistribute or publish the project, add a license file such as MIT, Apache-2.0, or GPL and update this section accordingly.
