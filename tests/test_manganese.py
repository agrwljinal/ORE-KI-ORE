import unittest

from modules.manganese import DEMO_STATUS, predict_demo_zones


class ManganeseDemoTests(unittest.TestCase):
    def test_predictions_are_schema_faithful_and_labelled(self):
        rows = predict_demo_zones()
        self.assertEqual(len(rows), 4)
        self.assertTrue(rows["predicted_mn_pct"].notna().all())
        self.assertTrue(rows["estimated_tonnage_proxy_t"].notna().all())
        self.assertEqual(set(rows["data_status"]), {DEMO_STATUS})

    def test_demo_does_not_plot_geology_rows_as_samples(self):
        rows = predict_demo_zones()
        self.assertEqual(len(rows), len(set(rows["zone_id"])))
        self.assertNotEqual(len(rows), 500)


if __name__ == "__main__":
    unittest.main()
