import base64
import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlmodel import Session, select

from . import config, geo, gee_service, scoring
from .db import get_session, init_db
from .models import Bag, Batch, BatchContribution, BoundaryVersion, Farm, Review, Verification
from .report import build_bag_labels_pdf, build_batch_pdf, build_pdf


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    config.CONSENT_PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="eudr_gee – Deforestation-free traceability API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

REVIEW_REASONS = ["Cloud cover", "Natural clearing", "Replanting of existing cocoa", "Boundary error", "Other"]
BATCHABLE_STATUSES = ("Verified", "Verified (manual review)")


# ---------------------------------------------------------------- schemas

class FarmCreate(BaseModel):
    name: str
    farmer_name: str
    village: str = ""
    harvest_date: Optional[str] = None
    crop: str = "Cocoa"
    captured_by: str = ""
    geometry: dict  # GeoJSON Polygon
    capture_method: str = "drawn"  # "gps_walk" or "drawn"
    gps_accuracy_m: Optional[float] = None
    flagged_points: int = 0
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
    contributions: list[BatchContributionIn]


class ReviewCreate(BaseModel):
    decision: str  # "clear" or "confirm"
    reason: str
    note: str = ""
    reviewer_name: str = ""


class RemapCreate(BaseModel):
    """Remapping reuses the farm's existing consent -- issue #4 says nothing about consent, so
    no new consent is asked for here."""
    geometry: dict  # GeoJSON Polygon
    capture_method: str = "drawn"  # "gps_walk" or "drawn"
    gps_accuracy_m: Optional[float] = None
    flagged_points: int = 0
    captured_by: str = ""


def needs_field_audit(farm: Farm) -> bool:
    """HLR-01: audit required if drawn on a map, or walked with avg accuracy worse than 10 m."""
    if farm.capture_method == "gps_walk":
        return farm.gps_accuracy_m is None or farm.gps_accuracy_m > 10
    return True


def is_batchable(farm: Farm) -> bool:
    return farm.status in BATCHABLE_STATUSES


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
    d["geometry"] = json.loads(farm.geometry)
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
        "geometry": json.loads(farm.geometry),
        "area_ha": farm.area_ha,
        "perimeter_m": farm.perimeter_m,
        "vertices": farm.vertices,
        "capture_method": farm.capture_method,
        "gps_accuracy_m": farm.gps_accuracy_m,
        "flagged_points": farm.flagged_points,
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
        "geometry": json.loads(v.geometry),
        "area_ha": v.area_ha,
        "perimeter_m": v.perimeter_m,
        "vertices": v.vertices,
        "capture_method": v.capture_method,
        "gps_accuracy_m": v.gps_accuracy_m,
        "flagged_points": v.flagged_points,
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
        captured_by=farm.captured_by,
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
                    "geometry": json.loads(bv.geometry), "area_ha": bv.area_ha,
                    "perimeter_m": bv.perimeter_m, "vertices": bv.vertices,
                    "capture_method": bv.capture_method, "gps_accuracy_m": bv.gps_accuracy_m,
                    "flagged_points": bv.flagged_points, "boundary_version": bv.version_no,
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
        items.append({
            "bags": c.bags,
            "kg_per_bag": c.kg_per_bag,
            "kg": kg,
            "farm": farm_payload,
        })
    return {
        "id": batch.id,
        "code": batch.code,
        "buyer": batch.buyer,
        "created_at": batch.created_at.isoformat(),
        "created_by": batch.created_by,
        "contributions": items,
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
def list_farms(session: Session = Depends(get_session)):
    farms = session.exec(select(Farm).order_by(Farm.code)).all()
    return [farm_out(f, reviews=farm_reviews(session, f.id), session=session) for f in farms]


@app.post("/api/farms", status_code=201)
def create_farm(body: FarmCreate, session: Session = Depends(get_session)):
    if not body.consent_given or not (body.consent_date or "").strip():
        raise HTTPException(422, detail=["Farmer consent (checkbox + date) is required before a farm can be submitted."])
    errors = geo.validate(body.geometry)
    if errors:
        raise HTTPException(422, detail=errors)
    area = geo.area_ha(body.geometry)
    farm = Farm(
        code=next_code(session),
        name=body.name.strip(),
        farmer_name=body.farmer_name.strip(),
        village=body.village.strip(),
        harvest_date=body.harvest_date,
        crop=body.crop,
        captured_by=body.captured_by,
        geometry=json.dumps(body.geometry),
        area_ha=round(area, 2),
        perimeter_m=round(geo.perimeter_m(body.geometry), 1),
        vertices=geo.vertex_count(body.geometry),
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
    out["warnings"] = geo.sanity_flags(area)
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
    errors = geo.validate(body.geometry)
    if errors:
        raise HTTPException(422, detail=errors)
    area = geo.area_ha(body.geometry)
    farm.geometry = json.dumps(body.geometry)
    farm.area_ha = round(area, 2)
    farm.perimeter_m = round(geo.perimeter_m(body.geometry), 1)
    farm.vertices = geo.vertex_count(body.geometry)
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
    out["warnings"] = geo.sanity_flags(area)
    return out


@app.get("/api/farms/{farm_id}/boundary_versions")
def farm_boundary_versions(farm_id: int, session: Session = Depends(get_session)):
    farm = session.get(Farm, farm_id)
    if not farm:
        raise HTTPException(404, "Farm not found")
    return [boundary_version_out(v) for v in boundary_versions_for(session, farm_id)]


@app.post("/api/farms/{farm_id}/verify")
def verify_farm(farm_id: int, session: Session = Depends(get_session)):
    farm = session.get(Farm, farm_id)
    if not farm:
        raise HTTPException(404, "Farm not found")
    geometry = json.loads(farm.geometry)
    try:
        result = gee_service.screen(geometry, farm.area_ha)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"Satellite screening failed: {e}")
    s = scoring.score(result)
    result.update({"flags": s["flags"] + geo.sanity_flags(farm.area_ha),
                   "breakdown": s["breakdown"]})

    current_version = latest_boundary_version(session, farm.id)
    v = Verification(
        farm_id=farm.id,
        boundary_version_id=current_version.id if current_version else None,
        run_id=f"GEE-{farm.code.split('-')[1]}-{uuid.uuid4().hex[:4].upper()}",
        source=result["source"],
        score=s["score"],
        level=s["level"],
        result_json=json.dumps(result),
    )
    farm.risk_score, farm.risk_level, farm.status = s["score"], s["level"], s["status"]
    farm.last_screened = datetime.now(timezone.utc)
    session.add(v)
    session.add(farm)
    session.commit()
    session.refresh(farm)
    session.refresh(v)
    return farm_out(farm, v, farm_reviews(session, farm.id), session=session)


@app.post("/api/farms/{farm_id}/review")
def review_farm(farm_id: int, body: ReviewCreate, session: Session = Depends(get_session)):
    """HLR-05 / DEL-02: Brand admin dispute decision. Only the UI restricts this to the admin role
    (no real auth in this MVP, same as the rest of the app); the backend validates the data itself.
    """
    farm = session.get(Farm, farm_id)
    if not farm:
        raise HTTPException(404, "Farm not found")
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
def consent_photo(farm_id: int, session: Session = Depends(get_session)):
    """Admin-only by convention (same no-real-auth model as /review): the consent photo itself
    is never embedded in list, public or PDF responses -- only reachable here, one farm at a time.
    """
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
    geometry = json.loads(bv.geometry) if bv else None
    facts_override = {
        "area_ha": bv.area_ha, "perimeter_m": bv.perimeter_m, "vertices": bv.vertices,
        "capture_method": bv.capture_method, "gps_accuracy_m": bv.gps_accuracy_m,
        "flagged_points": bv.flagged_points,
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
    }


# ---------------------------------------------------------------- batches (HLR-02 / DEL-03 / DEL-06)

@app.get("/api/batches")
def list_batches(session: Session = Depends(get_session)):
    batches = session.exec(select(Batch).order_by(Batch.created_at.desc())).all()
    return [batch_out(session, b) for b in batches]


@app.post("/api/batches", status_code=201)
def create_batch(body: BatchCreate, session: Session = Depends(get_session)):
    if not body.contributions:
        raise HTTPException(422, detail=["A batch needs at least one farm contribution."])
    errors = []
    for c in body.contributions:
        farm = session.get(Farm, c.farm_id)
        if not farm:
            errors.append(f"Farm {c.farm_id} not found.")
        elif farm.status == "Excluded":
            reviews = farm_reviews(session, farm.id)
            reason = reviews[0].reason if reviews else "manual review"
            errors.append(f"{farm.name} is Excluded ({reason}) and cannot be added to a batch.")
        elif not is_batchable(farm):
            errors.append(f"{farm.name} is not Verified and cannot be added to a batch.")
        elif c.bags <= 0:
            errors.append(f"{farm.name}: bag count must be positive.")
    if errors:
        raise HTTPException(422, detail=errors)

    batch = Batch(code=next_batch_code(session), buyer=body.buyer.strip() or Batch().buyer,
                  created_by=body.created_by)
    session.add(batch)
    session.commit()
    session.refresh(batch)
    for c in body.contributions:
        farm = session.get(Farm, c.farm_id)
        current_version = latest_boundary_version(session, c.farm_id)
        contribution = BatchContribution(batch_id=batch.id, farm_id=c.farm_id,
                                          bags=c.bags, kg_per_bag=c.kg_per_bag,
                                          boundary_version_id=current_version.id if current_version else None)
        session.add(contribution)
        session.commit()
        session.refresh(contribution)
        create_bags(session, batch, contribution, farm)
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
    data = batch_out(session, batch, public=True)
    pdf = build_batch_pdf(batch, data)
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="shipment_{batch.code}.pdf"'})


@app.get("/api/batches/{batch_id}/bag_labels.pdf")
def batch_bag_labels(batch_id: int, session: Session = Depends(get_session)):
    batch = session.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
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
