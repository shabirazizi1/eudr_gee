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
    geometry: str  # GeoJSON Polygon as a JSON string (WGS84)
    area_ha: float = 0.0
    perimeter_m: float = 0.0
    vertices: int = 0
    captured_by: str = ""
    capture_method: str = "drawn"  # "gps_walk" or "drawn"
    gps_accuracy_m: Optional[float] = None  # average GPS accuracy across walked points (m)
    flagged_points: int = 0  # walked points with accuracy worse than 10 m
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    # Latest screening summary (denormalised for the dashboard)
    risk_score: Optional[int] = None
    risk_level: Optional[str] = None  # Low / Medium / High
    status: str = "Not screened"  # Not screened / Verified / Under review
    last_screened: Optional[datetime] = None


class Verification(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    farm_id: int = Field(foreign_key="farm.id", index=True)
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


class BatchContribution(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    batch_id: int = Field(foreign_key="batch.id", index=True)
    farm_id: int = Field(foreign_key="farm.id", index=True)
    bags: int
    kg_per_bag: float = 64.0


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
