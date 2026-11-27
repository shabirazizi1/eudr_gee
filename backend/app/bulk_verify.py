"""F2.b – bulk satellite screening with progress tracking.

Runs many farm screenings in the background under a job id, so the UI can poll
progress instead of firing N parallel /verify requests and hoping. Deliberately
dependency-free: a small thread pool plus an in-process job registry. Swapping in
Celery/RQ later only touches this file -- the API contract stays the same.

Jobs live in memory only: a server restart drops them (farms keep whatever state they
already had, nothing half-committed), which is fine for the demo but worth saying out loud.

The screening itself is `screening.screen_farm`, the same helper /verify uses (PR #20):
a declared-area Point farm is buffered before Earth Engine sees it.
"""
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from sqlmodel import Session, select

from .db import engine
from .models import BoundaryVersion, Farm
from .screening import screen_farm

WORKERS = 3                  # GEE quota is finite -- don't fan out harder than this
JOB_TTL_SECONDS = 3600 * 6   # keep finished jobs in memory for 6 h, then drop
MAX_FARMS = 500              # refuse absurd selections before burning quota

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="bulk-verify")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot(job: dict) -> dict:
    """Public shape returned by the API -- never leak internals like futures."""
    total = job["total"]
    results = list(job["results"])   # copy: worker threads append while we read
    done = len(results)
    return {
        "job_id": job["job_id"],
        "status": job["status"],          # queued | running | done | failed
        "total": total,
        "done": done,
        "failed": job["failed"],
        "progress_pct": round(done / total * 100) if total else 0,
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
        "eta_seconds": job["eta_seconds"],
        "results": results,               # appended as each farm completes
        "triggered_by": job.get("triggered_by", ""),
    }


def get_job(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
        return _snapshot(job) if job else None


def _eta(job: dict):
    done = len(job["results"])
    if done < 2 or done >= job["total"]:
        return None
    per = (time.monotonic() - job["t0"]) / done
    return round(per * (job["total"] - done))


def _run_one(job_id: str, farm_id: int, farm_code: str):
    """Screens a single farm in its own short-lived session. Never raises -- a
    failure here must not sink the rest of the batch."""
    t0 = time.monotonic()
    with Session(engine) as s:
        try:
            farm = s.get(Farm, farm_id)
            if farm is None:
                raise ValueError("Farm no longer exists")

            # Read the boundary version BEFORE screening: the verification must name the
            # version that was current when this farm was picked up, not a remap landing mid-job.
            current_version = s.exec(
                select(BoundaryVersion).where(BoundaryVersion.farm_id == farm.id)
                .order_by(BoundaryVersion.version_no.desc())
            ).first()
            verification = screen_farm(s, farm, current_version.id if current_version else None)
            flags = json.loads(verification.result_json).get("flags", [])

            entry = {
                "farm_id": farm.id,
                "code": farm.code,
                "name": farm.name,
                "status": "ok",
                "score": verification.score,
                "level": verification.level,
                "flags": flags,
                "verification_id": verification.id,
                "seconds": round(time.monotonic() - t0, 1),
            }
        except Exception as e:  # noqa: BLE001 -- one bad farm must not fail the job
            entry = {
                "farm_id": farm_id, "code": farm_code, "status": "error",
                "message": f"{type(e).__name__}: {e}",
                "seconds": round(time.monotonic() - t0, 1),
            }

    with _lock:
        job = _jobs[job_id]
        job["results"].append(entry)
        if entry["status"] == "error":
            job["failed"] += 1
        job["eta_seconds"] = _eta(job)
        if len(job["results"]) >= job["total"]:
            job["status"] = "done"
            job["finished_at"] = _now()
            job["t_end"] = time.monotonic()
            job["eta_seconds"] = None


def start_job(farm_ids: list[int], triggered_by: str = "") -> dict:
    """Validates, registers the job and schedules the work. Returns the snapshot
    immediately so the caller can poll."""
    if not farm_ids:
        raise ValueError("Select at least one farm.")
    unique_ids = list(dict.fromkeys(farm_ids))
    if len(unique_ids) > MAX_FARMS:
        raise ValueError(f"Too many farms ({len(unique_ids)}). Maximum is {MAX_FARMS}.")

    with Session(engine) as s:
        farms = s.exec(select(Farm).where(Farm.id.in_(unique_ids))).all()
    if not farms:
        raise ValueError("None of the selected farms exist.")

    job_id = f"BULK-{uuid.uuid4().hex[:8].upper()}"
    with _lock:
        _purge_closed_jobs_locked()
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "total": len(farms),
            "failed": 0,
            "results": [],
            "started_at": _now(),
            "finished_at": None,
            "eta_seconds": None,
            "t0": time.monotonic(),
            "triggered_by": triggered_by,
        }

    for farm in farms:
        _pool.submit(_run_one, job_id, farm.id, farm.code)

    with _lock:
        _jobs[job_id]["status"] = "running"
        return _snapshot(_jobs[job_id])


def _purge_closed_jobs_locked():
    cutoff = time.monotonic() - JOB_TTL_SECONDS
    stale = [k for k, v in _jobs.items() if v.get("t_end") and v["t_end"] < cutoff]
    for k in stale:
        _jobs.pop(k, None)


def summary_of(job: dict) -> dict:
    """Roll-up used by the modal's final screen -- counts by risk level plus any
    flagged farms, so the operator knows what still needs a manual run."""
    counts = {"Low": 0, "Medium": 0, "High": 0}
    flagged = []
    for r in job["results"]:
        if r["status"] == "ok" and r["level"] in counts:
            counts[r["level"]] += 1
        if r.get("flags"):
            flagged.append({"code": r["code"], "flags": r["flags"]})
    return {"counts": counts, "flagged": flagged, "errors": job["failed"]}
