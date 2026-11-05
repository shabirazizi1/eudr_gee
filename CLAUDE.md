# eudr_gee – project notes for Claude

Solo MVP for a Mahidol Master's "Software Development" class. Demo is recorded on localhost.
**Deadline: Friday 6 Nov 2026.** Keep changes small and demo-focused; don't refactor working code.

## What it is
Deforestation-free cocoa traceability platform (EUDR). Cutoff date = **31 Dec 2020**.
Demo features: F2 farm boundary capture, F3 Google Earth Engine verification, F5 evidence dashboard + PDF.
F1 login is a role picker only (no real auth) – that's intentional.

## Stack
- `backend/` FastAPI + SQLModel (SQLite `eudr.db`) + earthengine-api + ReportLab
  - `app/gee_service.py` Earth Engine screening (Hansen loss, Sentinel-2 NDVI, WDPA); mock fallback
  - `app/scoring.py` 0–100 risk score; `app/report.py` PDF; `app/seed.py` demo farms
  - Config in `backend/.env` (GEE_PROJECT=eudr-gee-513515)
- `frontend/` React (Vite, plain JS) + Leaflet + Geoman, plain CSS in `src/styles.css`
  - Vite proxies `/api` to `localhost:8000`

## Commands
- Backend: `cd backend && source .venv/bin/activate && python -m uvicorn app.main:app --reload --reload-dir app`
- Always use `python -m ...` inside the venv (system Python lacks the packages).
- Seed: `python -m app.seed --reset --verify` · GEE check: `python -m app.check_gee`
- Frontend: `cd frontend && npm run dev` (http://localhost:5173) · build check: `npm run build`

## Rules
- Earth Engine runs with the user's own login on this Mac; real results show `source: "gee"`.
  Never present MOCK results as real evidence.
- SQLModel needs timezone-aware datetimes: use `datetime.now(timezone.utc)`.
- Keep the UI style consistent with `styles.css` tokens (cream/green, matches the design report figures).
- After backend changes, re-run the seed or restart uvicorn; after frontend changes, run `npm run build` to catch errors.
