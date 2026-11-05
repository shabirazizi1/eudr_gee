from app import scoring

BASE = {
    "area_ha": 10.0, "loss_ha": 0.0, "protected_areas": [], "ndvi_current": 0.75,
    "ndvi_baseline": 0.72, "images_current": 20, "coverage_pct": 95.0,
}


def result(**overrides):
    return {**BASE, **overrides}


def test_low_risk_when_nothing_flagged():
    s = scoring.score(result())
    assert s["level"] == "Low"
    assert s["score"] < 30
    assert s["status"] == "Verified"


def test_medium_risk_band():
    # 3% of a 10 ha farm lost -> loss_pts = 3 * 12 = 36 (Medium: 30 <= score < 60)
    s = scoring.score(result(loss_ha=0.3))
    assert s["level"] == "Medium"
    assert 30 <= s["score"] < 60
    assert s["status"] == "Under review"


def test_high_risk_band():
    # 10% of a 10 ha farm lost -> loss_pts capped at 60 (High: score >= 60)
    s = scoring.score(result(loss_ha=1.0))
    assert s["level"] == "High"
    assert s["score"] >= 60
    assert s["status"] == "Under review"


def test_loss_at_or_above_point_one_ha_is_never_low():
    # Even a tiny loss fraction on a big farm floors the score at 30 (Medium), never Low.
    s = scoring.score(result(area_ha=200.0, loss_ha=0.1))
    assert s["score"] >= 30
    assert s["level"] != "Low"


def test_protected_area_overlap_forces_review():
    s = scoring.score(result(protected_areas=["Ankasa Conservation Area"]))
    assert s["status"] == "Under review"
    assert any("protected area" in f.lower() for f in s["flags"])
    assert any(b["factor"] == "Protected-area overlap" and b["points"] == 25 for b in s["breakdown"])


def test_low_imagery_forces_review():
    s = scoring.score(result(images_current=1, coverage_pct=20.0))
    assert s["status"] == "Under review"
    assert any("imagery" in f.lower() for f in s["flags"])
