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
}

SELF_INTERSECTING = {
    "name": "Bad Farm",
    "farmer_name": "Someone",
    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 1], [1, 0], [0, 1], [0, 0]]]},
}


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
