<<<<<<< HEAD
"""Application-wide constants for the MOIL command centre.

Everything user-facing that isn't a live measurement lives here so a single
edit can flip demo data / thresholds / weights without hunting through code.

Provenance note
---------------
The ``CANDIDATE_ZONES`` list below is explicitly labelled
``SYNTHETIC_DEMO_ZONE_DATA``. Coordinates lie *inside* the real
76.409-ha MOIL Bharveli-Awalajhari AOI (loaded from the supplied KML),
but they are demo pins used to exercise the end-to-end spatial + spectral
pipeline. They are not claimed as ore locations detected by Sentinel-2.
"""

APP_TITLE = "MOIL Mining & Financial Risk Intelligence Command Center"

# --- Legacy team-shell inputs (kept for backward compat with other modules) ---

BASE_WEEKLY_TARGET_TONS = 1000.0
# Recentred on the real Bharveli-Awalajhari AOI mid-point (KML bounds
# ~21.83-21.86 lat, 80.21-80.25 lon).
DEFAULT_MAP_CENTER = [21.845, 80.232]

=======
APP_TITLE = "MOIL Mining & Financial Risk Intelligence Command Center"

# --- Required by app.py's pipeline; previously undefined (AttributeError on load) ---

# Member 3 (prediction.py) shortfall model input.
# TODO(M3): replace with the real weekly production target for the demo mine.
BASE_WEEKLY_TARGET_TONS = 14500.0

# Member 1 (spatial.py) default map center when no mine is selected.
# Matches the fallback already used in spatial_app.py.
DEFAULT_MAP_CENTER = [21.70, 79.80]

# Member 4 (prescriptive.py) candidate ore pockets for the resource-shift engine.
# TODO(M4): replace with real pit/pocket data; this is a placeholder so the
# app can run end-to-end for a demo.
>>>>>>> ab8d539bf6e2fff0a189b245984865cd621aa388
ORE_POCKETS = [
    {"name": "Pit 1", "grade_pct": 46.0, "mine_name": None},
    {"name": "Pit B", "grade_pct": 38.0, "mine_name": None},
]

<<<<<<< HEAD
MN_RATE_PER_TON_INR = 3808.49


# ---------------------------------------------------------------------------
# Fusion configuration (used by modules.fusion.evaluate_zone)
# ---------------------------------------------------------------------------

# Prototype weighted-sum weights. Configurable in one place per the spec.
# These are declared as a prototype rule and are NOT a scientifically
# validated weighting scheme.
FUSION_WEIGHTS = {
    "spatial": 0.5,
    "spectral": 0.5,
}
FUSION_PROTOTYPE_LABEL = (
    "Prototype fusion rule: final = 0.5 * spatial + 0.5 * spectral. "
    "Weights are configurable in constants.FUSION_WEIGHTS."
)


# ---------------------------------------------------------------------------
# Candidate zones - SYNTHETIC_DEMO_ZONE_DATA inside real AOI bounds
# ---------------------------------------------------------------------------

# Each zone gets:
#   zone_id       - stable string ID
#   name          - display name
#   latitude/     } coordinates INSIDE the real 76.409-ha KML AOI
#   longitude     }
#   spatial_score - 0-100 spatial prospectivity from the demo spatial model
#   spatial_provenance - clearly labels the source
#   zone_type     - descriptive
#   operational_status - links to the legacy telemetry story
#   linked_geology_record_id - which processed_geology.csv row supplies
#                              the (synthetic) Sentinel-2 reflectance
#
# The KML AOI covers roughly:
#     lat  21.828 - 21.856
#     lon  80.216 - 80.246
# Coordinates below sit comfortably inside that envelope.
SYNTHETIC_ZONE_TAG = "SYNTHETIC_DEMO_ZONE_DATA"

CANDIDATE_ZONES = [
    {
        "zone_id": "ZONE_A",
        "name": "Zone A (North Deep)",
        "latitude": 21.8515,
        "longitude": 80.2225,
        "spatial_score": 91.0,
        "spatial_provenance": SYNTHETIC_ZONE_TAG,
        "zone_type": "High-prospectivity candidate",
        "operational_status": "Flooded",
        "water_depth_m": 4.2,
        "pumps_active": 0,
        # Highest spectral similarity - visually CONFIRMED (pulsing green ring)
        "linked_geology_record_id": "GEO-000015",
    },
    {
        "zone_id": "ZONE_B",
        "name": "Zone B (South Ridge)",
        "latitude": 21.8385,
        "longitude": 80.2320,
        "spatial_score": 68.0,
        "spatial_provenance": SYNTHETIC_ZONE_TAG,
        "zone_type": "Medium-prospectivity candidate",
        "operational_status": "Dry",
        "water_depth_m": 0.0,
        "pumps_active": 2,
        # Mid-high spectral - LIKELY (solid green ring)
        "linked_geology_record_id": "GEO-000005",
    },
    {
        "zone_id": "ZONE_C",
        "name": "Zone C (East Extension)",
        "latitude": 21.8450,
        "longitude": 80.2410,
        "spatial_score": 31.0,
        "spatial_provenance": SYNTHETIC_ZONE_TAG,
        "zone_type": "Low-prospectivity candidate",
        "operational_status": "Anomaly",
        "water_depth_m": 0.8,
        "pumps_active": 1,
        # Mid spectral - WEAK match (amber dashed ring)
        "linked_geology_record_id": "GEO-000055",
    },
    {
        "zone_id": "ZONE_D",
        "name": "Zone D (Western Bench)",
        "latitude": 21.8460,
        "longitude": 80.2200,
        "spatial_score": 74.0,
        "spatial_provenance": SYNTHETIC_ZONE_TAG,
        "zone_type": "Medium-prospectivity candidate",
        "operational_status": "Active",
        "water_depth_m": 0.0,
        "pumps_active": 0,
        # Lowest spectral - MISMATCH (red dashed ring)
        # Story: spatial model was wrong here; spectral saves us a wasted field crew
        "linked_geology_record_id": "GEO-000026",
    },
]
=======
# Member 5 (weather.py) Rupee Loss Ledger conversion rate.
# TODO(M5): replace with the real reference Mn ore rate (see
# reference_price_inr_per_t in processed_geology.csv for a real IBM figure).
MN_RATE_PER_TON_INR = 3808.49

# NOTE: SAMPLE_EARTH_SPECTRA / ISRO_CLASS_BASELINE_SPECTRA are intentionally
# NOT defined here. app.py's original call to
# spectral.spectral_match(earth_reflectance=C.SAMPLE_EARTH_SPECTRA, ...)
# was a second, disconnected spectral computation that duplicated
# modules.spectral.build_bharveli_aoi_result() with undefined placeholder
# inputs. See app.py fix below -- it now reuses the real AOI result instead,
# so these two constants are no longer needed at all.
>>>>>>> ab8d539bf6e2fff0a189b245984865cd621aa388
