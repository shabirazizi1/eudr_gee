"""EUDR Due Diligence Statement (DDS) export.

Turns a batch into a TRACES-style JSON statement: operator, commodity, quantity,
geolocations, screening evidence and the resulting risk conclusion. It mirrors the
information a DDS must contain, but it is a demo artefact -- not an official
submission format, and `disclaimer` says so.
"""
import hashlib
import json
from datetime import datetime, timezone

RISK_ORDER = {"Low": 0, "Medium": 1, "High": 2}


def _canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _worst_level(levels: list) -> str:
    known = [lvl for lvl in levels if lvl in RISK_ORDER]
    return max(known, key=RISK_ORDER.get) if known else "Unscreened"


def _country(regions: list[str]) -> str:
    # Regions are stored as "Western Region, Ghana" -- the last segment is the country.
    for region in regions:
        tail = region.rsplit(",", 1)[-1].strip()
        if tail:
            return tail
    return "Unknown"


def _farm_entry(contribution: dict, geolocation: dict | None = None) -> dict:
    farm = contribution["farm"]
    verification = farm.get("verification") or {}
    review = farm.get("latest_review")
    screened_at = farm.get("last_screened")
    if hasattr(screened_at, "isoformat"):
        screened_at = screened_at.isoformat()
    # The statement reports the verification frozen on the batch's boundary version, so its
    # score/level must come from that verification -- not from a later re-run of the farm
    # (PR #20 review). Falls back to the farm's own fields when no verification is attached.
    risk_score = verification.get("score", farm["risk_score"])
    risk_level = verification.get("level", farm["risk_level"])
    if verification.get("created_at"):
        screened_at = verification["created_at"]
    # The public payload already drops village/audit flags; the geolocation feature carries the
    # true Point/Polygon (batch_out's display geometry may be a buffered circle for points).
    properties = (geolocation or {}).get("properties", {})
    entry = {
        "farm_id": farm["id"],
        "farm_code": farm["code"],
        "farm_name": farm["name"],
        "producer": farm["farmer_name"],
        "village": farm.get("village", ""),
        "region": farm["region"],
        "area_ha": properties.get("area_ha", farm["area_ha"]),
        "geolocation": (geolocation or {}).get("geometry", farm["geometry"]),
        "screening": {
            "status": farm["status"],
            "risk_level": risk_level,
            "risk_score": risk_score,
            "screened_at": screened_at,
            "run_id": verification.get("run_id"),
            "source": verification.get("source"),
            "flags": verification.get("flags", []),
            "needs_field_audit": farm.get("needs_field_audit", False),
        },
        "contribution": {
            "bags": contribution["bags"],
            "kg_per_bag": contribution["kg_per_bag"],
            "net_weight_kg": contribution["kg"],
        },
    }
    # Only present when a review actually exists -- no `"manual_review": null` padding.
    if review:
        entry["manual_review"] = {
            "decision": review["decision"],
            "reason": review["reason"],
            "note": review["note"],
            "reviewer": review["reviewer_name"],
            "reviewed_at": review["created_at"],
        }
    return entry


def _mitigation(entries: list[dict]) -> list[str]:
    measures = []
    for e in entries:
        code = e["farm_code"]
        screening = e["screening"]
        if screening["risk_level"] == "High":
            measures.append(f"{code}: on-site verification and supplier engagement before shipment.")
        if screening["needs_field_audit"]:
            measures.append(f"{code}: field audit to confirm the plot boundary (drawn or low-accuracy GPS).")
        if e.get("manual_review"):
            measures.append(
                f"{code}: manually reviewed and cleared by {e['manual_review']['reviewer']} "
                f"({e['manual_review']['reason']}).")
    return measures


def build_dds(batch: dict, geolocations: dict | None = None, base_url: str = "") -> dict:
    """`batch` is the minimized dict returned by main.batch_out(public=True);
    `geolocations` maps farm_code -> EUDR geolocation feature from main.geolocation_feature.
    `base_url` makes the attachment links absolute so the file is useful outside this API."""
    geolocations = geolocations or {}
    base_url = base_url.rstrip("/")
    entries = [_farm_entry(c, geolocations.get(c["farm"]["code"]))
               for c in batch["contributions"] if c.get("farm")]
    regions = sorted({e["region"] for e in entries})
    levels = [e["screening"]["risk_level"] for e in entries]
    overall = _worst_level(levels)
    high_risk = [e["farm_code"] for e in entries if e["screening"]["risk_level"] == "High"]

    if high_risk:
        conclusion = (f"Conditional: {len(high_risk)} high-risk farm(s) need the listed mitigation "
                      "measures before this batch is placed on the EU market.")
    else:
        conclusion = ("No deforestation indicators above the configured threshold were detected "
                      "in the satellite screening for the farms in this batch.")

    payload = {
        "format": "eudr-dds-demo/1.0",
        "status": "draft",
        "statement_id": f"DDS-{batch['code']}",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "operator": {
            "name": batch["buyer"],
            "role": "EU operator (downstream)",
        },
        "commodity": {
            "name": "Cocoa",
            "hs_code": "1801",
            "description": "Cocoa beans, whether or not broken",
            "quantity": {
                "batch": batch["code"],
                "bags": batch["totals"]["bags"],
                "net_weight_kg": batch["totals"]["kg"],
            },
        },
        "production": {
            "country": _country(regions),
            "regions": regions,
            "farms": entries,
        },
        "risk_assessment": {
            "approach": ("Satellite screening (Hansen global forest change, protected areas, "
                         "NDVI anomaly) with manual review of flagged farms. Decision support "
                         "only, not a legal determination."),
            "overall_risk_level": overall,
            "farms_assessed": len(entries),
            "high_risk_farms": high_risk,
            "mitigation_measures": _mitigation(entries),
            "conclusion": conclusion,
        },
        "supply_chain": {
            "batch": batch["code"],
            "buyer": batch["buyer"],
            "created_at": batch["created_at"],
            # no created_by: a staff member's name is exactly what data minimization removes
        },
        "attachments": [
            {"type": "shipment_compliance_package",
             "url": f"{base_url}/api/batches/{batch['id']}/report.pdf"},
            *[{"type": "farm_evidence", "farm_code": e["farm_code"],
                "url": f"{base_url}/api/farms/{e['farm_id']}/report.pdf"}
              for e in entries],
        ],
        "disclaimer": ("Demonstration due-diligence statement generated from screening data. "
                       "Satellite screening is decision support, not EUDR certification, and "
                       "this file is not an official EU TRACES submission."),
    }

    payload["integrity"] = {
        "algorithm": "sha256",
        "sha256": hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest(),
    }
    return payload
