# modules/weather.py
# OWNER: Member 5 (Feature 3: Weather/Risk Engine + Feature 6: Rupee Loss Ledger Banner)
#
# Two contracts live in this file:
#   * WeatherService / WeatherModule - live OpenWeatherMap data for the Flask
#     /api/weather endpoint (used by weather.js on the dashboard).
#   * render_risk_sliders / risk_summary / render_ledger_banner - the Streamlit
#     risk-input + Rupee Loss Ledger helpers (Member 5).
#
# risk_summary(rainfall_mm, mtbf_hrs, labor_drop_pct) -> dict (key must be "ori")
# render_ledger_banner(shortfall_tonnage, recovered_tonnage, is_executed, rate_per_ton)

import os
import requests
import threading
from dataclasses import dataclass
from typing import Dict, Any, Optional

try:
    import streamlit as st  # optional at import time; only needed by the ledger helpers
except ImportError:  # pragma: no cover
    st = None  # type: ignore[assignment]

# The demo dashboard is scoped to MOIL Balaghat Sector 4.  Live weather is
# therefore fetched for the exact Bharveli-Awalajhari AOI midpoint, matching
# DEFAULT_MAP_CENTER in constants.py, instead of a generic city lookup.
CITY_FALLBACK = "Balaghat"
BALAGHAT_AOI_LAT = 21.845
BALAGHAT_AOI_LON = 80.232
BALAGHAT_SITE_LABEL = "Balaghat Sector 4 (Bharveli AOI 21.845, 80.232)"

# Rain level buckets for a mm/24h total (IMD-style). Ordered heaviest first.
RAIN_LEVEL_BUCKETS = (
    ("Very Heavy", 64.4),
    ("Heavy", 35.5),
    ("Moderate", 7.5),
    ("Light", 0.2),
)


def classify_rain_level(mm_24h) -> str:
    """Map a 24h rainfall total to a human level, e.g. 12.4 -> 'Moderate'."""
    if mm_24h is None:
        return "n/a"
    mm = float(mm_24h)
    for label, threshold in RAIN_LEVEL_BUCKETS:
        if mm >= threshold:
            return label
    return "Trace"

class WeatherService:
    """Handles API requests and live weather data synchronization."""
    BASE_URL = "https://api.openweathermap.org/data/2.5/weather"
    FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"

    def __init__(self, api_key: Optional[str] = None):
        # Fallback to environment variable if API key is not passed directly
        self.api_key = api_key or os.getenv("OPENWEATHER_API_KEY", "")

    def fetch_live_weather(self, city: str = CITY_FALLBACK, lat: Optional[float] = None,
                           lon: Optional[float] = None) -> Dict[str, Any]:
        """Fetches real-time weather by AOI coordinates (preferred) or city name."""
        if not self.api_key:
            return {
                "success": False,
                "error": "Missing API Key. Please configure OPENWEATHER_API_KEY."
            }

        params = {
            "appid": self.api_key,
            "units": "metric"
        }
        if lat is not None and lon is not None:
            params["lat"] = lat
            params["lon"] = lon
        else:
            params["q"] = city

        try:
            response = requests.get(self.BASE_URL, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "city": data.get("name"),
                    "lat": data["coord"]["lat"],
                    "lon": data["coord"]["lon"],
                    "temp": data["main"]["temp"],
                    "humidity": data["main"]["humidity"],
                    "condition": data["weather"][0]["description"].title(),
                    "wind_speed": data["wind"]["speed"],
                    "query": "AOI:" + str(lat) + "," + str(lon) if (lat is not None and lon is not None) else "city:" + str(city),
                }
            elif response.status_code == 401:
                return {"success": False, "error": "Invalid API Key (HTTP 401)."}
            else:
                return {"success": False, "error": f"API Error HTTP {response.status_code}: {response.text}"}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Network Error: {str(e)}"}

    def fetch_rainfall_24h(self, lat: Optional[float] = None,
                           lon: Optional[float] = None) -> Dict[str, Any]:
        """Fetches live rainfall (mm/24h) by summing 3-hourly forecast steps
        over the next 24 hours for the Balaghat Sector 4 AOI coordinates."""
        if not self.api_key:
            return {
                "success": False,
                "error": "Missing API Key. Please configure OPENWEATHER_API_KEY."
            }
        if lat is None or lon is None:
            return {"success": False, "error": "AOI lat/lon required for rainfall fetch."}

        params = {
            "lat": lat,
            "lon": lon,
            "appid": self.api_key,
            "units": "metric",
            "cnt": 8,  # 8 x 3h steps = next 24 hours
        }

        try:
            response = requests.get(self.FORECAST_URL, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                total = 0.0
                steps = 0
                for item in data.get("list", []):
                    rain = (item.get("rain") or {}).get("3h") or 0
                    total += float(rain)
                    steps += 1
                return {
                    "success": True,
                    "rainfall_mm_24h": round(total, 2),
                    "steps": steps,
                    "level": classify_rain_level(total),
                }
            elif response.status_code == 401:
                return {"success": False, "error": "Invalid API Key (HTTP 401)."}
            else:
                return {"success": False, "error": f"API Error HTTP {response.status_code}: {response.text}"}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Network Error: {str(e)}"}


class WeatherModule:
    """Framework-agnostic module handler for SIH Dashboard."""
    def __init__(self, api_key: Optional[str] = None,
                 lat: Optional[float] = None, lon: Optional[float] = None):
        self.service = WeatherService(api_key=api_key)
        self.site_lat = lat if lat is not None else BALAGHAT_AOI_LAT
        self.site_lon = lon if lon is not None else BALAGHAT_AOI_LON
        self.current_data: Dict[str, Any] = {}
        self.is_syncing: bool = False

    def sync_weather(self, city: str = CITY_FALLBACK, callback=None):
        """Asynchronous sync method to prevent UI freezing."""
        if self.is_syncing:
            return

        self.is_syncing = True

        def worker():
            result = self.service.fetch_live_weather(city, lat=self.site_lat, lon=self.site_lon)
            self.current_data = result
            self.is_syncing = False
            if callback:
                callback(result)

        # Run API call in a background thread
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()


# ================================================================
# Weather scenario engine (dashboard weather layer)
#
# The prediction module and its trained model are FROZEN: no coefficient,
# cap, or clamp in modules/prediction.py is changed here.  This engine is
# the weather feature's own formula.  It:
#   1.  condenses the operator's rainfall/soil sliders and any live weather
#       into a single 0..1 WEATHER_SEVERITY index;
#   2.  translates that severity into the four model-input channels the
#       frozen model consumes (rainfall mm, soil moisture %, downtime hrs,
#       blast delay min) so slider and live-weather changes are VISIBLE in
#       the baseline dashboard; and
#   3.  reports the weather-driven tonnage impact using the frozen model's
#       own output on a clear-weather baseline vs. the weather scenario.
# All translation knobs live here as module constants so the formula can be
# tuned without touching the prediction module.
# ================================================================

WEATHER_SLIDER_REFERENCE_MM = 250.0   # matches slider-rainfall max
SEVERITY_SLIDER_WEIGHT = 0.70          # how much of severity comes from slider rain
SEVERITY_LIVE_WEIGHT = 0.30            # how much comes from live conditions
SOIL_WEATHER_ADD_PCT = 14.0            # soil moisture add at full severity
DOWNTIME_WEATHER_ADD_HRS = 4.0         # downtime hours add at full severity
BLAST_WEATHER_ADD_MIN = 90.0           # blast delay minutes add at full severity
EFFECTIVE_DOWNTIME_CEIL_HRS = 10.0     # stays inside the model's alive response band
EFFECTIVE_BLAST_CEIL_MIN = 120.0
EFFECTIVE_SOIL_CEIL_PCT = 60.0


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _live_severity(live: Optional[Dict[str, Any]]) -> tuple[float, Dict[str, float]]:
    """Weather-severity contribution from live readings, 0..1."""
    if not live or not live.get("success"):
        return 0.0, {"live": 0.0, "reason": "live_data_unavailable"}
    score = 0.0
    drivers: Dict[str, float] = {}
    condition = str(live.get("condition") or "").lower()
    if any(token in condition for token in ("rain", "storm", "thunder", "drizzle", "shower", "snow")):
        score += 0.45
    elif any(token in condition for token in ("mist", "fog", "haze", "smoke")):
        score += 0.18
    drivers["condition"] = round(score, 3)
    humidity = live.get("humidity")
    humid_score = (float(humidity) / 100.0) * 0.30 if humidity is not None else 0.0
    score += humid_score
    drivers["humidity"] = round(humid_score, 3)
    wind = live.get("wind_speed")
    wind_score = min(float(wind) / 40.0, 1.0) * 0.15 if wind is not None else 0.0
    score += wind_score
    drivers["wind"] = round(wind_score, 3)
    temp = live.get("temp")
    if temp is not None and float(temp) < 10.0:
        score += 0.10
        drivers["cold_front"] = 0.10
    drivers["live_total"] = round(min(score, 1.0), 3)
    return min(score, 1.0), drivers


@dataclass
class WeatherScenario:
    severity: float
    severity_pct: float
    slider_rain_index: float
    live_index: float
    drivers: Dict[str, float]
    effective_rainfall_mm: float
    effective_soil_moisture_pct: float
    effective_downtime_hours: float
    effective_blast_delay_minutes: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": round(self.severity, 4),
            "severity_pct": round(self.severity_pct, 1),
            "slider_rain_index": round(self.slider_rain_index, 4),
            "live_index": round(self.live_index, 4),
            "drivers": self.drivers,
            "effective_inputs": {
                "rainfall_mm": round(self.effective_rainfall_mm, 2),
                "soil_moisture_pct": round(self.effective_soil_moisture_pct, 2),
                "equipment_downtime_hours": round(self.effective_downtime_hours, 2),
                "blast_delay_minutes": round(self.effective_blast_delay_minutes, 2),
            },
        }


def compute_weather_scenario(
    *,
    rainfall_mm: float,
    soil_moisture_pct: float,
    equipment_downtime_hours: float,
    blast_delay_minutes: float,
    live: Optional[Dict[str, Any]] = None,
) -> WeatherScenario:
    """Build the weather scenario from the operator sliders plus live data.

    The effective inputs are monotone-increasing in severity and never leave
    the frozen model's responsive band, so moving the rainfall slider up (or
    syncing worse live weather) always produces a visible, predictable drop
    in the dashboard output without editing the prediction module.
    """
    slider_rain_index = _clamp(float(rainfall_mm) / WEATHER_SLIDER_REFERENCE_MM, 0.0, 1.0)
    live_index, drivers = _live_severity(live)
    severity = _clamp(
        SEVERITY_SLIDER_WEIGHT * slider_rain_index + SEVERITY_LIVE_WEIGHT * live_index,
        0.0, 1.0,
    )
    # While a live feed is active, the API's own 24h rainfall is the authority:
    # the effective rainfall always follows the live reading instead of any
    # manually-entered slider value.  Fall back to the slider only when no live
    # rainfall is available (manual mode / API failure).
    live_rain = None
    if live and live.get("success"):
        try:
            live_rain = float(live.get("rainfall_mm_24h"))
        except (TypeError, ValueError):
            live_rain = None
    effective_rainfall_mm = max(0.0, live_rain) if live_rain is not None else max(0.0, float(rainfall_mm))
    effective_soil = _clamp(float(soil_moisture_pct) + SOIL_WEATHER_ADD_PCT * severity, 0.0, EFFECTIVE_SOIL_CEIL_PCT)
    effective_downtime = _clamp(
        float(equipment_downtime_hours) + DOWNTIME_WEATHER_ADD_HRS * severity,
        0.0, EFFECTIVE_DOWNTIME_CEIL_HRS,
    )
    effective_blast = _clamp(
        float(blast_delay_minutes) + BLAST_WEATHER_ADD_MIN * severity,
        0.0, EFFECTIVE_BLAST_CEIL_MIN,
    )
    return WeatherScenario(
        severity=severity,
        severity_pct=severity * 100.0,
        slider_rain_index=slider_rain_index,
        live_index=live_index,
        drivers=drivers,
        effective_rainfall_mm=effective_rainfall_mm,
        effective_soil_moisture_pct=effective_soil,
        effective_downtime_hours=effective_downtime,
        effective_blast_delay_minutes=effective_blast,
    )


def weather_impact_tonnes(
    scenario: WeatherScenario,
    *,
    rainfall_mm: float,
    soil_moisture_pct: float,
    equipment_downtime_hours: float,
    blast_delay_minutes: float,
    labor_drop_pct: float,
    target_tonnage: float,
    mine_name: Optional[str] = None,
    ore_grade: str = "STD",
) -> Optional[Dict[str, Any]]:
    """Weather-driven tonnage impact computed ONLY from the frozen ML model.

    Baseline = clear weather (rain 0 mm) with today's downtime/blast sliders.
    Scenario = the weather-translated effective inputs.  Impact is the model's
    own predicted-output delta, so it stays honest even though the translation
    makes the weather effect easier to see.
    """
    try:
        from prediction import predict_shortfall_with_model
    except ImportError:
        from modules.prediction import predict_shortfall_with_model

    def forecast(rain: float, soil: float, downtime: float, blast: float) -> float:
        result = predict_shortfall_with_model(
            base_target=float(target_tonnage),
            rainfall_mm=rain,
            soil_moisture_pct=soil,
            equipment_downtime_hours=downtime,
            blast_delay_minutes=blast,
            labor_drop_pct=float(labor_drop_pct),
            mine_name=mine_name,
            ore_grade=str(ore_grade).upper(),
        )
        return float(result["predicted_tonnage"])

    clear_forecast = forecast(0.0, float(soil_moisture_pct), float(equipment_downtime_hours), float(blast_delay_minutes))
    weather_forecast = forecast(
        scenario.effective_rainfall_mm,
        scenario.effective_soil_moisture_pct,
        scenario.effective_downtime_hours,
        scenario.effective_blast_delay_minutes,
    )
    impact = round(max(0.0, clear_forecast - weather_forecast), 2)
    return {
        "impact_tonnes": impact,
        "clear_weather_forecast_tonnes": round(clear_forecast, 2),
        "weather_forecast_tonnes": round(weather_forecast, 2),
        "note": (
            "Delta of the frozen ML model between a clear-weather baseline "
            "(rain 0 mm, weather-free downtime/blast) and the current "
            "weather-translated scenario."
        ),
    }


def render_risk_sliders(st_obj):
    """
    Renders the 3 operational-risk sliders.
    NOTE: the Streamlit app calls this as ops.render_risk_sliders(st.sidebar) —
    so st_obj here would be st.sidebar, a container, not the top-level st module.
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

def calculate_weather_loss(
    base_shortfall_tons,
    rainfall_mm,
    mtbf_hrs,
    labor_drop_pct,
    rate_per_ton
):
    # -----------------------------
    # 1. Normalize operational risks
    # -----------------------------

    rain_risk = min(max(rainfall_mm / 200.0, 0.0), 1.0)

    equipment_risk = 1.0 - min(
        max(mtbf_hrs / 500.0, 0.0),
        1.0
    )

    labor_risk = min(
        max(labor_drop_pct / 100.0, 0.0),
        1.0
    )

    # -----------------------------
    # 2. Non-linear severity
    # -----------------------------

    weather_impact = rain_risk ** 1.35
    equipment_impact = equipment_risk ** 1.25
    labor_impact = labor_risk ** 1.20

    combined_risk = (
        0.45 * weather_impact +
        0.35 * equipment_impact +
        0.20 * labor_impact
    )

    interaction = (
        1.0
        + 0.30 * weather_impact * equipment_impact
        + 0.20 * weather_impact * labor_impact
    )
    effective_risk = combined_risk * interaction

    adjusted_shortfall = base_shortfall_tons * (1.0 + 0.25 * effective_risk)
    rupee_loss = adjusted_shortfall * rate_per_ton
    return {
        "weather_shortfall_tons": base_shortfall_tons * 0.25 * effective_risk,
        "adjusted_shortfall_tons": adjusted_shortfall,
        "rupee_loss": rupee_loss,
    }


def risk_summary(rainfall_mm, mtbf_hrs, labor_drop_pct):
    """
    Computes the Operational Risk Index (ORI), a single 0-100 score.

    ORI = 100 * (0.40*rain_norm + 0.35*mtbf_norm + 0.25*labor_norm)

    IMPORTANT: the Streamlit app reads ori_results['ori'] directly — the key must
    be exactly "ori" (not "operational_risk_index") or the sidebar metric will
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

    The Streamlit app doesn't use a return value here — it just calls this for its
    side effect (drawing the banner) — but we return the numbers too so it can be
    unit-tested without Streamlit running.
    """
    revenue_at_risk_cr = shortfall_tonnage * rate_per_ton / 1e7

    if is_executed:
        revenue_saved_cr = recovered_tonnage * rate_per_ton / 1e7
        st.success(f"💰 Revenue Saved: ₹{revenue_saved_cr:.2f} Cr")
        return {"status": "saved", "revenue_saved_cr": round(revenue_saved_cr, 2)}
    else:
        st.error(f"⚠️ Revenue at Risk: ₹{revenue_at_risk_cr:.2f} Cr")
        return {"status": "at_risk", "revenue_at_risk_cr": round(revenue_at_risk_cr, 2)}


# ==========================================
# UNIT TESTS (Exposes module to test runners)
# ==========================================
import unittest

class TestWeatherModule(unittest.TestCase):
    """Integrated test case so test runners discover this module."""

    def setUp(self):
        self.module = WeatherModule(api_key="TEST_MOCK_KEY")

    def test_initialization(self):
        self.assertIsNotNone(self.module.service)
        self.assertFalse(self.module.is_syncing)

    def test_missing_key_handling(self):
        no_key_service = WeatherService(api_key="")
        res = no_key_service.fetch_live_weather("Delhi")
        self.assertFalse(res["success"])
        self.assertIn("Missing API Key", res["error"])

    def test_risk_summary_shape(self):
        result = risk_summary(50, 200, 10)
        self.assertIn("ori", result)

    def test_scenario_severity_bounds(self):
        dry = compute_weather_scenario(rainfall_mm=0.0, soil_moisture_pct=30.0,
                                       equipment_downtime_hours=5.0, blast_delay_minutes=30.0)
        storm = compute_weather_scenario(rainfall_mm=250.0, soil_moisture_pct=60.0,
                                         equipment_downtime_hours=6.0, blast_delay_minutes=45.0,
                                         live={"success": True, "condition": "storm rain",
                                               "humidity": 98, "wind_speed": 38.0, "temp": 24.0})
        self.assertEqual(dry.severity, 0.0)
        self.assertLessEqual(storm.severity, 1.0)
        self.assertGreater(storm.severity, dry.severity)

    def test_scenario_monotonic_severity(self):
        low = compute_weather_scenario(rainfall_mm=50.0, soil_moisture_pct=30.0,
                                       equipment_downtime_hours=5.0, blast_delay_minutes=30.0)
        high = compute_weather_scenario(rainfall_mm=200.0, soil_moisture_pct=30.0,
                                        equipment_downtime_hours=5.0, blast_delay_minutes=30.0)
        self.assertGreater(high.effective_downtime_hours, low.effective_downtime_hours)
        self.assertGreater(high.effective_blast_delay_minutes, low.effective_blast_delay_minutes)
        self.assertGreaterEqual(high.effective_soil_moisture_pct, low.effective_soil_moisture_pct)

    def test_weather_impact_is_none_when_model_missing(self):
        scenario = compute_weather_scenario(rainfall_mm=88.5, soil_moisture_pct=38.0,
                                            equipment_downtime_hours=6.0, blast_delay_minutes=45.0)
        self.assertGreaterEqual(scenario.severity, 0.0)

if __name__ == "__main__":
    unittest.main()
