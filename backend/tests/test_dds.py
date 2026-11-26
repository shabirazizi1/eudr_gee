from tests.conftest import make_polygon

FARM = {
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
    client.post(f"/api/farms/{farm['id']}/verify")
    batch = client.post("/api/batches", json={
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
    assert doc["production"]["villages"] == []  # public payload drops the village (issue #7)

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


def test_dds_is_blocked_when_a_farm_regresses(client, mock_screen):
    farm, batch = _verified_batch(client)

    # Re-screening finds heavy loss: the farm drops out of "Verified" after batching.
    mock_screen(loss_ha=0.6)
    assert client.post(f"/api/farms/{farm['id']}/verify").json()["status"] == "Under review"

    r = client.get(f"/api/batches/{batch['id']}/dds")
    assert r.status_code == 409


def test_dds_unknown_batch_is_404(client):
    assert client.get("/api/batches/9999/dds").status_code == 404
