"""CASE A boundary-expansion tests (reversal of the marker-snapping fix).

The lease boundary polygon (authoritative 76.409-ha KML linework) is buffered
outward just enough to enclose the four demo-zone markers. Only the boundary
geometry changes: the markers keep their original coordinates, their original
count (4) and are never moved, duplicated or regenerated.
"""

import unittest

import app as app_module
from constants import CANDIDATE_ZONES
from modules import spectral

try:
    from shapely.geometry import Point
    from shapely.geometry import shape
    from shapely.ops import unary_union
    SHAPELY_PRESENT = True
except Exception:  # pragma: no cover
    SHAPELY_PRESENT = False


@unittest.skipUnless(SHAPELY_PRESENT, "shapely is required for the CASE A buffer")
class CaseABoundaryUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.markers = [dict(zone) for zone in CANDIDATE_ZONES]
        cls.expansion = spectral.expand_lease_boundary(markers=cls.markers)
        cls.polygons = unary_union(
            [shape(feature["geometry"]) for feature in cls.expansion["features"]]
        )

    def test_marker_count_and_coordinates_are_untouched(self):
        self.assertEqual(len(self.markers), 4)
        self.assertEqual({z["zone_id"] for z in self.markers},
                         {"ZONE_A", "ZONE_B", "ZONE_C", "ZONE_D"})
        # Everything matches constants.CANDIDATE_ZONES byte-for-byte.
        for marker, original in zip(self.markers, CANDIDATE_ZONES):
            self.assertEqual(marker["latitude"], original["latitude"])
            self.assertEqual(marker["longitude"], original["longitude"])

    def test_expansion_records_original_marker_coords_not_replacement_ones(self):
        for entry in self.expansion["markers_contained"]:
            self.assertEqual(
                (entry["original_latitude"], entry["original_longitude"]),
                (
                    next(z["latitude"] for z in CANDIDATE_ZONES if z["zone_id"] == entry["zone_id"]),
                    next(z["longitude"] for z in CANDIDATE_ZONES if z["zone_id"] == entry["zone_id"]),
                ),
            )

    def test_every_original_marker_is_inside_the_expanded_lease(self):
        for marker in self.markers:
            with self.subTest(zone=marker["zone_id"]):
                self.assertTrue(
                    self.polygons.covers(Point(marker["longitude"], marker["latitude"])),
                    f"{marker['zone_id']} still outside the expanded lease",
                )

    def test_expansion_is_just_enough_not_blown_up(self):
        self.assertTrue(self.expansion["buffered"])
        radius = self.expansion["buffer_radius_m"]
        self.assertGreater(radius, 0.0)
        # Tight: the outward margin is under ~180 m at these latitudes
        # (536 m is the farthest marker, so a 540-ish m buffer is the minimum
        # single uniform radius that contains all four pins).
        self.assertLess(radius, 600.0)

    def test_expanded_boundary_is_a_polygon_not_a_linestring(self):
        self.assertTrue(self.expansion["features"])
        for feature in self.expansion["features"]:
            self.assertEqual(feature["geometry"]["type"], "Polygon")
            self.assertGreaterEqual(
                len(feature["geometry"]["coordinates"][0]), 4
            )

    def test_raw_kml_linework_still_has_its_13_placemarks(self):
        features = spectral.load_aoi_kml(
            spectral.DEFAULT_AOI_KML
        )
        self.assertEqual(len(features), 13)


class CaseABoundaryRouteTests(unittest.TestCase):
    def setUp(self):
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def test_aoi_boundary_serves_expanded_polygon_with_all_markers_inside(self):
        body = self.client.get("/api/aoi_boundary").get_json()

        self.assertEqual(body["boundary_mode"], "expanded")
        self.assertGreater(body["buffer_radius_m"], 0.0)
        self.assertEqual(body["area_ha"], 76.409)
        self.assertTrue(body["features"])
        self.assertTrue(all(
            feature["geometry"]["type"] == "Polygon"
            for feature in body["features"]
        ))
        self.assertTrue(all(
            entry["contained"] for entry in body["markers_contained"]
        ), body["markers_contained"])
        self.assertEqual(len(body["markers_contained"]), 4)

    def test_zones_still_serve_the_four_original_markers_untouched(self):
        body = self.client.get("/api/zones").get_json()

        self.assertEqual(body["count"], 4)
        original = {zone["zone_id"]: zone for zone in CANDIDATE_ZONES}
        for served in body["zones"]:
            self.assertNotIn("coordinate_correction", served)
            self.assertEqual(served["latitude"], original[served["zone_id"]]["latitude"])
            self.assertEqual(served["longitude"], original[served["zone_id"]]["longitude"])


if __name__ == "__main__":
    unittest.main()