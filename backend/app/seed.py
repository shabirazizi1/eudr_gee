"""Load demo farms around Asankrangwa, Western Region, Ghana.

Usage (from the backend folder):
    python -m app.seed            # add demo farms
    python -m app.seed --verify   # add demo farms and run the satellite check on each
    python -m app.seed --reset    # wipe the database first
"""
import json
import math
import sys

from sqlmodel import Session, SQLModel, select

from . import geo
from .db import engine, init_db
from .models import Farm

DEMO = [
    # name, farmer, village, centre (lat, lon), radius (m), vertices
    ("Adom Cocoa Farm", "Ama Mensah", "Asankrangwa", (5.8085, -2.4335), 125, 6),
    ("Nhyira Cocoa Farm", "Kwame Asante", "Asankrangwa", (5.8215, -2.4180), 140, 7),
    ("Benso Cocoa Farm", "Akua Boateng", "Sefwi Wiawso Rd", (5.7950, -2.4520), 100, 5),
    ("Aseda Cocoa Farm", "Kofi Adjei", "Samreboi", (5.5720, -2.5830), 160, 8),
    ("Asempa Cocoa Farm", "Esi Ofori", "Enchi Rd", (5.8320, -2.4610), 130, 6),
    # GEE-verified real Hansen tree-cover loss after the cutoff (found via Earth Engine
    # grid screening of the Asankrangwa-Wiawso cocoa belt) -- gives Medium/High demo examples.
    ("Bodi Cocoa Farm", "Yaw Owusu", "Bodi", (5.7740, -2.4280), 130, 7),  # Medium: 0.18 ha loss, 2021
    ("Dadieso Cocoa Farm", "Abena Darko", "Dadieso Rd", (5.7980, -2.4160), 130, 6),  # High: 0.65 ha loss, 2021-23
]


def ring(lat, lon, radius_m, n, wobble=0.18):
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        r = radius_m * (1 + wobble * math.sin(3 * a + lat))
        dlat = (r * math.cos(a)) / 111_320
        dlon = (r * math.sin(a)) / (111_320 * math.cos(math.radians(lat)))
        pts.append([round(lon + dlon, 6), round(lat + dlat, 6)])
    pts.append(pts[0])
    return {"type": "Polygon", "coordinates": [pts]}


def main():
    if "--reset" in sys.argv:
        SQLModel.metadata.drop_all(engine)
    init_db()
    created = []
    with Session(engine) as s:
        existing = {f.name for f in s.exec(select(Farm)).all()}
        n = len(s.exec(select(Farm)).all())
        for i, (name, farmer, village, (lat, lon), r, v) in enumerate(DEMO):
            if name in existing:
                continue
            g = ring(lat, lon, r, v)
            f = Farm(code=f"GH-{241 + n + i:04d}", name=name, farmer_name=farmer, village=village,
                     geometry=json.dumps(g), area_ha=round(geo.area_ha(g), 2),
                     perimeter_m=round(geo.perimeter_m(g), 1), vertices=v,
                     harvest_date="2026-10-15", captured_by="Demo seed")
            s.add(f)
            created.append(f)
        s.commit()
        ids = [f.id for f in created]
    print(f"Added {len(ids)} demo farms.")

    if "--verify" in sys.argv:
        from fastapi.testclient import TestClient
        from .main import app
        with TestClient(app) as c:
            with Session(engine) as s:
                for f in s.exec(select(Farm)).all():
                    r = c.post(f"/api/farms/{f.id}/verify").json()
                    print(f"  {f.code} {f.name}: {r['risk_level']} ({r['risk_score']}) "
                          f"[{r['verification']['source']}]")


if __name__ == "__main__":
    main()
