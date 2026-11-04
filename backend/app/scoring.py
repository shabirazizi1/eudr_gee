"""F4 – turns raw screening numbers into a 0-100 risk score (higher = riskier)."""


def score(result: dict) -> dict:
    area = max(result["area_ha"], 0.01)
    loss = result["loss_ha"]
    loss_pct = loss / area * 100
    breakdown = []
    flags = []
    needs_review = False

    # 1. Post-cutoff tree-cover loss (up to 60 pts; 5% of the farm lost = 60)
    loss_pts = min(60.0, loss_pct * 12)
    breakdown.append({"factor": "Post-2020 forest loss", "points": round(loss_pts),
                      "detail": f"{loss:.2f} ha ({loss_pct:.1f}% of farm)"})
    if loss > 0:
        flags.append(f"Tree-cover loss after the cutoff detected: {loss:.2f} ha.")

    # 2. Protected-area overlap (25 pts)
    pa = result.get("protected_areas") or []
    pa_pts = 25 if pa else 0
    breakdown.append({"factor": "Protected-area overlap", "points": pa_pts,
                      "detail": ", ".join(pa) if pa else "None detected"})
    if pa:
        flags.append("Boundary overlaps a protected area.")
        needs_review = True

    # 3. Vegetation (NDVI) drop vs. the cutoff-year baseline (up to 15 pts)
    now, base = result.get("ndvi_current"), result.get("ndvi_baseline")
    ndvi_pts = 0
    if now is not None and base is not None:
        change = now - base
        if change < -0.05:
            ndvi_pts = min(15, round(-change * 100))
            flags.append(f"Vegetation signal dropped by {abs(change):.2f} NDVI since the baseline.")
        detail = f"{base:.2f} → {now:.2f}"
    else:
        detail = "Not enough clear imagery"
    breakdown.append({"factor": "Vegetation change (NDVI)", "points": ndvi_pts, "detail": detail})

    # 4. Data quality (10 pts) – too few clear images means we can't be sure
    dq_pts = 0
    if result.get("images_current", 0) < 3 or result.get("coverage_pct", 0) < 50:
        dq_pts = 10
        needs_review = True
        flags.append("Low usable imagery (clouds). Needs manual review.")
    breakdown.append({"factor": "Image quality", "points": dq_pts,
                      "detail": f"{result.get('images_current', 0)} images, "
                                f"{result.get('coverage_pct', 0):.0f}% of farm usable"})

    total = round(min(100, loss_pts + pa_pts + ndvi_pts + dq_pts))
    # Any loss above ~1 Landsat pixel (0.1 ha) can't be "Low"
    if loss >= 0.1:
        total = max(total, 30)

    level = "Low" if total < 30 else "Medium" if total < 60 else "High"
    if level != "Low":
        needs_review = True
    status = "Under review" if needs_review else "Verified"

    return {"score": total, "level": level, "status": status,
            "flags": flags, "breakdown": breakdown}
