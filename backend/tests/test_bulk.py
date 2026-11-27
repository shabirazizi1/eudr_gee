import time

from sqlmodel import Session, select

from app import gee_service
from app.db import engine
from app.models import Verification
from tests.conftest import DEFAULT_RESULT, make_polygon


def _farm(name, **overrides):
    body = {
        "captured_by_role": "agent",
        "name": name,
        "farmer_name": "Ama Mensah",
        "geometry": make_polygon(),
        "capture_method": "gps_walk",
        "gps_accuracy_m": 4.0,
        "flagged_points": 0,
        "consent_given": True,
        "consent_date": "2026-10-01",
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


def _start(client, ids, role="admin"):
    return client.post("/api/bulk/verify", json={
        "farm_ids": ids, "triggered_by": "Nadia Owusu", "by_role": role})


def test_bulk_screening_updates_every_selected_farm(client, mock_screen):
    ids = [client.post("/api/farms", json=_farm(n)).json()["id"] for n in ("Farm A", "Farm B")]

    start = _start(client, ids)
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
    r = client.post("/api/bulk/verify", json={"farm_ids": [], "by_role": "admin"})
    assert r.status_code == 422


def test_bulk_unknown_job_is_404(client):
    assert client.get("/api/bulk/BULK-NOPE").status_code == 404


def test_bulk_requires_admin_role(client, mock_screen):
    """PR #20 review: bulk screening is admin-only, enforced server-side like batch approval."""
    fid = client.post("/api/farms", json=_farm("Role farm")).json()["id"]
    assert _start(client, [fid], role="agent").status_code == 403
    assert client.post("/api/bulk/verify", json={"farm_ids": [fid]}).status_code == 403
    started = _start(client, [fid])
    assert started.status_code == 200
    assert _wait(client, started.json()["job_id"])["done"] == 1


def test_bulk_point_farm_is_buffered_before_screening(client, monkeypatch):
    """PR #20 review: a declared-area Point must NOT reach Earth Engine as a single pixel."""
    captured = {}

    def fake_screen(geojson, area):
        captured["geometry"] = geojson
        return dict(DEFAULT_RESULT)

    monkeypatch.setattr(gee_service, "screen", fake_screen)
    r = client.post("/api/farms", json=_farm(
        "Point farm", boundary_type="point", declared_area_ha=2.0, capture_method="pin_drop",
        geometry={"type": "Point", "coordinates": [-2.0, 5.0]}))
    assert r.status_code in (200, 201), r.text

    start = _start(client, [r.json()["id"]])
    done = _wait(client, start.json()["job_id"])
    assert done["failed"] == 0
    assert captured["geometry"]["type"] == "Polygon", "point farm was screened as a raw point"


def test_bulk_one_farm_failing_does_not_sink_the_job(client, monkeypatch):
    ids = [client.post("/api/farms", json=_farm(n)).json()["id"] for n in ("Farm A", "Farm B")]
    calls = {"n": 0}

    def flaky(geojson, area):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("GEE quota exceeded")
        return dict(DEFAULT_RESULT)

    monkeypatch.setattr(gee_service, "screen", flaky)
    start = _start(client, ids)
    done = _wait(client, start.json()["job_id"])
    assert done["done"] == 2
    assert done["failed"] == 1
    verdicts = [client.get(f"/api/farms/{fid}").json()["status"] for fid in ids]
    assert verdicts.count("Verified") == 1
    assert verdicts.count("Not screened") == 1


def test_bulk_can_rescreen_an_already_screened_farm(client, mock_screen):
    """PR #20 review: re-running a screened farm must stay possible."""
    fid = client.post("/api/farms", json=_farm("Rescreen me")).json()["id"]
    assert client.post(f"/api/farms/{fid}/verify", params={"role": "agent"}, json={"agent_name": "Test Agent"}).status_code == 200
    assert client.get(f"/api/farms/{fid}").json()["status"] == "Verified"

    done = _wait(client, _start(client, [fid]).json()["job_id"])
    assert done["done"] == 1
    assert done["failed"] == 0
    assert client.get(f"/api/farms/{fid}").json()["status"] == "Verified"
