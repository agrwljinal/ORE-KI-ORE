"""Tests for modules.fusion + per-zone spectral extraction."""

import unittest
from modules import fusion, spectral


class FusionCoreTests(unittest.TestCase):
    def test_weighted_sum_default_50_50(self):
        self.assertAlmostEqual(fusion.combine_spatial_spectral(80, 60), 70.0)

    def test_weighted_sum_custom_weights(self):
        w = {"spatial": 0.8, "spectral": 0.2}
        self.assertAlmostEqual(fusion.combine_spatial_spectral(80, 60, w), 76.0)

    def test_missing_spectral_falls_back_to_spatial(self):
        self.assertAlmostEqual(fusion.combine_spatial_spectral(72, None), 72.0)

    def test_clamped_to_0_100(self):
        self.assertLessEqual(fusion.combine_spatial_spectral(150, 150), 100.0)
        self.assertGreaterEqual(fusion.combine_spatial_spectral(-50, -50), 0.0)

    def test_priority_thresholds(self):
        self.assertEqual(fusion.priority_band(80), "HIGH")
        self.assertEqual(fusion.priority_band(50), "MEDIUM")
        self.assertEqual(fusion.priority_band(49.9), "LOW")


class ZoneEvaluationTests(unittest.TestCase):
    def _mock_scorer(self, high=True):
        return (lambda a, b: 0.95) if high else (lambda a, b: 0.40)

    def _reference(self, mid="pyrolusite"):
        return fusion.MineralReference(
            mineral_id=mid, display_name=mid.title(),
            reflectance={"B04": 0.05, "B08": 0.06, "B11": 0.09, "B12": 0.08},
            provenance="test",
        )

    def test_high_priority_zone(self):
        e = fusion.evaluate_zone(
            "Z1", 21.85, 80.22, spatial_score=91.0,
            zone_reflectance={"B04": 0.1, "B08": 0.2, "B11": 0.2, "B12": 0.1},
            mineral_references=[self._reference()],
            spectral_scorer=self._mock_scorer(True),
        )
        self.assertEqual(e.priority, "HIGH")
        self.assertEqual(e.best_mineral_match, "pyrolusite")
        self.assertGreater(e.final_exploration_score, 90)

    def test_missing_spectral_uses_spatial_only(self):
        e = fusion.evaluate_zone(
            "Z2", 21.85, 80.22, spatial_score=60.0,
            zone_reflectance=None,
            mineral_references=[self._reference()],
            spectral_scorer=self._mock_scorer(True),
        )
        self.assertIsNone(e.spectral_similarity)
        self.assertIsNone(e.best_mineral_match)
        self.assertEqual(e.final_exploration_score, 60.0)

    def test_best_mineral_is_max(self):
        refs = [self._reference("pyrolusite"), self._reference("psilomelane")]
        # Different scores per mineral
        def scorer(a, b):
            return 0.99 if b["B11"] == 0.09 else 0.55
        e = fusion.evaluate_zone(
            "Z3", 21.85, 80.22, spatial_score=70.0,
            zone_reflectance={"B04": 0.1, "B08": 0.2, "B11": 0.2, "B12": 0.1},
            mineral_references=refs, spectral_scorer=scorer,
        )
        self.assertEqual(e.best_mineral_match, "pyrolusite")

    def test_no_ore_grade_or_mn_pct_fields_leak(self):
        e = fusion.evaluate_zone(
            "Z4", 21.85, 80.22, spatial_score=91.0,
            zone_reflectance={"B04": 0.1, "B08": 0.2, "B11": 0.2, "B12": 0.1},
            mineral_references=[self._reference()],
            spectral_scorer=self._mock_scorer(True),
        )
        blob = e.to_dict()
        for banned in ("mn_pct", "mn_concentration", "ore_grade", "mn_grade"):
            self.assertNotIn(banned, blob)

    def test_scientific_note_present(self):
        e = fusion.evaluate_zone(
            "Z5", 21.85, 80.22, spatial_score=91.0, zone_reflectance=None,
            mineral_references=[], spectral_scorer=self._mock_scorer(True),
        )
        self.assertIn("screening signal", e.scientific_note)
        self.assertIn("prototype", e.scientific_note.lower())


class ZoneReflectanceExtractionTests(unittest.TestCase):
    def test_known_geology_record_returns_bands(self):
        r, p = spectral.get_zone_reflectance("GEO-000027")
        self.assertIsNotNone(r)
        self.assertEqual(set(r.keys()), {"B04", "B08", "B11", "B12"})
        self.assertEqual(p, spectral.ZONE_REFLECTANCE_PROVENANCE_SYNTHETIC)

    def test_unknown_geology_record_returns_none(self):
        r, p = spectral.get_zone_reflectance("GEO-DOES-NOT-EXIST")
        self.assertIsNone(r)
        self.assertEqual(p, spectral.ZONE_REFLECTANCE_PROVENANCE_UNAVAILABLE)

    def test_different_zones_get_different_scores(self):
        # This is the whole point of the refactor: no more mine-wide 97.84% pasted
        # on every pin. Each zone must score independently.
        r1, _ = spectral.get_zone_reflectance("GEO-000027")
        r2, _ = spectral.get_zone_reflectance("GEO-000015")
        s1 = spectral.score_zone_against_references(r1)["pyrolusite"]
        s2 = spectral.score_zone_against_references(r2)["pyrolusite"]
        self.assertNotAlmostEqual(s1, s2, places=2)


if __name__ == "__main__":
    unittest.main()