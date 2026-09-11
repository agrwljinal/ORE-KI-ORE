# modules/weather.py (or modules/ops.py)
# OWNER: Member 5 (Feature 3: Weather/Risk Engine + Feature 6: Rupee Loss Ledger Banner)

# This file must export exactly 3 functions, matching what app.py already calls:
#   render_risk_sliders(st_obj)   -> dict
#   risk_summary(rainfall_mm, mtbf_hrs, labor_drop_pct) -> dict (key must be "ori")
#   render_ledger_banner(shortfall_tonnage, recovered_tonnage, is_executed, rate_per_ton)

import os
import requests
import threading
from typing import Dict, Any, Optional

class WeatherService:
    """Handles API requests and live weather data synchronization."""
    BASE_URL = "https://api.openweathermap.org/data/2.5/weather"
    
    def __init__(self, api_key: Optional[str] = None):
        # Fallback to environment variable if API key is not passed directly
        self.api_key = api_key or os.getenv("OPENWEATHER_API_KEY", "")

    def fetch_live_weather(self, city: str = "Delhi") -> Dict[str, Any]:
        """Fetches real-time weather data for a given city."""
        if not self.api_key:
            return {
                "success": False,
                "error": "Missing API Key. Please configure OPENWEATHER_API_KEY."
            }
            
        params = {
            "q": city,
            "appid": self.api_key,
            "units": "metric"
        }
        
        try:
            response = requests.get(self.BASE_URL, params=params, timeout=10)
            if response.status_code == 200:
                data = response.json()
                return {
                    "success": True,
                    "city": data.get("name"),
                    "temp": data["main"]["temp"],
                    "humidity": data["main"]["humidity"],
                    "condition": data["weather"][0]["description"].title(),
                    "wind_speed": data["wind"]["speed"]
                }
            elif response.status_code == 401:
                return {"success": False, "error": "Invalid API Key (HTTP 401)."}
            else:
                return {"success": False, "error": f"API Error HTTP {response.status_code}: {response.text}"}
        except requests.exceptions.RequestException as e:
            return {"success": False, "error": f"Network Error: {str(e)}"}


class WeatherModule:
    """Framework-agnostic module handler for SIH Dashboard."""
    def __init__(self, api_key: Optional[str] = None):
        self.service = WeatherService(api_key=api_key)
        self.current_data: Dict[str, Any] = {}
        self.is_syncing: bool = False

    def sync_weather(self, city: str = "Delhi", callback=None):
        """Asynchronous sync method to prevent UI freezing."""
        if self.is_syncing:
            return
        
        self.is_syncing = True
        
        def worker():
            result = self.service.fetch_live_weather(city)
            self.current_data = result
            self.is_syncing = False
            if callback:
                callback(result)

        # Run API call in a background thread
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()


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

if __name__ == "__main__":
    unittest.main()
