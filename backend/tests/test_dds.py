from tests.conftest import make_polygon

FARM = {
    "captured_by_role": "agent",
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


def _verified_batch(client, bags=4, kg_per_bag=60.0):
    farm = client.post("/api/farms", json=FARM).json()
    client.post(f"/api/farms/{farm['id']}/verify", params={"role": "agent"})
    batch = client.post("/api/batches", json={"created_by_role": "warehouse",
        "contributions": [{"farm_id": farm["id"], "bags": bags, "kg_per_bag": kg_per_bag}],
    }).json()
    return farm, batch


def test_dds_export_contains_statement_fields(client, mock_screen):
    _, batch = _verified_batch(client)

    r = client.get(f"/api/batches/{batch['id']}/dds")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert f"DDS_{batch['code']}.json" in r.headers["content-disposition"]

    doc = r.json()
    assert doc["statement_id"] == f"DDS-{batch['code']}"
    assert doc["status"] == "draft"
    assert doc["commodity"]["quantity"]["bags"] == 4
    assert doc["commodity"]["quantity"]["net_weight_kg"] == 240.0
    assert doc["production"]["country"] == "Ghana"
    assert "villages" not in doc["production"]  # always-empty fields are omitted, not padded

    farm_entry = doc["production"]["farms"][0]
    assert farm_entry["geolocation"]["type"] == "Polygon"
    assert farm_entry["producer"] == "AM"  # initials, not the farmer's full name
    assert farm_entry["screening"]["risk_level"] == "Low"
    assert farm_entry["screening"]["run_id"]
    assert "Ama Mensah" not in r.text

    assert doc["risk_assessment"]["overall_risk_level"] == "Low"
    assert doc["risk_assessment"]["high_risk_farms"] == []
    assert len(doc["integrity"]["sha256"]) == 64
    assert len(doc["attachments"]) == 2
    assert all(a["url"].startswith("http") for a in doc["attachments"])  # absolute links
    assert "created_by" not in doc["supply_chain"]  # staff names are minimised too
    assert "manual_review" not in farm_entry  # no review happened -> no null placeholder


def test_dds_pending_batch_returns_400(client, mock_screen):
    """Pending approval means no bags exist yet, so there is nothing to make a statement about."""
    farm = client.post("/api/farms", json=FARM).json()
    client.post(f"/api/farms/{farm['id']}/verify", params={"role": "agent"})
    b = client.post("/api/batches", json={"created_by_role": "warehouse",
        "contributions": [{"farm_id": farm["id"], "bags": 100, "kg_per_bag": 64.0}],
        "justification_note": "Second harvest agreed with the cooperative",
    })
    assert b.status_code in (200, 201), b.text
    assert b.json()["status"] == "Pending approval"
    assert client.get(f"/api/batches/{b.json()['id']}/dds").status_code == 400


def test_dds_reports_the_frozen_verification_not_the_latest_rerun(client, mock_screen):
    """PR #20 review: a re-run after batching must not change the statement's score."""
    mock_screen(loss_ha=0.3)  # real loss -> Under review, then cleared by the Brand admin
    farm = client.post("/api/farms", json=FARM).json()
    assert client.post(f"/api/farms/{farm['id']}/verify", params={"role": "agent"}).json()["status"] == "Under review"
    rr = client.post(f"/api/farms/{farm['id']}/review", json={
        "decision": "clear", "reason": "Cloud cover", "note": "Checked on site",
        "reviewer_name": "Nadia Owusu", "reviewer_role": "admin"})
    assert rr.status_code == 200, rr.text

    frozen = client.get(f"/api/farms/{farm['id']}").json()
    frozen_level, frozen_score = frozen["risk_level"], frozen["risk_score"]
    assert frozen_level != "Low"

    batch = client.post("/api/batches", json={"created_by_role": "warehouse",
        "contributions": [{"farm_id": farm["id"], "bags": 4, "kg_per_bag": 60.0}]}).json()
    assert batch["status"] == "Approved"

    # A later re-run comes back clean: the farm is still batchable, so the export succeeds...
    mock_screen(loss_ha=0.0)
    assert client.post(f"/api/farms/{farm['id']}/verify", params={"role": "agent"}).json()["status"] == "Verified"
    assert client.get(f"/api/farms/{farm['id']}").json()["risk_level"] == "Low"

    doc = client.get(f"/api/batches/{batch['id']}/dds").json()
    entry = doc["production"]["farms"][0]
    # ...but the statement keeps the score/level frozen at batching time.
    assert entry["screening"]["risk_level"] == frozen_level
    assert entry["screening"]["risk_score"] == frozen_score


def test_dds_is_blocked_when_a_farm_regresses(client, mock_screen):
    farm, batch = _verified_batch(client)

    # Re-screening finds heavy loss: the farm drops out of "Verified" after batching.
    mock_screen(loss_ha=0.6)
    assert client.post(f"/api/farms/{farm['id']}/verify", params={"role": "agent"}).json()["status"] == "Under review"

    r = client.get(f"/api/batches/{batch['id']}/dds")
    assert r.status_code == 409


def test_dds_unknown_batch_is_404(client):
    assert client.get("/api/batches/9999/dds").status_code == 404
