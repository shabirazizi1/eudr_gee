"""F5 – one-click PDF evidence report for buyers."""
import io
import json

from reportlab.graphics.shapes import Drawing, Polygon, Rect
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Image as RLImage
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import gee_service

GREEN = colors.HexColor("#2f5d34")
LEVEL_COLORS = {"Low": "#2f7d3a", "Medium": "#b8860b", "High": "#b23b2e"}


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
    facts = [
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
    rows = [["Data source", "Observation window", "Resolution"]] + [
        [d["name"], d["window"], d["resolution"]] for d in res.get("datasets", [])]
    mt = Table(rows, colWidths=[80 * mm, 65 * mm, 25 * mm])
    mt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2e8")),
                            ("FONTSIZE", (0, 0), (-1, -1), 8),
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
    els.append(Paragraph(
        "Satellite screening is decision support, not EUDR certification or a legal determination. "
        "Small-scale change and agroforestry may be missed. Confirm land rights, legality and "
        "supply-chain evidence before due-diligence approval.", small))

    doc.build(els)
    return buf.getvalue()
