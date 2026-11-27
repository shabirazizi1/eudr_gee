# eudr_gee – project notes for Claude

Solo MVP for a Mahidol Master's "Software Development" class. Demo is recorded on localhost.
Keep changes small; main is protected; every change goes through a PR with green CI.

## What it is
Deforestation-free cocoa traceability platform (EUDR). Cutoff date = **31 Dec 2020**.
F1 login is a role picker only (no real auth) – that's intentional. Three roles: field agent,
Brand admin, warehouse operator.

## Features
- **F2 Farm registry & boundary capture** – draw a polygon on a satellite map, or walk it with
  GPS (live accuracy-checked point capture, flags points worse than 10 m). Live area/perimeter/
  vertex checks, self-intersection rejected. Drafts save offline and sync via an outbox queue
  when back online (`frontend/src/api.js`).
- **F3 Earth Engine verification** – screens the polygon with Hansen forest-loss-after-cutoff,
  Sentinel-2 NDVI, and WDPA protected-area overlap (`app/gee_service.py`); falls back to a
  clearly-labelled MOCK result if Earth Engine errors (`app/scoring.py` turns it into a 0–100
  risk score and Low/Medium/High level).
- **Manual review workflow** – a Brand admin clears a false positive or confirms/excludes a farm
  `Under review`; every decision is a permanent `Review` row (never edited or deleted).
- **Shipment batches (HLR-02)** – bundle Verified (or cleared) farms into a batch for a buyer;
  Excluded or un-Verified farms can't be added.
- **Bag-level QR traceability** – each batch mints one `Bag` row per bag with a unique
  `BAG-<farm>-<seq>` id; `/bag/:id` is a public, no-login page with the deforestation-free
  verdict for that bag.
- **PDF evidence** – per-farm evidence report, per-batch shipment compliance package, and a
  bag-label sheet (14 per A4), all built with ReportLab (`app/report.py`).
- **Evidence dashboard** – portfolio KPIs, risk distribution, review queue (`/api/summary`).
- **Mobile UI** – below 900px, a fixed bottom tab bar (Overview/Capture/Farms/Batches/Account)
  replaces the sidebar; capture, batch-detail and map/QR cards adapt to the phone layout.

## Models (`app/models.py`)
- `Farm` – boundary geometry (GeoJSON), capture method/accuracy, denormalised latest
  risk score/level/status for the dashboard.
- `Verification` – one row per `/verify` run (source `gee`/`mock`, score, level, full result JSON).
- `Review` – one row per admin dispute decision on a farm (decision/reason/note/reviewer).
- `Batch` / `BatchContribution` – a shipment batch and its per-farm bag+weight contribution.
- `Bag` – one physical bag, linked to its farm/batch/contribution, QR target.

## Stack
- `backend/` FastAPI + SQLModel (SQLite `eudr.db`) + earthengine-api + ReportLab
  - `app/gee_service.py` Earth Engine screening; `app/scoring.py` risk score; `app/geo.py` polygon
    math/validation; `app/report.py` PDFs; `app/seed.py` demo farms; `app/check_gee.py` EE sanity check
  - Config in `backend/.env` (see below)
- `frontend/` React (Vite, plain JS) + Leaflet + Geoman, plain CSS in `src/styles.css`
  - Vite proxies `/api` to `localhost:8000`

## Commands
- Backend: `cd backend && source .venv/bin/activate && python -m uvicorn app.main:app --reload --reload-dir app`
- **Always use `backend/.venv`, never the repo-root `.venv`** – and always `python -m ...` inside
  it (system Python lacks the packages).
- Seed: `python -m app.seed --reset --verify` · GEE check: `python -m app.check_gee`
- Tests: `python -m pytest -v` (never touches `eudr.db` or calls real Earth Engine – see `tests/conftest.py`)
- Frontend: `cd frontend && npm run dev` (http://localhost:5173)
- Phone testing (GPS walk needs HTTPS + LAN): `npm run dev:phone`
- Build check: `npm run build`

## `.env` settings (`backend/.env`, see `.env.example`)
- `GEE_PROJECT` – Earth Engine Cloud project ID (`eudr-gee-513515`)
- `USE_MOCK_GEE` – `true` = never call Earth Engine (deterministic fake results)
- `ALLOW_MOCK_FALLBACK` – if Earth Engine errors, fall back to a labelled MOCK result
- `HANSEN_ASSET` – Hansen Global Forest Change asset version
- `CUTOFF_YEAR` – EUDR cutoff year (2020)
- `FRONTEND_URL` – frontend origin, used to build batch/bag QR codes and PDF links

## Rules
- Always use `backend/.venv`, not the repo-root `.venv`.
- Earth Engine runs with the user's own login on this Mac; real results show `source: "gee"`.
  Never present MOCK results as real evidence.
- SQLModel needs timezone-aware datetimes: use `datetime.now(timezone.utc)`.
- Keep the UI style consistent with `styles.css` tokens (cream/green, matches the design report figures).
- Mobile styles only apply below 900px; the laptop layout must stay unchanged unless asked.
- After backend changes, re-run the seed or restart uvicorn; after frontend changes, run `npm run build` to catch errors.
