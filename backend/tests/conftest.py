"""Shared test fixtures: a throwaway temp-file SQLite database (never eudr.db) and a mockable
Earth Engine `screen()` so tests never touch the real database or call out to Earth Engine.

The DB_URL env var must be set before `app.config`/`app.db` are imported anywhere, since
`app.db` builds its engine from `config.DB_URL` at import time -- hence this happens at module
import time, above every other import in this file.
"""
import atexit
import os
import tempfile

_tmp_db = tempfile.NamedTemporaryFile(prefix="eudr_test_", suffix=".db", delete=False)
_tmp_db.close()
os.environ["DB_URL"] = f"sqlite:///{_tmp_db.name}"
atexit.register(lambda: os.path.exists(_tmp_db.name) and os.remove(_tmp_db.name))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from app import gee_service  # noqa: E402
from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402

DEFAULT_RESULT = {
    "source": "gee", "area_ha": 1.0, "forest_2020_ha": 0.9, "loss_ha": 0.0, "loss_by_year": {},
    "protected_areas": [], "ndvi_current": 0.75, "ndvi_baseline": 0.72, "images_current": 20,
    "coverage_pct": 95.0, "loss_tiles": None,
    "datasets": [{"name": "Hansen Global Forest Change", "window": "-", "resolution": "30 m"}],
}


def make_polygon(cx=-2.43, cy=5.80, half=0.0009):
    """A small (~1 ha) non-self-intersecting square around (cx, cy) in [lon, lat]."""
    ring = [[cx - half, cy - half], [cx + half, cy - half],
            [cx + half, cy + half], [cx - half, cy + half], [cx - half, cy - half]]
    return {"type": "Polygon", "coordinates": [ring]}


@pytest.fixture(autouse=True)
def _clean_db():
    """Every test starts with empty tables in the temp database."""
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    yield


@pytest.fixture
def mock_screen(monkeypatch):
    """Patches gee_service.screen (the only network-calling Earth Engine entrypoint `main.py`
    uses) to return a controllable, deterministic result. Call it with overrides before a
    verify call, e.g. `mock_screen(loss_ha=0.3)`; defaults to a clean "Low risk" result.
    """
    state = {"result": dict(DEFAULT_RESULT)}

    def _screen(geojson, area):
        r = dict(state["result"])
        r.setdefault("area_ha", round(area, 2))
        return r

    monkeypatch.setattr(gee_service, "screen", _screen)

    def _set(**overrides):
        state["result"] = {**DEFAULT_RESULT, **overrides}

    return _set


@pytest.fixture
def client():
    return TestClient(app)
