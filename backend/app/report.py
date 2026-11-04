"""F5 – one-click PDF evidence report for buyers."""
import io
import json

import qrcode
from reportlab.graphics.shapes import Drawing, Polygon, Rect
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import config, gee_service

GREEN = colors.HexColor("#2f5d34")
LEVEL_COLORS = {"Low": "#2f7d3a", "Medium": "#b8860b", "High": "#b23b2e"}
DISCLAIMER = (
    "Satellite screening is decision support, not EUDR certification or a legal determination. "
    "Small-scale change and agroforestry may be missed. Confirm land rights, legality and "
    "supply-chain evidence before due-diligence approval."
)


def _boundary_drawing(geojson: dict, size=60 * mm) -> Drawing:
    ring = geojson["coordinates"][0]
    xs, ys = [p[0] for p in ring], [p[1] for p in ring]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1e-6
    pad = 6
    scale = (size - 2 * pad) / span
    pts = []
    for x, y in ring:
        pts += [pad + (x - min(xs)) * scale, pad + (y - min(ys)) * scale]
    d = Drawing(size, size)
    d.add(Rect(0, 0, size, size, fillColor=colors.HexColor("#eef2e8"), strokeColor=None))
    d.add(Polygon(pts, fillColor=colors.HexColor("#b9d7a8"), strokeColor=GREEN, strokeWidth=1.5))
    return d


def _swatch(hex_color: str, size=8) -> Drawing:
    d = Drawing(size, size)
    d.add(Rect(0, 0, size, size, fillColor=colors.HexColor(hex_color), strokeColor=colors.grey, strokeWidth=0.5))
    return d


def _map_flowable(geo: dict, size=66 * mm):
    """Real GEE satellite map when available, else the plain polygon drawing."""
    try:
        png = gee_service.satellite_thumbnail(geo)
        img = RLImage(io.BytesIO(png))
        ratio = img.imageHeight / img.imageWidth
        img.drawWidth = size
        img.drawHeight = size * ratio
        return img, True
    except Exception:
        return _boundary_drawing(geo, size=size), False


def build_pdf(farm, verification) -> bytes:
    res = json.loads(verification.result_json)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=f"Evidence report {farm.code}",
                            leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm)
    ss = getSampleStyleSheet()
    h1, h2, body = ss["Title"], ss["Heading2"], ss["BodyText"]
    small = ss["BodyText"].clone("small", fontSize=8, leading=10, textColor=colors.grey)
    els = []

    els.append(Paragraph("Deforestation-Free Evidence Report", h1))
    els.append(Paragraph(
        f"{farm.name} · {farm.code} · {farm.farmer_name} · {farm.village}, {farm.region}", body))
    els.append(Paragraph(
        f"Run {verification.run_id} · {verification.created_at:%d %b %Y %H:%M} UTC · "
        f"Data source: {verification.source.upper()}", small))
    els.append(Spacer(1, 8))

    color = LEVEL_COLORS.get(verification.level, "#333333")
    els.append(Paragraph(
        f'<font color="{color}"><b>{verification.level} risk — score {verification.score}/100</b></font>'
        f" · Status: {farm.status}", ss["Heading3"]))
    if res.get("flags"):
        for f in res["flags"]:
            els.append(Paragraph(f"• {f}", body))
    else:
        els.append(Paragraph(
            "No post-2020 tree-cover loss, protected-area overlap or vegetation decline detected.", body))
    els.append(Spacer(1, 8))

    geo = json.loads(farm.geometry)
    if farm.capture_method == "gps_walk" and farm.gps_accuracy_m is not None:
        accuracy_label = f"±{farm.gps_accuracy_m:.1f} m avg"
        if farm.flagged_points:
            accuracy_label += f" · {farm.flagged_points} flagged point(s)"
    else:
        accuracy_label = "–"
    facts = [
        ["Capture method", "GPS walk" if farm.capture_method == "gps_walk" else "Drawn on map"],
        ["GPS accuracy", accuracy_label],
        ["Area", f"{farm.area_ha:.2f} ha"],
        ["Perimeter", f"{farm.perimeter_m:.0f} m"],
        ["Vertices", str(farm.vertices)],
        ["Forest-loss overlap", f"{res['loss_ha']:.2f} ha"],
        ["Protected areas", ", ".join(res.get("protected_areas") or []) or "None detected"],
        ["NDVI (baseline → now)", f"{res.get('ndvi_baseline')} → {res.get('ndvi_current')}"],
        ["Usable imagery", f"{res.get('coverage_pct', 0):.0f}% of farm"],
    ]
    t = Table(facts, colWidths=[40 * mm, 60 * mm])
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9),
                           ("TEXTCOLOR", (0, 0), (0, -1), colors.grey),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    map_img, is_real_map = _map_flowable(geo)
    els.append(Table([[map_img, t]], colWidths=[66 * mm, 104 * mm]))
    if is_real_map:
        legend = Table([[
            _swatch("#ffe034"), Paragraph("Farm boundary", small),
            _swatch("#e0453a"), Paragraph("Tree-cover loss after 2020", small),
            Paragraph(f"Source: Sentinel-2 / Hansen GFC (Google Earth Engine) · "
                      f"{verification.created_at:%d %b %Y}", small),
        ]], colWidths=[10, 32 * mm, 10, 48 * mm, 70 * mm])
        legend.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                                    ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
        els.append(legend)
    els.append(Spacer(1, 8))

    els.append(Paragraph("Score breakdown", h2))
    rows = [["Factor", "Points", "Detail"]] + [
        [b["factor"], str(b["points"]), b["detail"]] for b in res.get("breakdown", [])]
    bt = Table(rows, colWidths=[55 * mm, 20 * mm, 95 * mm])
    bt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), GREEN),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("FONTSIZE", (0, 0), (-1, -1), 9),
                            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    els.append(bt)
    els.append(Spacer(1, 8))

    els.append(Paragraph("Evidence & methodology", h2))
    cell = small.clone("cell", textColor=colors.black)
    head = cell.clone("cellhead", fontName="Helvetica-Bold")
    rows = [[Paragraph(h, head) for h in ("Data source", "Observation window", "Resolution")]] + [
        [Paragraph(d["name"], cell), Paragraph(d["window"], cell), Paragraph(d["resolution"], cell)]
        for d in res.get("datasets", [])]
    mt = Table(rows, colWidths=[80 * mm, 65 * mm, 25 * mm])
    mt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2e8")),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    els.append(mt)
    els.append(Spacer(1, 8))

    els.append(Paragraph("Boundary coordinates (WGS84)", h2))
    coords = geo["coordinates"][0][:-1] if geo["coordinates"][0][0] == geo["coordinates"][0][-1] \
        else geo["coordinates"][0]
    crow = [["#", "Latitude", "Longitude"]] + [
        [str(i + 1), f"{lat:.6f}", f"{lon:.6f}"] for i, (lon, lat) in enumerate(coords)]
    ct = Table(crow, colWidths=[12 * mm, 40 * mm, 40 * mm])
    ct.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 8),
                            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    els.append(ct)
    els.append(Spacer(1, 10))
    els.append(Paragraph(DISCLAIMER, small))

    doc.build(els)
    return buf.getvalue()


# ---------------------------------------------------------------- batch (shipment) report

def _combined_boundary_drawing(farms: list, size=70 * mm) -> Drawing:
    """All contributing farm boundaries on one simple map, same style as `_boundary_drawing`."""
    rings = [f["geometry"]["coordinates"][0] for f in farms if f.get("geometry")]
    d = Drawing(size, size)
    d.add(Rect(0, 0, size, size, fillColor=colors.HexColor("#eef2e8"), strokeColor=None))
    if not rings:
        return d
    xs = [p[0] for ring in rings for p in ring]
    ys = [p[1] for ring in rings for p in ring]
    span = max(max(xs) - min(xs), max(ys) - min(ys)) or 1e-6
    pad = 6
    scale = (size - 2 * pad) / span
    minx, miny = min(xs), min(ys)
    for ring in rings:
        pts = []
        for x, y in ring:
            pts += [pad + (x - minx) * scale, pad + (y - miny) * scale]
        d.add(Polygon(pts, fillColor=colors.HexColor("#b9d7a8"), strokeColor=GREEN, strokeWidth=1.2))
    return d


def _qr_drawing(url: str, size=32 * mm) -> RLImage:
    img = qrcode.make(url, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    rl_img = RLImage(buf)
    rl_img.drawWidth = size
    rl_img.drawHeight = size
    return rl_img


def build_batch_pdf(batch, data: dict) -> bytes:
    """`data` is the batch_out() dict: contributions carry the farm_out() payload for each farm."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=f"Shipment compliance {batch.code}",
                            leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm)
    ss = getSampleStyleSheet()
    h1, h2, body = ss["Title"], ss["Heading2"], ss["BodyText"]
    small = ss["BodyText"].clone("bsmall", fontSize=8, leading=10, textColor=colors.grey)
    cell = small.clone("bcell", textColor=colors.black)
    head = cell.clone("bcellhead", fontName="Helvetica-Bold")
    els = []

    els.append(Paragraph("Shipment Compliance Package", h1))
    els.append(Paragraph(f"Batch {batch.code} &middot; Buyer: {batch.buyer}", body))
    created_line = f"Created {batch.created_at:%d %b %Y %H:%M} UTC"
    if batch.created_by:
        created_line += f" by {batch.created_by}"
    els.append(Paragraph(created_line, small))
    els.append(Spacer(1, 8))

    totals = data["totals"]
    facts = [
        ["Contributing farms", str(totals["farms"])],
        ["Total bags", str(totals["bags"])],
        ["Total weight", f"{totals['kg']:.0f} kg"],
        ["Total mapped area", f"{totals['area_ha']:.2f} ha"],
    ]
    t = Table(facts, colWidths=[36 * mm, 26 * mm])  # must fit inside the 66mm outer column below
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9),
                           ("TEXTCOLOR", (0, 0), (0, -1), colors.grey),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))

    farms = [c["farm"] for c in data["contributions"] if c.get("farm")]
    combined_map = _combined_boundary_drawing(farms, size=64 * mm)
    qr = _qr_drawing(f"{config.FRONTEND_URL}/batches/{batch.id}")
    qr_block = Table([[qr], [Paragraph("Scan for this batch's traceability record", small)]],
                      colWidths=[32 * mm])
    qr_block.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER")]))

    top = Table([[combined_map, t, qr_block]], colWidths=[66 * mm, 66 * mm, 40 * mm])
    top.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    els.append(top)
    els.append(Spacer(1, 10))

    els.append(Paragraph("Contributing farms", h2))
    rows = [[Paragraph(h, head) for h in
             ("Code", "Farmer", "Area", "Risk score", "Capture method", "Verification run · date")]]
    for c in data["contributions"]:
        f = c["farm"]
        v = f.get("verification")
        capture = "GPS walk" if f.get("capture_method") == "gps_walk" else "Drawn on map"
        risk = f"{v['score']} ({v['level']})" if v else "Not screened"
        run = f"{v['run_id']} · {v['created_at'][:10]}" if v else "–"
        rows.append([
            Paragraph(f["code"], cell), Paragraph(f["farmer_name"], cell),
            Paragraph(f"{f['area_ha']:.2f} ha", cell), Paragraph(risk, cell),
            Paragraph(capture, cell), Paragraph(run, cell),
        ])
    ft = Table(rows, colWidths=[20 * mm, 30 * mm, 16 * mm, 24 * mm, 26 * mm, 54 * mm])
    ft.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), GREEN),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    els.append(ft)
    els.append(Spacer(1, 10))
    els.append(Paragraph(DISCLAIMER, small))

    doc.build(els)
    return buf.getvalue()
