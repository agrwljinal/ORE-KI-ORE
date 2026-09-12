"""CASE B marker-containment tests (authoritative lease boundary).

The lease boundary is the supplied 76.409-ha KML linework; the zone markers
are hand-placed SYNTHETIC_DEMO_ZONE_DATA pins. Every pin currently renders
outside the authoritative boundary, so each is snapped to the nearest point
on the boundary linework and logged for review. The boundary polygon itself
is never modified.
"""

import math
import unittest

from app import app
from constants import CANDIDATE_ZONES
from modules import zone_geometry
from modules.spectral import load_aoi_kml

# Lightweight "on the boundary linework" check: distance from a point to any
# true consecutive segment of any boundary feature (no invented shortcuts).
_BOUNDARY_FEATURES = load_aoi_kml()


def _distance_to_boundary(latitude, longitude):
    point = (longitude, latitude)
    best = float("inf")
    for feature in _BOUNDARY_FEATURES:
        coords = feature["geometry"]["coordinates"]
        for i in range(len(coords) - 1):
            ax, ay = coords[i]
            bx, by = coords[i + 1]
            dx, dy = bx - ax, by - ay
            if dx == 0.0 and dy == 0.0:
                best = min(best, math.hypot(point[0] - ax, point[1] - ay))
                continue
            t = max(0.0, min(1.0, ((point[0] - ax) * dx + (point[1] - ay) * dy) / (dx * dx + dy * dy)))
            qx, qy = ax + t * dx, ay + t * dy
            best = min(best, math.hypot(point[0] - qx, point[1] - qy))
    return best


class ZoneGeometryUnitTests(unittest.TestCase):
    """Pure-geometry behaviour (no HTTP)."""

    def test_point_in_polygon_ray_casting(self):
        ring = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0), (0.0, 0.0)]
        ring = [(lon, lat) for lon, lat in ring]  # (lon, lat) order

        self.assertTrue(zone_geometry.point_in_polygon_ring(0.5, 0.5, ring))
        self.assertFalse(zone_geometry.point_in_polygon_ring(1.5, 0.5, ring))
        self.assertFalse(zone_geometry.point_in_polygon_ring(0.5, 2.5, ring))

    def test_authoritative_boundary_loads(self):
        features = load_aoi_kml()
        self.assertGreaterEqual(len(features), 12)
        self.assertTrue(
            all(f["properties"]["source"] == "Supplied 76.409-ha KML boundary" for f in features)
        )

    def test_every_demo_zone_pin_is_outside_the_lease(self):
        # This is THE diagnostic: the hand-placed demo pins are outside the
        # authoritative lease boundary, so Case B (snap the markers) applies.
        outside = [z["zone_id"] for z in CANDIDATE_ZONES if not zone_geometry.is_zone_inside(z)]
        self.assertEqual(len(outside), len(CANDIDATE_ZONES), msg=f"outside: {outside}")

    def test_outside_pins_snap_onto_the_boundary_linework(self):
        for zone in CANDIDATE_ZONES:
            corrected, correction = zone_geometry.ensure_zone_inside(zone)
            self.assertIsNotNone(correction, zone["zone_id"])
            moved = _distance_to_boundary(
                corrected["latitude"], corrected["longitude"]
            )
            # Corrected pin lands on the boundary linework; 6-decimal rounding
            # of the nearest point leaves sub-metre tolerance.
            self.assertLess(moved, 1e-5, msg=f"{zone['zone_id']}: {moved} deg off the linework")
            self.assertNotEqual(
                (corrected["latitude"], corrected["longitude"]),
                (zone["latitude"], zone["longitude"]),
                msg=zone["zone_id"],
            )

    def test_boundary_linework_is_never_modified(self):
        before = load_aoi_kml()

        for zone in CANDIDATE_ZONES:
            zone_geometry.ensure_zone_inside(zone)

        after = load_aoi_kml()
        self.assertEqual(
            [f["geometry"]["coordinates"] for f in before],
            [f["geometry"]["coordinates"] for f in after],
        )

    def test_correction_log_is_reviewable(self):
        zone_geometry.CORRECTIONS.clear()
        for zone in CANDIDATE_ZONES:
            _, correction = zone_geometry.ensure_zone_inside(zone)
            self.assertIsNotNone(correction)
            self.assertIn("original", correction)
            self.assertIn("corrected", correction)
            self.assertIn("moved_distance_m", correction)
            self.assertGreater(correction["moved_distance_m"], 0)
            self.assertEqual(correction["status"], "OUTSIDE_LEASE -> REPOSITIONED")

        log = zone_geometry.corrections()
        self.assertEqual(len(log), len(CANDIDATE_ZONES))
        # Every corrected coordinate is on the boundary linework.
        for entry in log:
            moved = _distance_to_boundary(
                entry["corrected"]["latitude"],
                entry["corrected"]["longitude"],
            )
            self.assertLess(moved, 1e-5)


class ZoneGeometryRouteTests(unittest.TestCase):
    def setUp(self):
        app.config["TESTING"] = True
        self.client = app.test_client()
        # Fresh ledger per test; /api/zones repopulates it.
        zone_geometry.CORRECTIONS.clear()

    def test_zones_endpoint_reports_coordinate_corrections(self):
        body = self.client.get("/api/zones").get_json()

        self.assertEqual(body["count"], len(CANDIDATE_ZONES))
        for zone in body["zones"]:
            correction = zone.get("coordinate_correction")
            self.assertIsNotNone(correction, zone["zone_id"])
            self.assertEqual(correction["status"], "OUTSIDE_LEASE -> REPOSITIONED")
            # The served payload now carries the corrected (inside) coordinate.
            self.assertEqual(
                zone["latitude"],
                correction["corrected"]["latitude"],
            )
            self.assertEqual(
                zone["longitude"],
                correction["corrected"]["longitude"],
            )

    def test_correction_log_endpoint_is_a_reviewable_ledger(self):
        # Populate the ledger the same way the UI does (serve the zones).
        self.client.get("/api/zones").get_json()
        body = self.client.get("/api/zone_coordinate_corrections").get_json()

        self.assertEqual(body["count"], len(CANDIDATE_ZONES))
        self.assertEqual(body["boundary_source"], "Supplied 76.409-ha KML boundary")
        ids = {entry["zone_id"] for entry in body["corrections"]}
        self.assertEqual(ids, {z["zone_id"] for z in CANDIDATE_ZONES})

    def test_detail_endpoint_also_returns_corrected_coordinate(self):
        body = self.client.get("/api/zones/ZONE_D").get_json()

        self.assertIn("coordinate_correction", body)
        self.assertEqual(
            body["latitude"],
            body["coordinate_correction"]["corrected"]["latitude"],
        )


if __name__ == "__main__":
    unittest.main()