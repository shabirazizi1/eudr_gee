from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class Farm(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)  # e.g. GH-0241
    name: str
    farmer_name: str
    village: str = ""
    region: str = "Western Region, Ghana"
    crop: str = "Cocoa"
    harvest_date: Optional[str] = None
    geometry: str  # GeoJSON Polygon, or Point for a declared-area capture (issue #5), as JSON (WGS84)
    area_ha: float = 0.0
    perimeter_m: float = 0.0
    vertices: int = 0
    captured_by: str = ""
    capture_method: str = "drawn"  # "gps_walk", "drawn", or "gps_point" (issue #5)
    gps_accuracy_m: Optional[float] = None  # average GPS accuracy across walked/captured points (m)
    flagged_points: int = 0  # walked/captured points with accuracy worse than 10 m
    # EUDR allows plots <= 4 ha to be declared with a single point instead of a polygon (issue
    # #5); set only for `capture_method == "gps_point"`, where `area_ha` mirrors this value.
    declared_area_ha: Optional[float] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Latest screening summary (denormalised for the dashboard)
    risk_score: Optional[int] = None
    risk_level: Optional[str] = None  # Low / Medium / High
    status: str = "Not screened"  # Not screened / Verified / Under review
    last_screened: Optional[datetime] = None
    # Farmer consent (issue #7): a farm cannot be created without it. "attestation" is the
    # checkbox + agent name (captured_by) + date; a signed-form photo is optional on top.
    consent_method: str = "attestation"  # "attestation" or "photo"
    consent_given: bool = False
    consent_date: Optional[str] = None
    consent_photo_path: Optional[str] = None  # filename under uploads/consent/; never returned by farm_out()


class BoundaryVersion(SQLModel, table=True):
    """HLR / charter success criterion (issue #4): remapping a farm never overwrites its
    boundary -- it appends a new, read-only version. Version 1 is created alongside the farm;
    `POST /farms/{id}/remap` creates every version after that.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    farm_id: int = Field(foreign_key="farm.id", index=True)
    version_no: int
    geometry: str  # GeoJSON Polygon, or Point for a declared-area capture (issue #5), as JSON (WGS84)
    area_ha: float = 0.0
    perimeter_m: float = 0.0
    vertices: int = 0
    capture_method: str = "drawn"  # "gps_walk", "drawn", or "gps_point" (issue #5)
    gps_accuracy_m: Optional[float] = None
    flagged_points: int = 0
    declared_area_ha: Optional[float] = None  # set only for "gps_point" versions (issue #5)
    captured_by: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Verification(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    farm_id: int = Field(foreign_key="farm.id", index=True)
    boundary_version_id: Optional[int] = Field(default=None, foreign_key="boundaryversion.id", index=True)
    run_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source: str  # "gee" or "mock"
    score: int
    level: str
    result_json: str  # full screening result


class Batch(SQLModel, table=True):
    """HLR-02 / DEL-03 / DEL-06: a shipment batch bundling verified farms for a buyer."""
    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True, unique=True)  # e.g. BATCH-2026-001
    buyer: str = "Chocolaterie Vandermeer, Ghent, Belgium"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by: str = ""
    season: str = "2026/27"  # mass-balance capacity (issue #6) is tracked per farm per season
    # Mass-balance approval workflow (issue #15): a batch within every farm's remaining season
    # capacity is created "Approved" directly, as before. One that exceeds capacity is created
    # "Pending approval" instead -- its kg still count against the season's committed capacity
    # (so the same kilos can't be reserved twice) until a Brand admin approves or rejects it.
    status: str = "Approved"  # Approved / Pending approval / Rejected / Cancelled
    submission_note: str = ""  # operator's justification note when submitted for approval


class BatchContribution(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    batch_id: int = Field(foreign_key="batch.id", index=True)
    farm_id: int = Field(foreign_key="farm.id", index=True)
    bags: int
    kg_per_bag: float = 64.0
    # The boundary version the farm was on when it joined this batch (issue #4): a later remap
    # must not change what an already-shipped batch shows.
    boundary_version_id: Optional[int] = Field(default=None, foreign_key="boundaryversion.id")
    # Mass-balance override (issue #6): set only when this contribution exceeded the farm's
    # remaining season capacity and a Brand admin approved it anyway.
    override_reason: Optional[str] = None  # Second harvest / Yield above average / Data correction / Other
    override_note: str = ""
    overridden_by: str = ""
    overridden_at: Optional[datetime] = None


class Bag(SQLModel, table=True):
    """One physical bag within a batch contribution, for bag-level QR traceability.
    `bag_id` (e.g. "BAG-0241-0001") is the public, human-readable identifier the QR code encodes
    -- a numeric sequence per farm that keeps counting across batches, so it never repeats.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    bag_id: str = Field(index=True, unique=True)
    farm_id: int = Field(foreign_key="farm.id", index=True)
    batch_id: int = Field(foreign_key="batch.id", index=True)
    contribution_id: Optional[int] = Field(default=None, foreign_key="batchcontribution.id", index=True)
    weight_kg: float
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class BatchEvent(SQLModel, table=True):
    """Audit trail for the mass-balance approval workflow (issue #15): one row per submitted /
    approved / rejected / cancelled decision on a batch. Never edited or deleted."""
    id: Optional[int] = Field(default=None, primary_key=True)
    batch_id: int = Field(foreign_key="batch.id", index=True)
    event: str  # "submitted" / "approved" / "rejected" / "cancelled"
    by: str = ""
    by_role: Optional[str] = None
    note: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Review(SQLModel, table=True):
    """HLR-05 / DEL-02: a Brand admin's dispute decision on a farm Under review.
    Never deleted or edited -- the full history stays even after re-screening or further reviews.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    farm_id: int = Field(foreign_key="farm.id", index=True)
    decision: str  # "clear" or "confirm"
    reason: str  # Cloud cover / Natural clearing / Replanting of existing cocoa / Boundary error / Other
    note: str = ""
    reviewer_name: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
