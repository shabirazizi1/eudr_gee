import base64
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from . import bulk_verify, config, geo, gee_service, scoring
from .db import get_session, init_db
from .dds import build_dds
from .models import Bag, Batch, BatchContribution, BatchEvent, BoundaryVersion, Farm, Review, Verification
from .report import COMMODITY, build_bag_labels_pdf, build_batch_pdf, build_pdf
from .geo import display_geometry
from .screening import screen_farm


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    config.CONSENT_PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="eudr_gee – Deforestation-free traceability API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

REVIEW_REASONS = ["Cloud cover", "Natural clearing", "Replanting of existing cocoa", "Boundary error", "Other"]
BATCHABLE_STATUSES = ("Verified", "Verified (manual review)")
OVERRIDE_REASONS = ["Second harvest", "Yield above average", "Data correction", "Other"]


# ---------------------------------------------------------------- schemas

class FarmCreate(BaseModel):
    name: str
    farmer_name: str
    village: str = ""
    harvest_date: Optional[str] = None
    crop: str = "Cocoa"
    captured_by: str = ""
    captured_by_role: Optional[str] = None
    geometry: dict  # GeoJSON Polygon, or Point for a declared-area capture (issue #5)
    capture_method: str = "drawn"  # "gps_walk", "drawn", or "gps_point"
    gps_accuracy_m: Optional[float] = None
    flagged_points: int = 0
    declared_area_ha: Optional[float] = None  # required for a Point capture (EUDR <= 4 ha)
    consent_given: bool = False
    consent_date: Optional[str] = None
    consent_photo_base64: Optional[str] = None  # optional signed-form photo (data URL or raw base64)


class BatchContributionIn(BaseModel):
    farm_id: int
    bags: int
    kg_per_bag: float = 64.0


class BatchCreate(BaseModel):
    buyer: str = "Chocolaterie Vandermeer, Ghent, Belgium"
    created_by: str = ""
    created_by_role: Optional[str] = None
    season: str = config.DEFAULT_SEASON
    contributions: list[BatchContributionIn]
    # Mass-balance approval workflow (issue #15): required only if some contribution exceeds its
    # farm's remaining season capacity -- the batch is then created "Pending approval" instead of
    # being blocked outright. `overridden_by_role` mirrors the no-real-auth pattern used elsewhere.
    justification_note: Optional[str] = None


class ContributionOverrideIn(BaseModel):
    contribution_id: int
    override_reason: str
    override_note: str = ""


class BatchApproveIn(BaseModel):
    approved_by: str = ""
    approved_by_role: Optional[str] = None
    note: str = ""
    overrides: list[ContributionOverrideIn] = []


class BatchDecisionIn(BaseModel):
    by: str = ""
    by_role: Optional[str] = None
    reason: str = ""


class ReviewCreate(BaseModel):
    decision: str  # "clear" or "confirm"
    reason: str
    note: str = ""
    reviewer_name: str = ""
    reviewer_role: Optional[str] = None


class RemapCreate(BaseModel):
    """Remapping reuses the farm's existing consent -- issue #4 says nothing about consent, so
    no new consent is asked for here."""
    geometry: dict  # GeoJSON Polygon, or Point for a declared-area capture (issue #5)
    capture_method: str = "drawn"  # "gps_walk", "drawn", or "gps_point"
    gps_accuracy_m: Optional[float] = None
    flagged_points: int = 0
    captured_by: str = ""
    captured_by_role: Optional[str] = None
    declared_area_ha: Optional[float] = None


FIELD_ROLES = ("agent", "admin")  # create farm, satellite check, remap (admins fix boundary errors)


def require_role(role: Optional[str], allowed: tuple[str, ...], message: str) -> None:
    # MVP: role comes from the client; real authentication is on the roadmap.
    if role not in allowed:
        raise HTTPException(403, message)


def needs_field_audit(farm: Farm) -> bool:
    """HLR-01: audit required if drawn on a map, or GPS-captured (walk or point) with avg
    accuracy worse than 10 m."""
    if farm.capture_method in ("gps_walk", "gps_point"):
        return farm.gps_accuracy_m is None or farm.gps_accuracy_m > 10
    return True


def validate_geometry(geometry: dict, declared_area_ha: Optional[float] = None) -> list[str]:
    """Shared by create and remap: a Polygon validates as before; a Point (issue #5, EUDR
    plots <= 4 ha) also needs a valid declared area."""
    errors = geo.validate(geometry)
    if errors:
        return errors
    if geometry.get("type") == "Point":
        return geo.validate_declared_area(declared_area_ha)
    return []


def geometry_fields(geometry: dict, declared_area_ha: Optional[float] = None) -> dict:
    """area_ha/perimeter_m/vertices/declared_area_ha for either a drawn/walked Polygon or a
    declared-area Point (issue #5)."""
    if geometry.get("type") == "Point":
        area = round(declared_area_ha, 2)
        return {"area_ha": area, "perimeter_m": 0.0, "vertices": 0, "declared_area_ha": area}
    return {
        "area_ha": round(geo.area_ha(geometry), 2),
        "perimeter_m": round(geo.perimeter_m(geometry), 1),
        "vertices": geo.vertex_count(geometry),
        "declared_area_ha": None,
    }


def is_batchable(farm: Farm) -> bool:
    return farm.status in BATCHABLE_STATUSES


def farm_capacity_kg(session: Session, farm: Farm) -> float:
    """Issue #6: estimated max yield per farm per season = area x YIELD_KG_PER_HA, using the
    farm's CURRENT boundary version's area (or declared_area_ha for a point farm). A remap
    changes this for new batches only -- a batch already created keeps its own locked-in bag
    and weight records, which don't depend on area at all."""
    bv = latest_boundary_version(session, farm.id)
    area = (bv.declared_area_ha or bv.area_ha) if bv else (farm.declared_area_ha or farm.area_ha)
    return round(area * config.YIELD_KG_PER_HA, 1)


RESERVED_STATUSES = ("Approved", "Pending approval")  # count toward committed capacity


def season_committed_kg(session: Session, farm_id: int, season: str) -> float:
    """Total kg already committed for this farm across every batch in this season. A batch
    Pending approval still reserves its kg (issue #15), so the same kilos can't be used twice
    while it waits -- Rejected/Cancelled batches release it back by being excluded here."""
    batch_ids = session.exec(
        select(Batch.id).where(Batch.season == season, Batch.status.in_(RESERVED_STATUSES))
    ).all()
    if not batch_ids:
        return 0.0
    contribs = session.exec(
        select(BatchContribution).where(BatchContribution.farm_id == farm_id,
                                         BatchContribution.batch_id.in_(batch_ids))
    ).all()
    return sum(c.bags * c.kg_per_bag for c in contribs)


def farm_remaining_capacity_kg(session: Session, farm: Farm, season: str) -> float:
    return round(farm_capacity_kg(session, farm) - season_committed_kg(session, farm.id, season), 1)


def contribution_capacity(session: Session, farm: Farm, season: str, kg: float, batch_status: str) -> dict:
    """requested/remaining/overage for one contribution, for the admin's pending-approval view
    (issue #15). `remaining` is the farm's season capacity left BEFORE this contribution -- while
    the batch is Approved/Pending, this contribution's own kg is already part of the committed
    total, so it's added back; once Rejected/Cancelled it no longer counts, so no adjustment."""
    remaining_now = farm_remaining_capacity_kg(session, farm, season)
    remaining_before = remaining_now + kg if batch_status in RESERVED_STATUSES else remaining_now
    return {
        "requested_kg": round(kg, 1),
        "remaining_kg": round(remaining_before, 1),
        "overage_kg": round(max(kg - remaining_before, 0), 1),
        "over_capacity": kg > remaining_before,
    }


def batch_event_out(e: BatchEvent) -> dict:
    return {"event": e.event, "by": e.by, "by_role": e.by_role, "note": e.note,
            "created_at": e.created_at.isoformat()}


def batch_events_for(session: Session, batch_id: int) -> list[BatchEvent]:
    return session.exec(
        select(BatchEvent).where(BatchEvent.batch_id == batch_id).order_by(BatchEvent.created_at)
    ).all()


def review_out(r: Review) -> dict:
    return {"id": r.id, "decision": r.decision, "reason": r.reason, "note": r.note,
            "reviewer_name": r.reviewer_name, "created_at": r.created_at.isoformat()}


def farm_reviews(session: Session, farm_id: int) -> list[Review]:
    return session.exec(
        select(Review).where(Review.farm_id == farm_id).order_by(Review.created_at.desc())
    ).all()


def farm_out(farm: Farm, latest: Optional[Verification] = None, reviews: Optional[list] = None,
             session: Optional[Session] = None) -> dict:
    d = farm.model_dump()
    d.pop("consent_photo_path", None)  # the photo itself is only ever served via /consent_photo
    d["geometry"] = display_geometry(farm.geometry, farm.declared_area_ha)
    d["needs_field_audit"] = needs_field_audit(farm)
    if session is not None:
        lbv = latest_boundary_version(session, farm.id)
        d["boundary_version"] = lbv.version_no if lbv else None
    if latest:
        d["verification"] = {
            "run_id": latest.run_id,
            "created_at": latest.created_at.isoformat(),
            "source": latest.source,
            "score": latest.score,
            "level": latest.level,
            **json.loads(latest.result_json),
        }
    if reviews is not None:
        d["reviews"] = [review_out(r) for r in reviews]
        d["latest_review"] = d["reviews"][0] if d["reviews"] else None
    return d


def farm_out_public(farm: Farm, latest: Optional[Verification] = None) -> dict:
    """Minimized farm payload for the public bag page and buyer-facing shipment PDF (data
    minimization, issue #7): no farmer full name (-> initials), no village, no capturing
    agent's name, no consent record, no review/reviewer history. Satellite screening data,
    geometry and area/perimeter stay -- none of that is personal data, and EUDR requires the
    boundary coordinates.
    """
    initials = "".join(w[0] for w in farm.farmer_name.split() if w).upper() or "–"
    d = {
        "id": farm.id,
        "code": farm.code,
        "name": farm.name,
        "farmer_name": initials,  # same key as farm_out() so report.py needs no special-casing
        "region": farm.region,
        "crop": farm.crop,
        "harvest_date": farm.harvest_date,
        "geometry": display_geometry(farm.geometry, farm.declared_area_ha),
        "area_ha": farm.area_ha,
        "perimeter_m": farm.perimeter_m,
        "vertices": farm.vertices,
        "capture_method": farm.capture_method,
        "gps_accuracy_m": farm.gps_accuracy_m,
        "flagged_points": farm.flagged_points,
        "declared_area_ha": farm.declared_area_ha,
        "risk_score": farm.risk_score,
        "risk_level": farm.risk_level,
        "status": farm.status,
        "last_screened": farm.last_screened.isoformat() if farm.last_screened else None,
    }
    if latest:
        d["verification"] = {
            "run_id": latest.run_id,
            "created_at": latest.created_at.isoformat(),
            "source": latest.source,
            "score": latest.score,
            "level": latest.level,
            **json.loads(latest.result_json),
        }
    return d


def _save_consent_photo(farm: Farm, data: str) -> None:
    """Decodes a base64 (optionally data-URL-prefixed) image and saves it to
    uploads/consent/<farm code>.jpg -- gitignored, never returned by farm_out()."""
    raw = data.split(",", 1)[-1] if "," in data else data
    config.CONSENT_PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    path = config.CONSENT_PHOTOS_DIR / f"{farm.code}.jpg"
    path.write_bytes(base64.b64decode(raw))
    farm.consent_photo_path = path.name


def latest_verification(session: Session, farm_id: int) -> Optional[Verification]:
    return session.exec(
        select(Verification).where(Verification.farm_id == farm_id)
        .order_by(Verification.created_at.desc())
    ).first()


def latest_boundary_version(session: Session, farm_id: int) -> Optional[BoundaryVersion]:
    return session.exec(
        select(BoundaryVersion).where(BoundaryVersion.farm_id == farm_id)
        .order_by(BoundaryVersion.version_no.desc())
    ).first()


def boundary_versions_for(session: Session, farm_id: int) -> list[BoundaryVersion]:
    return session.exec(
        select(BoundaryVersion).where(BoundaryVersion.farm_id == farm_id)
        .order_by(BoundaryVersion.version_no)
    ).all()


def verification_for_boundary_version(session: Session, boundary_version_id: int) -> Optional[Verification]:
    return session.exec(
        select(Verification).where(Verification.boundary_version_id == boundary_version_id)
        .order_by(Verification.created_at.desc())
    ).first()


def boundary_version_out(v: BoundaryVersion) -> dict:
    return {
        "version_no": v.version_no,
        "geometry": display_geometry(v.geometry, v.declared_area_ha),
        "area_ha": v.area_ha,
        "perimeter_m": v.perimeter_m,
        "vertices": v.vertices,
        "capture_method": v.capture_method,
        "gps_accuracy_m": v.gps_accuracy_m,
        "flagged_points": v.flagged_points,
        "declared_area_ha": v.declared_area_ha,
        "captured_by": v.captured_by,
        "created_at": v.created_at.isoformat(),
    }


def create_boundary_version(session: Session, farm: Farm) -> BoundaryVersion:
    """Snapshots the farm's current boundary fields as the next read-only version. Used for
    both v1 (right after a farm is created) and every remap after that."""
    prev = latest_boundary_version(session, farm.id)
    version = BoundaryVersion(
        farm_id=farm.id, version_no=(prev.version_no + 1) if prev else 1,
        geometry=farm.geometry, area_ha=farm.area_ha, perimeter_m=farm.perimeter_m,
        vertices=farm.vertices, capture_method=farm.capture_method,
        gps_accuracy_m=farm.gps_accuracy_m, flagged_points=farm.flagged_points,
        declared_area_ha=farm.declared_area_ha, captured_by=farm.captured_by,
    )
    session.add(version)
    session.commit()
    session.refresh(version)
    session.refresh(farm)  # the commit above expired `farm` too; keep it usable for the caller
    return version


def next_code(session: Session) -> str:
    farms = session.exec(select(Farm.code)).all()
    nums = [int(c.split("-")[1]) for c in farms if c.startswith("GH-")]
    return f"GH-{(max(nums) + 1) if nums else 241:04d}"


def next_batch_code(session: Session) -> str:
    year = datetime.now(timezone.utc).year
    prefix = f"BATCH-{year}-"
    codes = session.exec(select(Batch.code)).all()
    nums = [int(c.split("-")[-1]) for c in codes if c.startswith(prefix)]
    return f"{prefix}{(max(nums) + 1) if nums else 1:03d}"


def create_bags(session: Session, batch: Batch, contribution: BatchContribution, farm: Farm) -> list[Bag]:
    """One Bag row per bag in the contribution, numbered BAG-<farm suffix>-<seq>. The sequence
    keeps counting across every batch that farm has ever contributed to, so IDs never repeat."""
    suffix = farm.code.split("-")[-1]
    existing = session.exec(select(Bag.bag_id).where(Bag.bag_id.like(f"BAG-{suffix}-%"))).all()
    nums = [int(b.split("-")[-1]) for b in existing]
    next_seq = (max(nums) + 1) if nums else 1
    bags = []
    for i in range(contribution.bags):
        bag = Bag(bag_id=f"BAG-{suffix}-{next_seq + i:04d}", farm_id=farm.id, batch_id=batch.id,
                  contribution_id=contribution.id, weight_kg=contribution.kg_per_bag)
        session.add(bag)
        bags.append(bag)
    return bags


def bag_out(session: Session, bag: Bag) -> dict:
    """Public, no-login endpoint -- always uses the minimized farm payload (data minimization,
    issue #7)."""
    farm = session.get(Farm, bag.farm_id)
    batch = session.get(Batch, bag.batch_id)
    return {
        "bag_id": bag.bag_id,
        "weight_kg": bag.weight_kg,
        "created_at": bag.created_at.isoformat(),
        "farm": farm_out_public(farm, latest_verification(session, farm.id)) if farm else None,
        "batch": {"id": batch.id, "code": batch.code, "buyer": batch.buyer} if batch else None,
    }


def batch_out(session: Session, batch: Batch, public: bool = False) -> dict:
    """`public=True` (used only for the buyer-facing shipment PDF) swaps in the minimized farm
    payload and drops review history -- the internal JSON API (list/detail) always uses the
    full one.

    Each contribution freezes its farm's boundary/geometry (and the verification run against
    it) to whatever version the farm was on when it joined the batch (issue #4) -- a later
    remap must not silently change what an already-built batch shows.
    """
    contribs = session.exec(
        select(BatchContribution).where(BatchContribution.batch_id == batch.id)
    ).all()
    items = []
    total_bags = total_kg = total_area = 0.0
    for c in contribs:
        farm = session.get(Farm, c.farm_id)
        kg = c.bags * c.kg_per_bag
        total_bags += c.bags
        total_kg += kg
        total_area += farm.area_ha if farm else 0.0
        if farm and public:
            farm_payload = farm_out_public(farm, latest_verification(session, farm.id))
        elif farm:
            farm_payload = farm_out(farm, latest_verification(session, farm.id),
                                     farm_reviews(session, farm.id), session=session)
        else:
            farm_payload = None
        if farm_payload and c.boundary_version_id:
            bv = session.get(BoundaryVersion, c.boundary_version_id)
            if bv:
                farm_payload.update({
                    "geometry": display_geometry(bv.geometry, bv.declared_area_ha), "area_ha": bv.area_ha,
                    "perimeter_m": bv.perimeter_m, "vertices": bv.vertices,
                    "capture_method": bv.capture_method, "gps_accuracy_m": bv.gps_accuracy_m,
                    "flagged_points": bv.flagged_points, "declared_area_ha": bv.declared_area_ha,
                    "boundary_version": bv.version_no,
                })
                v_for_version = verification_for_boundary_version(session, bv.id)
                if v_for_version:
                    farm_payload["verification"] = {
                        "run_id": v_for_version.run_id,
                        "created_at": v_for_version.created_at.isoformat(),
                        "source": v_for_version.source,
                        "score": v_for_version.score,
                        "level": v_for_version.level,
                        **json.loads(v_for_version.result_json),
                    }
        item = {
            "id": c.id,
            "bags": c.bags,
            "kg_per_bag": c.kg_per_bag,
            "kg": kg,
            "farm": farm_payload,
            "override": {
                "reason": c.override_reason,
                "note": c.override_note,
                "by": c.overridden_by,
                "at": c.overridden_at.isoformat() if c.overridden_at else None,
            } if c.override_reason else None,
        }
        if batch.status == "Pending approval" and farm:
            item["capacity"] = contribution_capacity(session, farm, batch.season, kg, batch.status)
        items.append(item)
    return {
        "id": batch.id,
        "code": batch.code,
        "buyer": batch.buyer,
        "season": batch.season,
        "created_at": batch.created_at.isoformat(),
        "created_by": batch.created_by,
        "status": batch.status,
        "submission_note": batch.submission_note,
        "contributions": items,
        "events": [batch_event_out(e) for e in batch_events_for(session, batch.id)],
        "totals": {
            "farms": len(items),
            "bags": int(total_bags),
            "kg": round(total_kg, 1),
            "area_ha": round(total_area, 2),
        },
    }


# ---------------------------------------------------------------- routes

@app.get("/api/health")
def health():
    mode = "mock" if config.USE_MOCK_GEE else "gee"
    return {"ok": True, "mode": mode, "project": config.GEE_PROJECT}


@app.get("/api/farms")
def list_farms(season: Optional[str] = None, session: Session = Depends(get_session)):
    """`season` (issue #6) adds each farm's mass-balance capacity/remaining capacity for that
    season -- used by the batch-creation picker. Omitted, the response is unchanged."""
    farms = session.exec(select(Farm).order_by(Farm.code)).all()
    out = [farm_out(f, reviews=farm_reviews(session, f.id), session=session) for f in farms]
    if season:
        for farm, payload in zip(farms, out):
            payload["capacity_kg"] = farm_capacity_kg(session, farm)
            payload["remaining_capacity_kg"] = farm_remaining_capacity_kg(session, farm, season)
    return out


@app.post("/api/farms", status_code=201)
def create_farm(body: FarmCreate, session: Session = Depends(get_session)):
    require_role(body.captured_by_role, FIELD_ROLES, "Only a field agent or Brand admin can register a farm.")
    if not body.consent_given or not (body.consent_date or "").strip():
        raise HTTPException(422, detail=["Farmer consent (checkbox + date) is required before a farm can be submitted."])
    errors = validate_geometry(body.geometry, body.declared_area_ha)
    if errors:
        raise HTTPException(422, detail=errors)
    fields = geometry_fields(body.geometry, body.declared_area_ha)
    farm = Farm(
        code=next_code(session),
        name=body.name.strip(),
        farmer_name=body.farmer_name.strip(),
        village=body.village.strip(),
        harvest_date=body.harvest_date,
        crop=body.crop,
        captured_by=body.captured_by,
        geometry=json.dumps(body.geometry),
        area_ha=fields["area_ha"],
        perimeter_m=fields["perimeter_m"],
        vertices=fields["vertices"],
        declared_area_ha=fields["declared_area_ha"],
        capture_method=body.capture_method,
        gps_accuracy_m=body.gps_accuracy_m,
        flagged_points=body.flagged_points,
        consent_method="photo" if body.consent_photo_base64 else "attestation",
        consent_given=True,
        consent_date=body.consent_date.strip(),
    )
    session.add(farm)
    session.commit()
    session.refresh(farm)
    if body.consent_photo_base64:
        _save_consent_photo(farm, body.consent_photo_base64)
        session.add(farm)
        session.commit()
        session.refresh(farm)
    create_boundary_version(session, farm)  # v1 (issue #4)
    out = farm_out(farm, reviews=[], session=session)
    out["warnings"] = geo.sanity_flags(fields["area_ha"])
    return out


@app.get("/api/farms/{farm_id}")
def get_farm(farm_id: int, session: Session = Depends(get_session)):
    farm = session.get(Farm, farm_id)
    if not farm:
        raise HTTPException(404, "Farm not found")
    return farm_out(farm, latest_verification(session, farm_id), farm_reviews(session, farm_id), session=session)


@app.post("/api/farms/{farm_id}/remap")
def remap_farm(farm_id: int, body: RemapCreate, session: Session = Depends(get_session)):
    """Issue #4: a remap never overwrites the boundary -- it appends a new, read-only version
    and resets screening (a new geometry means the old risk score no longer applies), which
    blocks the farm from new batches until it's re-verified. Existing batches keep the version
    they were built with (see `batch_out`)."""
    farm = session.get(Farm, farm_id)
    if not farm:
        raise HTTPException(404, "Farm not found")
    require_role(body.captured_by_role, FIELD_ROLES, "Only a field agent or Brand admin can remap a farm boundary.")
    errors = validate_geometry(body.geometry, body.declared_area_ha)
    if errors:
        raise HTTPException(422, detail=errors)
    fields = geometry_fields(body.geometry, body.declared_area_ha)
    farm.geometry = json.dumps(body.geometry)
    farm.area_ha = fields["area_ha"]
    farm.perimeter_m = fields["perimeter_m"]
    farm.vertices = fields["vertices"]
    farm.declared_area_ha = fields["declared_area_ha"]
    farm.capture_method = body.capture_method
    farm.gps_accuracy_m = body.gps_accuracy_m
    farm.flagged_points = body.flagged_points
    farm.captured_by = body.captured_by or farm.captured_by
    farm.status = "Not screened"
    farm.risk_score = None
    farm.risk_level = None
    farm.last_screened = None
    session.add(farm)
    session.commit()
    session.refresh(farm)
    create_boundary_version(session, farm)
    out = farm_out(farm, reviews=farm_reviews(session, farm.id), session=session)
    out["warnings"] = geo.sanity_flags(fields["area_ha"])
    return out


@app.get("/api/farms/{farm_id}/boundary_versions")
def farm_boundary_versions(farm_id: int, session: Session = Depends(get_session)):
    farm = session.get(Farm, farm_id)
    if not farm:
        raise HTTPException(404, "Farm not found")
    return [boundary_version_out(v) for v in boundary_versions_for(session, farm_id)]


@app.post("/api/farms/{farm_id}/verify")
def verify_farm(farm_id: int, role: Optional[str] = None, session: Session = Depends(get_session)):
    farm = session.get(Farm, farm_id)
    if not farm:
        raise HTTPException(404, "Farm not found")
    require_role(role, FIELD_ROLES, "Only a field agent or Brand admin can run a satellite check.")
    # A Point capture (issue #5, EUDR <= 4 ha) is screened as a circle buffered to the declared
    # area -- screen_farm() owns that, and bulk screening uses the same helper (PR #20 review).
    current_version = latest_boundary_version(session, farm.id)
    try:
        v = screen_farm(session, farm, current_version.id if current_version else None)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Satellite screening failed: {e}")
    session.refresh(farm)
    return farm_out(farm, v, farm_reviews(session, farm.id), session=session)


@app.post("/api/farms/{farm_id}/review")
def review_farm(farm_id: int, body: ReviewCreate, session: Session = Depends(get_session)):
    """HLR-05 / DEL-02: Brand admin dispute decision (clear / confirm)."""
    farm = session.get(Farm, farm_id)
    if not farm:
        raise HTTPException(404, "Farm not found")
    require_role(body.reviewer_role, ("admin",), "Only a Brand admin can review a farm.")
    if farm.status != "Under review":
        raise HTTPException(400, "Only farms currently Under review can be reviewed.")
    if body.decision not in ("clear", "confirm"):
        raise HTTPException(422, detail=["decision must be 'clear' or 'confirm'."])
    if body.reason not in REVIEW_REASONS:
        raise HTTPException(422, detail=[f"reason must be one of: {', '.join(REVIEW_REASONS)}"])
    if not body.note.strip():
        raise HTTPException(422, detail=["A note is required."])

    review = Review(farm_id=farm.id, decision=body.decision, reason=body.reason,
                     note=body.note.strip(), reviewer_name=body.reviewer_name.strip())
    session.add(review)
    farm.status = "Verified (manual review)" if body.decision == "clear" else "Excluded"
    session.add(farm)
    session.commit()
    session.refresh(farm)
    return farm_out(farm, latest_verification(session, farm.id), farm_reviews(session, farm.id), session=session)


@app.get("/api/farms/{farm_id}/consent_photo")
def consent_photo(farm_id: int, role: Optional[str] = None, session: Session = Depends(get_session)):
    """Admin-only (`?role=admin`): the consent photo itself is never embedded in list, public or
    PDF responses -- only reachable here, one farm at a time.
    """
    require_role(role, ("admin",), "Only a Brand admin can view a consent photo.")
    farm = session.get(Farm, farm_id)
    if not farm or not farm.consent_photo_path:
        raise HTTPException(404, "No consent photo on file")
    path = config.CONSENT_PHOTOS_DIR / farm.consent_photo_path
    if not path.exists():
        raise HTTPException(404, "No consent photo on file")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/api/farms/{farm_id}/report.pdf")
def farm_report(farm_id: int, session: Session = Depends(get_session)):
    farm = session.get(Farm, farm_id)
    if not farm:
        raise HTTPException(404, "Farm not found")
    v = latest_verification(session, farm_id)
    if not v:
        raise HTTPException(400, "Run the satellite check before exporting evidence.")
    reviews = [review_out(r) for r in farm_reviews(session, farm_id)]
    # Render the boundary (and its matching area/perimeter/vertices) the verification was
    # actually run against, not the farm's current one -- they can differ after a remap that
    # hasn't been re-verified yet (issue #4).
    bv = session.get(BoundaryVersion, v.boundary_version_id) if v.boundary_version_id else None
    geometry = display_geometry(bv.geometry, bv.declared_area_ha) if bv else None
    facts_override = {
        "area_ha": bv.area_ha, "perimeter_m": bv.perimeter_m, "vertices": bv.vertices,
        "capture_method": bv.capture_method, "gps_accuracy_m": bv.gps_accuracy_m,
        "flagged_points": bv.flagged_points, "declared_area_ha": bv.declared_area_ha,
    } if bv else None
    pdf = build_pdf(farm, v, reviews, geometry=geometry, boundary_version_no=(bv.version_no if bv else None),
                    facts_override=facts_override)
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="evidence_{farm.code}.pdf"'})


@app.get("/api/summary")
def summary(session: Session = Depends(get_session)):
    farms = session.exec(select(Farm)).all()
    screened = [f for f in farms if f.risk_level]
    dist = {lvl: sum(1 for f in screened if f.risk_level == lvl) for lvl in ("Low", "Medium", "High")}
    mass_balance_overrides = session.exec(
        select(BatchContribution).where(BatchContribution.override_reason.is_not(None))
    ).all()
    pending_approvals = session.exec(select(Batch).where(Batch.status == "Pending approval")).all()
    return {
        "farms": len(farms),
        "hectares": round(sum(f.area_ha for f in farms), 1),
        "screened": len(screened),
        "low_risk_pct": round(dist["Low"] / len(screened) * 100, 1) if screened else 0,
        "distribution": dist,
        "under_review": sum(1 for f in farms if f.status == "Under review"),
        "not_screened": sum(1 for f in farms if f.status == "Not screened"),
        "needs_field_audit": sum(1 for f in farms if needs_field_audit(f)),
        "reviewed": sum(1 for f in farms if f.status == "Verified (manual review)"),
        "excluded": sum(1 for f in farms if f.status == "Excluded"),
        "mass_balance_overrides": len(mass_balance_overrides),
        "pending_approvals": len(pending_approvals),
    }


# ---------------------------------------------------------------- batches (HLR-02 / DEL-03 / DEL-06)

@app.get("/api/batches")
def list_batches(session: Session = Depends(get_session)):
    batches = session.exec(select(Batch).order_by(Batch.created_at.desc())).all()
    return [batch_out(session, b) for b in batches]


@app.post("/api/batches", status_code=201)
def create_batch(body: BatchCreate, session: Session = Depends(get_session)):
    """A batch within every farm's remaining season capacity is created Approved, as before. One
    that exceeds a farm's remaining capacity needs a justification note and is created Pending
    approval instead (issue #15) -- no bags yet; a Brand admin approves or rejects it later on
    the batch page. `season` is tracked per batch so a new season resets every farm's remaining
    capacity."""
    # Separation of duties: admins approve batches, they don't create them.
    require_role(body.created_by_role, ("warehouse",), "Only a warehouse operator can create a batch.")
    if not body.contributions:
        raise HTTPException(422, detail=["A batch needs at least one farm contribution."])
    season = body.season.strip() or config.DEFAULT_SEASON
    errors = []
    over_capacity = False
    for c in body.contributions:
        farm = session.get(Farm, c.farm_id)
        if not farm:
            errors.append(f"Farm {c.farm_id} not found.")
            continue
        if farm.status == "Excluded":
            reviews = farm_reviews(session, farm.id)
            reason = reviews[0].reason if reviews else "manual review"
            errors.append(f"{farm.name} is Excluded ({reason}) and cannot be added to a batch.")
            continue
        if not is_batchable(farm):
            errors.append(f"{farm.name} is not Verified and cannot be added to a batch.")
            continue
        if c.bags <= 0:
            errors.append(f"{farm.name}: bag count must be positive.")
            continue
        kg = c.bags * c.kg_per_bag
        remaining = farm_remaining_capacity_kg(session, farm, season)
        if kg > remaining:
            over_capacity = True
    if errors:
        raise HTTPException(422, detail=errors)
    justification_note = (body.justification_note or "").strip()
    if over_capacity and not justification_note:
        raise HTTPException(422, detail=[
            "One or more farms exceed their remaining season capacity -- a justification note "
            "is required to submit this batch for admin approval."])

    status = "Pending approval" if over_capacity else "Approved"
    batch = Batch(code=next_batch_code(session), buyer=body.buyer.strip() or Batch().buyer,
                  created_by=body.created_by, season=season, status=status,
                  submission_note=justification_note if over_capacity else "")
    session.add(batch)
    session.commit()
    session.refresh(batch)
    for c in body.contributions:
        farm = session.get(Farm, c.farm_id)
        current_version = latest_boundary_version(session, c.farm_id)
        contribution = BatchContribution(
            batch_id=batch.id, farm_id=c.farm_id, bags=c.bags, kg_per_bag=c.kg_per_bag,
            boundary_version_id=current_version.id if current_version else None,
        )
        session.add(contribution)
        session.commit()
        session.refresh(contribution)
        if status == "Approved":
            create_bags(session, batch, contribution, farm)
    if status == "Pending approval":
        session.add(BatchEvent(batch_id=batch.id, event="submitted", by=body.created_by,
                                by_role=body.created_by_role, note=justification_note))
    session.commit()
    return batch_out(session, batch)


@app.post("/api/batches/{batch_id}/approve")
def approve_batch(batch_id: int, body: BatchApproveIn, session: Session = Depends(get_session)):
    """Issue #15: a Brand admin approves a Pending approval batch, supplying an override reason
    and note for each line that exceeds its farm's remaining capacity, then generates its bags."""
    batch = session.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    if body.approved_by_role != "admin":
        raise HTTPException(403, "Only a Brand admin can approve a batch.")
    if batch.status != "Pending approval":
        raise HTTPException(400, "Only a batch pending approval can be approved.")

    contribs = session.exec(select(BatchContribution).where(BatchContribution.batch_id == batch_id)).all()
    overrides_by_contribution = {o.contribution_id: o for o in body.overrides}
    errors = []
    for c in contribs:
        farm = session.get(Farm, c.farm_id)
        kg = c.bags * c.kg_per_bag
        cap = contribution_capacity(session, farm, batch.season, kg, batch.status)
        if not cap["over_capacity"]:
            continue
        o = overrides_by_contribution.get(c.id)
        if not o or o.override_reason not in OVERRIDE_REASONS:
            errors.append(f"{farm.name}: an override reason ({', '.join(OVERRIDE_REASONS)}) is "
                          f"required for this over-capacity line.")
        elif not o.override_note.strip():
            errors.append(f"{farm.name}: an override note is required for this over-capacity line.")
    if errors:
        raise HTTPException(422, detail=errors)

    for c in contribs:
        farm = session.get(Farm, c.farm_id)
        o = overrides_by_contribution.get(c.id)
        if o:
            c.override_reason = o.override_reason
            c.override_note = o.override_note.strip()
            c.overridden_by = body.approved_by.strip()
            c.overridden_at = datetime.now(timezone.utc)
            session.add(c)
            session.commit()
            session.refresh(c)
        create_bags(session, batch, c, farm)
    batch.status = "Approved"
    session.add(batch)
    session.add(BatchEvent(batch_id=batch.id, event="approved", by=body.approved_by.strip(),
                            by_role=body.approved_by_role, note=body.note.strip()))
    session.commit()
    return batch_out(session, batch)


@app.post("/api/batches/{batch_id}/reject")
def reject_batch(batch_id: int, body: BatchDecisionIn, session: Session = Depends(get_session)):
    """Issue #15: rejecting a Pending approval batch requires a reason and releases its reserved
    capacity (it stops counting toward `season_committed_kg` once it's no longer Approved/Pending)."""
    batch = session.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    if body.by_role != "admin":
        raise HTTPException(403, "Only a Brand admin can reject a batch.")
    if batch.status != "Pending approval":
        raise HTTPException(400, "Only a batch pending approval can be rejected.")
    if not body.reason.strip():
        raise HTTPException(422, detail=["A rejection reason is required."])
    batch.status = "Rejected"
    session.add(batch)
    session.add(BatchEvent(batch_id=batch.id, event="rejected", by=body.by.strip(),
                            by_role=body.by_role, note=body.reason.strip()))
    session.commit()
    return batch_out(session, batch)


@app.post("/api/batches/{batch_id}/cancel")
def cancel_batch(batch_id: int, body: BatchDecisionIn, session: Session = Depends(get_session)):
    """Issue #15: a warehouse operator or Brand admin can cancel a Pending approval batch before an admin
    decides -- this also releases its reserved capacity."""
    batch = session.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    require_role(body.by_role, ("warehouse", "admin"), "Only a warehouse operator or Brand admin can cancel a batch.")
    if batch.status != "Pending approval":
        raise HTTPException(400, "Only a batch pending approval can be cancelled.")
    batch.status = "Cancelled"
    session.add(batch)
    session.add(BatchEvent(batch_id=batch.id, event="cancelled", by=body.by.strip(),
                            by_role=body.by_role, note=body.reason.strip()))
    session.commit()
    return batch_out(session, batch)


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: int, session: Session = Depends(get_session)):
    batch = session.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    return batch_out(session, batch)


@app.get("/api/batches/{batch_id}/report.pdf")
def batch_report(batch_id: int, session: Session = Depends(get_session)):
    """Buyer-facing shipment compliance package -- uses the minimized farm payload (data
    minimization, issue #7)."""
    batch = session.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    if batch.status != "Approved":
        raise HTTPException(400, "This batch is pending approval -- exports are unavailable until it's approved.")
    data = batch_out(session, batch, public=True)
    pdf = build_batch_pdf(batch, data)
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="shipment_{batch.code}.pdf"'})


@app.get("/api/batches/{batch_id}/dds")
def batch_dds(request: Request, batch_id: int, session: Session = Depends(get_session)):
    """EUDR due-diligence statement as a TRACES-style JSON download. Uses the minimized
    (public) farm payload -- initials instead of farmer names -- and the frozen boundary
    version of each contribution, same as the other buyer-facing exports."""
    batch = session.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    if batch.status != "Approved":
        raise HTTPException(400, "This batch is pending approval -- exports are unavailable until it's approved.")
    data = batch_out(session, batch, public=True)
    blocked = [c["farm"]["name"] for c in data["contributions"]
               if c["farm"] and c["farm"]["status"] not in BATCHABLE_STATUSES]
    if blocked:
        raise HTTPException(409, "A DDS cannot be issued while these farms are not Verified: "
                            + ", ".join(blocked))
    contribs = session.exec(
        select(BatchContribution).where(BatchContribution.batch_id == batch_id)).all()
    features = {}
    for c in contribs:
        farm = session.get(Farm, c.farm_id)
        if farm:
            features[farm.code] = geolocation_feature(farm, c.boundary_version_id, session)
    # Freeze the screening evidence at batch-creation time (PR #20 review): batch_out pins the
    # boundary VERSION, but a re-run of that same version adds a newer verification -- the
    # statement must keep the run that was current when the farm joined the batch.
    for item in data["contributions"]:
        farm_payload = item.get("farm")
        if not farm_payload:
            continue
        frozen = session.exec(
            select(Verification)
            .where(Verification.farm_id == farm_payload["id"])
            .where(Verification.created_at <= batch.created_at)
            .order_by(Verification.created_at.desc())
        ).first()
        if frozen:
            farm_payload["verification"] = {
                "run_id": frozen.run_id,
                "created_at": frozen.created_at.isoformat(),
                "source": frozen.source,
                "score": frozen.score,
                "level": frozen.level,
                **json.loads(frozen.result_json),
            }

    payload = build_dds(data, features, base_url=str(request.base_url))
    body = json.dumps(payload, indent=2, ensure_ascii=False)
    return Response(body, media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="DDS_{batch.code}.json"'})


def geolocation_feature(farm: Farm, boundary_version_id: Optional[int], session: Session) -> dict:
    """One EUDR due-diligence geolocation feature for a farm: a true Point (<= 4 ha, issue #5)
    or Polygon -- never the buffered-circle stand-in `display_geometry` returns -- with
    coordinates rounded to 6 decimals and only non-personal properties (issue #7: no farmer
    names). Uses the boundary version (and its verification) the farm was on when it joined the
    batch, so a later remap doesn't change an already-exported batch's geolocation data.
    """
    bv = session.get(BoundaryVersion, boundary_version_id) if boundary_version_id else latest_boundary_version(session, farm.id)
    raw = json.loads(bv.geometry) if bv else json.loads(farm.geometry)
    if raw["type"] == "Point":
        lon, lat = raw["coordinates"]
        geometry = {"type": "Point", "coordinates": [round(lon, 6), round(lat, 6)]}
    else:
        ring = [[round(lon, 6), round(lat, 6)] for lon, lat in raw["coordinates"][0]]
        geometry = {"type": "Polygon", "coordinates": [ring]}
    v = verification_for_boundary_version(session, bv.id) if bv else latest_verification(session, farm.id)
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "farm_code": farm.code,
            "area_ha": bv.area_ha if bv else farm.area_ha,
            "producer_country": "GH",
            "commodity": COMMODITY,
            "production_period": farm.harvest_date,
            "boundary_version": bv.version_no if bv else None,
            "verification_date": v.created_at.date().isoformat() if v else None,
            "status": farm.status,
        },
    }


@app.get("/api/batches/{batch_id}/geolocation.geojson")
def batch_geolocation(batch_id: int, session: Session = Depends(get_session)):
    """EUDR due-diligence geolocation export (not asked for by issue #5's own text -- added on
    request): a GeoJSON FeatureCollection, one feature per contributing farm."""
    batch = session.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    if batch.status != "Approved":
        raise HTTPException(400, "This batch is pending approval -- exports are unavailable until it's approved.")
    contribs = session.exec(select(BatchContribution).where(BatchContribution.batch_id == batch_id)).all()
    features = []
    for c in contribs:
        farm = session.get(Farm, c.farm_id)
        if farm:
            features.append(geolocation_feature(farm, c.boundary_version_id, session))
    geojson = {"type": "FeatureCollection", "features": features}
    return Response(json.dumps(geojson), media_type="application/geo+json", headers={
        "Content-Disposition": f'attachment; filename="geolocation_{batch.code}.geojson"'})


@app.get("/api/batches/{batch_id}/bag_labels.pdf")
def batch_bag_labels(batch_id: int, session: Session = Depends(get_session)):
    batch = session.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    if batch.status != "Approved":
        raise HTTPException(400, "This batch is pending approval -- exports are unavailable until it's approved.")
    bags = session.exec(select(Bag).where(Bag.batch_id == batch_id).order_by(Bag.bag_id)).all()
    if not bags:
        raise HTTPException(400, "This batch has no bags yet.")
    items = []
    for b in bags:
        farm = session.get(Farm, b.farm_id)
        items.append({
            "bag_id": b.bag_id, "weight_kg": b.weight_kg,
            "farm_name": farm.name if farm else "–", "farm_code": farm.code if farm else "–",
            "batch_code": batch.code,
        })
    pdf = build_bag_labels_pdf(batch, items)
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="bag_labels_{batch.code}.pdf"'})


# ---------------------------------------------------------------- bags (public, no login -- QR target)

@app.get("/api/bags/{bag_id}")
def get_bag(bag_id: str, session: Session = Depends(get_session)):
    bag = session.exec(select(Bag).where(Bag.bag_id == bag_id)).first()
    if not bag:
        raise HTTPException(404, "Bag not found")
    return bag_out(session, bag)


# ---------------------------------------------------------------- bulk screening (F2.b)

class BulkStartBody(BaseModel):
    farm_ids: list[int]
    triggered_by: str = ""
    by_role: Optional[str] = None


@app.post("/api/bulk/verify")
def start_bulk_verify(body: BulkStartBody):
    # Same server-enforced rule as batch approval: the UI hides the button, the API still checks.
    if body.by_role != "admin":
        raise HTTPException(403, "Only a Brand admin can run bulk screening.")
    try:
        return bulk_verify.start_job(body.farm_ids, body.triggered_by)
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/api/bulk/{job_id}")
def get_bulk_job(job_id: str):
    job = bulk_verify.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Bulk job not found")
    return job


@app.get("/api/bulk/{job_id}/summary")
def get_bulk_summary(job_id: str):
    job = bulk_verify.get_job(job_id)
    if job is None:
        raise HTTPException(404, "Bulk job not found")
    return bulk_verify.summary_of(job)
