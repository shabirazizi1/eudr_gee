"""Load demo farms around Asankrangwa, Western Region, Ghana.

Usage (from the backend folder):
    python -m app.seed            # add demo farms
    python -m app.seed --verify   # add demo farms and run the satellite check on each
    python -m app.seed --reset    # wipe the database first
"""
import json
import math
import sys
from datetime import datetime, timezone

from sqlmodel import Session, SQLModel, select

from . import config, geo
from .db import engine, init_db
from .main import create_bags, create_boundary_version, latest_boundary_version  # reuse API logic
from .models import Batch, BatchContribution, BatchEvent, BoundaryVersion, Farm

DEMO = [
    # name, farmer, village, centre (lat, lon), radius (m), vertices, capture_method, gps_accuracy_m, flagged_points
    # Most farms are GPS-walked with realistic accuracy; Benso was drawn on a map (no field GPS) and
    # Aseda's walk averaged a weak 13 m fix -- those two are the only ones needing a field audit.
    # GEE-verified vegetated spot near Asankrangwa: NDVI 0.69 -> 0.72, zero post-2020 loss -- Low risk
    ("Adom Cocoa Farm", "Ama Mensah", "Asankrangwa", (5.8500, -2.4100), 125, 6, "gps_walk", 4.8, 0),
    ("Nhyira Cocoa Farm", "Afia Konadu", "Asankrangwa", (5.8215, -2.4180), 140, 7, "gps_walk", 6.2, 0),
    ("Benso Cocoa Farm", "Akua Boateng", "Sefwi Wiawso Rd", (5.7950, -2.4520), 100, 5, "drawn", None, 0),
    ("Aseda Cocoa Farm", "Kofi Adjei", "Samreboi", (5.5720, -2.5830), 160, 8, "gps_walk", 13.0, 2),
    ("Asempa Cocoa Farm", "Esi Ofori", "Enchi Rd", (5.8320, -2.4610), 130, 6, "gps_walk", 4.3, 0),
    # GEE-verified real Hansen tree-cover loss after the cutoff (found via Earth Engine
    # grid screening of the Asankrangwa-Wiawso cocoa belt) -- gives Medium/High demo examples.
    ("Bodi Cocoa Farm", "Yaw Owusu", "Bodi", (5.7740, -2.4280), 130, 7,
     "gps_walk", 5.5, 0),  # Medium: 0.18 ha loss, 2021
    ("Dadieso Cocoa Farm", "Abena Darko", "Dadieso Rd", (5.7980, -2.4160), 130, 6,
     "gps_walk", 7.1, 0),  # High: 0.65 ha loss, 2021-23
]

# Demo shipment batch: bag counts for the Low-risk farms it bundles -- each comfortably within
# its farm's mass-balance capacity (area x YIELD_KG_PER_HA, issue #6). Kuma's declared 1.2 ha
# caps it at 720 kg (11.25 bags), far below the 15-bag default other farms get.
BATCH_BAGS = {"Adom Cocoa Farm": 18, "Benso Cocoa Farm": 14, "Aseda Cocoa Farm": 22,
              "Asempa Cocoa Farm": 16, "Kuma Cocoa Plot": 10}

# One point-captured demo farm (issue #5, EUDR <= 4 ha): the exact centre of Adom Cocoa Farm's
# GEE-verified clear spot (confirmed directly via gee_service.gee_screen -- zero Hansen loss, no
# protected-area overlap, stable NDVI) -- a blind nearby offset previously landed in a real loss
# patch and scored High.
POINT_FARM_DEMO = {
    "name": "Kuma Cocoa Plot", "farmer": "Yaw Mensah", "village": "Asankrangwa",
    "lat": 5.8500, "lon": -2.4100, "declared_area_ha": 1.2, "gps_accuracy_m": 4.0,
}


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
        for i, (name, farmer, village, (lat, lon), r, v, method, accuracy, flagged) in enumerate(DEMO):
            if name in existing:
                continue
            g = ring(lat, lon, r, v)
            f = Farm(code=f"GH-{241 + n + i:04d}", name=name, farmer_name=farmer, village=village,
                     geometry=json.dumps(g), area_ha=round(geo.area_ha(g), 2),
                     perimeter_m=round(geo.perimeter_m(g), 1), vertices=v,
                     harvest_date="2026-10-15", captured_by="Demo seed", capture_method=method,
                     gps_accuracy_m=accuracy, flagged_points=flagged,
                     consent_method="attestation", consent_given=True, consent_date="2026-10-01")
            s.add(f)
            created.append(f)

        pf = POINT_FARM_DEMO
        if pf["name"] not in existing:
            point_geom = {"type": "Point", "coordinates": [pf["lon"], pf["lat"]]}
            f = Farm(code=f"GH-{241 + n + len(DEMO):04d}", name=pf["name"], farmer_name=pf["farmer"],
                     village=pf["village"], geometry=json.dumps(point_geom),
                     area_ha=pf["declared_area_ha"], perimeter_m=0.0, vertices=0,
                     declared_area_ha=pf["declared_area_ha"],
                     harvest_date="2026-10-15", captured_by="Demo seed", capture_method="gps_point",
                     gps_accuracy_m=pf["gps_accuracy_m"], flagged_points=0,
                     consent_method="attestation", consent_given=True, consent_date="2026-10-01")
            s.add(f)
            created.append(f)

        s.commit()
        for f in created:
            create_boundary_version(s, f)  # v1 for every newly created demo farm (issue #4)
        ids = [f.id for f in created]
    print(f"Added {len(ids)} demo farms.")

    # Give one farm a second boundary version (GPS-walk remap) so the version-history panel has
    # something to show in the demo (issue #4). Guarded by version_no==2 so a re-run without
    # --reset doesn't pile up more versions.
    with Session(engine) as s:
        benso = s.exec(select(Farm).where(Farm.name == "Benso Cocoa Farm")).first()
        already_v2 = benso and s.exec(
            select(BoundaryVersion).where(BoundaryVersion.farm_id == benso.id, BoundaryVersion.version_no == 2)
        ).first()
        if benso and not already_v2:
            g2 = ring(5.7950, -2.4520, 115, 6)  # the farmer bought a neighbouring plot
            benso.geometry = json.dumps(g2)
            benso.area_ha = round(geo.area_ha(g2), 2)
            benso.perimeter_m = round(geo.perimeter_m(g2), 1)
            benso.vertices = 6
            benso.capture_method = "gps_walk"
            benso.gps_accuracy_m = 5.5
            benso.flagged_points = 0
            benso.captured_by = "Demo seed (remap)"
            benso.status = "Not screened"
            benso.risk_score = None
            benso.risk_level = None
            benso.last_screened = None
            s.add(benso)
            s.commit()
            s.refresh(benso)
            create_boundary_version(s, benso)
            print(f"  {benso.code} Benso Cocoa Farm: remapped to v2 (GPS walk, neighbouring plot added).")

    if "--verify" in sys.argv:
        from fastapi.testclient import TestClient
        from .main import app
        with TestClient(app) as c:
            with Session(engine) as s:
                for f in s.exec(select(Farm)).all():
                    r = c.post(f"/api/farms/{f.id}/verify", params={"role": "admin"}).json()
                    print(f"  {f.code} {f.name}: {r['risk_level']} ({r['risk_score']}) "
                          f"[{r['verification']['source']}]")

        with Session(engine) as s:
            if not s.exec(select(Batch)).first():
                low_farms = [f for f in s.exec(select(Farm)).all()
                             if f.status == "Verified" and f.risk_level == "Low"]
                if low_farms:
                    batch = Batch(code="BATCH-2026-001",
                                  buyer="Chocolaterie Vandermeer, Ghent, Belgium",
                                  created_by="Demo seed", season=config.DEFAULT_SEASON)
                    s.add(batch)
                    s.commit()
                    s.refresh(batch)
                    total_bags = 0
                    for f in low_farms:
                        bags = BATCH_BAGS.get(f.name, 15)
                        current_version = latest_boundary_version(s, f.id)
                        contribution = BatchContribution(batch_id=batch.id, farm_id=f.id,
                                                          bags=bags, kg_per_bag=64.0,
                                                          boundary_version_id=current_version.id if current_version else None)
                        s.add(contribution)
                        s.commit()
                        s.refresh(contribution)
                        create_bags(s, batch, contribution, f)
                        total_bags += bags
                    s.commit()
                    print(f"Seeded {batch.code}: {len(low_farms)} farms, {total_bags} bags "
                          f"({total_bags} bag labels).")

                    # One demo mass-balance override (issue #6, optional): a second shipment in
                    # the same season pushes Adom past its remaining capacity, approved by an admin.
                    adom = next((f for f in low_farms if f.name == "Adom Cocoa Farm"), None)
                    if adom and not s.exec(select(Batch).where(Batch.code == "BATCH-2026-002")).first():
                        batch2 = Batch(code="BATCH-2026-002", buyer="Artisan du Chocolat, Lyon, France",
                                      created_by="Demo seed", season=config.DEFAULT_SEASON)
                        s.add(batch2)
                        s.commit()
                        s.refresh(batch2)
                        bv = latest_boundary_version(s, adom.id)
                        override_bags = 25  # 1,600 kg -- exceeds Adom's remaining season capacity
                        contribution2 = BatchContribution(
                            batch_id=batch2.id, farm_id=adom.id, bags=override_bags, kg_per_bag=64.0,
                            boundary_version_id=bv.id if bv else None,
                            override_reason="Second harvest", override_note="Second flowering this season, confirmed by field visit.",
                            overridden_by="Nadia Owusu", overridden_at=datetime.now(timezone.utc),
                        )
                        s.add(contribution2)
                        s.commit()
                        s.refresh(contribution2)
                        create_bags(s, batch2, contribution2, adom)
                        s.commit()
                        print(f"Seeded {batch2.code}: 1 farm, {override_bags} bags, "
                              f"with a mass-balance override on Adom Cocoa Farm.")

                    # One demo mass-balance approval request (issue #15): a buyer top-up on Benso
                    # that exceeds its remaining season capacity, awaiting a Brand admin's
                    # decision -- no bags yet, since nothing is generated until it's approved.
                    benso = next((f for f in low_farms if f.name == "Benso Cocoa Farm"), None)
                    if benso and not s.exec(select(Batch).where(Batch.code == "BATCH-2026-003")).first():
                        batch3 = Batch(code="BATCH-2026-003", buyer="Nouvelle Chocolaterie, Paris, France",
                                      created_by="Kofi Mensah", season=config.DEFAULT_SEASON,
                                      status="Pending approval",
                                      submission_note="Buyer increased the order after Benso's second "
                                                       "flowering; the extra bags exceed its remaining "
                                                       "season capacity.")
                        s.add(batch3)
                        s.commit()
                        s.refresh(batch3)
                        bv3 = latest_boundary_version(s, benso.id)
                        pending_bags = 30  # 1,920 kg -- exceeds Benso's remaining season capacity
                        contribution3 = BatchContribution(
                            batch_id=batch3.id, farm_id=benso.id, bags=pending_bags, kg_per_bag=64.0,
                            boundary_version_id=bv3.id if bv3 else None,
                        )
                        s.add(contribution3)
                        s.add(BatchEvent(batch_id=batch3.id, event="submitted", by="Kofi Mensah",
                                         by_role="warehouse", note=batch3.submission_note))
                        s.commit()
                        print(f"Seeded {batch3.code}: Pending approval, {pending_bags} bags "
                              f"requested on Benso Cocoa Farm.")


if __name__ == "__main__":
    main()
