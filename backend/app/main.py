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
from .models import Farm, Verification
from .report import build_pdf

app = FastAPI(title="eudr_gee – Deforestation-free traceability API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


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


def farm_out(farm: Farm, latest: Optional[Verification] = None) -> dict:
    d = farm.model_dump()
    d["geometry"] = json.loads(farm.geometry)
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


def latest_verification(session: Session, farm_id: int) -> Optional[Verification]:
    return session.exec(
        select(Verification).where(Verification.farm_id == farm_id)
        .order_by(Verification.created_at.desc())
    ).first()


def next_code(session: Session) -> str:
    farms = session.exec(select(Farm.code)).all()
    nums = [int(c.split("-")[1]) for c in farms if c.startswith("GH-")]
    return f"GH-{(max(nums) + 1) if nums else 241:04d}"


# ---------------------------------------------------------------- routes

@app.get("/api/health")
def health():
    mode = "mock" if config.USE_MOCK_GEE else "gee"
    return {"ok": True, "mode": mode, "project": config.GEE_PROJECT}


@app.get("/api/farms")
def list_farms(session: Session = Depends(get_session)):
    farms = session.exec(select(Farm).order_by(Farm.code)).all()
    return [farm_out(f) for f in farms]


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
    )
    session.add(farm)
    session.commit()
    session.refresh(farm)
    out = farm_out(farm)
    out["warnings"] = geo.sanity_flags(area)
    return out


@app.get("/api/farms/{farm_id}")
def get_farm(farm_id: int, session: Session = Depends(get_session)):
    farm = session.get(Farm, farm_id)
    if not farm:
        raise HTTPException(404, "Farm not found")
    return farm_out(farm, latest_verification(session, farm_id))


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
    return farm_out(farm, v)


@app.get("/api/farms/{farm_id}/report.pdf")
def farm_report(farm_id: int, session: Session = Depends(get_session)):
    farm = session.get(Farm, farm_id)
    if not farm:
        raise HTTPException(404, "Farm not found")
    v = latest_verification(session, farm_id)
    if not v:
        raise HTTPException(400, "Run the satellite check before exporting evidence.")
    pdf = build_pdf(farm, v)
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
    }
