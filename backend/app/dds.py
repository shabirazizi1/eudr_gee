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


def _farm_entry(contribution: dict) -> dict:
    farm = contribution["farm"]
    verification = farm.get("verification") or {}
    review = farm.get("latest_review")
    screened_at = farm.get("last_screened")
    if hasattr(screened_at, "isoformat"):
        screened_at = screened_at.isoformat()
    return {
        "farm_id": farm["id"],
        "farm_code": farm["code"],
        "farm_name": farm["name"],
        "producer": farm["farmer_name"],
        "village": farm["village"],
        "region": farm["region"],
        "area_ha": farm["area_ha"],
        "geolocation": farm["geometry"],
        "screening": {
            "status": farm["status"],
            "risk_level": farm["risk_level"],
            "risk_score": farm["risk_score"],
            "screened_at": screened_at,
            "run_id": verification.get("run_id"),
            "source": verification.get("source"),
            "flags": verification.get("flags", []),
            "needs_field_audit": farm["needs_field_audit"],
        },
        "manual_review": {
            "decision": review["decision"],
            "reason": review["reason"],
            "note": review["note"],
            "reviewer": review["reviewer_name"],
            "reviewed_at": review["created_at"],
        } if review else None,
        "contribution": {
            "bags": contribution["bags"],
            "kg_per_bag": contribution["kg_per_bag"],
            "net_weight_kg": contribution["kg"],
        },
    }


def _mitigation(entries: list[dict]) -> list[str]:
    measures = []
    for e in entries:
        code = e["farm_code"]
        screening = e["screening"]
        if screening["risk_level"] == "High":
            measures.append(f"{code}: on-site verification and supplier engagement before shipment.")
        if screening["needs_field_audit"]:
            measures.append(f"{code}: field audit to confirm the plot boundary (drawn or low-accuracy GPS).")
        if e["manual_review"]:
            measures.append(
                f"{code}: manually reviewed and cleared by {e['manual_review']['reviewer']} "
                f"({e['manual_review']['reason']}).")
    return measures


def build_dds(batch: dict) -> dict:
    """`batch` is the dict returned by main.batch_out()."""
    entries = [_farm_entry(c) for c in batch["contributions"] if c.get("farm")]
    regions = sorted({e["region"] for e in entries})
    villages = sorted({e["village"] for e in entries if e["village"]})
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
            "villages": villages,
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
            "created_by": batch["created_by"],
        },
        "attachments": [
            {"type": "shipment_compliance_package",
             "url": f"/api/batches/{batch['id']}/report.pdf"},
            *[{"type": "farm_evidence", "farm_code": e["farm_code"],
                "url": f"/api/farms/{e['farm_id']}/report.pdf"}
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
