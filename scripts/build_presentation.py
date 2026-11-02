"""Build presentation/Project24_Demo.pptx in the same editorial style as the showcase.

    python scripts/build_presentation.py

Reads reports/analysis_summary.json, reports/tables/*.csv and reports/charts/*.png, so the
numbers on the slides always match the latest pipeline run. Needs only python-pptx + pandas.
Display type is Georgia and body type Calibri because they exist on every PowerPoint
install (the web showcase uses Fraunces / Inter Tight).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
OUT = ROOT / "presentation" / "Project24_Demo.pptx"
AUTHOR = os.environ.get("SHOWCASE_AUTHOR", "S. Prabhath")

PAPER, PAPER2 = RGBColor(0xF2, 0xEC, 0xDE), RGBColor(0xE9, 0xE0, 0xCB)
INK, MUTE = RGBColor(0x1B, 0x17, 0x12), RGBColor(0x6C, 0x63, 0x56)
SUN, SUN2, NIGHT = RGBColor(0xEE, 0x5A, 0x22), RGBColor(0xF6, 0xA2, 0x3A), RGBColor(0x15, 0x11, 0x0D)
RED, AMBER, SLATE, GREEN = (RGBColor(0xC4, 0x30, 0x1A), RGBColor(0xD5, 0x8A, 0x10),
                            RGBColor(0x5A, 0x6C, 0x8C), RGBColor(0x4A, 0x7A, 0x52))
SERIF, SANS, MONO = "Georgia", "Calibri", "Consolas"
W, H = Inches(13.333), Inches(7.5)
MX = Inches(0.75)                       # side margin

prs = Presentation()
prs.slide_width, prs.slide_height = W, H
BLANK = prs.slide_layouts[6]


# ---------------------------------------------------------------- helpers
def bg(slide, color):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def text(slide, x, y, w, h, content, size=16, color=INK, font=SANS, bold=False, italic=False,
         align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, spacing=None, line=None):
    """content: str or list of (str | list-of-runs). A run is (text, {overrides})."""
    box = slide.shapes.add_textbox(x, y, w, h)
    tf = box.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    paragraphs = content if isinstance(content, list) else [content]
    for i, para in enumerate(paragraphs):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        if line:
            p.line_spacing = line
        runs = para if isinstance(para, list) else [(para, {})]
        for run_text, over in runs:
            r = p.add_run()
            r.text = run_text
            f = r.font
            f.name = over.get("font", font)
            f.size = Pt(over.get("size", size))
            f.bold = over.get("bold", bold)
            f.italic = over.get("italic", italic)
            f.color.rgb = over.get("color", color)
            if spacing is not None or "spacing" in over:       # letter-spacing (hundredths of a point)
                r._r.get_or_add_rPr().set("spc", str(over.get("spacing", spacing)))
        if i:
            p.space_before = Pt(over.get("gap", 8)) if False else Pt(8)
    return box


def rect(slide, x, y, w, h, fill=None, line=None, shape=MSO_SHAPE.RECTANGLE, lw=0.75):
    s = slide.shapes.add_shape(shape, x, y, w, h)
    if fill is None:
        s.fill.background()
    else:
        s.fill.solid()
        s.fill.fore_color.rgb = fill
    if line is None:
        s.line.fill.background()
    else:
        s.line.color.rgb = line
        s.line.width = Pt(lw)
    s.shadow.inherit = False
    return s


def hair(slide, x, y, w, color=INK, weight=0.75):
    return rect(slide, x, y, w, Emu(int(Pt(weight))), fill=color)


def sun(slide, cx, cy, d):
    """Concentric translucent-looking discs that read as a glowing sun."""
    for scale, col in ((1.0, RGBColor(0xF5, 0xDF, 0xB8)), (0.78, RGBColor(0xF6, 0xBF, 0x84)),
                       (0.56, RGBColor(0xF2, 0x8F, 0x55)), (0.34, SUN)):
        r = int(d * scale / 2)
        rect(slide, cx - r, cy - r, 2 * r, 2 * r, fill=col, shape=MSO_SHAPE.OVAL)


def chrome(slide, label, n, dark=False):
    c = RGBColor(0xA7, 0x9D, 0x8C) if dark else MUTE
    text(slide, MX, Inches(0.42), Inches(8), Inches(0.3), label.upper(), 10.5, c, bold=True, spacing=300)
    text(slide, Inches(9.5), Inches(0.42), W - Inches(9.5) - MX, Inches(0.3),
         "PROJECT 24  ·  FLOW", 10.5, c, bold=True, align=PP_ALIGN.RIGHT, spacing=300)
    hair(slide, MX, Inches(0.8), W - 2 * MX, color=PAPER if dark else INK, weight=0.5)
    text(slide, MX, Inches(7.0), Inches(9), Inches(0.3),
         "Supply chain optimization & inventory forecasting  ·  decision support only", 10, c)
    text(slide, W - MX - Inches(1), Inches(7.0), Inches(1), Inches(0.3), f"{n:02d}", 12, c,
         font=SERIF, align=PP_ALIGN.RIGHT)


def title(slide, head, sub=None, color=INK, size=40, y=Inches(1.1)):
    text(slide, MX, y, Inches(11.8), Inches(1.2), head, size, color, font=SERIF, line=0.95)
    if sub:
        text(slide, MX, y + Inches(0.8), Inches(11.5), Inches(0.5), sub, 16, MUTE if color == INK else RGBColor(0xA7, 0x9D, 0x8C))


def table(slide, rows, widths, x, y, row_h=Inches(0.5), size=13, header=True, mono_cols=(), color_col=None,
          num_cols=(), tw=None):
    total = sum(widths)
    span = tw or (W - 2 * MX)
    cy = y
    for ri, row in enumerate(rows):
        cx = x
        is_head = header and ri == 0
        for ci, cell in enumerate(row):
            w = Emu(int(widths[ci] / total * span))
            font, col, bold, sz = SANS, INK, False, size
            if is_head:
                col, bold, sz = MUTE, True, 10.5
            elif ci in mono_cols:
                font, sz = MONO, size - 1
            if color_col is not None and ci == color_col and not is_head:
                col = {"STOCKOUT": RED, "AT_RISK": AMBER, "EXCESS": SLATE, "OK": GREEN}.get(cell, INK)
                bold = True
            if ci == 0 and not is_head:
                font = SERIF
            text(slide, cx, cy, w - Inches(0.15), row_h, cell.upper() if is_head else cell, sz, col, font=font, bold=bold,
                 spacing=150 if is_head else None, anchor=MSO_ANCHOR.MIDDLE,
                 align=PP_ALIGN.RIGHT if ci in num_cols else PP_ALIGN.LEFT)
            cx += w
        cy += row_h
        hair(slide, x, cy, int(span),
             color=INK if is_head else RGBColor(0xC9, 0xC0, 0xAC), weight=0.75 if is_head else 0.5)
    return cy


def bullets(slide, items, x, y, w, size=18, gap=14, color=INK):
    cy = y
    for it in items:
        rect(slide, x, cy + Inches(0.1), Inches(0.09), Inches(0.09), fill=SUN, shape=MSO_SHAPE.OVAL)
        lines = max(1, round(len(it) * size * 0.0072 / (w / 914400)) + (1 if len(it) * size * 0.0072 / (w / 914400) % 1 > 0 else 0)) if False else None
        box = text(slide, x + Inches(0.3), cy, w - Inches(0.3), Inches(0.9), it, size, color, line=1.05)
        est = max(1, -(-int(len(it) * size * 0.52) // int((w - Inches(0.3)) / 12700)))   # chars-per-line estimate
        cy += Inches(0.34 * est * size / 18) + Pt(gap)
    return cy


def picture(slide, path, x, y, w, h=None):
    frame_pad = Inches(0.08)
    pic = slide.shapes.add_picture(str(path), x + frame_pad, y + frame_pad, width=w - 2 * frame_pad)
    if h is not None and pic.height > h - 2 * frame_pad:             # fit by height
        ratio = (h - 2 * frame_pad) / pic.height
        pic.height, pic.width = int(pic.height * ratio), int(pic.width * ratio)
    rect(slide, x, y, pic.width + 2 * frame_pad, pic.height + 2 * frame_pad, line=INK, lw=1)
    return pic


def kpi(slide, x, y, w, value, label, color=INK, size=54):
    hair(slide, x, y, w - Inches(0.2), weight=0.75)
    text(slide, x, y + Inches(0.12), w, Inches(1.0), value, size, color, font=SERIF)
    text(slide, x, y + Inches(1.15), w - Inches(0.2), Inches(0.5), label.upper(), 10, MUTE, bold=True, spacing=150)


# ---------------------------------------------------------------- data
summary = json.loads((REPORTS / "analysis_summary.json").read_text())
P, F = summary["portfolio"], summary["forecast"]
rec = pd.read_csv(REPORTS / "tables" / "reorder_recommendations.csv")
sup = pd.read_csv(REPORTS / "tables" / "supplier_delivery_summary.csv")
CH = REPORTS / "charts"
lakh = lambda v: f"{v / 1e5:,.1f}"
fmt = lambda v: f"{v:,.0f}"

# ================================================================ 1. cover
s = prs.slides.add_slide(BLANK); bg(s, PAPER)
sun(s, int(W * 0.5), int(H * 0.55), Inches(8.2))
text(s, MX, Inches(0.5), Inches(9), Inches(0.3), "PROJECT 24  ·  ADVANCED  ·  PYTHON · FLASK · MYSQL · PANDAS · NUMPY · MATPLOTLIB", 10.5,
     INK, bold=True, spacing=250)
text(s, 0, Inches(2.0), W, Inches(2.4), "F  l  o  w", 150, INK, font=SERIF, align=PP_ALIGN.CENTER)
text(s, Inches(3.2), Inches(4.45), Inches(6.9), Inches(1.0),
     "Supply chain optimization & inventory forecasting — a decision-support system for the rhythm of demand, supply and stock.",
     17, INK, font=SERIF, italic=True, align=PP_ALIGN.CENTER, line=1.1)
text(s, MX, Inches(6.85), Inches(8), Inches(0.3), f"{AUTHOR}", 11, INK, bold=True, spacing=200)
text(s, Inches(7.5), Inches(6.85), W - Inches(7.5) - MX, Inches(0.3), "THE SYSTEM NEVER PLACES AN ORDER", 11, INK, bold=True,
     align=PP_ALIGN.RIGHT, spacing=200)

# ================================================================ 2. problem
s = prs.slides.add_slide(BLANK); bg(s, PAPER); chrome(s, "The problem", 2)
text(s, MX, Inches(1.15), Inches(11.8), Inches(2.4), [[("Why spreadsheets fail as a ", {}), ("replenishment", {"italic": True, "color": SUN}), (" system.", {})]],
     52, INK, font=SERIF, line=0.95)
items = [("Typed once, never recalculated", "Reorder points are typed once — demand moves, the minimum level does not."),
         ("Remembered, not measured", "Supplier reliability is anecdotal. Variability, not the average, forces safety stock."),
         ("No audit trail", "Six weeks later nobody can explain why 300 units were ordered."),
         ("The cost", "Stockouts of items that matter, cash frozen in items that do not.")]
cw = (W - 2 * MX) / 4
for i, (h_, b_) in enumerate(items):
    x = MX + int(cw * i)
    hair(s, x, Inches(4.2), int(cw) - Inches(0.3))
    text(s, x, Inches(4.35), int(cw) - Inches(0.3), Inches(0.4), f"0{i + 1}", 12, SUN, font=MONO, bold=True)
    text(s, x, Inches(4.75), int(cw) - Inches(0.3), Inches(0.8), h_, 19, INK, font=SERIF, line=1.0)
    text(s, x, Inches(5.5), int(cw) - Inches(0.3), Inches(1.3), b_, 13.5, MUTE, line=1.1)

# ================================================================ 3. objectives
s = prs.slides.add_slide(BLANK); bg(s, PAPER); chrome(s, "Objectives → delivery", 3)
title(s, "Each objective maps to code and a test.", size=38)
table(s, [["Objective", "Delivered as"],
          ["Model the supply chain", "MySQL: 6 tables, 2 views, PK / FK / CHECK / indexes"],
          ["Serve data safely", "Flask REST API: CRUD + validation + JSON error envelope"],
          ["Measure suppliers", "On-time rate, fill rate, lead-time CV, score, grade"],
          ["Estimate reorder points", "ROP = μ·L + SS, three configurable safety-stock policies"],
          ["Compare with a forecast", "4 models vs naive baseline, 28-day hold-out"],
          ["Flag risk", "STOCKOUT / AT_RISK / EXCESS thresholds + value at stake"],
          ["Report", "Markdown report, 9 CSV tables, Excel workbook, 10 charts"],
          ["Prove correctness", "199 pytest tests incl. DB-down and empty data"]],
      [3.6, 8.4], MX, Inches(2.3), Inches(0.46), size=15)

# ================================================================ 4. architecture
s = prs.slides.add_slide(BLANK); bg(s, PAPER); chrome(s, "Architecture", 4)
title(s, "Layers with one job each.", size=44)
layers = [("config.py", "every parameter and credential from environment variables (.env)"),
          ("db.py", "pooled connections, parameterised SQL, explicit transactions, driver errors mapped to API errors"),
          ("api/", "application factory, CRUD + analytics blueprints, validators, uniform error envelope"),
          ("analytics/", "pure formula module + pandas modules (demand, supplier, inventory, forecast) + pipeline"),
          ("viz/", "Matplotlib charts, each returning its own written interpretation"),
          ("scripts/", "data generator, SQL loader, analysis pipeline, document builder")]
y = Inches(2.35)
for name, desc in layers:
    hair(s, MX, y, W - 2 * MX, color=RGBColor(0xC9, 0xC0, 0xAC), weight=0.5)
    text(s, MX, y + Inches(0.1), Inches(2.6), Inches(0.6), name, 22, SUN, font=SERIF, italic=True, anchor=MSO_ANCHOR.MIDDLE)
    text(s, Inches(3.6), y + Inches(0.1), Inches(9), Inches(0.6), desc, 15, INK, anchor=MSO_ANCHOR.MIDDLE)
    y += Inches(0.74)
hair(s, MX, y, W - 2 * MX, color=RGBColor(0xC9, 0xC0, 0xAC), weight=0.5)

# ================================================================ 5. database
s = prs.slides.add_slide(BLANK); bg(s, PAPER); chrome(s, "Database design", 5)
title(s, "Keys, constraints, indexes, views.", "MySQL 8 / MariaDB — plus audit tables", size=38)
table(s, [["Table", "Key columns", "Purpose"],
          ["suppliers", "supplier_id PK · name UNIQUE", "vendor master, promised lead time"],
          ["products", "product_id PK · sku UNIQUE · supplier_id FK", "item master, cost, manual ROP"],
          ["purchase_orders", "po_id PK · po_number UNIQUE · 2 FKs", "order / expected / received dates"],
          ["stock_transactions", "transaction_id PK · product_id FK", "signed perpetual ledger"],
          ["demand_records", "PK · (product_id, demand_date) UNIQUE", "daily demand"],
          ["analysis_runs / reorder_recommendations", "FKs to run and product", "audit trail of recommendations"]],
      [4.7, 4.2, 3.1], MX, Inches(2.65), Inches(0.5), size=13.5, mono_cols=(1,))
text(s, MX, Inches(6.1), Inches(11.5), Inches(0.6),
     [[("on_hand is never stored", {"italic": True, "color": SUN}), (" — it is SUM(quantity) of the ledger, so stock can always be reconciled. "
       "Deleting a parent row with children is refused (409).", {})]], 17, INK, font=SERIF, line=1.05)

# ================================================================ 6. API
s = prs.slides.add_slide(BLANK); bg(s, PAPER); chrome(s, "REST API", 6)
title(s, "Validation before SQL. One error envelope.", "No write path from analytics.", size=38)
table(s, [["Endpoint", "What it demonstrates"],
          ["GET /api/health", "service + database health, degrades to 503"],
          ["POST /api/purchase-orders", "date and quantity rules, FK checks, 422 details"],
          ["POST /api/purchase-orders/{id}/receive", "PO and ledger updated in one transaction"],
          ["GET /api/analytics/reorder-recommendations", "ROP, safety stock, flags, quantities"],
          ["GET /api/analytics/suppliers", "on-time, fill rate, lead-time CV, grade"],
          ["GET /api/analytics/risks", "shortage and excess lists with INR value"],
          ["GET /api/analytics/forecast", "forecast + hold-out metrics vs baseline"],
          ["POST /api/analytics/run", "stores an audit snapshot — places no orders"]],
      [5.4, 6.6], MX, Inches(2.75), Inches(0.46), size=14, mono_cols=(0,))

# ================================================================ 7. formulas (dark)
s = prs.slides.add_slide(BLANK); bg(s, NIGHT); chrome(s, "Formulas and units", 7, dark=True)
text(s, MX, Inches(1.1), Inches(11), Inches(1.2), "Documented once, unit tested, reused everywhere.", 40, PAPER, font=SERIF, line=0.95)
rect(s, MX, Inches(2.55), Inches(0.05), Inches(3.55), fill=SUN)
formulas = ["μ_d = demand / calendar days                      [units/day]", "σ_d = std dev of daily demand                     [units/day]",
            "L   = observed lead time (≥3 deliveries) else master  [days]", "SS  = max( z(service)·σ_d·√L ,  days·μ_d )          [units]",
            "ROP = μ_d·L + SS                                    [units]", "IP  = on_hand + open PO quantity                    [units]",
            "Q   = max(0, ROP + μ_d·review − IP)                 [units]", "DoC = on_hand / μ_d                                 [days]",
            "MASE < 1  ⇒  better than the naive baseline"]
text(s, MX + Inches(0.4), Inches(2.55), Inches(8.4), Inches(3.7), formulas, 15, RGBColor(0xD9, 0xCF, 0xBC), font=MONO, line=1.18)
text(s, Inches(9.6), Inches(2.55), Inches(3), Inches(0.4), "DEFAULT POLICY", 10.5, SUN2, bold=True, spacing=250)
text(s, Inches(9.6), Inches(2.95), Inches(3), Inches(3.2),
     [[("0.95", {"size": 44, "font": SERIF, "color": PAPER}), ("  service level", {"size": 13})],
      [("90", {"size": 44, "font": SERIF, "color": PAPER}), ("  day demand window", {"size": 13})],
      [("7", {"size": 44, "font": SERIF, "color": PAPER}), ("  day review period", {"size": 13})],
      [("90", {"size": 44, "font": SERIF, "color": PAPER}), ("  days cover = excess", {"size": 13})]],
     13, RGBColor(0xA7, 0x9D, 0x8C), line=1.0)

# ================================================================ 8. results
s = prs.slides.add_slide(BLANK); bg(s, PAPER); chrome(s, f"Results at a glance · as of 19 October 2026", 8)
title(s, "The portfolio, at a glance.", size=40)
cw = Inches(2.35)
kp = [(str(P["products"]), "SKUs analysed", INK), (str(P["stockout"]), "Stockout risk", RED), (str(P["at_risk"]), "At / below ROP", AMBER),
      (str(P["excess"]), "Excess stock", SLATE), (lakh(P["inventory_value"]), "Inventory on hand · lakh ₹", INK),
      (lakh(P["suggested_order_value"]), "Suggested orders · lakh ₹ (not placed)", INK),
      (f'{F["mean_mae_selected"]:.2f}', f'Forecast MAE · baseline {F["mean_mae_baseline"]:.2f}', INK),
      (f'{F["improved_over_baseline"]}/{F["products_evaluated"]}', "SKUs beating baseline", GREEN)]
for i, (v, l, c) in enumerate(kp):
    kpi(s, MX + cw * (i % 4), Inches(2.4) + Inches(1.95) * (i // 4), cw, v, l, c)
text(s, MX, Inches(6.35), Inches(11.5), Inches(0.5),
     "Decision support: the suggested orders are calculations for a planner to review — nothing is ordered.", 14, MUTE, font=SERIF, italic=True)

# ================================================================ 9. flags
s = prs.slides.add_slide(BLANK); bg(s, PAPER); chrome(s, "Shortage and excess flags", 9)
title(s, "Rule-based, threshold-driven, configurable.", size=38)
flagged = rec[rec.risk_flag != "OK"].copy()
order = {"STOCKOUT": 0, "AT_RISK": 1, "EXCESS": 2}
flagged = flagged.sort_values(by="risk_flag", key=lambda c: c.map(order))
rows = [["SKU", "Flag", "On hand", "ROP", "Suggest qty", "Value (INR)"]] + [
    [r.sku, r.risk_flag, fmt(r.on_hand), fmt(r.reorder_point_calc), fmt(r.suggested_order_qty), fmt(r.order_value)]
    for r in flagged.itertuples()]
table(s, rows, [3.2, 2.0, 1.6, 1.4, 1.8, 2.0], MX, Inches(2.2), Inches(0.42), size=13.5, color_col=1, num_cols=(2, 3, 4, 5), mono_cols=())
bullets(s, ["STOCKOUT — out of stock, or position cannot cover lead-time demand.", "AT_RISK — below the reorder point but lead-time demand is covered.",
            "EXCESS — above policy with more than the cover threshold."], MX, Inches(5.85), Inches(11.5), size=14, gap=4)

# ================================================================ 10. suppliers
s = prs.slides.add_slide(BLANK); bg(s, PAPER); chrome(s, "Supplier performance", 10)
title(s, "Punctuality and consistency, measured.", size=38)
picture(s, CH / "04_supplier_on_time.png", MX, Inches(2.1), Inches(6.9), Inches(4.7))
best, worst = sup.sort_values("supplier_score").iloc[-1], sup.sort_values("supplier_score").iloc[0]
bullets(s, [f"Best: {best.supplier_name} — {best.on_time_rate:.1%} on time, CV {best.lead_time_cv:.2f}, grade {best.grade}.",
            f"Weakest: {worst.supplier_name} — {worst.on_time_rate:.1%} on time, CV {worst.lead_time_cv:.2f}, grade {worst.grade}.",
            "The score weights consistency (1 − CV) because variability drives safety stock.",
            "Open orders are excluded from lead-time statistics."], Inches(8.1), Inches(2.2), Inches(4.5), size=15, gap=10)

# ================================================================ 11. forecasting
s = prs.slides.add_slide(BLANK); bg(s, PAPER); chrome(s, "Forecasting against a baseline", 11)
title(s, "Four methods, one winner per SKU.", "28-day hold-out; the naive mean is the bar to beat.", size=38)
pic = picture(s, CH / "07_forecast_vs_actual.png", MX, Inches(2.8), Inches(8.2), Inches(4.0))
kx = Inches(9.4)
kpi(s, kx, Inches(2.35), Inches(3.2), f'{F["mean_improvement_pct"]}%', "Mean MAE improvement", SUN, 48)
kpi(s, kx, Inches(3.95), Inches(3.2), f'{F["improved_over_baseline"]} of {F["products_evaluated"]}', "SKUs beat the baseline", INK, 48)
text(s, kx, Inches(5.65), Inches(3.2), Inches(1.0),
     f'MAE {F["mean_mae_baseline"]:.2f} → {F["mean_mae_selected"]:.2f} units/day. A model is adopted only if it wins by more than 5%.',
     13, MUTE, line=1.1)

# ================================================================ 12. testing
s = prs.slides.add_slide(BLANK); bg(s, PAPER); chrome(s, "Testing", 12)
title(s, "199 tests, five groups, real failure injection.", size=34)
table(s, [["Group", "Tests", "Focus"],
          ["Formulas", "42", "hand calculations, invalid inputs, boundaries"],
          ["Validators", "38", "types, ranges, dates, cross-field rules"],
          ["Analysis", "33", "zero-fill, ABC, reorder engine, turnover, forecast"],
          ["CRUD API", "45", "contracts, duplicates, FKs, receipts, conflicts"],
          ["Analytics API", "41", "parameters, empty data, DB-down, no-orders guarantee"]],
      [2.0, 0.9, 5.0], MX, Inches(2.0), Inches(0.5), size=14, num_cols=(1,), tw=Inches(7.5))
# right column: a small console panel
rect(s, Inches(8.65), Inches(2.0), Inches(3.95), Inches(3.0), fill=NIGHT)
text(s, Inches(8.9), Inches(2.2), Inches(3.5), Inches(2.7),
     [[("$ python -m pytest tests", {"color": SUN2})], [("199 passed in 55.89s", {"color": RGBColor(0x8F, 0xD0, 0xA0)})], [("", {})],
      [("$ DB_PORT=65001 python -m pytest tests", {"color": SUN2})], [("113 passed, 85 skipped in 1.45s", {"color": RGBColor(0xD9, 0xCF, 0xBC)})]],
     12, RGBColor(0xD9, 0xCF, 0xBC), font=MONO, line=1.2)
bullets(s, ["Integration tests skip with a reason when MySQL is unreachable.", "Tests never modify seeded data — they create and delete their own rows.",
            "One test asserts the analysis creates no purchase order."], MX, Inches(5.4), Inches(11.8), size=14, gap=3)

# ================================================================ 13. limitations
s = prs.slides.add_slide(BLANK); bg(s, PAPER); chrome(s, "Limitations and next steps", 13)
title(s, "Stated up front, not discovered in Q&A.", size=40)
lim = ["Safety stock ignores lead-time variability → add σ_L: SS = z·√(L·σ_d² + μ_d²·σ_L²).", "Suggested quantity ignores MOQ, pack size and budget.",
       "Open POs are trusted — a stalled order understates risk.", "Single hold-out period → rolling-origin backtest.",
       "Single warehouse, one supplier per SKU, single-line POs.", "No authentication; in-process cache assumes a small portfolio.",
       "Synthetic data: the method is demonstrated, not a business outcome."]
y = Inches(2.3)
for i, t_ in enumerate(lim):
    hair(s, MX, y, W - 2 * MX, color=RGBColor(0xC9, 0xC0, 0xAC), weight=0.5)
    text(s, MX, y + Inches(0.08), Inches(0.8), Inches(0.5), f"{i + 1:02d}", 13, SUN, font=MONO, bold=True, anchor=MSO_ANCHOR.MIDDLE)
    text(s, MX + Inches(0.9), y + Inches(0.08), Inches(11), Inches(0.5), t_, 16, INK, font=SERIF, anchor=MSO_ANCHOR.MIDDLE)
    y += Inches(0.6)

# ================================================================ 14. close
s = prs.slides.add_slide(BLANK); bg(s, SUN)
text(s, MX, Inches(0.42), Inches(8), Inches(0.3), "WHAT THE PLANNER GETS", 10.5, NIGHT, bold=True, spacing=300)
text(s, Inches(9.5), Inches(0.42), W - Inches(9.5) - MX, Inches(0.3), "PROJECT 24  ·  FLOW", 10.5, NIGHT, bold=True, align=PP_ALIGN.RIGHT, spacing=300)
hair(s, MX, Inches(0.8), W - 2 * MX, color=NIGHT, weight=0.5)
gets = ["Which SKUs need action today, how much to order, and why.", "Which suppliers cause the risk, and how reliable they really are.",
        "Where cash is frozen in stock that is not moving.", "A forecast tested on data it never saw — and the baseline it must beat.",
        "An audit trail of every parameter used — and no order placed without a human."]
y = Inches(1.25)
for g in gets:
    text(s, MX, y, Inches(11.8), Inches(0.6), g, 23, NIGHT, font=SERIF, line=1.0)
    y += Inches(0.72)
text(s, MX, Inches(5.7), Inches(11.8), Inches(1.0), "Thank you — questions welcome.", 40, NIGHT, font=SERIF, italic=True)
text(s, MX, Inches(7.0), Inches(9), Inches(0.3), f"{AUTHOR}  ·  Supply chain optimization & inventory forecasting", 10.5, NIGHT)
text(s, W - MX - Inches(1), Inches(7.0), Inches(1), Inches(0.3), "14", 12, NIGHT, font=SERIF, align=PP_ALIGN.RIGHT)

OUT.parent.mkdir(parents=True, exist_ok=True)
prs.save(OUT)
print(f"written {OUT} ({len(prs.slides)} slides)")
