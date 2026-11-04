"""F3 – GEE satellite verification.

Screens a farm polygon against:
  1. Hansen Global Forest Change  -> tree-cover loss after the EUDR cutoff (31 Dec 2020)
  2. Sentinel-2 surface reflectance -> NDVI now vs. the 2020 baseline, and usable image coverage
  3. WDPA protected areas           -> overlap with any protected area
"""
import hashlib
import random
from datetime import date, timedelta

from . import config

try:
    import ee
except ImportError:  # allows mock mode without the package
    ee = None

_initialized = False


def _init():
    global _initialized
    if not _initialized:
        if ee is None:
            raise RuntimeError("earthengine-api is not installed")
        ee.Initialize(project=config.GEE_PROJECT or None)
        _initialized = True


# ---------------------------------------------------------------- real GEE

def _mask_s2_clouds(img):
    scl = img.select("SCL")
    # 3 = cloud shadow, 8/9 = cloud medium/high probability, 10 = cirrus
    clear = scl.neq(3).And(scl.neq(8)).And(scl.neq(9)).And(scl.neq(10))
    return img.updateMask(clear)


def _s2_collection(geom, start: str, end: str):
    return (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(geom)
        .filterDate(start, end)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 60))
        .map(_mask_s2_clouds)
    )


def _ndvi_stats(geom, start: str, end: str):
    col = _s2_collection(geom, start, end)
    count = col.size().getInfo()
    if count == 0:
        return {"ndvi": None, "images": 0, "coverage_pct": 0.0}
    ndvi = col.map(lambda i: i.normalizedDifference(["B8", "B4"]).rename("NDVI")).median()
    stats = ee.Dictionary({
        "ndvi": ndvi.reduceRegion(ee.Reducer.mean(), geom, 10, maxPixels=1e9).get("NDVI"),
        "valid": ndvi.mask().rename("valid")
        .reduceRegion(ee.Reducer.mean(), geom, 10, maxPixels=1e9).get("valid"),
    }).getInfo()
    return {
        "ndvi": round(stats["ndvi"], 3) if stats.get("ndvi") is not None else None,
        "images": count,
        "coverage_pct": round((stats.get("valid") or 0) * 100, 1),
    }


def gee_screen(geojson: dict) -> dict:
    _init()
    geom = ee.Geometry(geojson)
    cutoff = config.CUTOFF_YEAR - 2000  # Hansen lossyear is years since 2000

    hansen = ee.Image(config.HANSEN_ASSET)
    lossyear = hansen.select("lossyear").unmask(0)
    treecover = hansen.select("treecover2000")
    px = ee.Image.pixelArea()

    post_loss = lossyear.gt(cutoff)
    forest_2020 = treecover.gte(10).And(lossyear.eq(0).Or(lossyear.gt(cutoff)))

    hansen_stats = ee.Dictionary({
        "loss_m2": post_loss.multiply(px).rename("a")
        .reduceRegion(ee.Reducer.sum(), geom, 30, maxPixels=1e9).get("a"),
        "forest2020_m2": forest_2020.multiply(px).rename("a")
        .reduceRegion(ee.Reducer.sum(), geom, 30, maxPixels=1e9).get("a"),
        "by_year": px.addBands(lossyear).updateMask(post_loss)
        .reduceRegion(ee.Reducer.sum().group(groupField=1, groupName="year"),
                      geom, 30, maxPixels=1e9).get("groups"),
        "area_m2": geom.area(1),
    }).getInfo()

    wdpa = ee.FeatureCollection("WCMC/WDPA/current/polygons").filterBounds(geom)
    protected = wdpa.aggregate_array("NAME").distinct().slice(0, 5).getInfo()

    today = date.today()
    current = _ndvi_stats(geom, (today - timedelta(days=365)).isoformat(), today.isoformat())
    baseline = _ndvi_stats(geom, f"{config.CUTOFF_YEAR}-01-01", f"{config.CUTOFF_YEAR + 1}-01-01")

    loss_by_year = {
        str(2000 + int(g["year"])): round(g["sum"] / 10_000, 3)
        for g in (hansen_stats.get("by_year") or [])
    }

    # Map tiles of post-cutoff loss so the frontend can overlay them
    try:
        mapid = post_loss.selfMask().getMapId({"palette": ["e0453a"]})
        loss_tiles = mapid["tile_fetcher"].url_format
    except Exception:
        loss_tiles = None

    return {
        "source": "gee",
        "area_ha": round(hansen_stats["area_m2"] / 10_000, 2),
        "forest_2020_ha": round((hansen_stats.get("forest2020_m2") or 0) / 10_000, 2),
        "loss_ha": round((hansen_stats.get("loss_m2") or 0) / 10_000, 3),
        "loss_by_year": loss_by_year,
        "protected_areas": protected,
        "ndvi_current": current["ndvi"],
        "ndvi_baseline": baseline["ndvi"],
        "images_current": current["images"],
        "coverage_pct": current["coverage_pct"],
        "loss_tiles": loss_tiles,
        "datasets": [
            {"name": f"Hansen Global Forest Change ({config.HANSEN_ASSET.split('/')[-1]})",
             "window": f"Loss after 31 Dec {config.CUTOFF_YEAR}", "resolution": "30 m"},
            {"name": "Sentinel-2 surface reflectance (NDVI)",
             "window": f"Last 12 months vs. {config.CUTOFF_YEAR} baseline", "resolution": "10 m"},
            {"name": "WDPA protected areas", "window": "Current snapshot", "resolution": "Vector"},
        ],
    }


# ---------------------------------------------------------------- mock

def mock_screen(geojson: dict, area: float) -> dict:
    """Deterministic fake result (same polygon -> same result). For offline dev only."""
    seed = int(hashlib.sha256(str(geojson["coordinates"]).encode()).hexdigest(), 16) % 10**8
    rng = random.Random(seed)
    roll = rng.random()
    if roll < 0.65:
        loss = 0.0
    elif roll < 0.85:
        loss = round(area * rng.uniform(0.005, 0.04), 3)
    else:
        loss = round(area * rng.uniform(0.06, 0.25), 3)
    years = [str(y) for y in range(config.CUTOFF_YEAR + 1, 2025)]
    by_year = {rng.choice(years): loss} if loss else {}
    base = round(rng.uniform(0.70, 0.82), 3)
    drop = (loss / area) * rng.uniform(0.5, 1.5) if area else 0
    return {
        "source": "mock",
        "area_ha": round(area, 2),
        "forest_2020_ha": round(area * rng.uniform(0.6, 0.95), 2),
        "loss_ha": loss,
        "loss_by_year": by_year,
        "protected_areas": ["Mock Forest Reserve"] if rng.random() < 0.08 else [],
        "ndvi_current": round(base - drop, 3),
        "ndvi_baseline": base,
        "images_current": rng.randint(2, 30),
        "coverage_pct": round(rng.uniform(40, 99), 1),
        "loss_tiles": None,
        "datasets": [
            {"name": "MOCK data (Earth Engine not called)", "window": "-", "resolution": "-"},
        ],
    }


def screen(geojson: dict, area: float) -> dict:
    if config.USE_MOCK_GEE:
        return mock_screen(geojson, area)
    try:
        return gee_screen(geojson)
    except Exception as e:  # noqa: BLE001
        if not config.ALLOW_MOCK_FALLBACK:
            raise
        result = mock_screen(geojson, area)
        result["gee_error"] = str(e)[:300]
        return result
