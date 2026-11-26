"""Shared farm-screening logic (PR #20 review).

Both `POST /api/farms/{id}/verify` and the bulk job MUST go through here: a declared-area
Point capture has to be buffered to its declared area before Earth Engine sees it (screening
a raw point is a single pixel and silently reports no loss), and the Verification is pinned to
the boundary version the caller read before screening started.
"""
import json
import uuid
from datetime import datetime, timezone

from sqlmodel import Session

from . import geo, gee_service, scoring
from .geo import display_geometry
from .models import Farm, Verification


def screen_farm(session: Session, farm: Farm, boundary_version_id=None) -> Verification:
    """Screen one farm, persist the Verification and update the farm's denormalised risk fields.
    Raises on Earth Engine failure -- the caller decides how to report that (502 for /verify,
    a per-farm error entry for bulk)."""
    screening_geometry = display_geometry(farm.geometry, farm.declared_area_ha)
    result = gee_service.screen(screening_geometry, farm.area_ha)
    scored = scoring.score(result)
    result.update({"flags": scored["flags"] + geo.sanity_flags(farm.area_ha),
                   "breakdown": scored["breakdown"]})

    verification = Verification(
        farm_id=farm.id,
        boundary_version_id=boundary_version_id,
        run_id=f"GEE-{farm.code.split('-')[1]}-{uuid.uuid4().hex[:4].upper()}",
        source=result["source"],
        score=scored["score"],
        level=scored["level"],
        result_json=json.dumps(result),
    )
    farm.risk_score, farm.risk_level, farm.status = (
        scored["score"], scored["level"], scored["status"])
    farm.last_screened = datetime.now(timezone.utc)
    session.add(verification)
    session.add(farm)
    session.commit()
    session.refresh(verification)
    return verification
