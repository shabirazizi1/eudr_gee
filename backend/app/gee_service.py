"""F3 – GEE satellite verification.

Screens a farm polygon against:
  1. Hansen Global Forest Change  -> tree-cover loss after the EUDR cutoff (31 Dec 2020)
  2. Sentinel-2 surface reflectance -> NDVI now vs. the 2020 baseline, and usable image coverage
  3. WDPA protected areas           -> overlap with any protected area
"""
import hashlib
import math
import random
from datetime import date, timedelta
from io import BytesIO

import certifi
import requests
from PIL import Image, ImageDraw, ImageFont

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

    # Map tiles of post-cutoff loss so the frontend can overlay them, clipped to the
    # farm + a 500 m buffer so the overlay stays focused on the farm, not the whole tile.
    try:
        mapid = post_loss.selfMask().clip(geom.buffer(500)).getMapId({"palette": ["e0453a"]})
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


def _geojson_bbox(geoms: list) -> tuple:
    xs = [p[0] for g in geoms for p in g["coordinates"][0]]
    ys = [p[1] for g in geoms for p in g["coordinates"][0]]
    return min(xs), min(ys), max(xs), max(ys)


def _buffer_bbox(minx, miny, maxx, maxy, meters):
    lat_mid = (miny + maxy) / 2
    buf_lat = meters / 111_320
    buf_lon = meters / (111_320 * math.cos(math.radians(lat_mid)))
    return minx - buf_lon, miny - buf_lat, maxx + buf_lon, maxy + buf_lat


def _to_jpeg(img, quality=85) -> bytes:
    buf = BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _label_font(size=18):
    for path in ("/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf",
                 "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


# ---------------------------------------------------------------- Esri tile imagery (sharp background)

ESRI_TILE_URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
TILE_SIZE = 256


def _deg2tile(lat, lon, zoom):
    """Slippy-map (Web Mercator) tile coordinates, kept fractional for sub-tile pixel cropping."""
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    xtile = (lon + 180.0) / 360.0 * n
    ytile = (1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return xtile, ytile


def _pick_zoom(minx, miny, maxx, maxy, max_zoom=17, min_zoom=11, max_tiles=48):
    """Highest zoom (sharpest) for which the bbox needs at most `max_tiles` Esri tiles."""
    for z in range(max_zoom, min_zoom - 1, -1):
        x0f, y0f = _deg2tile(maxy, minx, z)
        x1f, y1f = _deg2tile(miny, maxx, z)
        cols = int(math.floor(max(x0f, x1f))) - int(math.floor(min(x0f, x1f))) + 1
        rows = int(math.floor(max(y0f, y1f))) - int(math.floor(min(y0f, y1f))) + 1
        if cols * rows <= max_tiles:
            return z
    return min_zoom


def _stitch_esri_tiles(minx, miny, maxx, maxy, zoom):
    """Fetch and stitch Esri World Imagery tiles covering the bbox, cropped to it exactly."""
    x0f, y0f = _deg2tile(maxy, minx, zoom)   # top-left corner (max lat, min lon)
    x1f, y1f = _deg2tile(miny, maxx, zoom)   # bottom-right corner (min lat, max lon)
    xt0, xt1 = int(math.floor(min(x0f, x1f))), int(math.floor(max(x0f, x1f)))
    yt0, yt1 = int(math.floor(min(y0f, y1f))), int(math.floor(max(y0f, y1f)))

    canvas = Image.new("RGB", ((xt1 - xt0 + 1) * TILE_SIZE, (yt1 - yt0 + 1) * TILE_SIZE))
    for tx in range(xt0, xt1 + 1):
        for ty in range(yt0, yt1 + 1):
            url = ESRI_TILE_URL.format(z=zoom, y=ty, x=tx)
            resp = requests.get(url, timeout=15, verify=certifi.where())
            resp.raise_for_status()
            tile = Image.open(BytesIO(resp.content)).convert("RGB")
            canvas.paste(tile, ((tx - xt0) * TILE_SIZE, (ty - yt0) * TILE_SIZE))

    px0, py0 = (x0f - xt0) * TILE_SIZE, (y0f - yt0) * TILE_SIZE
    px1, py1 = (x1f - xt0) * TILE_SIZE, (y1f - yt0) * TILE_SIZE
    return canvas.crop((round(px0), round(py0), round(px1), round(py1)))


def _ee_overlay_png(minx, miny, maxx, maxy, w, h, loss_geom, boundary_fc):
    """Transparent PNG (post-2020 Hansen loss in red, boundary in yellow) sized to exactly
    match an Esri tile crop of the same bbox, so the two composite in pixel-perfect alignment.
    """
    cutoff = config.CUTOFF_YEAR - 2000
    hansen = ee.Image(config.HANSEN_ASSET)
    post_loss = hansen.select("lossyear").unmask(0).gt(cutoff)
    loss_rgb = post_loss.selfMask().clip(loss_geom).visualize(palette=["e0453a"])

    boundary = ee.Image().byte().paint(boundary_fc, 1, 3)
    boundary_rgb = boundary.selfMask().visualize(min=1, max=1, palette=["ffe034"])

    overlay = loss_rgb.blend(boundary_rgb)
    region = ee.Geometry.Rectangle([minx, miny, maxx, maxy])
    url = overlay.getThumbURL({"region": region, "dimensions": f"{w}x{h}", "format": "png"})

    resp = requests.get(url, timeout=30, verify=certifi.where())
    resp.raise_for_status()
    return Image.open(BytesIO(resp.content)).convert("RGBA")


def satellite_thumbnail_pair(geojson: dict) -> dict:
    """Two JPEG maps for the PDF report, each with post-2020 Hansen loss (clipped to a 300 m
    buffer) in red and the farm boundary in yellow on top:
      "esri"      -- sharp Esri World Imagery (can have clouds on the capture date)
      "sentinel2" -- a cloud-free Sentinel-2 composite (last 12 months), lower resolution
    Either key is None if that source failed; the caller falls back to a plain drawing if both are.
    """
    _init()
    geom = ee.Geometry(geojson)
    out = {"esri": None, "sentinel2": None}

    try:
        minx, miny, maxx, maxy = _buffer_bbox(*_geojson_bbox([geojson]), 300)
        zoom = _pick_zoom(minx, miny, maxx, maxy, max_zoom=17)
        base = _stitch_esri_tiles(minx, miny, maxx, maxy, zoom)
        w, h = base.size
        overlay = _ee_overlay_png(minx, miny, maxx, maxy, w, h, loss_geom=geom.buffer(300),
                                   boundary_fc=ee.FeatureCollection([ee.Feature(geom)]))
        base = base.convert("RGBA")
        base.alpha_composite(overlay)
        out["esri"] = _to_jpeg(base)
    except Exception:
        pass

    try:
        out["sentinel2"] = _to_jpeg(_sentinel_thumbnail_image(geom))
    except Exception:
        pass

    return out


def _sentinel_thumbnail_image(geom):
    """A cloud-free Sentinel-2 true-colour composite (last 12 months) with the boundary +
    loss overlay, as a PIL Image."""
    buffered = geom.buffer(300)
    region = buffered.bounds()

    today = date.today()
    start = (today - timedelta(days=365)).isoformat()
    s2 = _s2_collection(buffered, start, today.isoformat())
    if s2.size().getInfo() == 0:
        raise RuntimeError("No cloud-free Sentinel-2 imagery in the last 12 months")
    rgb = s2.median().select(["B4", "B3", "B2"]).visualize(min=0, max=3000, gamma=1.3)

    cutoff = config.CUTOFF_YEAR - 2000
    hansen = ee.Image(config.HANSEN_ASSET)
    post_loss = hansen.select("lossyear").unmask(0).gt(cutoff)
    loss_rgb = post_loss.selfMask().clip(geom.buffer(300)).visualize(palette=["e0453a"])

    boundary = ee.Image().byte().paint(ee.FeatureCollection([ee.Feature(geom)]), 1, 3)
    boundary_rgb = boundary.selfMask().visualize(min=1, max=1, palette=["ffe034"])

    composite = rgb.blend(loss_rgb).blend(boundary_rgb)
    url = composite.getThumbURL({"region": region, "dimensions": 600, "format": "png"})

    resp = requests.get(url, timeout=30, verify=certifi.where())
    resp.raise_for_status()
    return Image.open(BytesIO(resp.content)).convert("RGB")


def satellite_overview_thumbnail(farms: list) -> tuple:
    """A labelled PNG map for the batch PDF: sharp Esri World Imagery covering every
    contributing farm, with every farm boundary in yellow (labelled with its farm code) and
    post-2020 Hansen loss in red, each clipped to a 300 m buffer around its own farm.
    Falls back to a Sentinel-2 composite, then lets the caller fall back to a plain drawing.
    `farms` is a list of {"geometry": GeoJSON Polygon, "code": str}.
    Returns (png_bytes, source) where source is "esri" or "sentinel2".
    """
    _init()
    geoms = [f["geometry"] for f in farms]
    minx, miny, maxx, maxy = _buffer_bbox(*_geojson_bbox(geoms), 300)
    try:
        zoom = _pick_zoom(minx, miny, maxx, maxy, max_zoom=17)
        base = _stitch_esri_tiles(minx, miny, maxx, maxy, zoom)
        w, h = base.size
        loss_geom = ee.FeatureCollection(
            [ee.Feature(ee.Geometry(g).buffer(300)) for g in geoms]).geometry()
        boundary_fc = ee.FeatureCollection([ee.Feature(ee.Geometry(g)) for g in geoms])
        overlay = _ee_overlay_png(minx, miny, maxx, maxy, w, h, loss_geom, boundary_fc)
        base = base.convert("RGBA")
        base.alpha_composite(overlay)
        img = base.convert("RGB")
        source = "esri"
    except Exception:
        img = _sentinel_overview_image(geoms, minx, miny, maxx, maxy)
        source = "sentinel2"

    # Earth Engine/Esri can't label features inside a raster image, so composite the farm
    # codes on afterwards with the same linear bbox->pixel mapping used for the request.
    # Larger font + a white outline keeps the yellow label legible over any background.
    w, h = img.size
    draw = ImageDraw.Draw(img)
    font = _label_font(26)
    for f, g in zip(farms, geoms):
        ring = g["coordinates"][0]
        cx = sum(p[0] for p in ring) / len(ring)
        cy = sum(p[1] for p in ring) / len(ring)
        x = (cx - minx) / (maxx - minx) * w
        y = (maxy - cy) / (maxy - miny) * h - 16
        label = f["code"]
        for dx in (-2, -1, 0, 1, 2):
            for dy in (-2, -1, 0, 1, 2):
                if dx or dy:
                    draw.text((x + dx, y + dy), label, font=font, fill="white", anchor="mm")
        draw.text((x, y), label, font=font, fill="#ffe034", anchor="mm")

    return _to_jpeg(img), source


def _sentinel_overview_image(geoms, minx, miny, maxx, maxy):
    """Fallback base image for `satellite_overview_thumbnail`: a Sentinel-2 composite."""
    region = ee.Geometry.Rectangle([minx, miny, maxx, maxy])
    today = date.today()
    start = (today - timedelta(days=365)).isoformat()
    s2 = _s2_collection(region, start, today.isoformat())
    if s2.size().getInfo() == 0:
        raise RuntimeError("No cloud-free Sentinel-2 imagery in the last 12 months")
    rgb = s2.median().select(["B4", "B3", "B2"]).visualize(min=0, max=3000, gamma=1.3)

    loss_geom = ee.FeatureCollection(
        [ee.Feature(ee.Geometry(g).buffer(300)) for g in geoms]).geometry()
    cutoff = config.CUTOFF_YEAR - 2000
    hansen = ee.Image(config.HANSEN_ASSET)
    post_loss = hansen.select("lossyear").unmask(0).gt(cutoff)
    loss_rgb = post_loss.selfMask().clip(loss_geom).visualize(palette=["e0453a"])

    fc = ee.FeatureCollection([ee.Feature(ee.Geometry(g)) for g in geoms])
    boundary = ee.Image().byte().paint(fc, 1, 3)
    boundary_rgb = boundary.selfMask().visualize(min=1, max=1, palette=["ffe034"])

    composite = rgb.blend(loss_rgb).blend(boundary_rgb)
    url = composite.getThumbURL({"region": region, "dimensions": 700, "format": "png"})
    resp = requests.get(url, timeout=30, verify=certifi.where())
    resp.raise_for_status()
    return Image.open(BytesIO(resp.content)).convert("RGB")


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
