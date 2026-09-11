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


if __name__ == "__main__":
    unittest.main()
