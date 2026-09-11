"""Regression tests for the Bharveli-Awalajhari AOI spectral overlay."""

from pathlib import Path
import unittest

from modules import spectral


REPO_DIR = Path(__file__).resolve().parents[1]
KML_PATH = REPO_DIR / "data" / "07_Aug_2019_1659504705RGLR1LRProjectSite.kml"


class BharveliSpectralResultTests(unittest.TestCase):
    def test_command_header_is_html_not_indented_markdown(self) -> None:
        header = spectral.command_header_html()

        self.assertTrue(header.startswith("<div"))
        self.assertNotIn("\n    <div", header)

    def test_supplied_scene_matches_pyrolusite_at_9784_percent(self) -> None:
        result = spectral.build_bharveli_aoi_result()

        self.assertAlmostEqual(result["similarity"], 0.9784, places=4)
        self.assertEqual(result["label"], "Pyrolusite Spectral Similarity")
        self.assertEqual(result["spectral_potential"], "HIGH")
        self.assertNotIn("mn_concentration", result)
        self.assertNotIn("ore_grade", result)
        self.assertIn("not Mn concentration", result["interpretation"])

    def test_scene_metadata_is_the_supplied_sentinel_scene(self) -> None:
        result = spectral.build_bharveli_aoi_result()

        self.assertEqual(result["scene"]["scene_id"], "S2C_MSIL2A_20260107T051211_R019_T44QMK")
        self.assertEqual(result["scene"]["date"], "2026-01-07")
        self.assertEqual(result["scene"]["tile"], "T44QMK")
        self.assertEqual(result["scene"]["cloud_cover_pct"], 0.000319)


class BharveliBoundaryTests(unittest.TestCase):
    def test_overlay_uses_a_no_key_basemap(self) -> None:
        self.assertEqual(spectral.OVERLAY_BASEMAP, "OpenStreetMap")

    def test_actual_kml_preserves_all_13_boundary_placemarks(self) -> None:
        features = spectral.load_aoi_kml(KML_PATH)

        self.assertEqual(len(features), 13)
        coordinates = [coordinate for feature in features for coordinate in feature["geometry"]["coordinates"]]
        self.assertTrue(all(80.21 < longitude < 80.25 for longitude, _ in coordinates))
        self.assertTrue(all(21.82 < latitude < 21.86 for _, latitude in coordinates))
        self.assertTrue(all(feature["properties"]["source"] == "Supplied 76.409-ha KML boundary" for feature in features))


# ---------------------------------------------------------------------------
# Vegetation (NDVI) masking
# ---------------------------------------------------------------------------
def _multi_pixel_chip():
    """100-element chip: 95 exposed pixels + 5 vegetation-dominated pixels."""
    b04, b08, b11, b12 = [], [], [], []
    for _ in range(95):
        # Bare rock: B04 ~= B08 -> NDVI ~0.05 (exposed, kept)
        b04.append(0.20)
        b08.append(0.22)
        b11.append(0.30)
        b12.append(0.20)
    for _ in range(5):
        # Green vegetation: B08 >> B04 -> NDVI ~0.85 (removed)
        b04.append(0.05)
        b08.append(0.60)
        b11.append(0.30)
        b12.append(0.20)
    return {"B04": b04, "B08": b08, "B11": b11, "B12": b12}


class NdviVegetationMaskTests(unittest.TestCase):
    def test_ndvi_formula(self) -> None:
        self.assertEqual(spectral.compute_ndvi([0.2], [0.2]), [0.0])
        self.assertGreater(spectral.compute_ndvi([0.05], [0.45])[0], 0.7)
        self.assertLess(spectral.compute_ndvi([0.3], [0.1])[0], 0.0)

    def test_zero_denominator_yields_none_not_a_fabricated_value(self) -> None:
        self.assertIsNone(spectral.compute_ndvi([0.0], [0.0])[0])
        self.assertIsNone(spectral.compute_ndvi(["nope"], [0.2])[0])

    def test_default_threshold_is_030(self) -> None:
        self.assertEqual(spectral.NDVI_VEGETATION_THRESHOLD, 0.30)
        self.assertEqual(spectral.NdviMaskConfig().ndvi_threshold, 0.30)

    def test_mask_removes_vegetated_pixels_and_reports_quality(self) -> None:
        result = spectral.apply_ndvi_mask(_multi_pixel_chip())

        self.assertTrue(result.applied)
        self.assertEqual(result.status, spectral.VEG_MASK_APPLIED)
        self.assertEqual(result.total_pixels, 100)
        self.assertEqual(result.vegetation_pixels_removed, 5)
        self.assertEqual(result.valid_pixels_remaining, 95)
        self.assertEqual(result.surface_coverage_pct, 95.0)
        self.assertTrue(result.scorable)
        # Band means are computed over the VALID pixels only.
        self.assertAlmostEqual(result.mean_reflectance["B04"], 0.2, places=6)
        self.assertAlmostEqual(result.mean_reflectance["B08"], 0.22, places=6)

    def test_quality_dict_shape(self) -> None:
        result = spectral.apply_ndvi_mask(_multi_pixel_chip())
        quality = result.quality()

        self.assertEqual(
            set(quality.keys()),
            {"total_pixels", "vegetation_pixels_removed", "water_pixels_excluded",
             "valid_pixels_remaining", "surface_coverage_pct", "ndvi_statistics"},
        )
        self.assertEqual(quality["total_pixels"], 100)
        self.assertIsNotNone(quality["ndvi_statistics"])
        self.assertEqual(
            set(quality["ndvi_statistics"]),
            {"mean", "min", "max", "median"},
        )

    def test_threshold_is_configurable(self) -> None:
        # With a 0.01 threshold every pixel (NDVI >= 0.048) is 'vegetation'.
        config = spectral.NdviMaskConfig(ndvi_threshold=0.01)
        result = spectral.apply_ndvi_mask(_multi_pixel_chip(), config)

        self.assertEqual(result.vegetation_pixels_removed, 100)
        self.assertEqual(result.valid_pixels_remaining, 0)
        self.assertFalse(result.scorable)
        self.assertIsNone(result.mean_reflectance)
        self.assertIsNotNone(result.reason)

    def test_all_vegetation_does_not_produce_a_misleading_score(self) -> None:
        bands = {
            "B04": [0.05] * 50,
            "B08": [0.60] * 50,
            "B11": [0.30] * 50,
            "B12": [0.20] * 50,
        }
        result = spectral.apply_ndvi_mask(bands)

        self.assertEqual(result.vegetation_pixels_removed, 50)
        self.assertEqual(result.valid_pixels_remaining, 0)
        self.assertEqual(result.surface_coverage_pct, 0.0)
        self.assertFalse(result.scorable)
        self.assertIsNone(result.mean_reflectance)
        self.assertIn("All 50 pixels", result.reason)

    def test_too_few_valid_pixels_do_not_score(self) -> None:
        bands = {
            "B04": [0.05] * 99 + [0.20],
            "B08": [0.60] * 99 + [0.22],
            "B11": [0.30] * 100,
            "B12": [0.20] * 100,
        }
        result = spectral.apply_ndvi_mask(bands)

        self.assertEqual(result.vegetation_pixels_removed, 99)
        self.assertEqual(result.valid_pixels_remaining, 1)
        self.assertLess(result.surface_coverage_pct, spectral.MIN_SURFACE_COVERAGE_PCT)
        self.assertFalse(result.scorable)
        self.assertIsNone(result.mean_reflectance)
        self.assertIn("thresholds", result.reason)

    def test_masked_mean_feeds_mineral_scoring(self) -> None:
        result = spectral.apply_ndvi_mask(_multi_pixel_chip())

        scores = spectral.score_zone_against_references(result.mean_reflectance)
        self.assertIn("pyrolusite", scores)
        self.assertTrue(0.0 <= scores["pyrolusite"] <= 100.0)

    def test_no_pixel_data_is_reported_honestly(self) -> None:
        result = spectral.zone_vegetation_mask(zone_config={}, band_pixels=None)

        self.assertEqual(result.status, spectral.VEG_MASK_NO_PIXEL_DATA)
        self.assertFalse(result.applied)
        self.assertFalse(result.scorable)
        self.assertIsNone(result.total_pixels)
        self.assertIsNone(result.mean_reflectance)
        self.assertIn("no per-pixel", result.reason.lower())

    def test_water_pixels_are_excluded_from_usable_surface(self) -> None:
        # 10 water pixels (B08 << B04 -> NDVI far below the water threshold).
        bands = {
            "B04": [0.20] * 90 + [0.08] * 10,
            "B08": [0.22] * 90 + [0.03] * 10,
            "B11": [0.30] * 100,
            "B12": [0.20] * 100,
        }
        result = spectral.apply_ndvi_mask(bands)

        self.assertEqual(result.water_pixels_excluded, 10)
        self.assertEqual(result.valid_pixels_remaining, 90)
        self.assertEqual(result.total_pixels, 100)

    def test_default_zone_pixel_provider_is_none(self) -> None:
        self.assertIsNone(spectral.get_zone_pixel_data({"zone_id": "Z1"}))
        old = spectral.ZONE_PIXEL_DATA_PROVIDER
        try:
            spectral.ZONE_PIXEL_DATA_PROVIDER = lambda cfg: None
            self.assertIsNone(spectral.get_zone_pixel_data({"zone_id": "Z1"}))
        finally:
            spectral.ZONE_PIXEL_DATA_PROVIDER = old

    def test_zone_mask_accepts_explicit_band_pixels(self) -> None:
        result = spectral.zone_vegetation_mask(zone_config=None, band_pixels=_multi_pixel_chip())

        self.assertTrue(result.applied)
        self.assertEqual(result.vegetation_pixels_removed, 5)

    def test_aoi_result_is_labelled_aoi_level_and_mask_not_applied(self) -> None:
        result = spectral.build_bharveli_aoi_result()

        self.assertAlmostEqual(result["similarity"], 0.9784, places=4)
        self.assertEqual(result["vegetation_mask"]["status"], "NOT_APPLIED")
        self.assertEqual(result["vegetation_mask"]["level"], "AOI_LEVEL_PROTOTYPE")


class SyntheticDemoChipTests(unittest.TestCase):
    """The simulate_zone_chip demo mode must be clearly SYNTHETIC_DEMO."""

    def test_chip_is_explicitly_labelled_synthetic_demo(self) -> None:
        chip = spectral.simulate_zone_chip({
            "zone_id": "ZONE_A", "linked_geology_record_id": "GEO-000015",
            "demo_chip": {"size": 10, "seed": 7, "vegetation_fraction": 0.2},
        })

        self.assertEqual(chip["dataset"], spectral.SIMULATED_CHIP_DATASET)
        self.assertEqual(chip["provenance"], spectral.SIMULATED_CHIP_PROVENANCE)
        self.assertEqual(chip["total_pixels"], 100)
        self.assertIn("NOT a Sentinel-2 observation", chip["note"])

    def test_chip_is_deterministic_for_a_given_seed(self) -> None:
        config = {"zone_id": "ZONE_A", "linked_geology_record_id": "GEO-000015",
                  "demo_chip": {"size": 20, "seed": 1101, "vegetation_fraction": 0.08}}
        first = spectral.simulate_zone_chip(config)
        second = spectral.simulate_zone_chip(config)

        self.assertEqual(first["bands"], second["bands"])
        self.assertEqual(first["classes"], second["classes"])
        self.assertEqual(first["seed"], second["seed"])

    def test_chip_class_counts_reconcile_exactly_with_mask(self) -> None:
        config = {"zone_id": "ZONE_A", "linked_geology_record_id": "GEO-000015",
                  "demo_chip": {"size": 20, "seed": 1101, "vegetation_fraction": 0.08,
                                "water_fraction": 0.06}}
        chip = spectral.simulate_zone_chip(config)
        mask = spectral.apply_ndvi_mask(chip["bands"])

        veg = sum(1 for cls in chip["classes"] if cls == "vegetation")
        water = sum(1 for cls in chip["classes"] if cls == "water")
        exposed = sum(1 for cls in chip["classes"] if cls == "exposed")
        self.assertEqual(mask.vegetation_pixels_removed, veg)
        self.assertEqual(mask.water_pixels_excluded, water)
        self.assertEqual(mask.valid_pixels_remaining, exposed)
        self.assertEqual(veg + water + exposed, mask.total_pixels)
        self.assertTrue(all(cls in ("exposed", "vegetation", "water") for cls in chip["classes"]))

    def test_vegetation_pixels_rise_above_threshold_exposed_stays_low(self) -> None:
        config = {"zone_id": "ZONE_A", "linked_geology_record_id": "GEO-000015",
                  "demo_chip": {"size": 20, "seed": 1101, "vegetation_fraction": 0.08}}
        chip = spectral.simulate_zone_chip(config)
        mask = spectral.apply_ndvi_mask(chip["bands"])

        self.assertTrue(mask.applied)
        self.assertGreater(mask.vegetation_pixels_removed, 0)  # the demo visibly removes canopy
        self.assertTrue(mask.scorable)
        # Surviving exposed-surface means stay below the vegetation threshold.
        mean = mask.mean_reflectance
        mean_ndvi = (mean["B08"] - mean["B04"]) / (mean["B08"] + mean["B04"])
        self.assertLessEqual(mean_ndvi, spectral.NDVI_VEGETATION_THRESHOLD)

    def test_heavy_vegetation_chip_triggers_no_misleading_score(self) -> None:
        chip = spectral.simulate_zone_chip({
            "zone_id": "ZONE_C", "linked_geology_record_id": "GEO-000055",
            "demo_chip": {"size": 20, "seed": 1103, "vegetation_fraction": 0.90},
        })
        mask = spectral.apply_ndvi_mask(chip["bands"])

        self.assertFalse(mask.scorable)
        self.assertIsNone(mask.mean_reflectance)
        self.assertLess(mask.surface_coverage_pct, spectral.MIN_SURFACE_COVERAGE_PCT)

    def test_aoi_level_result_untouched_by_demo_mode(self) -> None:
        result = spectral.build_bharveli_aoi_result()

        self.assertAlmostEqual(result["similarity"], 0.9784, places=4)
        self.assertEqual(result["vegetation_mask"]["level"], "AOI_LEVEL_PROTOTYPE")
        self.assertNotIn("demo_chip", result)


if __name__ == "__main__":
    unittest.main()
