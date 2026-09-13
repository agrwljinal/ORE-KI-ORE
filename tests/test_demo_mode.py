"""Route-level tests for the SYNTHETIC_DEMO vegetation-masking demo mode."""

import unittest

from app import _prediction_view, app


class VegDemoModeRouteTests(unittest.TestCase):
    def test_prediction_banner_applies_plan_recovery_once(self):
        view = _prediction_view(
            {"predicted_tonnage": 746.0, "shortfall_tonnage": 154.0},
            900.0,
            {"total_expected_recovery_tonnes": 63.0},
        )

        self.assertEqual(view["predicted_tonnage"], 809.0)
        self.assertEqual(view["shortfall_tonnage"], 91.0)
        self.assertEqual(view["banner"]["remaining_shortfall_tonnes"], 91.0)

    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_default_zones_are_unmasked_and_honest(self):
        body = self.client.get("/api/zones").get_json()

        self.assertFalse(body["demo_mode"])
        for zone in body["zones"]:
            self.assertEqual(zone["vegetation_mask"]["status"], "NO_PIXEL_DATA")
            self.assertFalse(zone["vegetation_mask"]["applied"])
            self.assertIsNone(zone.get("demo_chip"))
            self.assertIn("SYNTHETIC", zone["zone_reflectance_provenance"])

    def test_demo_zones_apply_mask_and_report_chain(self):
        body = self.client.get("/api/zones?veg_demo=1").get_json()

        self.assertTrue(body["demo_mode"])
        self.assertEqual(body["dataset"], "SYNTHETIC_DEMO")
        for zone in body["zones"]:
            mask = zone["vegetation_mask"]
            chip = zone["demo_chip"]
            self.assertEqual(mask["status"], "APPLIED")
            self.assertEqual(
                zone["zone_reflectance_provenance"],
                "SYNTHETIC_DEMO_CHIP_VEG_MASKED_PIXEL_MEANS",
            )
            # The visible chain: total -> removed rows -> usable -> spectral -> priority
            self.assertEqual(mask["total_pixels"], chip["total_pixels"])
            self.assertEqual(
                mask["valid_pixels_remaining"],
                mask["total_pixels"]
                - mask["vegetation_pixels_removed"]
                - mask["water_pixels_excluded"],
            )
            # The chip class labels reconcile EXACTLY with the mask accounting,
            # so the client-side surface renderer matches the backend numbers.
            veg = sum(1 for cls in chip["classes"] if cls == "vegetation")
            water = sum(1 for cls in chip["classes"] if cls == "water")
            exposed = sum(1 for cls in chip["classes"] if cls == "exposed")
            self.assertEqual(mask["vegetation_pixels_removed"], veg)
            self.assertEqual(mask["water_pixels_excluded"], water)
            self.assertEqual(mask["valid_pixels_remaining"], exposed)
            if mask["scorable"]:
                self.assertIsNotNone(zone["spectral_similarity"])
            else:
                self.assertIsNone(zone["spectral_similarity"])  # no misleading score

    def test_zone_c_demo_is_scorable_like_every_other_zone(self):
        body = self.client.get("/api/zones?veg_demo=1").get_json()
        zone = next(z for z in body["zones"] if z["zone_id"] == "ZONE_C")

        # ZONE_C behaves exactly like the other zones: its heavily-vegetated
        # chip still leaves enough exposed surface to produce a real score.
        self.assertTrue(zone["vegetation_mask"]["scorable"])
        self.assertIsNotNone(zone["spectral_similarity"])
        self.assertIsNotNone(zone["best_mineral_match"])
        self.assertIsNotNone(zone["zone_reflectance"])
        self.assertTrue(zone["spectral_scene"])
        # Fused priority is a genuine fusion, not a spatial-only fallback.
        self.assertNotEqual(zone["final_exploration_score"], zone["spatial_score"])

    def test_demo_mode_never_pretends_to_be_real_satellite(self):
        body = self.client.get("/api/zones?veg_demo=1").get_json()

        self.assertTrue(all(z["data_source"]["dataset"] == "SYNTHETIC_DEMO" for z in body["zones"]))
        self.assertIn("NOT a real Sentinel-2 observation", body["zones"][0]["data_source"]["note"])

    def test_detail_endpoint_honors_demo_flag(self):
        plain = self.client.get("/api/zones/ZONE_A").get_json()
        demo = self.client.get("/api/zones/ZONE_A?veg_demo=1").get_json()

        self.assertEqual(plain["vegetation_mask"]["status"], "NO_PIXEL_DATA")
        self.assertEqual(demo["vegetation_mask"]["status"], "APPLIED")
        self.assertEqual(demo["demo_chip"]["dataset"], "SYNTHETIC_DEMO")
        self.assertEqual(demo["demo_chip"]["total_pixels"], 400)

    def test_aoi_level_result_stays_aoi_level_prototype(self):
        body = self.client.get("/api/spectral").get_json()

        self.assertAlmostEqual(body["similarity_pct"], 97.84, places=2)
        self.assertEqual(body["vegetation_mask"]["level"], "AOI_LEVEL_PROTOTYPE")
        self.assertNotIn("demo_chip", body)

    def test_xai_route_restores_confidence_and_explanation_shape(self):
        body = self.client.get("/api/xai").get_json()

        self.assertEqual(body["status"], "success")
        self.assertIn("confidence_pct", body)
        self.assertIn("confidence_status", body)
        self.assertIsInstance(body["confidence_pct"], (int, float))
        self.assertGreaterEqual(body["confidence_pct"], 0)
        self.assertLessEqual(body["confidence_pct"], 100)
        self.assertIn("xai_chart_data", body)
        self.assertIsInstance(body["xai_chart_data"], list)
        self.assertGreater(len(body["xai_chart_data"]), 0)
        self.assertIn("xai_bullet_reasons", body)
        self.assertIsInstance(body["xai_bullet_reasons"], list)

    def test_xai_route_accepts_slider_payload_and_returns_expected_keys(self):
        payload = {
            "rainfall_mm": 90.0,
            "soil_moisture_pct": 40.0,
            "equipment_downtime_hours": 8.0,
            "blast_delay_minutes": 50.0,
            "labor_drop_pct": 12.0,
            "target_tonnage": 14500.0,
            "ore_grade": "STD",
        }
        body = self.client.post("/api/xai", json=payload)

        self.assertEqual(body.status_code, 200)
        data = body.get_json()
        self.assertEqual(data["status"], "success")
        self.assertIn("confidence_pct", data)
        self.assertIn("confidence_status", data)
        self.assertIn("attributions", data)
        self.assertEqual(set(data["attributions"].keys()), {
            "Rainfall",
            "Soil Moisture",
            "Equipment Downtime",
            "Blast Delay",
            "Labor Drop",
            "Ore Quality",
        })
        self.assertIn("xai_chart_data", data)
        self.assertIn("xai_bullet_reasons", data)
        self.assertIn("narrative", data)


if __name__ == "__main__":
    unittest.main()