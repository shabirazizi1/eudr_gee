"""Small geometry helpers (no GIS dependencies needed)."""
import math

R = 6378137.0  # WGS84 equatorial radius (m), same as turf.js


def _ring(geojson: dict) -> list:
    ring = geojson["coordinates"][0]
    if ring[0] != ring[-1]:
        ring = ring + [ring[0]]
    return ring


def area_ha(geojson: dict) -> float:
    """Geodesic polygon area (same algorithm as turf.area)."""
    ring = _ring(geojson)
    n = len(ring)
    if n < 4:
        return 0.0
    total = 0.0
    for i in range(n - 1):
        lo = i
        mid = (i + 1) % (n - 1)
        hi = (i + 2) % (n - 1)
        p1, p2, p3 = ring[lo], ring[mid], ring[hi]
        total += (math.radians(p3[0]) - math.radians(p1[0])) * math.sin(math.radians(p2[1]))
    return abs(total * R * R / 2.0) / 10_000


def perimeter_m(geojson: dict) -> float:
    ring = _ring(geojson)
    d = 0.0
    for (lon1, lat1), (lon2, lat2) in zip(ring[:-1], ring[1:]):
        p1, p2 = math.radians(lat1), math.radians(lat2)
        dp, dl = p2 - p1, math.radians(lon2 - lon1)
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        d += 2 * R * math.asin(math.sqrt(a))
    return d


def vertex_count(geojson: dict) -> int:
    return len(_ring(geojson)) - 1


def _ccw(a, b, c):
    return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])


def _segments_cross(a, b, c, d):
    return _ccw(a, c, d) != _ccw(b, c, d) and _ccw(a, b, c) != _ccw(a, b, d)


def self_intersects(geojson: dict) -> bool:
    ring = _ring(geojson)
    segs = list(zip(ring[:-1], ring[1:]))
    n = len(segs)
    for i in range(n):
        for j in range(i + 1, n):
            if j == i + 1 or (i == 0 and j == n - 1):
                continue  # adjacent segments share a vertex
            if _segments_cross(*segs[i], *segs[j]):
                return True
    return False


def validate(geojson: dict) -> list[str]:
    """Return a list of blocking errors (empty list = valid)."""
    errors = []
    if geojson.get("type") != "Polygon":
        return ["Geometry must be a GeoJSON Polygon."]
    if vertex_count(geojson) < 3:
        errors.append("A farm boundary needs at least 3 points.")
    elif self_intersects(geojson):
        errors.append("Boundary crosses itself. Redraw it without crossing lines.")
    return errors


def sanity_flags(area: float) -> list[str]:
    """Non-blocking warnings for implausible farm sizes (smallholder cocoa)."""
    flags = []
    if area < 0.1:
        flags.append("Very small plot (<0.1 ha). Check the boundary was fully walked.")
    if area > 50:
        flags.append("Unusually large plot (>50 ha) for a smallholder farm. Verify with the farmer.")
    return flags
