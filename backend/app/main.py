import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel
from sqlmodel import Session, select

from . import config, geo, gee_service, scoring
from .db import get_session, init_db
from .models import Bag, Batch, BatchContribution, Farm, Review, Verification
from .report import build_bag_labels_pdf, build_batch_pdf, build_pdf

app = FastAPI(title="eudr_gee – Deforestation-free traceability API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

REVIEW_REASONS = ["Cloud cover", "Natural clearing", "Replanting of existing cocoa", "Boundary error", "Other"]
BATCHABLE_STATUSES = ("Verified", "Verified (manual review)")


@app.on_event("startup")
def on_startup():
    init_db()


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


def farm_out(farm: Farm, latest: Optional[Verification] = None, reviews: Optional[list] = None) -> dict:
    d = farm.model_dump()
    d["geometry"] = json.loads(farm.geometry)
    d["needs_field_audit"] = needs_field_audit(farm)
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


def latest_verification(session: Session, farm_id: int) -> Optional[Verification]:
    return session.exec(
        select(Verification).where(Verification.farm_id == farm_id)
        .order_by(Verification.created_at.desc())
    ).first()


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
    farm = session.get(Farm, bag.farm_id)
    batch = session.get(Batch, bag.batch_id)
    return {
        "bag_id": bag.bag_id,
        "weight_kg": bag.weight_kg,
        "created_at": bag.created_at.isoformat(),
        "farm": farm_out(farm, latest_verification(session, farm.id)) if farm else None,
        "batch": {"id": batch.id, "code": batch.code, "buyer": batch.buyer} if batch else None,
    }


def batch_out(session: Session, batch: Batch) -> dict:
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
        items.append({
            "bags": c.bags,
            "kg_per_bag": c.kg_per_bag,
            "kg": kg,
            "farm": farm_out(farm, latest_verification(session, farm.id),
                             farm_reviews(session, farm.id)) if farm else None,
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
    return [farm_out(f, reviews=farm_reviews(session, f.id)) for f in farms]


@app.post("/api/farms", status_code=201)
def create_farm(body: FarmCreate, session: Session = Depends(get_session)):
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
    )
    session.add(farm)
    session.commit()
    session.refresh(farm)
    out = farm_out(farm, reviews=[])
    out["warnings"] = geo.sanity_flags(area)
    return out


@app.get("/api/farms/{farm_id}")
def get_farm(farm_id: int, session: Session = Depends(get_session)):
    farm = session.get(Farm, farm_id)
    if not farm:
        raise HTTPException(404, "Farm not found")
    return farm_out(farm, latest_verification(session, farm_id), farm_reviews(session, farm_id))


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

    v = Verification(
        farm_id=farm.id,
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
    return farm_out(farm, v, farm_reviews(session, farm.id))


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
    return farm_out(farm, latest_verification(session, farm.id), farm_reviews(session, farm.id))


@app.get("/api/farms/{farm_id}/report.pdf")
def farm_report(farm_id: int, session: Session = Depends(get_session)):
    farm = session.get(Farm, farm_id)
    if not farm:
        raise HTTPException(404, "Farm not found")
    v = latest_verification(session, farm_id)
    if not v:
        raise HTTPException(400, "Run the satellite check before exporting evidence.")
    reviews = [review_out(r) for r in farm_reviews(session, farm_id)]
    pdf = build_pdf(farm, v, reviews)
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
        contribution = BatchContribution(batch_id=batch.id, farm_id=c.farm_id,
                                          bags=c.bags, kg_per_bag=c.kg_per_bag)
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
    batch = session.get(Batch, batch_id)
    if not batch:
        raise HTTPException(404, "Batch not found")
    data = batch_out(session, batch)
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
