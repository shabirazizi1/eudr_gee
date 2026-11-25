import base64
import io

from PIL import Image
from sqlmodel import Session, select

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


def _tiny_jpeg_base64() -> str:
    buf = io.BytesIO()
    Image.new("RGB", (2, 2), color="white").save(buf, format="JPEG")
    return base64.b64encode(buf.getvalue()).decode()


def create_verified_farm(client, mock_screen, **screen_overrides):
    farm = client.post("/api/farms", json=VALID_FARM).json()
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

    batch = client.post("/api/batches", json={
        "contributions": [{"farm_id": verified["id"], "bags": 5}],
    })
    assert batch.status_code == 422


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
