import time

from sqlmodel import Session, select

from app.db import engine
from app.models import Verification
from tests.conftest import make_polygon


def _farm(name, **overrides):
    body = {
        "name": name,
        "farmer_name": "Ama Mensah",
        "geometry": make_polygon(),
        "capture_method": "gps_walk",
        "gps_accuracy_m": 4.0,
        "flagged_points": 0,
    }
    body.update(overrides)
    return body


def _wait(client, job_id, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/bulk/{job_id}").json()
        if job["status"] in ("done", "failed"):
            return job
        time.sleep(0.1)
    raise AssertionError(f"bulk job {job_id} did not finish in time")


def test_bulk_screening_updates_every_selected_farm(client, mock_screen):
    ids = [client.post("/api/farms", json=_farm(n)).json()["id"] for n in ("Farm A", "Farm B")]

    start = client.post("/api/bulk/verify", json={"farm_ids": ids, "triggered_by": "Nadia Owusu"})
    assert start.status_code == 200
    job = start.json()
    assert job["total"] == 2
    assert job["status"] in ("queued", "running")

    done = _wait(client, job["job_id"])
    assert done["status"] == "done"
    assert done["done"] == 2
    assert done["failed"] == 0
    assert {r["farm_id"] for r in done["results"]} == set(ids)
    assert all(r["status"] == "ok" for r in done["results"])

    for farm_id in ids:
        farm = client.get(f"/api/farms/{farm_id}").json()
        assert farm["status"] == "Verified"
        assert farm["risk_level"] == "Low"

    with Session(engine) as s:
        assert len(s.exec(select(Verification)).all()) == 2

    summary = client.get(f"/api/bulk/{job['job_id']}/summary").json()
    assert summary["counts"]["Low"] == 2
    assert summary["errors"] == 0


def test_bulk_rejects_empty_selection(client):
    r = client.post("/api/bulk/verify", json={"farm_ids": []})
    assert r.status_code == 422


def test_bulk_unknown_job_is_404(client):
    assert client.get("/api/bulk/BULK-NOPE").status_code == 404
