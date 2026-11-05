"""F5 – one-click PDF evidence report for buyers."""
import io
import json
from datetime import date

import qrcode
from reportlab.graphics.shapes import Drawing, Polygon, Rect
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import Image as RLImage
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from . import config, gee_service

GREEN = colors.HexColor("#2f5d34")
LEVEL_COLORS = {"Low": "#2f7d3a", "Medium": "#b8860b", "High": "#b23b2e"}
DISCLAIMER_LINES = (
    "Satellite screening is decision support, not EUDR certification or a legal determination.",
    "Small-scale change and agroforestry may be missed. Confirm land rights, legality and "
    "supply-chain evidence before due-diligence approval.",
)
COMMODITY = "Cocoa beans (HS 1801)"
COUNTRY_OF_PRODUCTION = "Ghana"
EXPORTER = "Akokoaso Cocoa Cooperative"

PAGE_MARGIN = 18 * mm
CONTENT_WIDTH = A4[0] - 2 * PAGE_MARGIN


# ---------------------------------------------------------------- page chrome (header/footer)

def _make_canvas(title: str, code: str):
    """A canvasmaker that stamps every page with a title/code header and a disclaimer +
    'Page X of Y' footer, once the total page count is known (the classic ReportLab
    buffered-canvas pattern -- `showPage` defers drawing until `save` sees every page).
    """
    generated = f"{date.today():%d %b %Y}"

    class _ChromeCanvas(pdfcanvas.Canvas):
        def __init__(self, *args, **kwargs):
            pdfcanvas.Canvas.__init__(self, *args, **kwargs)
            self._saved_page_states = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._saved_page_states)
            for state in self._saved_page_states:
                self.__dict__.update(state)
                self._draw_chrome(total)
                pdfcanvas.Canvas.showPage(self)
            pdfcanvas.Canvas.save(self)

        def _draw_chrome(self, total):
            w, h = A4
            self.setFont("Helvetica-Bold", 9)
            self.setFillColor(GREEN)
            self.drawString(PAGE_MARGIN, h - 12 * mm, title)
            self.setFont("Helvetica", 9)
            self.setFillColor(colors.grey)
            self.drawRightString(w - PAGE_MARGIN, h - 12 * mm, code)
            self.setStrokeColor(colors.HexColor("#e6dfd2"))
            self.setLineWidth(0.5)
            self.line(PAGE_MARGIN, h - 14 * mm, w - PAGE_MARGIN, h - 14 * mm)

            self.setStrokeColor(colors.HexColor("#e6dfd2"))
            self.line(PAGE_MARGIN, 18 * mm, w - PAGE_MARGIN, 18 * mm)
            self.setFont("Helvetica", 7)
            self.setFillColor(colors.grey)
            self.drawString(PAGE_MARGIN, 14.5 * mm, DISCLAIMER_LINES[0])
            self.drawString(PAGE_MARGIN, 11.5 * mm, DISCLAIMER_LINES[1])
            self.drawRightString(w - PAGE_MARGIN, 8 * mm,
                                  f"Page {self._pageNumber} of {total} · Generated {generated}")

    return _ChromeCanvas


# ---------------------------------------------------------------- shared building blocks
# Used by both the single-farm evidence report and the batch per-farm appendix pages below.

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


def _place(village: str, region: str) -> str:
    """Join village + region, dropping either when blank so a farm with no village recorded
    doesn't print a stray leading comma (e.g. ", Western Region")."""
    return ", ".join(p for p in (village, region) if p)


def _swatch(hex_color: str, size=8) -> Drawing:
    d = Drawing(size, size)
    d.add(Rect(0, 0, size, size, fillColor=colors.HexColor(hex_color), strokeColor=colors.grey, strokeWidth=0.5))
    return d


def _fit_image(png_bytes: bytes, max_w: float, max_h: float) -> RLImage:
    """Scale an image to fit within a box, preserving aspect ratio -- used so a map of any
    aspect ratio (farm shape, or a batch's farm spread) never overflows its page."""
    img = RLImage(io.BytesIO(png_bytes))
    ratio = img.imageHeight / img.imageWidth
    w, h = max_w, max_w * ratio
    if h > max_h:
        h = max_h
        w = max_h / ratio
    img.drawWidth = w
    img.drawHeight = h
    return img


def _map_pair_flowable(geo: dict, caption_style, cell_w=38 * mm, max_h=48 * mm):
    """Side-by-side farm maps: sharp Esri imagery (can be cloudy) and a cloud-free Sentinel-2
    composite, each with the boundary + loss overlay, so a cloudy Esri capture doesn't hide the
    farm. Falls back to the plain polygon drawing if both sources fail.
    Returns (flowable, has_real_image, legend_source).
    """
    try:
        pair = gee_service.satellite_thumbnail_pair(geo)
    except Exception:
        pair = {}
    imgs, caps, sources = [], [], []
    if pair.get("esri"):
        imgs.append(_fit_image(pair["esri"], cell_w, max_h))
        caps.append(Paragraph("High-res context (Esri)", caption_style))
        sources.append("esri")
    if pair.get("sentinel2"):
        imgs.append(_fit_image(pair["sentinel2"], cell_w, max_h))
        caps.append(Paragraph("Cloud-free composite (Sentinel-2, last 12 months)", caption_style))
        sources.append("sentinel2")
    if not imgs:
        return _boundary_drawing(geo, size=cell_w * 2), False, None
    gallery = Table([imgs, caps], colWidths=[cell_w] * len(imgs))
    gallery.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                 ("TOPPADDING", (0, 1), (-1, 1), 2),
                                 ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                 ("RIGHTPADDING", (0, 0), (-1, -1), 4)]))
    legend_source = "both" if len(sources) == 2 else sources[0]
    return gallery, True, legend_source


CAPTIONS = {
    "esri": "Context imagery © Esri, Maxar · Loss: Hansen GFC (Earth Engine)",
    "sentinel2": "Source: Sentinel-2 / Hansen GFC (Google Earth Engine)",
    "both": "Loss overlay: Hansen GFC (Earth Engine)",
}


def _legend_row(small_style, date_label: str, source="esri", widths=(32 * mm, 48 * mm, 70 * mm)):
    caption = f"{CAPTIONS.get(source, CAPTIONS['esri'])} · {date_label}"
    legend = Table([[
        _swatch("#ffe034"), Paragraph("Farm boundary", small_style),
        _swatch("#e0453a"), Paragraph("Tree-cover loss after 2020", small_style),
        Paragraph(caption, small_style),
    ]], colWidths=[10, widths[0], 10, widths[1], widths[2]])
    legend.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                                ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
    return legend


def _facts_table(farm: dict, res: dict, col_widths=(40 * mm, 60 * mm)) -> Table:
    """`farm` is a dict with the Farm model's fields (ORM `.model_dump()` or the API's farm_out()).
    NDVI values stay sourced from Sentinel-2 regardless of which imagery the map above uses."""
    if farm.get("capture_method") == "gps_walk" and farm.get("gps_accuracy_m") is not None:
        accuracy_label = f"±{farm['gps_accuracy_m']:.1f} m avg"
        if farm.get("flagged_points"):
            accuracy_label += f" · {farm['flagged_points']} flagged point(s)"
    else:
        accuracy_label = "–"
    facts = [
        ["Capture method", "GPS walk" if farm.get("capture_method") == "gps_walk" else "Drawn on map"],
        ["GPS accuracy", accuracy_label],
        ["Area", f"{farm['area_ha']:.2f} ha"],
        ["Perimeter", f"{farm['perimeter_m']:.0f} m"],
        ["Vertices", str(farm["vertices"])],
        ["Forest-loss overlap", f"{res.get('loss_ha', 0):.2f} ha"],
        ["Protected areas", ", ".join(res.get("protected_areas") or []) or "None detected"],
        ["NDVI (baseline → now)", f"{res.get('ndvi_baseline')} → {res.get('ndvi_current')}"],
        ["Usable imagery", f"{res.get('coverage_pct', 0):.0f}% of farm"],
    ]
    t = Table(facts, colWidths=list(col_widths))
    t.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9),
                           ("TEXTCOLOR", (0, 0), (0, -1), colors.grey),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                           ("LEFTPADDING", (0, 0), (-1, -1), 2),
                           ("RIGHTPADDING", (0, 0), (-1, -1), 2)]))
    return t


def _manual_review_table(reviews, cell_style, head_style, col_widths=(34 * mm, 26 * mm, 58 * mm, 26 * mm, 36 * mm)):
    """Returns a Table of the review history, or None if the farm has never been reviewed."""
    if not reviews:
        return None
    rows = [[Paragraph(h, head_style) for h in ("Decision", "Reason", "Note", "Reviewer", "Date")]]
    for r in reviews:
        decision_label = "Cleared — false positive" if r["decision"] == "clear" else "Confirmed — excluded"
        rows.append([
            Paragraph(decision_label, cell_style), Paragraph(r["reason"], cell_style),
            Paragraph(r["note"] or "–", cell_style), Paragraph(r["reviewer_name"] or "–", cell_style),
            Paragraph(r["created_at"][:16].replace("T", " "), cell_style),
        ])
    rt = Table(rows, colWidths=list(col_widths))
    rt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2e8")),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    return rt


def _boundary_table_compact(geo: dict, max_rows_per_group=6, max_groups=3,
                             col_widths=(8 * mm, 20 * mm, 20 * mm), gap=6 * mm) -> Table:
    """Boundary coordinates as 1-3 '#/Lat/Lon' groups side by side instead of one long column,
    so farms with up to ~12 points fit on the same page as everything above them."""
    coords = geo["coordinates"][0][:-1] if geo["coordinates"][0][0] == geo["coordinates"][0][-1] \
        else geo["coordinates"][0]
    n = len(coords)
    groups = min(max_groups, max(1, -(-n // max_rows_per_group)))
    rows_per_group = -(-n // groups)
    mini_style = TableStyle([("FONTSIZE", (0, 0), (-1, -1), 7.5),
                             ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                             ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                             ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                             ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)])
    minis = []
    for g in range(groups):
        chunk = coords[g * rows_per_group:(g + 1) * rows_per_group]
        if not chunk:
            continue
        rows = [["#", "Lat", "Lon"]] + [
            [str(g * rows_per_group + i + 1), f"{lat:.5f}", f"{lon:.5f}"]
            for i, (lon, lat) in enumerate(chunk)]
        t = Table(rows, colWidths=list(col_widths))
        t.setStyle(mini_style)
        minis.append(t)
    outer = Table([minis], colWidths=[sum(col_widths)] * len(minis))
    outer.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), gap)]))
    return outer


# ---------------------------------------------------------------- single-farm evidence report

def build_pdf(farm, verification, reviews=None) -> bytes:
    res = json.loads(verification.result_json)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=f"Evidence report {farm.code}",
                            leftMargin=PAGE_MARGIN, rightMargin=PAGE_MARGIN,
                            topMargin=22 * mm, bottomMargin=22 * mm)
    ss = getSampleStyleSheet()
    h1, h2, body = ss["Title"], ss["Heading2"], ss["BodyText"]
    small = ss["BodyText"].clone("small", fontSize=8, leading=10, textColor=colors.grey)
    cell = small.clone("cell", textColor=colors.black)
    head = cell.clone("cellhead", fontName="Helvetica-Bold")
    caption = small.clone("caption", fontSize=7, leading=9)
    els = []

    els.append(Paragraph("Deforestation-Free Evidence Report", h1))
    els.append(Paragraph(
        f"{farm.name} · {farm.code} · {farm.farmer_name} · {_place(farm.village, farm.region)}", body))
    els.append(Paragraph(
        f"Run {verification.run_id} · {verification.created_at:%d %b %Y %H:%M} UTC · "
        f"Data source: {verification.source.upper()}", small))
    els.append(Spacer(1, 5))

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
    els.append(Spacer(1, 5))

    geo = json.loads(farm.geometry)
    t = _facts_table(farm.model_dump(), res, col_widths=(38 * mm, 44 * mm))
    gallery, has_real_map, legend_source = _map_pair_flowable(geo, caption, cell_w=42 * mm, max_h=56 * mm)
    els.append(Table([[gallery, t]], colWidths=[86 * mm, 84 * mm]))
    if has_real_map:
        els.append(_legend_row(small, f"{verification.created_at:%d %b %Y}", legend_source))
    els.append(Spacer(1, 5))

    els.append(Paragraph("Score breakdown", h2))
    rows = [["Factor", "Points", "Detail"]] + [
        [b["factor"], str(b["points"]), b["detail"]] for b in res.get("breakdown", [])]
    bt = Table(rows, colWidths=[55 * mm, 20 * mm, 95 * mm])
    bt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), GREEN),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("FONTSIZE", (0, 0), (-1, -1), 9),
                            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    els.append(bt)
    els.append(Spacer(1, 5))

    review_table = _manual_review_table(reviews, cell, head)
    if review_table:
        els.append(Paragraph("Manual review", h2))
        els.append(review_table)
        els.append(Spacer(1, 5))

    els.append(Paragraph("Evidence & methodology", h2))
    rows = [[Paragraph(h, head) for h in ("Data source", "Observation window", "Resolution")]] + [
        [Paragraph(d["name"], cell), Paragraph(d["window"], cell), Paragraph(d["resolution"], cell)]
        for d in res.get("datasets", [])]
    mt = Table(rows, colWidths=[80 * mm, 65 * mm, 25 * mm])
    mt.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2e8")),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    els.append(mt)
    els.append(Spacer(1, 5))

    # KeepTogether: the boundary table moves to the next page as a whole rather than splitting
    els.append(KeepTogether([Paragraph("Boundary coordinates (WGS84)", h2), _boundary_table_compact(geo)]))

    doc.build(els, canvasmaker=_make_canvas("Deforestation-Free Evidence Report", farm.code))
    return buf.getvalue()


# ---------------------------------------------------------------- batch (shipment) report

def _combined_boundary_drawing(farms: list, size=70 * mm) -> Drawing:
    """All contributing farm boundaries on one simple map, same style as `_boundary_drawing`.
    Fallback for the batch overview map when Earth Engine is unavailable."""
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


def _batch_map_flowable(farms: list, max_w=CONTENT_WIDTH, max_h=210 * mm):
    """Real multi-farm sharp satellite overview when available, else the combined polygon
    drawing. Returns (flowable, is_real, source)."""
    try:
        payload = [{"geometry": f["geometry"], "code": f["code"]} for f in farms]
        png, source = gee_service.satellite_overview_thumbnail(payload)
        return _fit_image(png, max_w, max_h), True, source
    except Exception:
        return _combined_boundary_drawing(farms, size=min(max_w, max_h)), False, None


def _qr_drawing(url: str, size=32 * mm) -> RLImage:
    img = qrcode.make(url, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    rl_img = RLImage(buf)
    rl_img.drawWidth = size
    rl_img.drawHeight = size
    return rl_img


def _harvest_range(farms: list) -> str:
    dates = sorted({f.get("harvest_date") for f in farms if f.get("harvest_date")})
    if not dates:
        return "–"
    return dates[0] if dates[0] == dates[-1] else f"{dates[0]} – {dates[-1]}"


def _contrib_notes(f: dict) -> str:
    if f.get("status") != "Verified (manual review)":
        return ""
    lr = f.get("latest_review")
    if not lr:
        return "Manually cleared"
    return f"Cleared: {lr['reason']} — {lr.get('reviewer_name') or 'reviewer n/a'} ({lr['created_at'][:10]})"


def build_batch_pdf(batch, data: dict) -> bytes:
    """`data` is the batch_out() dict: contributions carry the farm_out() payload for each farm
    (including `reviews`/`latest_review`), so the per-farm appendix can reuse the single-farm
    report's helpers without re-querying anything.

    Page order: 1) title, compliance statement, shipment details + QR, contributing farms table;
    2) satellite overview (kept together, never split across pages); then one appendix page per farm.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, title=f"Shipment compliance {batch.code}",
                            leftMargin=PAGE_MARGIN, rightMargin=PAGE_MARGIN,
                            topMargin=22 * mm, bottomMargin=22 * mm)
    ss = getSampleStyleSheet()
    h1, h2, h3, body = ss["Title"], ss["Heading2"], ss["Heading3"], ss["BodyText"]
    small = ss["BodyText"].clone("bsmall", fontSize=8, leading=10, textColor=colors.grey)
    cell = small.clone("bcell", textColor=colors.black)
    head = cell.clone("bcellhead", fontName="Helvetica-Bold")
    caption = small.clone("bcaption", fontSize=7, leading=9)
    els = []

    contributions = data["contributions"]
    farms = [c["farm"] for c in contributions if c.get("farm")]
    totals = data["totals"]
    today_label = f"{date.today():%d %b %Y}"

    # ---- Page 1: title -------------------------------------------------
    els.append(Paragraph("Shipment Compliance Package", h1))
    els.append(Paragraph(f"Batch {batch.code} &middot; Buyer: {batch.buyer}", body))
    created_line = f"Created {batch.created_at:%d %b %Y %H:%M} UTC"
    if batch.created_by:
        created_line += f" by {batch.created_by}"
    els.append(Paragraph(created_line, small))
    els.append(Spacer(1, 8))

    # ---- Compliance statement -------------------------------------------------
    passed = sum(1 for f in farms if f.get("status") in ("Verified", "Verified (manual review)"))
    manually_cleared = sum(1 for f in farms if f.get("status") == "Verified (manual review)")
    statement = (
        f"All {passed} of {len(farms)} contributing farms passed satellite screening for "
        f"tree-cover loss after 31 Dec {config.CUTOFF_YEAR} as of {today_label}."
    )
    if manually_cleared:
        verb = "was" if manually_cleared == 1 else "were"
        plural = "" if manually_cleared == 1 else "s"
        statement += f" {manually_cleared} farm{plural} {verb} cleared by manual review."
    els.append(Paragraph(f'<font color="#2f5d34"><b>{statement}</b></font>', h3))
    els.append(Spacer(1, 10))

    # ---- Shipment details + QR -------------------------------------------------
    els.append(Paragraph("Shipment details", h2))
    regions = sorted({f.get("region") for f in farms if f.get("region")})
    details = [
        ["Commodity", COMMODITY],
        ["Country of production", COUNTRY_OF_PRODUCTION],
        ["Region", ", ".join(regions) or "–"],
        ["Quantity", f"{totals['bags']} bags · {totals['kg']:.0f} kg"],
        ["Harvest date range", _harvest_range(farms)],
        ["Cutoff date", f"31 Dec {config.CUTOFF_YEAR}"],
        ["Buyer", batch.buyer],
        ["Exporter", EXPORTER],
    ]
    dt = Table(details, colWidths=[42 * mm, 86 * mm])
    dt.setStyle(TableStyle([("FONTSIZE", (0, 0), (-1, -1), 9),
                            ("TEXTCOLOR", (0, 0), (0, -1), colors.grey),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    qr = _qr_drawing(f"{config.FRONTEND_URL}/batches/{batch.id}")
    qr_block = Table([[qr], [Paragraph("Scan for this batch's traceability record", small)]],
                      colWidths=[32 * mm])
    qr_block.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    details_row = Table([[dt, qr_block]], colWidths=[130 * mm, 40 * mm])
    details_row.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    els.append(details_row)
    els.append(Spacer(1, 10))

    # ---- Contributing farms -------------------------------------------------
    els.append(Paragraph("Contributing farms", h2))
    # A dedicated white-on-green header style: a Paragraph draws with its own textColor and
    # ignores the table's TEXTCOLOR command, which previously left this header black-on-green.
    head_white = head.clone("bcellheadwhite", textColor=colors.white)
    rows = [[Paragraph(h, head_white) for h in
             ("Code", "Farmer", "Area", "Risk score", "Capture method", "Status", "Verification run · date", "Notes")]]
    for c in contributions:
        f = c["farm"]
        if not f:
            continue
        v = f.get("verification")
        capture = "GPS walk" if f.get("capture_method") == "gps_walk" else "Drawn on map"
        risk = f"{v['score']} ({v['level']})" if v else "Not screened"
        run = f"{v['run_id']} · {v['created_at'][:10]}" if v else "–"
        rows.append([
            Paragraph(f["code"], cell), Paragraph(f["farmer_name"], cell),
            Paragraph(f"{f['area_ha']:.2f} ha", cell), Paragraph(risk, cell),
            Paragraph(capture, cell), Paragraph(f["status"], cell),
            Paragraph(run, cell), Paragraph(_contrib_notes(f), cell),
        ])
    ft = Table(rows, colWidths=[18 * mm, 20 * mm, 12 * mm, 16 * mm, 20 * mm, 20 * mm, 26 * mm, 38 * mm])
    ft.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), GREEN),
                            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                            ("FONTSIZE", (0, 0), (-1, 0), 8),
                            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                            ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey)]))
    els.append(ft)

    # ---- Page 2: satellite overview (kept together on its own page) -------------------------------------------------
    overview_img, is_real_overview, overview_source = _batch_map_flowable(farms)
    overview_block = [Paragraph("Satellite overview", h2), overview_img]
    if is_real_overview:
        overview_block.append(_legend_row(small, today_label, overview_source, widths=(32 * mm, 48 * mm, 60 * mm)))
    else:
        overview_block.append(Paragraph(
            "Earth Engine satellite imagery unavailable for this overview — showing farm boundaries only.",
            small))
    els.append(PageBreak())
    els.append(KeepTogether(overview_block))

    # ---- Per-farm appendix -------------------------------------------------
    for c in contributions:
        f = c["farm"]
        if not f:
            continue
        els.append(PageBreak())
        els.append(Paragraph(f"Appendix · {f['name']}", h1))
        els.append(Paragraph(
            f"{f['code']} · {f['farmer_name']} · {_place(f.get('village', ''), f.get('region', ''))}", body))

        res = f.get("verification") or {}
        if res:
            color = LEVEL_COLORS.get(res.get("level"), "#333333")
            els.append(Paragraph(
                f'<font color="{color}"><b>{res.get("level", "–")} risk — score {res.get("score", "–")}/100</b></font>'
                f" · Status: {f['status']}", h3))
        else:
            els.append(Paragraph(f"Status: {f['status']}", h3))
        els.append(Spacer(1, 5))

        appendix_facts = _facts_table(f, res, col_widths=(40 * mm, 48 * mm))
        gallery, has_real_map, legend_source = _map_pair_flowable(f["geometry"], caption,
                                                                   cell_w=34 * mm, max_h=42 * mm)
        els.append(Table([[gallery, appendix_facts]], colWidths=[74 * mm, 98 * mm]))
        if has_real_map:
            run_date = res.get("created_at", "")[:10] or today_label
            els.append(_legend_row(small, run_date, legend_source, widths=(28 * mm, 42 * mm, 56 * mm)))
        els.append(Spacer(1, 5))

        review_table = _manual_review_table(f.get("reviews"), cell, head)
        if review_table:
            els.append(Paragraph("Manual review", h2))
            els.append(review_table)
            els.append(Spacer(1, 5))

        # KeepTogether: the boundary table moves to the next page as a whole rather than splitting
        els.append(KeepTogether([Paragraph("Boundary coordinates (WGS84)", h2),
                                  _boundary_table_compact(f["geometry"])]))

    doc.build(els, canvasmaker=_make_canvas("Shipment Compliance Package", batch.code))
    return buf.getvalue()


# ---------------------------------------------------------------- bag labels (HLR-02: bag-level QR)

LABEL_COLS, LABEL_ROWS = 2, 4  # 8 labels per A4 page


def build_bag_labels_pdf(batch, bags: list) -> bytes:
    """A printable sheet of bag labels, ~8 per A4 page: each has a QR linking to the public
    `/bag/{bag_id}` page, the bag ID, farm code + name, batch code and weight.
    `bags` is a list of {"bag_id", "weight_kg", "farm_name", "farm_code", "batch_code"}.
    """
    buf = io.BytesIO()
    side_margin = 10 * mm
    # Top/bottom match the header+footer chrome band used elsewhere, so the label grid's
    # computed height can't run under the footer and get silently split across two PDF pages.
    doc = SimpleDocTemplate(buf, pagesize=A4, title=f"Bag labels {batch.code}",
                            leftMargin=side_margin, rightMargin=side_margin,
                            topMargin=22 * mm, bottomMargin=22 * mm)
    ss = getSampleStyleSheet()
    bag_id_style = ss["BodyText"].clone("bagid", fontSize=12, leading=14, fontName="Helvetica-Bold")
    farm_style = ss["BodyText"].clone("bagfarm", fontSize=9, leading=11)
    meta_style = ss["BodyText"].clone("bagmeta", fontSize=8, leading=10, textColor=colors.grey)

    cell_w = (A4[0] - 2 * side_margin) / LABEL_COLS

    def make_label(bag):
        """A single bordered label: QR + text, sized by its natural content height (not a
        forced row height) -- avoids ReportLab nested-table height quirks entirely."""
        qr = _qr_drawing(f"{config.FRONTEND_URL}/bag/{bag['bag_id']}", size=24 * mm)
        info = Table([
            [Paragraph(bag["bag_id"], bag_id_style)],
            [Paragraph(bag["farm_name"], farm_style)],
            [Paragraph(bag["farm_code"], meta_style)],
            [Paragraph(f"Batch {bag['batch_code']}", meta_style)],
            [Paragraph(f"{bag['weight_kg']:.0f} kg", meta_style)],
        ], colWidths=[cell_w - 42 * mm])
        info.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 0.5),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 0.5),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
        cell = Table([[qr, info]], colWidths=[26 * mm, cell_w - 26 * mm - 20 * mm])
        cell.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#cccccc")),
                                  ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                  ("LEFTPADDING", (0, 0), (0, 0), 8),
                                  ("RIGHTPADDING", (1, 0), (1, 0), 6),
                                  ("TOPPADDING", (0, 0), (-1, -1), 8),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
        return cell

    per_page = LABEL_COLS * LABEL_ROWS
    els = []
    for start in range(0, len(bags), per_page):
        page_bags = bags[start:start + per_page]
        rows = []
        for r in range(0, len(page_bags), LABEL_COLS):
            row = [make_label(b) for b in page_bags[r:r + LABEL_COLS]]
            while len(row) < LABEL_COLS:
                row.append("")
            rows.append(row)
        grid = Table(rows, colWidths=[cell_w] * LABEL_COLS)
        grid.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                  ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                  ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                                  ("TOPPADDING", (0, 0), (-1, -1), 4),
                                  ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        els.append(KeepTogether([grid]))  # safety net: whole page of labels or none, never split
        if start + per_page < len(bags):
            els.append(PageBreak())

    doc.build(els, canvasmaker=_make_canvas("Bag Labels", batch.code))
    return buf.getvalue()
