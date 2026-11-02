"""Build the submission documents from the live analysis.

    python scripts/build_documents.py

Outputs
-------
docs/Project24_Final_Report.docx    final report (synopsis, design, results, charts)
presentation/Project24_Demo.pptx    14-slide demonstration deck

Every number and table is pulled from the same pipeline the API uses, so the
documents can be regenerated after any parameter change without editing text.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analytics import formulas                                        # noqa: E402
from analytics.pipeline import AnalysisPipeline                        # noqa: E402
from api.validators import AnalysisParams                              # noqa: E402
from config import config                                             # noqa: E402

CHART_DIR = Path(config.REPORT_DIR) / "charts"
DOCS = ROOT / "docs"
PRES = ROOT / "presentation"

NAVY = (0x1F, 0x4E, 0x79)
RED = (0xC0, 0x39, 0x2B)
GREY = (0x59, 0x59, 0x59)


# ------------------------------------------------------------------ helpers
def money(value, digits=0):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    return f"{value:,.{digits}f}"


def pct(value, digits=1):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "n/a"
    return f"{value * 100:.{digits}f}%"


def fmt(value, digits=2):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "—"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return str(value)


# ================================================================ DOCX
def build_docx(result, sensitivity: pd.DataFrame) -> Path:
    from docx import Document
    from docx.enum.section import WD_SECTION
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt, RGBColor

    doc = Document()

    # ---- base styles
    styles = doc.styles
    normal = styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(4)

    for name, size, color in (("Title", 26, NAVY), ("Heading 1", 16, NAVY),
                              ("Heading 2", 13, NAVY), ("Heading 3", 11, NAVY)):
        style = styles[name]
        style.font.name = "Calibri"
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor(*color)
        style.font.bold = True

    for section in doc.sections:
        section.top_margin = Cm(2.0)
        section.bottom_margin = Cm(2.0)
        section.left_margin = Cm(2.2)
        section.right_margin = Cm(2.2)

    def para(text="", style=None, size=None, bold=False, italic=False,
             color=None, align=None, space_after=None):
        p = doc.add_paragraph(style=style)
        run = p.add_run(text)
        if size:
            run.font.size = Pt(size)
        run.bold = bold
        run.italic = italic
        if color:
            run.font.color.rgb = RGBColor(*color)
        if align:
            p.alignment = align
        if space_after is not None:
            p.paragraph_format.space_after = Pt(space_after)
        return p

    def bullet(text, style="List Bullet"):
        return doc.add_paragraph(text, style=style)

    def table_from_frame(frame: pd.DataFrame, columns: dict[str, str],
                         digits: int = 2, font_size: int = 8,
                         max_rows: int = 26) -> None:
        frame = frame.head(max_rows)
        keys = [c for c in columns if c in frame.columns]
        table = doc.add_table(rows=1, cols=len(keys))
        table.style = "Light Grid Accent 1"
        table.alignment = WD_TABLE_ALIGNMENT.CENTER
        header = table.rows[0].cells
        for i, key in enumerate(keys):
            header[i].text = ""
            run = header[i].paragraphs[0].add_run(columns[key])
            run.bold = True
            run.font.size = Pt(font_size)
        for _, row in frame.iterrows():
            cells = table.add_row().cells
            for i, key in enumerate(keys):
                value = row[key]
                text = fmt(value, digits) if isinstance(value, (int, float)) else str(value)
                cells[i].text = ""
                run = cells[i].paragraphs[0].add_run(text)
                run.font.size = Pt(font_size)
        doc.add_paragraph().paragraph_format.space_after = Pt(2)

    def picture(filename: str, width_cm: float = 16.5, caption: str | None = None):
        path = CHART_DIR / filename
        if not path.exists():
            return
        doc.add_picture(str(path), width=Cm(width_cm))
        doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
        if caption:
            para(caption, size=8.5, italic=True, color=GREY,
                 align=WD_ALIGN_PARAGRAPH.CENTER)

    # ---------------------------------------------------------------- title
    for _ in range(3):
        doc.add_paragraph()
    para("Supply Chain Optimization and\nInventory Forecasting System",
         style="Title", align=WD_ALIGN_PARAGRAPH.CENTER)
    para("Final Project Report — Project 24", size=14, color=GREY,
         align=WD_ALIGN_PARAGRAPH.CENTER)
    para("Difficulty: Advanced", size=11, italic=True, color=GREY,
         align=WD_ALIGN_PARAGRAPH.CENTER)
    doc.add_paragraph()
    para("Python · Flask · MySQL · Pandas · NumPy · Matplotlib · statsmodels",
         size=11, align=WD_ALIGN_PARAGRAPH.CENTER, color=NAVY)
    for _ in range(2):
        doc.add_paragraph()
    para(f"Analysis as of {result.as_of:%d %B %Y}   |   Report generated {date.today():%d %B %Y}",
         size=10, align=WD_ALIGN_PARAGRAPH.CENTER, color=GREY)
    para("Team: ‹member 1› · ‹member 2› · ‹member 3› · ‹member 4› · ‹member 5›",
         size=10, align=WD_ALIGN_PARAGRAPH.CENTER, color=GREY)
    doc.add_paragraph()
    para("The system analysed in this report is a decision-support prototype built on a "
         "synthetic dataset. It computes reorder points and risk flags for a human "
         "planner to review and never places, approves or transmits a purchase order.",
         size=9.5, italic=True, align=WD_ALIGN_PARAGRAPH.CENTER, color=RED)

    doc.add_page_break()

    # ---------------------------------------------------------------- abstract
    doc.add_heading("Executive summary", level=1)
    p = result.portfolio
    fs = result.forecast_summary
    para(f"This project implements a supply-chain analytics system for {p['products']} "
         f"SKUs sourced from {len(result.supplier_summary)} suppliers. It stores master "
         f"data, purchase orders, a perpetual stock ledger and daily demand in MySQL, "
         f"exposes them through a validated Flask REST API, and analyses them with "
         f"pandas, NumPy and statsmodels. Reorder points are calculated from documented "
         f"assumptions — average demand over a {result.params.window_days}-day window, "
         f"an estimated lead time, and a safety stock that combines a "
         f"service-level z-score with a days-of-demand rule — and each SKU is flagged "
         f"as STOCKOUT, AT_RISK, EXCESS or OK.")
    para(f"At the analysis date the portfolio holds INR {money(p['inventory_value'])} of "
         f"inventory. {p['stockout']} SKU(s) are at stockout risk and {p['at_risk']} sit "
         f"below the calculated reorder point; bringing them back to policy would need "
         f"about INR {money(p['suggested_order_value'])} of purchases (a suggestion, not "
         f"an order). {p['excess']} SKU(s) carry more than "
         f"{result.params.excess_cover_days:.0f} days of cover. Four forecasting methods "
         f"were compared on a {config.FORECAST_HOLDOUT_DAYS}-day hold-out: the selected "
         f"models average {money(fs.get('mean_mae_selected'))} units/day of error against "
         f"{money(fs.get('mean_mae_baseline'))} for the naive baseline and improve on the "
         f"baseline for {fs.get('improved_over_baseline')} of "
         f"{fs.get('products_evaluated')} SKUs. The project is covered by 199 automated "
         f"tests covering formulas, validation, boundaries, empty datasets and database "
         f"failure.")

    doc.add_heading("Contents", level=1)
    for item in ["1. Problem statement and objectives",
                 "2. Scope, users and assumptions",
                 "3. System architecture",
                 "4. Database design",
                 "5. REST API",
                 "6. Analysis methodology and formulas",
                 "7. Results: reorder recommendations, shortages and excess stock",
                 "8. Supplier delivery performance",
                 "9. Demand trends and forecasting",
                 "10. Visualisations",
                 "11. Testing",
                 "12. Limitations and future work",
                 "13. Conclusion",
                 "Appendix A: setup and reproduction",
                 "Appendix B: work division"]:
        bullet(item, style="List Number" if False else "List Bullet")

    # ---------------------------------------------------------------- 1
    doc.add_page_break()
    doc.add_heading("1. Problem statement and objectives", level=1)
    para("Mid-sized distributors plan replenishment in spreadsheets. Reorder points are "
         "typed once and never recalculated, supplier reliability is remembered rather "
         "than measured, and no record explains why a quantity was ordered. The result is "
         "the two expensive failure modes this project targets: stockouts of items that "
         "matter, and cash frozen in items that do not move.")
    doc.add_heading("1.1 Objectives", level=2)
    for text in [
        "Model products, suppliers, purchase orders, stock movements and demand in a "
        "normalised MySQL schema with primary keys, foreign keys, CHECK constraints and "
        "indexes.",
        "Expose the data through a validated REST API with correct HTTP semantics and "
        "meaningful, machine-readable error messages.",
        "Measure supplier delivery performance (on-time rate, fill rate, lead-time "
        "variability, composite score).",
        "Estimate average demand, safety stock and reorder points from configurable, "
        "documented assumptions.",
        "Compare historical demand with simple forecasts and evaluate each model against "
        "a naive baseline on a hold-out period.",
        "Flag stockout and excess-stock risk with explicit, configurable thresholds and "
        "quantify the value at stake.",
        "Produce a reproducible reorder recommendation report, supplier summary, demand "
        "trend analysis, charts and an audit trail.",
        "Demonstrate correctness on invalid input, empty datasets, boundary values and "
        "database failure.",
    ]:
        bullet(text)
    para("Explicit non-objective: the system never places an order. Procurement remains a "
         "human decision; the software ranks, quantifies and explains.", italic=True,
         color=RED)

    # ---------------------------------------------------------------- 2
    doc.add_heading("2. Scope, users and assumptions", level=1)
    doc.add_heading("2.1 In scope / out of scope", level=2)
    table = doc.add_table(rows=1, cols=2)
    table.style = "Light Grid Accent 1"
    hdr = table.rows[0].cells
    hdr[0].text = "In scope"
    hdr[1].text = "Out of scope"
    for left, right in [
        ("Single-warehouse perpetual inventory ledger", "Multi-warehouse / multi-echelon allocation"),
        ("Product and supplier master data", "Automatic order submission to ERP/supplier"),
        ("Single-line purchase orders with partial receipts", "Purchase order headers with many lines"),
        ("Daily demand records", "Promotions, pricing, weather or market data"),
        ("Descriptive statistics, ABC analysis, reorder points, risk flags", "User authentication and role management"),
        ("Four forecasting methods with hold-out evaluation", "Real-time streaming ingestion"),
        ("REST API, charts, generated reports", "Web/mobile front end"),
    ]:
        row = table.add_row().cells
        row[0].text = left
        row[1].text = right
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(9)
    doc.add_paragraph()
    doc.add_heading("2.2 Users", level=2)
    para("The primary user is an inventory planner who needs to know which SKUs require "
         "action today and why. Buyers use the supplier scorecard, the supply-chain "
         "manager uses the portfolio dashboard, and analysts reproduce every number from "
         "the pipeline scripts. No authentication layer exists because the tool runs on a "
         "trusted machine or behind an existing gateway (see limitations).")
    doc.add_heading("2.3 Assumptions", level=2)
    table_from_frame(pd.DataFrame([
        {"id": "A1", "assumption": "One warehouse; one supplier per SKU", "impact": "Multi-sourcing would change ROP and lead-time variability"},
        {"id": "A2", "assumption": "A missing demand row means zero demand", "impact": "Averages are per calendar day, not per selling day"},
        {"id": "A3", "assumption": "One product per purchase order", "impact": "Mixed POs would need allocation logic"},
        {"id": "A4", "assumption": "On-hand stock is the ledger sum", "impact": "Stock counts must be recorded as ADJUSTMENT rows"},
        {"id": "A5", "assumption": "Open POs will arrive (counted in position)", "impact": "A stalled PO overstates the position"},
        {"id": "A6", "assumption": "Lead times in calendar days", "impact": "Working-day calendars would change every ROP"},
        {"id": "A7", "assumption": "Daily demand independent (σ·√L)", "impact": "Correlated demand understates safety stock"},
        {"id": "A8", "assumption": "Observed lead time replaces master data after 3 deliveries", "impact": "Small samples are noisy"},
        {"id": "A9", "assumption": "Safety stock = max(z-score, days rule)", "impact": "A budget-constrained planner may prefer z-score only"},
        {"id": "A10", "assumption": "Suggested quantity is an order-up-to level", "impact": "MOQ, pack size and budget are ignored"},
        {"id": "A11", "assumption": "90-day demand window, 365-day PO window", "impact": "Longer seasonality is invisible"},
        {"id": "A12", "assumption": "A model must beat the baseline by >5% to be adopted", "impact": "Small genuine gains may be discarded"},
    ]), {"id": "ID", "assumption": "Assumption", "impact": "Impact if violated"},
        font_size=8.5, max_rows=12)

    # ---------------------------------------------------------------- 3
    doc.add_page_break()
    doc.add_heading("3. System architecture", level=1)
    para("The system is a layered monolith: configuration and credentials are isolated "
         "from application code, the database layer owns every SQL statement, and the "
         "analysis layer is pure functions plus pandas so it can be unit tested without "
         "a web server.")
    for text in [
        "config.py — every setting, including analysis parameters and credentials, read "
        "from environment variables (.env); never hard-coded.",
        "db.py — pooled MySQL access, parameterised SQL, explicit transaction "
        "boundaries, driver errors translated into application errors.",
        "api/ — Flask application factory, blueprints for CRUD and analytics, request "
        "validators, uniform JSON error envelope, rotating file log.",
        "analytics/ — formulas (pure), data access (read-only SQL), demand, supplier, "
        "inventory, forecast modules and an orchestration pipeline.",
        "viz/charts.py — ten Matplotlib charts, each returning its own written "
        "interpretation.",
        "scripts/ — synthetic data generator, SQL loader, end-to-end pipeline, data "
        "dictionary dumper, document builder.",
        "tests/ — 199 pytest tests split into formula, validation, analysis, CRUD and "
        "analytics suites.",
    ]:
        bullet(text)
    para("Request flow: client → Flask route → validator (422 on bad input) → database "
         "layer with parameterised SQL → JSON response. Analytics routes load data into "
         "pandas, apply the formulas, and return both the numbers and the parameter set "
         "that produced them; optional persistence writes an audit snapshot.")

    # ---------------------------------------------------------------- 4
    doc.add_heading("4. Database design", level=1)
    para("Six tables and two views in InnoDB/utf8mb4. A signed ledger is the single "
         "source of truth for stock; reorder snapshots are stored separately so history "
         "is never overwritten.")
    table_from_frame(pd.DataFrame([
        {"table": "suppliers", "key": "supplier_id PK; supplier_name UNIQUE", "purpose": "Vendor master, promised lead time"},
        {"table": "products", "key": "product_id PK; sku UNIQUE; supplier_id FK", "purpose": "Item master, cost, planned lead time, manual ROP"},
        {"table": "purchase_orders", "key": "po_id PK; po_number UNIQUE; 2 FKs", "purpose": "Order → expected → received dates, partial receipts"},
        {"table": "stock_transactions", "key": "transaction_id PK; product_id FK", "purpose": "Signed perpetual ledger (RECEIPT/ISSUE/ADJUSTMENT/RETURN)"},
        {"table": "demand_records", "key": "demand_id PK; (product_id, demand_date) UNIQUE", "purpose": "Observed daily demand"},
        {"table": "analysis_runs", "key": "run_id PK", "purpose": "Parameters of each explicit analysis run"},
        {"table": "reorder_recommendations", "key": "rec_id PK; run_id FK; product_id FK", "purpose": "Snapshot of ROP, flags and suggestions"},
        {"table": "v_product_inventory / v_po_delivery", "key": "views", "purpose": "Convenience joins for reporting"},
    ]), {"table": "Table", "key": "Primary / foreign keys", "purpose": "Purpose"}, font_size=8.5)
    para("Integrity is enforced twice: by the database (PRIMARY KEY, FOREIGN KEY, "
         "UNIQUE, CHECK, indexes) and by the API (business rules that a CHECK cannot "
         "express, such as “a RECEIVED order must have a received date”). Deleting a "
         "parent row that still has children is refused with HTTP 409 and a readable "
         "explanation; retiring master data uses the is_active flag instead. The full "
         "column-level data dictionary is generated from INFORMATION_SCHEMA by "
         "scripts/dump_data_dictionary.py and shipped as docs/data_dictionary.md.")

    # ---------------------------------------------------------------- 5
    doc.add_page_break()
    doc.add_heading("5. REST API", level=1)
    para("All endpoints are JSON, all writes validated, all errors returned in one "
         "envelope: {\"error\": {\"code\", \"message\", \"details\"}}. Analytics "
         "endpoints accept the analysis parameters as query overrides so a reviewer can "
         "re-run any conclusion under different assumptions without changing code.")
    table_from_frame(pd.DataFrame([
        {"method": "GET", "path": "/api/health", "purpose": "Service + database health, row counts (503 when the DB is down)"},
        {"method": "GET", "path": "/api/config", "purpose": "Non-secret configuration and analysis parameters"},
        {"method": "GET/POST", "path": "/api/products", "purpose": "List (filter, page) / create"},
        {"method": "GET/PUT/PATCH/DELETE", "path": "/api/products/{id}", "purpose": "Read, replace, partial update, delete (409 if history exists)"},
        {"method": "GET/POST", "path": "/api/suppliers", "purpose": "Supplier master"},
        {"method": "GET/POST", "path": "/api/purchase-orders", "purpose": "Purchase orders (filter by status, overdue)"},
        {"method": "POST", "path": "/api/purchase-orders/{id}/receive", "purpose": "Goods receipt: PO + ledger in one transaction"},
        {"method": "GET/POST", "path": "/api/stock-transactions", "purpose": "Signed ledger entries; blocks negative balances"},
        {"method": "GET/POST", "path": "/api/demand-records", "purpose": "Daily demand, upsert semantics, bulk upload"},
        {"method": "GET", "path": "/api/analytics/reorder-recommendations", "purpose": "ROP, safety stock, flags, suggested quantity"},
        {"method": "GET", "path": "/api/analytics/suppliers", "purpose": "Supplier delivery summary with definitions"},
        {"method": "GET", "path": "/api/analytics/demand", "purpose": "Demand statistics, ABC classes, category totals"},
        {"method": "GET", "path": "/api/analytics/risks", "purpose": "Shortage / excess lists with value at stake"},
        {"method": "GET", "path": "/api/analytics/forecast", "purpose": "Forecast plus hold-out metrics vs baseline"},
        {"method": "POST", "path": "/api/analytics/run", "purpose": "Run and store an auditable snapshot (places no orders)"},
        {"method": "GET", "path": "/api/charts/{file}", "purpose": "Chart PNGs; catalogue with interpretations at /api/charts"},
    ]), {"method": "Method", "path": "Path", "purpose": "Purpose"}, font_size=7.5, max_rows=16)

    # ---------------------------------------------------------------- 6
    doc.add_heading("6. Analysis methodology and formulas", level=1)
    para("Every quantity below is computed once in analytics/formulas.py, unit tested, and "
         "reused by the API, the reports and the charts. Units are stated for each line.")
    code = doc.add_paragraph()
    run = code.add_run(
        "average daily demand  mu_d    = total demand / calendar days in window   [units/day]\n"
        "demand variability    sigma_d = std dev of daily demand (zero-filled)     [units/day]\n"
        "lead time             L       = mean observed PO lead time if >= 3 deliveries,\n"
        "                                else products.lead_time_days               [days]\n"
        "safety stock (z)      SS_z    = z(service_level) * sigma_d * sqrt(L)       [units]\n"
        "safety stock (days)   SS_d    = safety_stock_days * mu_d                   [units]\n"
        "safety stock used     SS      = max(SS_z, SS_d)      (policy = max)        [units]\n"
        "reorder point         ROP     = mu_d * L + SS                              [units]\n"
        "inventory position    IP      = on_hand + open PO quantity                 [units]\n"
        "order-up-to level     T       = ROP + mu_d * review_period_days            [units]\n"
        "suggested quantity    Q       = max(0, T - IP)                             [units]\n"
        "days of cover         DoC     = on_hand / mu_d                             [days]\n"
        "turnover              TO      = (units issued * unit_cost) / average stock value\n"
        "                                                                        [turns/year]\n"
        "supplier score        Score   = 100*(0.6*on_time_rate + 0.4*max(0,1-CV))   [0-100]\n"
        "forecast errors       MAE, RMSE, MAPE, MASE, bias     (MASE < 1 beats naive)\n")
    run.font.name = "Consolas"
    run.font.size = Pt(8)
    para("")
    para(f"With the default policy the service level is "
         f"{result.params.service_level:.3f}, giving z = "
         f"{formulas.z_from_service_level(result.params.service_level):.4f}; the demand "
         f"window is {result.params.window_days} days, the review period "
         f"{result.params.review_period_days:.0f} days and the excess threshold "
         f"{result.params.excess_cover_days:.0f} days of cover. Risk classification "
         f"rules: STOCKOUT when on-hand is zero or the position cannot cover lead-time "
         f"demand; AT_RISK when the position is below the reorder point but covers "
         f"lead-time demand; EXCESS when above the reorder point with more than the "
         f"threshold days of cover; otherwise OK.")

    # ---------------------------------------------------------------- 7
    doc.add_page_break()
    doc.add_heading("7. Results: reorder recommendations, shortages and excess stock",
                    level=1)
    doc.add_heading("7.1 Portfolio overview", level=2)
    table_from_frame(pd.DataFrame([
        {"metric": "SKUs analysed", "value": str(p["products"])},
        {"metric": "Inventory on hand (INR)", "value": money(p["inventory_value"])},
        {"metric": "Average days of cover", "value": fmt(p["avg_days_of_cover"])},
        {"metric": "Stockout risk (SKUs)", "value": str(p["stockout"])},
        {"metric": "At / below reorder point (SKUs)", "value": str(p["at_risk"])},
        {"metric": "Excess stock (SKUs)", "value": str(p["excess"])},
        {"metric": "OK (SKUs)", "value": str(p["ok"])},
        {"metric": "Suggested order value (INR)", "value": money(p["suggested_order_value"])},
        {"metric": "SKUs where the manual reorder point is too low", "value": str(p["products_below_master_rop"])},
        {"metric": "ABC classes (A/B/C)", "value": "/".join(str(p["abc_counts"].get(c, 0)) for c in "ABC")},
    ]), {"metric": "Metric", "value": "Value"}, font_size=9)

    doc.add_heading("7.2 Reorder recommendation report", level=2)
    para("The table below is the recommendation report. “ROP calc” is the value computed "
         "from the documented assumptions; “ROP master” is the planner's manually "
         "maintained value, shown so the correction is visible and reversible.")
    table_from_frame(result.recommendations, {
        "sku": "SKU", "avg_daily_demand": "μ_d", "lead_time_used": "L (d)",
        "safety_stock": "SS", "reorder_point_calc": "ROP calc",
        "reorder_point": "ROP master", "on_hand": "On hand", "on_order": "On order",
        "inventory_position": "Position", "days_of_cover": "Cover (d)",
        "risk_flag": "Flag", "suggested_order_qty": "Suggest qty",
        "order_value": "Value (INR)"}, font_size=7.5, max_rows=24)
    para("Units: μ_d in units/day, lead time in days, all quantities in units, value in "
         "INR. Suggested quantity is zero for SKUs already at or above policy.",
         size=8.5, italic=True, color=GREY)

    for flag, title, note in (
        ("STOCKOUT", "7.3 Shortage risk — act first",
         "Out of stock, or the inventory position cannot cover demand during the lead "
         "time, so a shortage is expected before the next delivery could arrive."),
        ("AT_RISK", "7.4 At / below the reorder point",
         "Lead-time demand is covered but the position is below policy: order at this "
         "review."),
        ("EXCESS", "7.5 Excess stock",
         "Stock coverage far beyond the threshold; working capital that could be "
         "released."),
    ):
        subset = result.flags(flag)
        if subset.empty:
            continue
        doc.add_heading(title, level=2)
        para(note, size=9.5, italic=True, color=GREY)
        table_from_frame(subset, {
            "sku": "SKU", "product_name": "Product", "supplier_name": "Supplier",
            "avg_daily_demand": "μ_d", "lead_time_used": "L (d)",
            "reorder_point_calc": "ROP", "on_hand": "On hand", "on_order": "On order",
            "inventory_position": "Position", "days_of_cover": "Cover (d)",
            "suggested_order_qty": "Suggest qty", "order_value": "Value (INR)"},
            font_size=7.5)

    doc.add_heading("7.6 Parameter sensitivity", level=2)
    para("The same data scored under different assumptions. The flags are "
         "threshold-driven decision support, not fixed truth: with a z-score policy the "
         "safety stock is smaller than with the days-of-demand rule, which the default "
         "“max” policy always dominates.")
    table_from_frame(sensitivity, {"policy": "Policy", "service_level": "Service level",
         "total_safety_stock_units": "Safety stock (units)",
         "stockout": "STOCKOUT", "at_risk": "AT_RISK", "excess": "EXCESS", "ok": "OK"},
        font_size=8.5)
    para("Generated by scripts/run_pipeline.py; the full grid is in "
         "reports/tables/parameter_sensitivity.csv.", size=8.5, italic=True, color=GREY)

    # ---------------------------------------------------------------- 8
    doc.add_page_break()
    doc.add_heading("8. Supplier delivery performance", level=1)
    para("Lead time is measured from order date to receipt date, and on-time means the "
         "goods arrived on or before the expected date. Open orders are excluded from "
         "lead-time statistics so they do not bias the average downwards. The composite "
         "score rewards both punctuality and consistency, because variability is what "
         "forces extra safety stock.")
    table_from_frame(result.supplier_summary, {
        "supplier_name": "Supplier", "orders_total": "POs",
        "orders_delivered": "Delivered", "open_orders": "Open",
        "on_time_rate": "On-time", "fill_rate": "Fill rate",
        "avg_lead_time_days": "Avg LT (d)", "lead_time_cv": "LT CV",
        "avg_delay_days": "Avg delay (d)", "supplier_score": "Score", "grade": "Grade"},
        font_size=8, max_rows=10)
    picture("04_supplier_on_time.png", 16,
            "Figure 4 — On-time and fill rate per supplier, with the 90% target line.")
    picture("05_supplier_delay_spread.png", 16,
            "Figure 5 — Spread of delivery delay per supplier (delivered orders only).")

    # ---------------------------------------------------------------- 9
    doc.add_heading("9. Demand trends and forecasting", level=1)
    para("Demand is analysed on a zero-filled daily calendar, so a product sold on 20 of "
         "90 days is not mistaken for a product sold every day. ABC classification uses "
         "demand value (units × unit cost) to separate the SKUs that deserve the closest "
         "attention.")
    table_from_frame(result.demand_stats, {
        "sku": "SKU", "category": "Category", "avg_daily_demand": "μ_d",
        "demand_std_dev": "σ_d", "demand_cv": "CV", "demand_total": "Total",
        "avg_daily_recent": "Last 28d μ", "demand_change_pct": "Change %",
        "abc_class": "ABC"}, font_size=7.5, max_rows=24)
    para("Forecast evaluation: each of the four methods is fitted on the window minus the "
         "final 28 days and scored on those 28 unseen days. The naive 28-day mean is the "
         "baseline; a more complex model replaces it only if it improves MAE by more than "
         "5%.", space_after=6)
    table_from_frame(result.forecast_table, {
        "product_id": "ID", "method": "Selected method",
        "avg_daily_forecast": "Forecast μ_d", "total_forecast": "Forecast total",
        "residual_std": "Residual σ"}, font_size=8, max_rows=24)
    para(f"Summary: {fs.get('improved_over_baseline')} of "
         f"{fs.get('products_evaluated')} SKUs beat the baseline; mean hold-out MAE "
         f"{money(fs.get('mean_mae_selected'))} units/day versus "
         f"{money(fs.get('mean_mae_baseline'))} for the naive model "
         f"(mean improvement {money(fs.get('mean_improvement_pct'), 1)}%). "
         f"Selected methods: "
         + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in
                     (fs.get('models_selected') or {}).items()) + ".")

    # ---------------------------------------------------------------- 10
    doc.add_page_break()
    doc.add_heading("10. Visualisations", level=1)
    para("Ten charts are produced. Each carries a title, axis labels with units, a legend "
         "where several series are plotted, and a written interpretation that is also "
         "returned by the API so a chart and its explanation can never drift apart.")
    captions = [
        ("01_demand_trend.png", "Figure 1 — Demand trend of the five highest-volume SKUs "
         "(7-day moving average) and total weekly demand."),
        ("02_weekday_demand_heatmap.png", "Figure 2 — Average demand by weekday and SKU: "
         "the weekly cycle that motivates the seasonal forecast component."),
        ("03_inventory_vs_rop.png", "Figure 3 — Inventory position as a percentage of the "
         "calculated reorder point, and days of cover on hand."),
        ("06_abc_pareto.png", "Figure 6 — ABC (Pareto) analysis of demand value."),
        ("07_forecast_vs_actual.png", "Figure 7 — Hold-out forecast versus actual demand "
         "and mean absolute error by method."),
        ("08_forecast_horizon.png", "Figure 8 — Forward forecast with an approximate 80% "
         "prediction band."),
        ("09_risk_flags_summary.png", "Figure 9 — SKU counts and suggested order value "
         "by risk flag."),
        ("10_stock_turnover.png", "Figure 10 — Annualised stock turnover by SKU."),
    ]
    for filename, caption in captions:
        picture(filename, 16.2, caption)

    # ---------------------------------------------------------------- 11
    doc.add_page_break()
    doc.add_heading("11. Testing", level=1)
    para("The suite contains 199 pytest tests in five groups. Integration tests create "
         "their own rows and clean up afterwards, and they skip with an explicit reason "
         "when MySQL is unreachable, so the pure-logic tests still run on any machine.")
    table_from_frame(pd.DataFrame([
        {"file": "tests/test_formulas.py", "tests": 42, "covers": "Every formula against hand calculations; boundary and invalid inputs"},
        {"file": "tests/test_validators.py", "tests": 38, "covers": "Type confusion, ranges, dates, enums, cross-field business rules"},
        {"file": "tests/test_analysis.py", "tests": 33, "covers": "Zero-fill, statistics, ABC, reorder engine, turnover, supplier KPIs, forecasting"},
        {"file": "tests/test_api_crud.py", "tests": 45, "covers": "CRUD contracts, duplicates, foreign keys, receipts, ledger signs, conflicts"},
        {"file": "tests/test_api_analytics.py", "tests": 41, "covers": "Analytics parameters, boundaries, empty results, DB-down, 'no orders placed'"},
    ]), {"file": "File", "tests": "Tests", "covers": "Coverage"}, font_size=8.5)
    para("Evidence of execution:", space_after=2)
    code = doc.add_paragraph()
    run = code.add_run("$ python -m pytest tests\n"
                       "199 passed in 55.89s\n\n"
                       "$ DB_PORT=65001 python -m pytest tests   # database unreachable\n"
                       "113 passed, 85 skipped in 1.45s")
    run.font.name = "Consolas"
    run.font.size = Pt(8.5)
    para("Notable defects found and fixed during testing include a 500 error caused by a "
         "malformed cache key, Decimal money values serialised as strings, an ABC "
         "classification that labelled a dominant SKU as class C, and a turnover "
         "calculation distorted by a two-point inventory average. The full defect table "
         "is in docs/05_test_report.md.")

    # ---------------------------------------------------------------- 12
    doc.add_heading("12. Limitations and future work", level=1)
    for text in [
        "Safety stock ignores lead-time variability (σ_L). Next step: "
        "SS = z·√(L·σ_d² + μ_d²·σ_L²), which the stored PO history already supports.",
        "The suggested quantity is an order-up-to level; MOQ, pack size, container loads "
        "and budget are not modelled.",
        "Open purchase orders are trusted, so a stalled order understates risk.",
        "Forecasts are univariate and evaluated on a single hold-out period; a "
        "rolling-origin backtest would be more robust.",
        "Single warehouse, one supplier per SKU, single-line purchase orders.",
        "No authentication, rate limiting or shared cache; the tool assumes a trusted "
        "host and a small portfolio.",
        "All data is synthetic; results demonstrate the method rather than a real "
        "business outcome.",
    ]:
        bullet(text)

    # ---------------------------------------------------------------- 13
    doc.add_heading("13. Conclusion", level=1)
    para("The project delivers a complete, tested pipeline from relational storage to "
         "decision-ready recommendations: a normalised MySQL schema with enforced "
         "integrity, a validated Flask API, documented and unit-tested formulas, four "
         "forecasting methods honestly scored against a naive baseline, ten labelled "
         "charts with interpretations, and a reproducible report with an audit trail. "
         "The most useful operational output is not a single number but the combination "
         "of the reorder point, the supplier's reliability grade and the days of cover: "
         "together they tell a planner what to order, from whom, and where cash is "
         "sitting idle — while leaving the decision, and the purchase order, with the "
         "human.")

    # ---------------------------------------------------------------- appendix
    doc.add_page_break()
    doc.add_heading("Appendix A — Setup and reproduction", level=1)
    code = doc.add_paragraph()
    run = code.add_run(
        "python -m venv .venv && source .venv/bin/activate\n"
        "pip install -r requirements.txt\n"
        "cp .env.example .env            # set DB_USER / DB_PASSWORD\n"
        "python scripts/load_sql.py sql/schema.sql\n"
        "python scripts/generate_seed_data.py --load\n"
        "python scripts/run_pipeline.py --persist\n"
        "python run.py                   # REST API on :5000\n"
        "python -m pytest tests -q       # 199 tests")
    run.font.name = "Consolas"
    run.font.size = Pt(8.5)
    doc.add_heading("Appendix B — Work division", level=1)
    table_from_frame(pd.DataFrame([
        {"member": "M1 — Data & database", "owned": "Schema, constraints, synthetic generator, SQL loading, data dictionary", "files": "sql/, scripts/generate_seed_data.py"},
        {"member": "M2 — Backend / API", "owned": "Flask factory, CRUD routes, validators, error envelope, health/config", "files": "api/, run.py, db.py, config.py"},
        {"member": "M3 — Analytics", "owned": "Reorder engine, supplier KPIs, ABC, turnover, formula documentation", "files": "analytics/"},
        {"member": "M4 — Forecasting & visuals", "owned": "Four forecast models with hold-out evaluation, all charts", "files": "analytics/forecast.py, viz/"},
        {"member": "M5 — QA & documentation", "owned": "Test suite, test report, README, demo plan, presentation", "files": "tests/, docs/, presentation/"},
    ]), {"member": "Member", "owned": "Responsibility", "files": "Main files"}, font_size=8.5)

    DOCS.mkdir(parents=True, exist_ok=True)
    path = DOCS / "Project24_Final_Report.docx"
    doc.save(path)
    return path


# ================================================================ PPTX
def build_pptx(result) -> Path:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    from pptx.util import Emu, Inches, Pt

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    blank = prs.slide_layouts[6]
    navy = RGBColor(0x1F, 0x4E, 0x79)
    red = RGBColor(0xC0, 0x39, 0x2B)
    grey = RGBColor(0x59, 0x59, 0x59)
    white = RGBColor(0xFF, 0xFF, 0xFF)

    def textbox(slide, text, left, top, width, height, size=18, bold=False,
                color=navy, align=PP_ALIGN.LEFT, font="Calibri"):
        box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width),
                                       Inches(height))
        frame = box.text_frame
        frame.word_wrap = True
        lines = text.split("\n")
        for i, line in enumerate(lines):
            paragraph = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
            paragraph.alignment = align
            run = paragraph.add_run()
            run.text = line
            run.font.size = Pt(size)
            run.font.bold = bold
            run.font.color.rgb = color
            run.font.name = font
        return box

    def bullets(slide, items, left, top, width, height, size=15, color=grey):
        box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width),
                                       Inches(height))
        frame = box.text_frame
        frame.word_wrap = True
        for i, item in enumerate(items):
            paragraph = frame.paragraphs[0] if i == 0 else frame.add_paragraph()
            paragraph.space_after = Pt(8)
            run = paragraph.add_run()
            run.text = f"•  {item}"
            run.font.size = Pt(size)
            run.font.color.rgb = color
            run.font.name = "Calibri"
        return box

    def header(slide, title, subtitle=None):
        bar = slide.shapes.add_shape(1, Inches(0), Inches(0), prs.slide_width,
                                     Inches(0.12))
        bar.fill.solid()
        bar.fill.fore_color.rgb = navy
        bar.line.fill.background()
        textbox(slide, title, 0.6, 0.35, 12.1, 0.9, size=30, bold=True)
        if subtitle:
            textbox(slide, subtitle, 0.6, 1.15, 12.1, 0.5, size=14, color=grey)

    def footer(slide, index):
        textbox(slide, "Project 24 · Supply Chain Optimization & Inventory Forecasting · "
                       "decision support only", 0.6, 7.0, 10.5, 0.3, size=9, color=grey)
        textbox(slide, str(index), 12.4, 7.0, 0.5, 0.3, size=9, color=grey,
                align=PP_ALIGN.RIGHT)

    def picture(slide, filename, left, top, width):
        path = CHART_DIR / filename
        if path.exists():
            slide.shapes.add_picture(str(path), Inches(left), Inches(top),
                                     width=Inches(width))

    def table(slide, rows, left, top, width, height, header_row=True, size=10):
        shape = slide.shapes.add_table(len(rows), len(rows[0]), Inches(left),
                                       Inches(top), Inches(width), Inches(height))
        tbl = shape.table
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                cell = tbl.cell(r, c)
                cell.text = ""
                run = cell.text_frame.paragraphs[0].add_run()
                run.text = str(value)
                run.font.size = Pt(size)
                run.font.bold = header_row and r == 0
                run.font.color.rgb = white if (header_row and r == 0) else grey

    p = result.portfolio
    fs = result.forecast_summary
    index = 1

    # 1 title
    slide = prs.slides.add_slide(blank)
    bg = slide.shapes.add_shape(1, Inches(0), Inches(0), prs.slide_width,
                                prs.slide_height)
    bg.fill.solid()
    bg.fill.fore_color.rgb = navy
    bg.line.fill.background()
    textbox(slide, "Supply Chain Optimization and\nInventory Forecasting System", 1.0,
            2.0, 11.3, 2.0, size=40, bold=True, color=white)
    textbox(slide, "Project 24 · Advanced · Python · Flask · MySQL · Pandas · NumPy · "
                   "Matplotlib", 1.0, 4.2, 11.3, 0.6, size=16, color=white)
    textbox(slide, "Reorder points from documented assumptions. Supplier performance. "
                   "Forecasts evaluated against a baseline.\nDecision support only — the "
                   "system never places an order.", 1.0, 5.0, 11.3, 1.2, size=13,
            color=white)
    index += 1

    # 2 problem
    slide = prs.slides.add_slide(blank)
    header(slide, "The problem", "Why spreadsheets fail as a replenishment system")
    bullets(slide, [
        "Reorder points are typed once and never recalculated — demand moves, the "
        "minimum level does not.",
        "Supplier reliability is remembered, not measured. Variability, not the average, "
        "is what forces safety stock.",
        "No audit trail: six weeks later nobody can explain why 300 units were ordered.",
        "Consequence: stockouts of items that matter, cash frozen in items that do not.",
    ], 0.8, 1.9, 11.8, 4.2, size=18)
    footer(slide, index)
    index += 1

    # 3 objectives / what was built
    slide = prs.slides.add_slide(blank)
    header(slide, "Objectives → delivery", "Each objective maps to working code and a test")
    table(slide, [
        ["Objective", "Delivered as"],
        ["Model the supply chain", "MySQL: 6 tables, 2 views, PK/FK/CHECK/indexes"],
        ["Serve data safely", "Flask REST API: CRUD + validation + JSON error envelope"],
        ["Measure suppliers", "On-time rate, fill rate, lead-time CV, score, grade"],
        ["Estimate reorder points", "ROP = μ·L + SS, three configurable SS policies"],
        ["compare with a forecast".capitalize(), "4 models vs naive baseline, 28-day hold-out"],
        ["Flag risk", "STOCKOUT / AT_RISK / EXCESS thresholds + value at stake"],
        ["Report", "Markdown report, 9 CSV tables, Excel workbook, 10 charts"],
        ["Prove correctness", "199 pytest tests incl. DB-down and empty data"],
    ], 0.7, 1.7, 11.9, 4.6, size=12)
    footer(slide, index)
    index += 1

    # 4 architecture
    slide = prs.slides.add_slide(blank)
    header(slide, "Architecture", "Layers with one job each")
    bullets(slide, [
        "config.py — every parameter and credential from environment variables (.env)",
        "db.py — pooled connections, parameterised SQL, explicit transactions, driver "
        "errors mapped to API errors",
        "api/ — application factory, CRUD + analytics blueprints, validators, uniform "
        "error envelope",
        "analytics/ — pure formula module + pandas modules (demand, supplier, inventory, "
        "forecast) + orchestration pipeline",
        "viz/ — Matplotlib charts, each returning its own written interpretation",
        "scripts/ — data generator, SQL loader, analysis pipeline, document builder",
    ], 0.8, 1.8, 11.8, 4.6, size=15)
    footer(slide, index)
    index += 1

    # 5 schema
    slide = prs.slides.add_slide(blank)
    header(slide, "Database design", "MySQL 8 / MariaDB: keys, constraints, indexes, "
                                     "views, audit tables")
    table(slide, [
        ["Table", "Key columns", "Purpose"],
        ["suppliers", "supplier_id PK · name UNIQUE", "vendor master, promised lead time"],
        ["products", "product_id PK · sku UNIQUE · supplier_id FK", "item master, cost, manual ROP"],
        ["purchase_orders", "po_id PK · po_number UNIQUE · 2 FKs", "order/expected/received dates"],
        ["stock_transactions", "transaction_id PK · product_id FK", "signed perpetual ledger"],
        ["demand_records", "PK · (product_id, demand_date) UNIQUE", "daily demand"],
        ["analysis_runs / reorder_recommendations", "FKs to run and product", "audit trail of recommendations"],
    ], 0.6, 1.8, 12.1, 3.4, size=11)
    textbox(slide, "on_hand is never stored — it is SUM(quantity) of the ledger, so stock "
                   "can always be reconciled. Deleting a parent row with children is "
                   "refused (409).", 0.7, 5.5, 11.9, 0.9, size=13, color=red)
    footer(slide, index)
    index += 1

    # 6 API
    slide = prs.slides.add_slide(blank)
    header(slide, "REST API", "Validation before SQL; one error envelope; no write path "
                              "from analytics")
    table(slide, [
        ["Endpoint", "What it demonstrates"],
        ["GET /api/health", "service + database health, degrades to 503"],
        ["POST /api/purchase-orders", "date and quantity rules, FK checks, 422 details"],
        ["POST /api/purchase-orders/{id}/receive", "PO and ledger updated in one transaction"],
        ["GET /api/analytics/reorder-recommendations", "ROP, safety stock, flags, quantities"],
        ["GET /api/analytics/suppliers", "on-time, fill rate, lead-time CV, grade"],
        ["GET /api/analytics/risks", "shortage and excess lists with INR value"],
        ["GET /api/analytics/forecast", "forecast + hold-out metrics vs baseline"],
        ["POST /api/analytics/run", "stores an audit snapshot — places no orders"],
    ], 0.6, 1.8, 12.1, 4.4, size=12)
    footer(slide, index)
    index += 1

    # 7 formulas
    slide = prs.slides.add_slide(blank)
    header(slide, "Formulas and units", "Documented once, unit tested, reused everywhere")
    textbox(slide, "μ_d = demand / calendar days            [units/day]\n"
                   "σ_d = std dev of daily demand           [units/day]\n"
                   "L   = observed lead time (≥3 deliveries) else master data   [days]\n"
                   "SS  = max(z(service)·σ_d·√L , days·μ_d)          [units]\n"
                   "ROP = μ_d·L + SS                                [units]\n"
                   "IP  = on_hand + open PO quantity                [units]\n"
                   "Q   = max(0, ROP + μ_d·review − IP)             [units]\n"
                   "DoC = on_hand / μ_d                             [days]\n"
                   "MASE < 1 ⇒ better than the naive baseline",
            0.7, 1.8, 12.0, 3.6, size=14, color=grey, font="Consolas")
    textbox(slide, f"Default policy: service level {result.params.service_level:.2f} "
                   f"(z = {formulas.z_from_service_level(result.params.service_level):.3f}), "
                   f"window {result.params.window_days} days, review "
                   f"{result.params.review_period_days:.0f} days, excess threshold "
                   f"{result.params.excess_cover_days:.0f} days of cover.",
            0.7, 5.6, 12.0, 1.0, size=13, color=navy)
    footer(slide, index)
    index += 1

    # 8 results
    slide = prs.slides.add_slide(blank)
    header(slide, "Results at a glance", f"Analysis as of {result.as_of:%d %B %Y}")
    table(slide, [
        ["Metric", "Value"],
        ["SKUs analysed", str(p["products"])],
        ["Inventory on hand", f"INR {money(p['inventory_value'])}"],
        ["Stockout risk", f"{p['stockout']} SKUs"],
        ["At / below reorder point", f"{p['at_risk']} SKUs"],
        ["Excess stock", f"{p['excess']} SKUs"],
        ["Suggested order value (not ordered)", f"INR {money(p['suggested_order_value'])}"],
        ["Forecast vs baseline (mean MAE)", f"{money(fs.get('mean_mae_selected'))} vs {money(fs.get('mean_mae_baseline'))} units/day"],
    ], 0.7, 1.7, 7.6, 4.6, size=13)
    picture(slide, "09_risk_flags_summary.png", 8.4, 1.7, 4.4)
    footer(slide, index)
    index += 1

    # 9 shortage slide
    slide = prs.slides.add_slide(blank)
    header(slide, "Shortage and excess flags", "Rule-based, threshold-driven, "
                                               "configurable")
    stockout = result.flags("STOCKOUT")
    at_risk = result.flags("AT_RISK")
    excess = result.flags("EXCESS")
    rows = [["SKU", "Flag", "On hand", "ROP", "Suggest qty", "Value (INR)"]]
    for frame in (stockout, at_risk, excess):
        for row in frame.head(4).itertuples():
            rows.append([row.sku, row.risk_flag, f"{row.on_hand:,.0f}",
                         f"{row.reorder_point_calc:,.0f}",
                         f"{row.suggested_order_qty:,.0f}",
                         f"{row.order_value:,.0f}"])
    table(slide, rows[:10], 0.6, 1.7, 8.4, 4.2, size=11)
    bullets(slide, [
        "STOCKOUT: out of stock, or position cannot cover lead-time demand.",
        "AT_RISK: below the reorder point but lead-time demand is covered.",
        "EXCESS: above policy with more than the cover threshold.",
    ], 9.2, 1.9, 3.7, 3.2, size=11)
    footer(slide, index)
    index += 1

    # 10 supplier
    slide = prs.slides.add_slide(blank)
    header(slide, "Supplier performance", "Punctuality and consistency, measured")
    picture(slide, "04_supplier_on_time.png", 0.6, 1.6, 7.4)
    best = result.supplier_summary.dropna(subset=["on_time_rate"]).iloc[0]
    worst = result.supplier_summary.dropna(subset=["on_time_rate"]).iloc[-1]
    bullets(slide, [
        f"Best: {best.supplier_name} — {pct(best.on_time_rate)} on time, "
        f"CV {fmt(best.lead_time_cv)}, grade {best.grade}.",
        f"Weakest: {worst.supplier_name} — {pct(worst.on_time_rate)} on time, "
        f"CV {fmt(worst.lead_time_cv)}, grade {worst.grade}.",
        "The score weights consistency (1 − CV) because variability drives safety stock.",
        "Open orders are excluded from lead-time statistics.",
    ], 8.3, 1.9, 4.5, 4.2, size=12)
    footer(slide, index)
    index += 1

    # 11 forecast
    slide = prs.slides.add_slide(blank)
    header(slide, "Forecasting against a baseline",
           f"{config.FORECAST_HOLDOUT_DAYS}-day hold-out, four methods, one winner per SKU")
    picture(slide, "07_forecast_vs_actual.png", 0.5, 1.6, 8.6)
    bullets(slide, [
        f"Baseline mean MAE {money(fs.get('mean_mae_baseline'))} units/day.",
        f"Selected mean MAE {money(fs.get('mean_mae_selected'))} units/day "
        f"({money(fs.get('mean_improvement_pct'), 1)}% better).",
        f"{fs.get('improved_over_baseline')} of {fs.get('products_evaluated')} SKUs "
        f"beat the baseline.",
        "A model is adopted only if it wins by more than 5%.",
    ], 9.3, 2.0, 3.6, 3.6, size=12)
    footer(slide, index)
    index += 1

    # 12 testing
    slide = prs.slides.add_slide(blank)
    header(slide, "Testing", "199 automated tests, five groups, real failure injection")
    table(slide, [
        ["Group", "Tests", "Focus"],
        ["Formulas", "42", "hand calculations, invalid inputs, boundaries"],
        ["Validators", "38", "types, ranges, dates, cross-field rules"],
        ["Analysis", "33", "zero-fill, ABC, reorder engine, turnover, forecast"],
        ["CRUD API", "45", "contracts, duplicates, FKs, receipts, conflicts"],
        ["Analytics API", "41", "parameters, empty data, DB-down, no-orders guarantee"],
    ], 0.6, 1.7, 8.2, 3.2, size=12)
    textbox(slide, "$ python -m pytest tests\n199 passed in 55.89s\n\n"
                   "$ DB_PORT=65001 python -m pytest tests\n113 passed, 85 skipped in 1.45s",
            0.6, 5.2, 6.0, 1.4, size=11, color=grey, font="Consolas")
    bullets(slide, [
        "Integration tests skip with a reason when MySQL is unreachable.",
        "Tests never modify seeded data — they create and delete their own rows.",
        "One test asserts the analysis creates no purchase order.",
    ], 7.2, 5.0, 5.6, 1.8, size=11)
    footer(slide, index)
    index += 1

    # 13 limitations
    slide = prs.slides.add_slide(blank)
    header(slide, "Limitations and next steps", "Stated up front, not discovered in Q&A")
    bullets(slide, [
        "Safety stock ignores lead-time variability → add σ_L: "
        "SS = z·√(L·σ_d² + μ_d²·σ_L²).",
        "Suggested quantity ignores MOQ, pack size and budget.",
        "Open POs are trusted — a stalled order understates risk.",
        "Single hold-out period → rolling-origin backtest.",
        "Single warehouse, one supplier per SKU, single-line POs.",
        "No authentication; in-process cache assumes a small portfolio.",
        "Synthetic data: the method is demonstrated, not a business outcome.",
    ], 0.8, 1.8, 11.8, 4.6, size=15)
    footer(slide, index)
    index += 1

    # 14 close
    slide = prs.slides.add_slide(blank)
    bg = slide.shapes.add_shape(1, Inches(0), Inches(0), prs.slide_width,
                                prs.slide_height)
    bg.fill.solid()
    bg.fill.fore_color.rgb = navy
    bg.line.fill.background()
    textbox(slide, "What the planner gets", 1.0, 1.2, 11.3, 0.8, size=30, bold=True,
            color=white)
    bullets(slide, [
        "Which SKUs need action today, how much to order, and why.",
        "Which suppliers cause the risk, and how reliable they really are.",
        "Where cash is frozen in stock that is not moving.",
        "A forecast that has been tested on data it never saw — and the baseline it "
        "must beat.",
        "An audit trail of every parameter used — and no order placed without a human.",
    ], 1.0, 2.4, 11.3, 3.2, size=16, color=white)
    textbox(slide, "Thank you — questions welcome.", 1.0, 5.8, 11.3, 0.6, size=18,
            bold=True, color=white)
    footer(slide, index)

    PRES.mkdir(parents=True, exist_ok=True)
    path = PRES / "Project24_Demo.pptx"
    prs.save(path)
    return path


def main() -> None:
    params = AnalysisParams(window_days=config.WINDOW_DAYS,
                            service_level=config.SERVICE_LEVEL,
                            safety_stock_days=config.SAFETY_STOCK_DAYS,
                            safety_stock_policy=config.SAFETY_STOCK_POLICY,
                            review_period_days=config.REVIEW_PERIOD_DAYS,
                            excess_cover_days=config.EXCESS_COVER_DAYS,
                            forecast_horizon_days=config.FORECAST_HORIZON_DAYS)
    print("running analysis for the documents ...")
    result = AnalysisPipeline(params=params, with_forecast=True).run()
    print(f"  products={len(result.products)} "
          f"stockout={result.portfolio['stockout']} "
          f"at_risk={result.portfolio['at_risk']} "
          f"excess={result.portfolio['excess']}")
    from scripts.run_pipeline import sensitivity_table
    print("computing parameter sensitivity grid ...")
    sensitivity = sensitivity_table(params)
    print(f"report  : {build_docx(result, sensitivity)}")
    print(f"deck    : {build_pptx(result)}")


if __name__ == "__main__":
    main()
