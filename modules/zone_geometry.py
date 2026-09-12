"""Lease-boundary containment geometry for candidate zones (CASE B).

Diagnosis that led to this module
---------------------------------
The lease boundary polygon is the supplied, authoritative 76.409-ha KML
linework (``data/07_Aug_2019_1659504705RGLR1LRProjectSite.kml``, served by
``/api/aoi_boundary``).  The zone markers are ``SYNTHETIC_DEMO_ZONE_DATA``
(``constants.CANDIDATE_ZONES``): hand-placed demo pins chosen against a loose
envelope ("lat 21.828-21.856, lon 80.216-80.246") that is wider than the real
leased extent ("lat 21.827-21.847, lon 80.218-80.238").  Every current pin
falls *outside* the authoritative boundary linework (72-536 m).

Because the boundary is authoritative and the markers were placed loosely,
we apply the CASE B fix:

  * the boundary polygon itself is NEVER modified;
  * every zone marker is checked against the boundary (point-in-polygon);
  * any marker outside is repositioned to the nearest point ON the boundary
    linework (no cross-feature shortcuts are invented);
  * each correction is appended to ``corrections()`` so it can be reviewed
    before finalising.

All geometry is pure Python (no shapely/turf runtime dependency).  Latitude /
longitude are WGS84 decimal degrees on both layers, so no reprojection is
needed (KML carries no EPSG tag; its coordinates are plain degrees).
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import spectral

# Approx. metres per degree (at Bharveli ~21.84 N): used only for human-
# readable movement distances in the correction log.
_M_PER_DEG_LAT = 110574.0
_M_PER_DEG_LON = 111320.0 * 0.9283

_Ring = List[Tuple[float, float]]
_Segments = List[List[Tuple[float, float]]]

_ZONE_NAME = "zone_id"

CORRECTIONS: List[Dict[str, Any]] = []


def point_in_polygon_ring(
    latitude: float, longitude: float, ring: _Ring
) -> bool:
    """Ray-casting point-in-polygon test over a closed ring of (lon,lat)."""

    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        x1, y1 = ring[i]
        x2, y2 = ring[j]
        if ((y1 > latitude) != (y2 > latitude)) and (
            longitude < (x2 - x1) * (latitude - y1) / (y2 - y1) + x1
        ):
            inside = not inside
        j = i
    return inside


def _dist_to_segment(
    point: Tuple[float, float], start: Tuple[float, float], end: Tuple[float, float]
) -> Tuple[float, Tuple[float, float]]:
    """Euclidean distance and closest point from ``point`` to segment."""

    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay), start
    t = max(
        0.0,
        min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)),
    )
    closest = (ax + t * dx, ay + t * dy)
    return math.hypot(px - closest[0], py - closest[1]), closest


@lru_cache(maxsize=1)
def _boundary_geometry() -> Tuple[_Ring, _Segments]:
    """Return (combined gate ring, per-feature segment lists) from the KML.

    Only *true* consecutive vertices of each LineString feature are used for
    nearest-point work; the concatenated ring is used only for the coarse
    containment gate (it mirrors exactly the linework the map displays).
    """

    features = spectral.load_aoi_kml()
    segments: _Segments = [
        feature["geometry"]["coordinates"] for feature in features
    ]
    combined: _Ring = [pt for seg in segments for pt in seg]
    ring: _Ring = combined + [features[0]["geometry"]["coordinates"][0]]
    return ring, segments


def nearest_boundary_point(
    latitude: float, longitude: float, segments: Optional[_Segments] = None
) -> Tuple[float, float]:
    """Nearest point on the authoritative boundary linework to (lat, lon)."""

    if segments is None:
        _, segments = _boundary_geometry()
    point = (longitude, latitude)
    best: Optional[Tuple[float, Tuple[float, float]]] = None
    for feature in segments:
        for i in range(len(feature) - 1):
            distance, closest = _dist_to_segment(point, feature[i], feature[i + 1])
            if best is None or distance < best[0]:
                best = (distance, closest)
    if best is None:
        raise ValueError("Authoritative boundary linework is empty.")
    nearest_lon, nearest_lat = best[1]
    return nearest_lat, nearest_lon


def is_zone_inside(zone: Mapping[str, Any]) -> bool:
    """True if the zone's (lat, lon) lies inside the authoritative boundary."""

    ring, _ = _boundary_geometry()
    return point_in_polygon_ring(
        float(zone["latitude"]), float(zone["longitude"]), ring
    )


def ensure_zone_inside(
    zone: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
    """CASE B fix: snap a zone whose pin falls outside the lease boundary.

    Returns ``(zone, correction)`` where ``zone`` is a copy with corrected
    latitude/longitude when the pin was outside (unchanged otherwise) and
    ``correction`` is a reviewable log entry (or ``None``).
    """

    latitude = float(zone["latitude"])
    longitude = float(zone["longitude"])
    if is_zone_inside(zone):
        return dict(zone), None

    nearest_lat, nearest_lon = nearest_boundary_point(latitude, longitude)
    moved_m = math.hypot(
        (longitude - nearest_lon) * _M_PER_DEG_LON,
        (latitude - nearest_lat) * _M_PER_DEG_LAT,
    )
    corrected = dict(zone)
    corrected["latitude"] = round(nearest_lat, 6)
    corrected["longitude"] = round(nearest_lon, 6)

    entry = {
        "zone_id": zone.get(_ZONE_NAME),
        "zone_name": zone.get("name"),
        "status": "OUTSIDE_LEASE -> REPOSITIONED",
        "original": {"latitude": round(latitude, 6), "longitude": round(longitude, 6)},
        "corrected": {"latitude": round(nearest_lat, 6), "longitude": round(nearest_lon, 6)},
        "moved_distance_m": round(moved_m, 1),
        "boundary_source": "Supplied 76.409-ha KML boundary",
        "rule": (
            "CASE B: authoritative boundary unchanged; demo pin moved to the "
            "nearest point on the boundary linework."
        ),
    }
    CORRECTIONS.append(entry)
    return corrected, entry


def corrections() -> List[Dict[str, Any]]:
    """Return a copy of every recorded coordinate correction for review."""

    return list(CORRECTIONS)


__all__ = [
    "point_in_polygon_ring",
    "nearest_boundary_point",
    "is_zone_inside",
    "ensure_zone_inside",
    "corrections",
    "CORRECTIONS",
]