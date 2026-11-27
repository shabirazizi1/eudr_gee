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