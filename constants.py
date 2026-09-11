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

# Weekly production target used as the default in the dashboard and as the
# feature-scale input for the prediction pipeline. Frontend default is 14500 MT.
# NOTE: per item 1 of the model refactor, this target is ONLY ever used for
# comparison AFTER the output calculation. It never appears inside the
# predicted-output formula.
BASE_WEEKLY_TARGET_TONS = 14500.0

# Fleet capacity baseline (Member 3 - item 1 refactor).
# Independent, fixed reference for the fleet's realistic best-case weekly
# output. Derived from the historical best-realistic output in
# data/processed_production.csv (weekly realized best ~12,200 MT, weekly
# target best ~13,000 MT; the 14,500 figure is the fleet's capacity target).
# predictedOutput is computed ONLY from this baseline times the operating
# factors (rainfall x uptime x labor x ore grade). The monthly/weekly target
# (BASE_WEEKLY_TARGET_TONS + user slider) is used only afterwards for the
# shortfall / ledger comparison.
FLEET_CAPACITY_BASELINE_TONS = 14500.0

# Ore-grade multipliers (item 3). Illustrative until real assay data replaces
# them. Applied as an independent multiplicative factor on predicted output.
#   HG  = Grade High (44-46% Mn) -> 1.10
#   STD = Grade Standard (38-42% Mn) -> 1.00 (baseline)
#   FB  = Ferro Blend (34-37% Mn) -> 0.88
ORE_GRADE_FACTORS = {
    "HG": 1.10,
    "STD": 1.00,
    "FB": 0.88,
}

# Recentred on the real Bharveli-Awalajhari AOI mid-point (KML bounds
# ~21.83-21.86 lat, 80.21-80.25 lon).
DEFAULT_MAP_CENTER = [21.845, 80.232]

# Member 4 (prescriptive.py) candidate ore pockets for the resource-shift engine.
# TODO(M4): replace with real pit/pocket data; this is a placeholder so the
# app can run end-to-end for a demo.
ORE_POCKETS = [
    {"name": "Pit 1", "grade_pct": 46.0, "mine_name": None},
    {"name": "Pit B", "grade_pct": 38.0, "mine_name": None},
]

# Member 5 (weather.py) Rupee Loss Ledger conversion rate.
# TODO(M5): replace with the real reference Mn ore rate (see
# reference_price_inr_per_t in processed_geology.csv for a real IBM figure).
MN_RATE_PER_TON_INR = 3808.49

# Rupee Gain/Loss ledger rates (item 5 of the prediction refactor).
# The ledger is now derived from the LIVE predicted output vs target gap:
#   loss = (target - predicted) * MN_COST_PER_TON_INR   shown as a loss (red)
#   gain = (predicted - target) * MN_PRICE_PER_TON_INR  shown as a gain (green)
# cost and price are intentionally separate knobs; a real finance team can
# set them to the actual per-ton production cost and the realized Mn price.
MN_COST_PER_TON_INR = 3808.49
MN_PRICE_PER_TON_INR = 3808.49

# Banner tiers (single derived-state rule, item 4).
# Derived from the live (post-recovery) predicted output vs the target:
TIER_TARGET_EXCEEDED_RATIO = 1.0   # predicted >= target  -> GREEN "TARGET EXCEEDED"
TIER_ON_TRACK_RATIO = 0.90         # predicted >= 90%      -> AMBER "ON TRACK / MINOR VARIANCE"
TIER_ON_TRACK_LABEL = "ON TRACK — MINOR VARIANCE"
TIER_TARGET_EXCEEDED_LABEL = "TARGET EXCEEDED — SURPLUS PROJECTED"
TIER_SHORTFALL_LABEL = "SHORTFALL ALERT"
SIM_STATE_LABELS = {
    "unmitigated": "UNMITIGATED RISK",   # ratio < 90% and no mitigation selected
    "mitigating": "MITIGATING",          # recovery actions selected but tail still short
    "optimal": "OPTIMAL — TARGET SECURED",  # at/above target, or fully mitigated
}


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
        # SYNTHETIC_DEMO chip config for the vegetation-masking demo mode.
        "demo_chip": {"size": 20, "seed": 1101, "vegetation_fraction": 0.08, "water_fraction": 0.06},
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
        "demo_chip": {"size": 20, "seed": 1102, "vegetation_fraction": 0.26, "water_fraction": 0.05},
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
        # Heavily vegetated: demo shows the NDVI guard suppressing a misleading
        # spectral score (too few usable surface pixels remain).
        "demo_chip": {"size": 20, "seed": 1103, "vegetation_fraction": 0.90, "water_fraction": 0.02},
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
        "demo_chip": {"size": 20, "seed": 1104, "vegetation_fraction": 0.40, "water_fraction": 0.05},
    },
]