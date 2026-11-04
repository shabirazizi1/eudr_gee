# eudr_gee – Deforestation-free cocoa traceability (MVP)

Demo features:
- **F2 Farm registry & GPS boundary capture** – draw a farm on a satellite map, live area/perimeter/vertex checks, offline drafts that sync later.
- **F3 GEE satellite verification** – screens the polygon with Google Earth Engine (Hansen forest loss after 31 Dec 2020, Sentinel-2 NDVI, WDPA protected areas) and returns a 0–100 risk score.
- **F5 Evidence dashboard** – portfolio KPIs, risk distribution, review queue, one-click PDF evidence report.
- F1 (simplified) – role picker login (no real auth).

```
backend/   FastAPI + SQLite + earthengine-api + ReportLab
frontend/  React (Vite) + Leaflet + Geoman
```

## 1. First-time setup (Mac)

Needs Python 3.11+, Node 18+ (`brew install node`), and the Earth Engine login you already did.

```bash
# Backend
cd eudr_gee/backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # already set to project eudr-gee-513515

# Frontend (new Terminal tab)
cd eudr_gee/frontend
npm install
```

## 2. Test Earth Engine (once)

```bash
cd eudr_gee/backend && source .venv/bin/activate
python -m app.check_gee
```

You should see real numbers (`"source": "gee"`). If you get an asset error for Hansen, change
`HANSEN_ASSET` in `.env` to the newest version listed in the Earth Engine data catalog.

## 3. Load demo farms

```bash
python -m app.seed --reset --verify   # 5 farms near Asankrangwa, screened with Earth Engine
```

## 4. Run the app (two Terminal tabs)

```bash
# Tab 1 – API on http://localhost:8000  (docs at /docs)
cd eudr_gee/backend && source .venv/bin/activate
uvicorn app.main:app --reload

# Tab 2 – web app on http://localhost:5173
cd eudr_gee/frontend
npm run dev
```

Open http://localhost:5173.

## 5. Demo script (≈5 min)

1. **Login** – pick *Field agent* → sign in.
2. **Capture (F2)** – click the polygon tool, draw a farm, fill farm + farmer name. Show live hectares, vertices,
   "valid geometry". Optional: turn Wi-Fi off, save → "saved on this device"; Wi-Fi on → it syncs.
3. **Verify (F3)** – *Save & run satellite check* → result page with score, forest-loss overlay (red pixels),
   NDVI change, protected areas, methodology.
4. **Dashboard (F5)** – sign out, log in as *Brand admin* → KPIs, risk distribution, queue → *Export evidence* PDF.

## Settings (`backend/.env`)

| Key | Meaning |
| --- | --- |
| `GEE_PROJECT` | Earth Engine Cloud project ID |
| `USE_MOCK_GEE` | `true` = never call Earth Engine (fake results, labelled MOCK) |
| `ALLOW_MOCK_FALLBACK` | if Earth Engine errors, show a labelled MOCK result instead of failing |
| `HANSEN_ASSET` | Hansen Global Forest Change version |
| `CUTOFF_YEAR` | EUDR cutoff year (2020) |

## Risk score (0–100)

| Factor | Points |
| --- | --- |
| Tree-cover loss after cutoff | up to 60 (5% of farm lost = 60) |
| Protected-area overlap | 25 |
| NDVI drop vs. 2020 baseline | up to 15 |
| Too little clear imagery | 10 |

Low < 30 ≤ Medium < 60 ≤ High. Any loss ≥ 0.1 ha is at least Medium. Anything not Low, or with
missing data, goes to **Under review**.

## Troubleshooting

- `CERTIFICATE_VERIFY_FAILED` → run `open "/Applications/Python 3.12/Install Certificates.command"`.
- `Please authorize access` → `python -c "import ee; ee.Authenticate(force=True)"`.
- Result page shows a yellow **MOCK** banner → Earth Engine failed; the error text is in the banner.
