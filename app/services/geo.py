"""Spherical geometry helpers used by the prediction engine and map queries."""

import math

EARTH_RADIUS_KM = 6371.0088

COMPASS = [
    ("N", 0), ("NNE", 22.5), ("NE", 45), ("ENE", 67.5),
    ("E", 90), ("ESE", 112.5), ("SE", 135), ("SSE", 157.5),
    ("S", 180), ("SSW", 202.5), ("SW", 225), ("WSW", 247.5),
    ("W", 270), ("WNW", 292.5), ("NW", 315), ("NNW", 337.5),
]


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1, lon1, lat2, lon2):
    """Initial great-circle bearing from point 1 to point 2, 0..360."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def destination(lat, lon, bearing, distance_km):
    """Point reached travelling `distance_km` along `bearing` from (lat, lon)."""
    ang = distance_km / EARTH_RADIUS_KM
    br = math.radians(bearing)
    p1 = math.radians(lat)
    l1 = math.radians(lon)
    p2 = math.asin(math.sin(p1) * math.cos(ang) + math.cos(p1) * math.sin(ang) * math.cos(br))
    l2 = l1 + math.atan2(
        math.sin(br) * math.sin(ang) * math.cos(p1),
        math.cos(ang) - math.sin(p1) * math.sin(p2),
    )
    return math.degrees(p2), (math.degrees(l2) + 540.0) % 360.0 - 180.0


def compass_label(bearing):
    if bearing is None:
        return None
    b = bearing % 360.0
    best = min(COMPASS, key=lambda item: min(abs(b - item[1]), 360 - abs(b - item[1])))
    return best[0]


def mean_bearing(bearings, weights=None):
    """Circular mean. Averaging 350 and 10 must give 0, not 180."""
    if not bearings:
        return None
    if weights is None:
        weights = [1.0] * len(bearings)
    x = sum(w * math.cos(math.radians(b)) for b, w in zip(bearings, weights))
    y = sum(w * math.sin(math.radians(b)) for b, w in zip(bearings, weights))
    if abs(x) < 1e-12 and abs(y) < 1e-12:
        return None
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def angular_delta(a, b):
    """Smallest absolute difference between two bearings, 0..180."""
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


def bbox_around(lat, lon, radius_km):
    """Rough lat/lon bounding box, good enough to pre-filter an index scan."""
    dlat = radius_km / 111.32
    coslat = max(0.01, math.cos(math.radians(lat)))
    dlon = radius_km / (111.32 * coslat)
    return lat - dlat, lat + dlat, lon - dlon, lon + dlon
