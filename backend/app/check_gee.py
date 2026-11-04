"""Quick test of the real Earth Engine screening on one demo farm.

Usage (from the backend folder):  python -m app.check_gee
"""
import json
import time

from . import config, geo
from .gee_service import gee_screen
from .scoring import score
from .seed import ring

if __name__ == "__main__":
    print(f"Project: {config.GEE_PROJECT}  Hansen: {config.HANSEN_ASSET}")
    g = ring(5.8085, -2.4335, 125, 6)
    t = time.time()
    result = gee_screen(g)
    result.pop("loss_tiles", None)
    print(json.dumps(result, indent=2))
    print("Score:", json.dumps(score(result), indent=2))
    print(f"Local area estimate: {geo.area_ha(g):.2f} ha · took {time.time() - t:.1f}s")
