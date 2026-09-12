"""End-to-end NDVI vegetation-filter demo flow (synthetic chip).

Exercises: zone chip (B04/B08/B11/B12 + classes) -> per-pixel NDVI ->
vegetation mask -> statistics -> Flask API exposure. All the numbers the UI
renders must come from the generated pixel grid + the NDVI calculation - this
file proves the percentages/counts reconcile exactly.
"""

import unittest

import constants as C
from app import app
from modules import spectral


class NdviDemoFlowTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()

    # -- chip -> NDVI -------------------------------------------------------
    def test_chip_has_all_bands_and_per_pixel_ndvi(self):
        for zone in C.CANDIDATE_ZONES:
            chip = spectral.simulate_zone_chip(zone)
            total = chip["total_pixels"]
            self.assertEqual([len(v) for v in chip["bands"].values()], [total] * 4)
            for band in ("B04", "B08", "B11", "B12"):
                self.assertIn(band, chip["bands"])
            self.assertEqual(len(chip["ndvi"]), total)
            self.assertEqual(len(chip["classes"]), total)

            recomputed = spectral.compute_ndvi(chip["bands"]["B04"], chip["bands"]["B08"])
            for given, expected in zip(chip["ndvi"], recomputed):
                self.assertAlmostEqual(given, expected, places=6)

    def test_class_ndvi_windows_follow_the_mask_contract(self):
        for zone in C.CANDIDATE_ZONES:
            chip = spectral.simulate_zone_chip(zone)
            hi = chip["ndvi_threshold"]          # NDVI > hi  -> vegetation
            lo = chip["ndvi_water_low_threshold"]  # NDVI < lo -> water
            for cls, ndvi in zip(chip["classes"], chip["ndvi"]):
                if cls == "vegetation":
                    self.assertGreater(ndvi, hi)
                elif cls == "water":
                    self.assertLess(ndvi, lo)
                else:  # exposed / usable surface pool
                    self.assertTrue(lo <= ndvi <= hi)

    # -- mask accounting ----------------------------------------------------
    def test_mask_counts_reconcile_exactly_with_pixel_grid(self):
        for zone in C.CANDIDATE_ZONES:
            chip = spectral.simulate_zone_chip(zone)
            mask = spectral.zone_vegetation_mask({}, band_pixels=chip["bands"])
            veg = sum(1 for c in chip["classes"] if c == "vegetation")
            water = sum(1 for c in chip["classes"] if c == "water")
            exposed = sum(1 for c in chip["classes"] if c == "exposed")

            self.assertEqual(mask.total_pixels, chip["total_pixels"])
            self.assertEqual(mask.vegetation_pixels_removed, veg)
            self.assertEqual(mask.water_pixels_excluded, water)
            self.assertEqual(mask.valid_pixels_remaining, exposed)
            self.assertEqual(
                mask.valid_pixels_remaining,
                chip["total_pixels"] - veg - water,
            )

    def test_ndvi_statistics_come_from_the_pixel_grid(self):
        chip = spectral.simulate_zone_chip(C.CANDIDATE_ZONES[0])
        mask = spectral.zone_vegetation_mask({}, band_pixels=chip["bands"])

        self.assertIsNotNone(mask.ndvi_statistics)
        stats = mask.ndvi_statistics
        values = [n for n in chip["ndvi"] if n is not None]
        self.assertAlmostEqual(stats["mean"], sum(values) / len(values), places=6)
        self.assertAlmostEqual(stats["min"], min(values), places=6)
        self.assertAlmostEqual(stats["max"], max(values), places=6)
        # median sanity: must sit between min and max
        self.assertGreaterEqual(stats["median"], stats["min"])
        self.assertLessEqual(stats["median"], stats["max"])

    # -- API -----------------------------------------------------------------
    def test_api_demo_zone_payload_is_complete_and_labelled(self):
        for zone_id in ("ZONE_A", "ZONE_B", "ZONE_C", "ZONE_D"):
            zone = self.client.get(f"/api/zones/{zone_id}?veg_demo=1").get_json()
            mask = zone["vegetation_mask"]
            chip = zone["demo_chip"]

            self.assertEqual(mask["status"], "APPLIED")
            self.assertEqual(chip["dataset"], "SYNTHETIC_DEMO")
            # The renderer needs per-pixel classes + NDVI; the raw bands stay
            # out of the payload (they are the internal NDVI input).
            self.assertTrue(chip["classes"])
            self.assertTrue(chip["ndvi"])
            self.assertNotIn("bands", chip)
            # NDVI statistics from the actual mask engine.
            self.assertIsNotNone(mask["ndvi_statistics"])
            self.assertEqual(
                set(mask["ndvi_statistics"]),
                {"mean", "min", "max", "median"},
            )

            # UI-visible percentages must mirror the pixel grid exactly.
            pct = round((mask["vegetation_pixels_removed"] / mask["total_pixels"]) * 100)
            self.assertEqual(
                mask["surface_coverage_pct"],
                round((mask["valid_pixels_remaining"] / mask["total_pixels"]) * 100, 2),
            )
            self.assertGreaterEqual(mask["surface_coverage_pct"], 0.0)
            self.assertLessEqual(mask["surface_coverage_pct"], 100.0)
            # Total = veg removed + water excluded + usable.
            self.assertEqual(
                mask["total_pixels"],
                mask["vegetation_pixels_removed"]
                + mask["water_pixels_excluded"]
                + mask["valid_pixels_remaining"],
            )

    def test_zone_c_reports_scorable_chain_with_honest_stats(self):
        zone = self.client.get("/api/zones/ZONE_C?veg_demo=1").get_json()
        mask = zone["vegetation_mask"]

        # ZONE_C is heavily vegetated but enough exposed surface survives the
        # NDVI mask to produce a real, zone-specific spectral score.
        self.assertTrue(mask["scorable"])
        self.assertIsNotNone(zone["spectral_similarity"])
        self.assertGreaterEqual(mask["surface_coverage_pct"], 15.0)  # at/above MIN_SURFACE_COVERAGE_PCT
        self.assertIsNotNone(mask["ndvi_statistics"])                # stats still real

    # -- /api/ndvi/filter (the RUN NDVI SURFACE FILTER request) --------------
    def test_ndvi_filter_endpoint_generates_chip_and_mask(self):
        for zone_id in ("ZONE_A", "ZONE_B", "ZONE_C", "ZONE_D"):
            body = self.client.post(
                "/api/ndvi/filter", json={"zone_id": zone_id}
            ).get_json()

            self.assertEqual(body["status"], "APPLIED")
            self.assertTrue(body["applied"])
            self.assertEqual(body["mode"], "SYNTHETIC_DEMO")
            self.assertEqual(len(body["classes"]), body["total_pixels"])
            self.assertEqual(len(body["ndvi"]), body["total_pixels"])
            # Full synthetic chip returned: B04/B08 present for NDVI recomputation.
            self.assertIn("B04", body["bands"])
            self.assertIn("B08", body["bands"])
            self.assertIn("B11", body["bands"])
            self.assertIn("B12", body["bands"])
            # Accounting reconciles exactly with the pixel grid.
            self.assertEqual(
                body["total_pixels"],
                body["vegetation_pixels_removed"]
                + body["water_pixels_excluded"]
                + body["valid_pixels_remaining"],
            )
            self.assertEqual(
                body["vegetation_pct"],
                round((body["vegetation_pixels_removed"] / body["total_pixels"]) * 100),
            )
            self.assertEqual(
                body["usable_surface_pct"],
                round((body["valid_pixels_remaining"] / body["total_pixels"]) * 100, 2),
            )
            self.assertIsNotNone(body["ndvi_statistics"])
            self.assertEqual(body["dataset"], "SYNTHETIC_DEMO")
            self.assertIn("NOT a real Sentinel-2 observation", body["note"])

    def test_ndvi_filter_endpoint_reports_ndvi_from_b04_b08(self):
        body = self.client.post(
            "/api/ndvi/filter", json={"zone_id": "ZONE_A"}
        ).get_json()

        # NDVI exposed to the client must equal (B08-B04)/(B08+B04) per pixel.
        recomputed = spectral.compute_ndvi(body["bands"]["B04"], body["bands"]["B08"])
        for given, expected in zip(body["ndvi"], recomputed):
            self.assertAlmostEqual(given, expected, places=6)

    def test_ndvi_filter_endpoint_error_handling(self):
        missing = self.client.post("/api/ndvi/filter", json={"zone_id": "NOPE"})
        self.assertEqual(missing.status_code, 404)
        self.assertIn("error", missing.get_json())

        no_id = self.client.post("/api/ndvi/filter", json={})
        self.assertEqual(no_id.status_code, 400)

        bare = self.client.post("/api/ndvi/filter", data="not json")
        self.assertEqual(bare.status_code, 400)


if __name__ == "__main__":
    unittest.main()