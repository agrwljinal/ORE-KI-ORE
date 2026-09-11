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
ORE_POCKETS = [
    {"name": "Pit 1", "grade_pct": 46.0, "mine_name": None},
    {"name": "Pit B", "grade_pct": 38.0, "mine_name": None},
]

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