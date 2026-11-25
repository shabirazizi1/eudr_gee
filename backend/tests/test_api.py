import base64
import io
import json

import pymupdf
from PIL import Image
from sqlmodel import Session, select

from app import config
from app.db import engine
from app.models import Bag
from tests.conftest import make_polygon

VALID_FARM = {
    "name": "Adom Cocoa Farm",
    "farmer_name": "Ama Mensah",
    "village": "Asankrangwa",
    "capture_method": "gps_walk",
    "gps_accuracy_m": 4.3,
    "flagged_points": 0,
    "geometry": make_polygon(),
    "consent_given": True,
    "consent_date": "2026-10-01",
}

SELF_INTERSECTING = {
    "name": "Bad Farm",
    "farmer_name": "Someone",
    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]},
    "consent_given": True,
    "consent_date": "2026-10-01",
}

POINT_FARM = {
    "name": "Tiny Cocoa Plot",
    "farmer_name": "Kwame Asante",
    "village": "Enchi Rd",
    "geometry": {"type": "Point", "coordinates": [-2.4305, 5.8003]},
    "capture_method": "gps_point",
    "gps_accuracy_m": 4.5,
    "flagged_points": 0,
    "declared_area_ha": 1.5,
    "consent_given": True,
    "consent_date": "2026-10-01",
}


def _tiny_jpeg_base64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), color="white").save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def create_verified_farm(client, mock_screen, farm_payload=None, **screen_overrides):
    farm = client.post("/api/farms", json=farm_payload or VALID_FARM).json()
    mock_screen(**screen_overrides)
    verified = client.post(f"/api/farms/{farm['id']}/verify").json()
    return verified


# ---------------------------------------------------------------- farms

def test_create_farm_stores_gps_walk_capture_method_and_accuracy(client):
    r = client.post("/api/farms", json=VALID_FARM)
    assert r.status_code == 201
    body = r.json()
    assert body["capture_method"] == "gps_walk"
    assert body["gps_accuracy_m"] == 4.3
    assert body["code"].startswith("GH-")
    assert body["area_ha"] > 0

    # round-trips through GET too, not just the create response
    got = client.get(f"/api/farms/{body['id']}").json()
    assert got["capture_method"] == "gps_walk"
    assert got["gps_accuracy_m"] == 4.3


def test_create_farm_rejects_self_intersecting_polygon(client):
    r = client.post("/api/farms", json=SELF_INTERSECTING)
    assert r.status_code == 422


# ---------------------------------------------------------------- point capture (issue #5)

def test_create_point_farm_within_4ha_is_accepted(client):
    r = client.post("/api/farms", json=POINT_FARM)
    assert r.status_code == 201
    body = r.json()
    assert body["capture_method"] == "gps_point"
    assert body["declared_area_ha"] == 1.5
    assert body["area_ha"] == 1.5
    assert body["vertices"] == 0
    assert body["perimeter_m"] == 0
    # the display geometry is a buffered polygon, not the raw point (for maps/PDFs)
    assert body["geometry"]["type"] == "Polygon"


def test_create_point_farm_over_4ha_is_rejected(client):
    r = client.post("/api/farms", json={**POINT_FARM, "declared_area_ha": 4.5})
    assert r.status_code == 422
    assert any("4 ha" in d for d in r.json()["detail"])


def test_create_point_farm_without_declared_area_is_rejected(client):
    r = client.post("/api/farms", json={**POINT_FARM, "declared_area_ha": None})
    assert r.status_code == 422


def test_polygon_farms_are_unaffected_by_point_capture(client):
    r = client.post("/api/farms", json=VALID_FARM)
    assert r.status_code == 201
    body = r.json()
    assert body["capture_method"] == "gps_walk"
    assert body["declared_area_ha"] is None
    assert body["geometry"]["type"] == "Polygon"
    assert body["vertices"] > 0


# ---------------------------------------------------------------- consent & data minimization (issue #7)

def test_create_farm_without_consent_is_422(client):
    body = {**VALID_FARM, "consent_given": False, "consent_date": None}
    r = client.post("/api/farms", json=body)
    assert r.status_code == 422


def test_create_farm_with_attestation_consent_succeeds(client):
    r = client.post("/api/farms", json=VALID_FARM)
    assert r.status_code == 201
    body = r.json()
    assert body["consent_method"] == "attestation"
    assert body["consent_given"] is True
    assert "consent_photo_path" not in body


def test_create_farm_with_photo_consent_is_not_exposed_publicly(client):
    body = {**VALID_FARM, "consent_photo_base64": _tiny_jpeg_base64()}
    r = client.post("/api/farms", json=body)
    assert r.status_code == 201
    farm = r.json()
    assert farm["consent_method"] == "photo"
    assert "consent_photo_path" not in farm

    got = client.get(f"/api/farms/{farm['id']}").json()
    assert "consent_photo_path" not in got

    listed = client.get("/api/farms").json()
    assert all("consent_photo_path" not in f for f in listed)

    photo = client.get(f"/api/farms/{farm['id']}/consent_photo")
    assert photo.status_code == 200
    assert photo.headers["content-type"] == "image/jpeg"

    missing = client.get("/api/farms/999999/consent_photo")
    assert missing.status_code == 404


def test_report_before_verify_is_400(client):
    farm = client.post("/api/farms", json=VALID_FARM).json()
    r = client.get(f"/api/farms/{farm['id']}/report.pdf")
    assert r.status_code == 400


def test_verify_then_report_is_200(client, mock_screen):
    farm = client.post("/api/farms", json=VALID_FARM).json()
    v = client.post(f"/api/farms/{farm['id']}/verify")
    assert v.status_code == 200
    assert v.json()["status"] == "Verified"

    r = client.get(f"/api/farms/{farm['id']}/report.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content[:4] == b"%PDF"


def test_farm_evidence_pdf_has_no_full_farmer_name_or_consent(client, mock_screen):
    farm = client.post("/api/farms", json=VALID_FARM).json()
    client.post(f"/api/farms/{farm['id']}/verify")
    r = client.get(f"/api/farms/{farm['id']}/report.pdf")
    assert r.status_code == 200

    doc = pymupdf.open(stream=r.content, filetype="pdf")
    text = "\n".join(page.get_text() for page in doc)
    assert VALID_FARM["farmer_name"] not in text
    assert VALID_FARM["village"] not in text
    assert "consent" not in text.lower()
    assert "AM" in text  # farmer initials still shown


def test_point_farm_evidence_and_batch_pdfs_render(client, mock_screen):
    """A point-captured farm's PDFs must render using the buffered display geometry, not the
    raw GeoJSON Point (which would crash the map/boundary-table code) -- issue #5."""
    point_farm = create_verified_farm(client, mock_screen, farm_payload=POINT_FARM)

    r = client.get(f"/api/farms/{point_farm['id']}/report.pdf")
    assert r.status_code == 200
    text = "\n".join(p.get_text() for p in pymupdf.open(stream=r.content, filetype="pdf"))
    assert "Point (declared 1.50 ha)" in text

    batch = client.post("/api/batches", json={
        "contributions": [{"farm_id": point_farm["id"], "bags": 3}],
    }).json()
    r = client.get(f"/api/batches/{batch['id']}/report.pdf")
    assert r.status_code == 200
    text = "\n".join(p.get_text() for p in pymupdf.open(stream=r.content, filetype="pdf"))
    assert "Point (declared 1.50 ha)" in text


# ---------------------------------------------------------------- batches & review

def test_batch_blocks_non_verified_farm(client):
    farm = client.post("/api/farms", json=VALID_FARM).json()  # never verified -> "Not screened"
    r = client.post("/api/batches", json={"contributions": [{"farm_id": farm["id"], "bags": 5}]})
    assert r.status_code == 422


def test_review_clear_allows_batch_join(client, mock_screen):
    verified = create_verified_farm(client, mock_screen, protected_areas=["Ankasa Conservation Area"])
    assert verified["status"] == "Under review"

    r = client.post(f"/api/farms/{verified['id']}/review", json={
        "decision": "clear", "reason": "Cloud cover", "note": "Checked manually, no real loss.",
        "reviewer_name": "Nadia Owusu",
    })
    assert r.status_code == 200
    assert r.json()["status"] == "Verified (manual review)"

    batch = client.post("/api/batches", json={
        "contributions": [{"farm_id": verified["id"], "bags": 5}],
    })
    assert batch.status_code == 201
    assert batch.json()["totals"]["farms"] == 1


def test_review_confirm_blocks_batch_join(client, mock_screen):
    verified = create_verified_farm(client, mock_screen, protected_areas=["Ankasa Conservation Area"])
    assert verified["status"] == "Under review"

    r = client.post(f"/api/farms/{verified['id']}/review", json={
        "decision": "confirm", "reason": "Boundary error", "note": "Confirmed real clearing.",
        "reviewer_name": "Nadia Owusu",
    })
    assert r.status_code == 200
    assert r.json()["status"] == "Excluded"


# ---------------------------------------------------------------- mass balance (issue #6)

def test_capacity_maths_for_polygon_and_point_farms(client, mock_screen):
    polygon_farm = create_verified_farm(client, mock_screen)
    point_farm = create_verified_farm(client, mock_screen, farm_payload=POINT_FARM)

    farms = {f["id"]: f for f in client.get("/api/farms", params={"season": "2026/27"}).json()}
    poly_expected = round(polygon_farm["area_ha"] * config.YIELD_KG_PER_HA, 1)
    point_expected = round(POINT_FARM["declared_area_ha"] * config.YIELD_KG_PER_HA, 1)

    assert farms[polygon_farm["id"]]["capacity_kg"] == poly_expected
    assert farms[polygon_farm["id"]]["remaining_capacity_kg"] == poly_expected  # nothing committed yet
    assert farms[point_farm["id"]]["capacity_kg"] == point_expected
    assert farms[point_farm["id"]]["remaining_capacity_kg"] == point_expected


def test_batch_exceeding_capacity_is_blocked_without_override(client, mock_screen):
    verified = create_verified_farm(client, mock_screen)
    r = client.post("/api/batches", json={
        "contributions": [{"farm_id": verified["id"], "bags": 1000}],  # 64,000 kg -- always over capacity
    })
    assert r.status_code == 422
    assert any("override" in d.lower() for d in r.json()["detail"])


def test_admin_override_accepted_and_recorded(client, mock_screen):
    verified = create_verified_farm(client, mock_screen)
    r = client.post("/api/batches", json={
        "contributions": [{
            "farm_id": verified["id"], "bags": 1000,
            "override_reason": "Second harvest", "override_note": "Confirmed via field visit.",
            "overridden_by": "Nadia Owusu", "overridden_by_role": "admin",
        }],
    })
    assert r.status_code == 201
    override = r.json()["contributions"][0]["override"]
    assert override["reason"] == "Second harvest"
    assert override["by"] == "Nadia Owusu"
    assert override["note"] == "Confirmed via field visit."
    assert override["at"]


def test_non_admin_override_is_rejected(client, mock_screen):
    verified = create_verified_farm(client, mock_screen)
    r = client.post("/api/batches", json={
        "contributions": [{
            "farm_id": verified["id"], "bags": 1000,
            "override_reason": "Second harvest", "override_note": "Confirmed via field visit.",
            "overridden_by": "Kofi Mensah", "overridden_by_role": "warehouse",
        }],
    })
    assert r.status_code == 422
    assert any("admin" in d.lower() for d in r.json()["detail"])


def test_capacity_is_per_season(client, mock_screen):
    verified = create_verified_farm(client, mock_screen)
    capacity = verified["area_ha"] * config.YIELD_KG_PER_HA
    bags_70pct = int((capacity * 0.7) / 64)

    first = client.post("/api/batches", json={
        "contributions": [{"farm_id": verified["id"], "bags": bags_70pct}],
        "season": "2026/27",
    })
    assert first.status_code == 201

    # same season, same farm, another 70% -> exceeds the remaining 30% -> blocked
    blocked = client.post("/api/batches", json={
        "contributions": [{"farm_id": verified["id"], "bags": bags_70pct}],
        "season": "2026/27",
    })
    assert blocked.status_code == 422

    # a new season resets remaining capacity -> the same request now succeeds
    new_season = client.post("/api/batches", json={
        "contributions": [{"farm_id": verified["id"], "bags": bags_70pct}],
        "season": "2027/28",
    })
    assert new_season.status_code == 201


def test_remapped_area_applies_to_new_batches_only(client, mock_screen):
    verified = create_verified_farm(client, mock_screen)
    capacity1 = verified["area_ha"] * config.YIELD_KG_PER_HA
    bags_half = int((capacity1 * 0.5) / 64)

    batch1 = client.post("/api/batches", json={
        "contributions": [{"farm_id": verified["id"], "bags": bags_half}],
        "season": "2026/27",
    })
    assert batch1.status_code == 201
    batch1_id = batch1.json()["id"]

    # remap to a much smaller plot, then re-verify to make it batchable again
    small_square = make_polygon(cx=-2.45, cy=5.82, half=0.0002)
    remapped = client.post(f"/api/farms/{verified['id']}/remap", json={"geometry": small_square}).json()
    assert remapped["area_ha"] < verified["area_ha"]
    mock_screen()
    client.post(f"/api/farms/{verified['id']}/verify")

    # the new (smaller) capacity applies to a NEW batch in the same season -- already over
    # capacity from batch1 alone, so even one more bag is blocked
    blocked = client.post("/api/batches", json={
        "contributions": [{"farm_id": verified["id"], "bags": 1}],
        "season": "2026/27",
    })
    assert blocked.status_code == 422

    # the existing batch1 is untouched by the remap
    assert client.get(f"/api/batches/{batch1_id}").status_code == 200

    batch = client.post("/api/batches", json={
        "contributions": [{"farm_id": verified["id"], "bags": 5}],
    })
    assert batch.status_code == 422


# ---------------------------------------------------------------- boundary history (issue #4)

def test_create_farm_creates_v1(client):
    farm = client.post("/api/farms", json=VALID_FARM).json()
    assert farm["boundary_version"] == 1

    versions = client.get(f"/api/farms/{farm['id']}/boundary_versions").json()
    assert len(versions) == 1
    assert versions[0]["version_no"] == 1
    assert versions[0]["geometry"] == farm["geometry"]


def test_remap_creates_v2_with_v1_unchanged(client):
    farm = client.post("/api/farms", json=VALID_FARM).json()
    v1_geometry = farm["geometry"]

    r = client.post(f"/api/farms/{farm['id']}/remap", json={
        "geometry": make_polygon(cx=-2.431, cy=5.801), "capture_method": "gps_walk",
        "gps_accuracy_m": 5.0, "flagged_points": 0, "captured_by": "Field Agent 2",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["boundary_version"] == 2
    assert body["geometry"] != v1_geometry
    assert body["capture_method"] == "gps_walk"

    versions = client.get(f"/api/farms/{farm['id']}/boundary_versions").json()
    assert len(versions) == 2
    assert versions[0]["version_no"] == 1
    assert versions[0]["geometry"] == v1_geometry  # read-only: untouched by the remap
    assert versions[1]["version_no"] == 2
    assert versions[1]["geometry"] == body["geometry"]


def test_remap_rejects_self_intersecting_polygon(client):
    farm = client.post("/api/farms", json=VALID_FARM).json()
    r = client.post(f"/api/farms/{farm['id']}/remap", json={
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]},
    })
    assert r.status_code == 422
    # the bad attempt must not have created a new version
    assert len(client.get(f"/api/farms/{farm['id']}/boundary_versions").json()) == 1


def test_remap_resets_status_and_blocks_batching(client, mock_screen):
    verified = create_verified_farm(client, mock_screen)
    assert verified["status"] == "Verified"

    remapped = client.post(f"/api/farms/{verified['id']}/remap", json={
        "geometry": make_polygon(cx=-2.432, cy=5.802),
    }).json()
    assert remapped["status"] == "Not screened"
    assert remapped["risk_score"] is None

    r = client.post("/api/batches", json={"contributions": [{"farm_id": verified["id"], "bags": 5}]})
    assert r.status_code == 422


def test_verification_links_to_the_boundary_version_it_screened(client, mock_screen):
    farm = client.post("/api/farms", json=VALID_FARM).json()
    v1 = client.post(f"/api/farms/{farm['id']}/verify").json()
    assert v1["verification"]["run_id"]
    assert v1["boundary_version"] == 1

    client.post(f"/api/farms/{farm['id']}/remap", json={"geometry": make_polygon(cx=-2.433, cy=5.803)})
    v2 = client.post(f"/api/farms/{farm['id']}/verify").json()
    assert v2["boundary_version"] == 2


def test_batch_keeps_the_boundary_version_it_was_built_with(client, mock_screen):
    verified = create_verified_farm(client, mock_screen)
    batch = client.post("/api/batches", json={
        "contributions": [{"farm_id": verified["id"], "bags": 5}],
    }).json()
    v1_geometry = batch["contributions"][0]["farm"]["geometry"]
    assert batch["contributions"][0]["farm"]["boundary_version"] == 1

    client.post(f"/api/farms/{verified['id']}/remap", json={"geometry": make_polygon(cx=-2.434, cy=5.804)})

    got = client.get(f"/api/batches/{batch['id']}").json()
    assert got["contributions"][0]["farm"]["boundary_version"] == 1
    assert got["contributions"][0]["farm"]["geometry"] == v1_geometry
    # the live farm record itself did move on
    assert client.get(f"/api/farms/{verified['id']}").json()["boundary_version"] == 2


# ---------------------------------------------------------------- due-diligence geolocation export (issue #5)

def test_batch_geolocation_export_is_valid(client, mock_screen):
    polygon_farm = create_verified_farm(client, mock_screen)
    point_farm = create_verified_farm(client, mock_screen, farm_payload=POINT_FARM)
    batch = client.post("/api/batches", json={
        "contributions": [
            {"farm_id": polygon_farm["id"], "bags": 5},
            {"farm_id": point_farm["id"], "bags": 3},
        ],
    }).json()

    r = client.get(f"/api/batches/{batch['id']}/geolocation.geojson")
    assert r.status_code == 200
    assert "geo+json" in r.headers["content-type"]
    data = r.json()
    assert data["type"] == "FeatureCollection"
    assert len(data["features"]) == 2

    by_code = {f["properties"]["farm_code"]: f for f in data["features"]}
    poly_feature = by_code[polygon_farm["code"]]
    point_feature = by_code[point_farm["code"]]

    # correct geometry types: Polygon stays a Polygon, a <=4 ha point stays a true Point
    assert poly_feature["geometry"]["type"] == "Polygon"
    assert point_feature["geometry"]["type"] == "Point"

    # coordinates rounded to 6 decimals
    for lon, lat in poly_feature["geometry"]["coordinates"][0]:
        assert round(lon, 6) == lon and round(lat, 6) == lat
    plon, plat = point_feature["geometry"]["coordinates"]
    assert round(plon, 6) == plon and round(plat, 6) == plat

    # required properties, and no personal data in them
    for props in (poly_feature["properties"], point_feature["properties"]):
        assert props["farm_code"]
        assert props["area_ha"] > 0
        assert props["producer_country"] == "GH"
        assert props["commodity"] == "Cocoa beans (HS 1801)"
        assert "production_period" in props
        assert props["boundary_version"] == 1
        assert props["verification_date"]
        assert props["status"]

    dumped = json.dumps(data)
    assert VALID_FARM["farmer_name"] not in dumped
    assert POINT_FARM["farmer_name"] not in dumped


# ---------------------------------------------------------------- bags (public QR target)

def test_bag_lookup_and_404(client, mock_screen):
    verified = create_verified_farm(client, mock_screen)
    assert verified["status"] == "Verified"
    batch = client.post("/api/batches", json={
        "contributions": [{"farm_id": verified["id"], "bags": 3}],
    }).json()

    with Session(engine) as s:
        bag = s.exec(select(Bag).where(Bag.batch_id == batch["id"])).first()
    assert bag is not None

    r = client.get(f"/api/bags/{bag.bag_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["bag_id"] == bag.bag_id
    assert body["farm"]["id"] == verified["id"]
    assert body["batch"]["code"] == batch["code"]

    missing = client.get("/api/bags/BAG-9999-0001")
    assert missing.status_code == 404


def test_bag_page_has_no_full_farmer_name(client, mock_screen):
    verified = create_verified_farm(client, mock_screen)
    batch = client.post("/api/batches", json={
        "contributions": [{"farm_id": verified["id"], "bags": 3}],
    }).json()
    with Session(engine) as s:
        bag = s.exec(select(Bag).where(Bag.batch_id == batch["id"])).first()

    body = client.get(f"/api/bags/{bag.bag_id}").json()
    assert body["farm"]["id"] == verified["id"]
    assert body["farm"]["farmer_name"] != VALID_FARM["farmer_name"]
    assert VALID_FARM["farmer_name"] not in body["farm"]["farmer_name"]
    assert "village" not in body["farm"]
    assert "consent_given" not in body["farm"]
    assert "consent_photo_path" not in body["farm"]
