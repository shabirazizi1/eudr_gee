# eudr_gee – cocoa traceability platform

[![CI](https://github.com/shabirazizi1/eudr_gee/actions/workflows/ci.yml/badge.svg)](https://github.com/shabirazizi1/eudr_gee/actions/workflows/ci.yml)

## What is this?

Cocoa cooperatives need to prove to EU buyers that their cocoa doesn't come from land deforested
after 31 December 2020 — a requirement of the EU Deforestation Regulation (EUDR). This platform
helps them do that: field agents map each farm's boundary with GPS, the platform checks it
against real satellite data, and the cooperative hands buyers evidence that stands up to scrutiny.

It's for cocoa cooperatives, the brands and buyers they sell to, and anyone checking a shipment's
origin (a customs officer, an auditor).

**This is a demo / MVP for a university course, not a certified compliance product.** It's a
decision-support tool, not a legal determination — see "Limitations" below before relying on it.

## Quick start (any computer with Docker)

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/).
2. `git clone https://github.com/shabirazizi1/eudr_gee.git && cd eudr_gee`
3. `docker compose up --build`
4. Open **http://localhost:8080**.

The app is reachable within seconds, but demo farms take about **2 minutes** to appear — they're
screened against real satellite data in the background. No other setup step is needed.

Stop with `docker compose down`; add `-v` to also delete the saved database and start fresh.

## Demo data vs. real data

The farms, farmers and boundaries here are **fictional**, placed at real locations in Ghana's
Western Region. The **satellite results are real** — every risk score comes from an actual call
to Google Earth Engine for those exact coordinates.

If Earth Engine can't be reached (no credentials, or they've expired), the app falls back to a
clearly labelled **MOCK** result and shows a yellow "Earth Engine: MOCK mode" badge.

To get real results, you need a Google Earth Engine project:
- **Use your own** — register free at [earthengine.google.com](https://earthengine.google.com),
  then set `GEE_PROJECT` in `backend/.env` to your project ID.
- **Or ask the owner to add you** to their Google Cloud project with the **Earth Engine Resource
  Writer** and **Service Usage Consumer** roles, then run `earthengine authenticate` yourself.

## Features

Login is a simple role picker (no real password check) — pick a role to see that part of the app.

**Field agent** — draws a farm's boundary on a satellite map, walks it with GPS, or drops a
single point for a plot 4 hectares or smaller (as EUDR allows), with live area/perimeter/
self-intersection checks. Records the farmer's consent before saving. Drafts save offline and
sync once back online. Every boundary edit keeps the old version — nothing is overwritten.

**Brand admin** — every farm is screened against satellite data for tree-cover loss, vegetation
change and protected-area overlap, producing a 0–100 risk score. Flagged farms go to a review
queue: clear a false positive or confirm and exclude it, permanently on record. A dashboard shows
portfolio KPIs and risk distribution, plus a queue of batches awaiting approval. An **overview
map** colours every farm by risk and follows the same filter as the table, and **bulk screening**
checks many unscreened farms in one background job — with live progress, ETA and per-farm results
— instead of clicking each farm one at a time.**

**Warehouse operator** — bundles verified farms into a shipment batch for a buyer. Each farm has
a maximum it can ship per season (area × estimated yield); a batch that would exceed it is
submitted for a Brand admin's approval (with a note) instead of being blocked, and can be
cancelled before it's decided. Once approved, every bag gets a unique QR-coded ID, printable as
sticker labels, plus one-click PDF/GeoJSON exports and a **due-diligence statement (DDS) as
JSON** — commodity, quantities, plot geolocations, screening evidence, the risk conclusion and a
SHA-256 integrity hash, ready to hand to a buyer or auditor.

**Buyer & the public** — anyone can scan a bag's QR code (no login) to see its deforestation-free
verdict. The buyer-facing shipment PDF shows only what they need, with no farmer names.

Below a phone-sized screen, the layout switches to a bottom tab bar (Overview, Capture, Farms,
Batches, Account) so the whole workflow works in the field.

## How it works

1. A **field agent** maps a farm's boundary (draw, GPS walk, or a point for a small plot) and
   records the farmer's consent.
2. The platform screens it against **Google Earth Engine**: forest loss after the cutoff date,
   vegetation change, and protected-area overlap — producing a 0–100 risk score.
3. A clean farm is auto-marked **Verified**; a flagged one goes to a **Brand admin**'s review
   queue to be cleared or excluded.
4. A **warehouse operator** bundles Verified farms into a shipment batch. A contribution
   exceeding a farm's remaining seasonal capacity is submitted for the admin's approval instead.
5. Once approved, the platform mints a QR-coded ID per bag and generates the evidence PDFs, the
   EUDR geolocation export and a JSON due-diligence statement (DDS) covering the whole batch.
6. Anyone downstream — the **buyer**, a customs officer, an auditor — scans a bag's QR code and
   sees its verdict, no login required.

## Roles & permissions

Login has no real password check, so anyone can switch roles freely. This table is what each
role is *meant* to do; only the row marked **server-enforced** is actually checked by the backend.

| Role | What they do | Enforcement |
| --- | --- | --- |
| **Field agent** | Register farms, capture boundaries, record consent, run satellite checks | UI only |
| **Brand admin** | Review flagged farms; approve/reject pending batches; view the dashboard | Approve/reject is **server-enforced**; review is UI only |
| **Warehouse operator** | Create batches, submit/cancel over-capacity requests, print labels, export PDFs | UI only |
| **Buyer & public** | Scan a bag's QR code, view its public page, receive shipment PDFs | No login at all |

## For developers

### Local setup

Needs Python 3.12+ and Node 20+ (a Google Earth Engine account is optional — everything still
works in MOCK mode without one).

```bash
# Backend (macOS / Linux)
cd backend && python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && cp .env.example .env

# Backend (Windows, PowerShell) -- venv activation differs
cd backend; python -m venv .venv; .venv\Scripts\Activate.ps1
pip install -r requirements.txt; copy .env.example .env

# Frontend (any OS, new terminal)
cd frontend && npm install
```

**Always activate and use `backend/.venv`** for backend commands — the system Python doesn't
have the required packages.

### Running it

```bash
# Backend -- API on http://localhost:8000 (docs at /docs)
cd backend && source .venv/bin/activate   # or .venv\Scripts\Activate.ps1 on Windows
python -m uvicorn app.main:app --reload --reload-dir app

# Frontend -- web app on http://localhost:5173
cd frontend && npm run dev
```

**Phone testing** ("Walk the boundary" needs a real device + HTTPS): run
`npm run dev:phone` instead, open the printed `https://192.168.x.x:5173` address on a phone on
the same Wi-Fi, accept the self-signed certificate warning, and set `FRONTEND_URL` in
`backend/.env` to that address if you need its QR codes to point at the phone.

**Seed demo data:**
```bash
cd backend && source .venv/bin/activate && python -m app.seed --reset --verify
```
Adds demo farms, screens them against Earth Engine, and bundles some into demo shipment batches
(including one pending approval).

**Tests:**
```bash
cd backend && source .venv/bin/activate && python -m pytest -v
```
Never touches the real database or calls Earth Engine — `tests/conftest.py` points the app at a
throwaway temporary database and mocks the Earth Engine call.

### Bulk screening & DDS endpoints

| Method & path | What it does |
| --- | --- |
| `POST /api/bulk/verify` | Start a background screening job for a list of farm IDs |
| `GET /api/bulk/{job_id}` | Job status, progress, ETA and per-farm results |
| `GET /api/bulk/{job_id}/summary` | Roll-up: counts per risk level plus flagged farms |
| `GET /api/batches/{id}/dds` | EUDR due-diligence statement as JSON (Approved batches only) |

### Project structure

```
backend/app/
  main.py          API routes
  models.py        database tables (Farm, Batch, Bag, Review, BatchEvent, ...)
  bulk_verify.py   background bulk screening (thread pool, in-process job progress)
  dds.py           builds the EUDR due-diligence statement (JSON) from a batch
  gee_service.py   Earth Engine satellite screening
  scoring.py       turns screening results into a 0-100 risk score
  geo.py           polygon area/validation math
  report.py        PDF generation (ReportLab)
  seed.py          demo data loader
  config.py        settings (reads backend/.env)
backend/tests/     pytest suite
frontend/src/
  pages/           one file per screen (Capture, Dashboard, Batches, BagPage, ...)
  components/      shared pieces (maps, risk badges)
  api.js           API client + offline draft queue
docker-compose.yml runs both halves in containers
```

### Settings (`backend/.env`, see `.env.example`)

| Key | Meaning |
| --- | --- |
| `GEE_PROJECT` | Your Earth Engine-registered Google Cloud project ID |
| `USE_MOCK_GEE` | `true` = never call Earth Engine; always return a fake, deterministic result |
| `ALLOW_MOCK_FALLBACK` | If a real Earth Engine call fails, fall back to a labelled MOCK result |
| `HANSEN_ASSET` | Which version of the Hansen Global Forest Change dataset to use |
| `CUTOFF_YEAR` | The EUDR deforestation cutoff year (2020) |
| `FRONTEND_URL` | The frontend's address, used to build QR codes and links in PDFs |

Docker needs none of these set — `backend/.env.example` (committed) supplies working defaults;
create `backend/.env` only to change something, such as your own `GEE_PROJECT`.

### Risk score (0–100)

| Factor | Points |
| --- | --- |
| Tree-cover loss after the cutoff date | up to 60 (5% of the farm lost = 60) |
| Protected-area overlap | 25 |
| Vegetation (NDVI) drop vs. the cutoff-year baseline | up to 15 |
| Too little clear satellite imagery to be sure | 10 |

**Low** < 30, **Medium** 30–59, **High** 60+. Any detected loss ≥ 0.1 ha is automatically at
least Medium. Anything that isn't Low, or where the data was inconclusive, goes to review.

## Demo script (≈6 min)

Re-seed right before recording (`python -m app.seed --reset --verify`) so the numbers match. If
Earth Engine isn't available, say up front that the badge reads MOCK and the results are simulated.

| Time | Beat | What to say / do |
| --- | --- | --- |
| 0:00 | Login | Open `http://localhost:5173`. *"A deforestation-free traceability platform for cocoa. Login is a role picker, so we can jump straight into each part."* Pick **Brand admin**. |
| 0:20 | Portfolio | Point at the KPI cards and distribution bar. *"Every farm is screened against satellite data and scored 0–100. Eight farms, two need attention."* Scroll to **Risk overview map**: *"Same portfolio on the map — green low, yellow medium, red high. Click a plot to open it; the map follows the filter in the table, so filtering to High isolates exactly the risky plots."* |
| 1:10 | Bulk screening | Click **Run bulk screening**, keep the unscreened farms selected, start. *"Field teams register farms faster than they can be checked one by one. This runs the satellite check in the background, three at a time, with progress, ETA and per-farm results — and closing the window doesn't cancel the job."* Let it finish, point at the Low/Medium/High summary. |
| 2:10 | Review queue | Open a flagged farm, show its evidence PDF, then clear or confirm it. *"Anything not Low goes to manual review; both decisions stay permanently on record."* |
| 2:50 | Batch + DDS | Go to **Batches** → `BATCH-2026-001`. *"Verified farms are bundled into a shipment; each contribution is capped at the farm's seasonal yield, and over-capacity batches go to the admin for approval."* Click **Export DDS (JSON)**. *"This is the due-diligence statement a buyer or regulator receives: statement ID, commodity and quantity, each plot's geolocation, screening evidence, risk conclusion and mitigation steps, plus a SHA-256 hash over the whole document. It only issues while every farm is still Verified — if one regresses the export refuses with a 409."* |
| 4:20 | Traceability | Open a bag's QR link. *"Anyone downstream scans a bag and sees its verdict — no login, no farmer names, just the evidence."* Then show the buyer-facing shipment PDF. |
| 5:10 | Close | *"That's the full chain: capture with consent, satellite screening, human review, shipment batching, and a due-diligence statement. It's a course MVP, so real authentication, hosting and direct TRACES submission are next."* |

If the demo machine has no Docker, run the two dev servers from **For developers → Running it**
instead and open `http://localhost:5173`.

## Contributing

- One GitHub issue per change, one branch per issue.
- Open a pull request whose description includes `Closes #<issue number>`.
- All three CI checks (backend tests, frontend build, Docker build) must pass before merging.
- `main` is protected — changes land through a reviewed pull request, not a direct push.

## Personal data & consent

A farm can't be saved without the farmer's consent, recorded by the field agent before capture: a
checkbox plus the agent's attestation and a date (the default), or a photo of a signed consent
form on top of that.

**What's stored:** the farmer's name, village, GPS boundary, the agent's name, and the consent
record (method, date, optional photo). Consent photos live as files on the server, are never
committed to the repository, and are only ever returned by an admin-only endpoint — never shown
in the farm list, the public bag page, or any PDF.

**Why:** the cooperative's own data-protection obligations and the EUDR both require farmer
consent before personal and location data is collected and shared, and that no more of it is
shared downstream than necessary.

**Who sees what:** internal pages (dashboard, farm/batch detail) show the full farmer name,
village and consent status. The public bag page and the buyer-facing shipment PDF show only the
farm code, name, region, status and the farmer's **initials** — never the full name, village or
consent record. The GPS boundary itself is still shown, since the EUDR requires it.

## Troubleshooting

- **`CERTIFICATE_VERIFY_FAILED`** (macOS) — run `open "/Applications/Python 3.12/Install Certificates.command"`.
- **"Please authorize access"** from Earth Engine — run `python -c "import ee; ee.Authenticate(force=True)"`.
- **A yellow "MOCK" badge** — Earth Engine couldn't be reached; expected until you set up access.
- **"Bad Gateway" / empty farm list in the first 2 min of `docker compose up`** — expected; the
  app starts immediately but demo farms are still being screened. Wait and refresh.
- **`ModuleNotFoundError: No module named '...'`** — you're not in `backend/.venv`; activate it
  from inside `backend/` first.
- **"Location access is blocked"** — your browser denied location for this site; allow it in
  site settings and reload.
- **"Address already in use"** — something's already listening on that port (8000, 5173, 8080);
  stop it, or run `docker compose down` if that's the cause.

## Limitations & roadmap

This is a course MVP, missing things a production version would need:

- **Real authentication** — login is a role picker, with no password check or session security.
- **Cloud hosting** — currently runs only on a laptop or in local Docker containers.
- **A full offline mobile app** — drafts save offline, but it isn't an installable native app.
- **Direct submission to the EU's TRACES system** — the GeoJSON and JSON due-diligence statement
  are EUDR-shaped but neither is wired up to file automatically.
- **Near-real-time deforestation alerts** — screening is on demand, not ongoing monitoring.
- **Other commodities** — built around cocoa; other EUDR commodities (coffee, palm oil, rubber,
  timber, soy, cattle) aren't supported yet.

## AI Usage Declaration

This project was built with AI assistance from two tools: **Claude** (Anthropic's claude.ai chat) for planning, requirements analysis against the client interview and charter, stack and architecture choice, the initial project scaffold, and reviewing outputs (PDFs, screenshots, UI); and **Claude Code** for most implementation, debugging, tests, and documentation. Both run Claude models (Anthropic).

| Area | AI's role | Student's role |
| --- | --- | --- |
| Requirements & scope | Claude (chat) analyzed the client interview and charter to surface requirements and open questions | Conducted the client interview with the group, set scope and priorities, and made the final decision on each ambiguity and change request |
| Architecture & stack | Claude (chat) gave input on the stack and generated the initial project scaffold | Chose the stack (largely the one taught in class) and adapted the scaffold to the course deliverables |
| Code (backend, frontend) | Claude Code wrote most of the backend and frontend implementation | Directed each feature, reviewed and edited every diff before committing |
| Tests | Claude proposed test cases; Claude Code wrote the pytest suite | Approved the test scope and ran the suite before every commit |
| PDFs & UI | Claude Code built the PDF report/label layouts and UI components; Claude (chat) reviewed rendered PDFs and screenshots | Chose the visual style and approved every layout and screenshot before use |
| Earth Engine setup | Claude Code wrote the integration code in `gee_service.py` | Registered the GEE Cloud project, ran authentication, and verified real vs. mock results |
| Testing & QA | Claude Code ran automated test suites and build checks after each change | Performed all manual testing — desktop, phone, and offline — before accepting any change |
| Demo | Claude (chat) suggested a demo outline and recording checklist | Planned, recorded and presented the demo in their own words |

All satellite results are real Google Earth Engine data. Demo farm boundaries are illustrative, placed at real locations in Ghana's Western Region. Every AI-generated change was reviewed and tested by the student before committing.
