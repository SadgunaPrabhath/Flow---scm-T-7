"""Build the single self-contained showcase page (docs/project_showcase.html).

The page is an editorial, scroll-driven site: variable-font display type, live reorder
calculator, forecast explorer, filterable stock and supplier views, the ten charts and
real API responses. Everything (fonts, charts, data) is embedded, so it opens in any
browser with no server, no network and no database.

By default the page is built OFFLINE from the files the pipeline already wrote:

    reports/tables/*.csv, reports/analysis_summary.json, reports/analysis_report.md,
    reports/charts/*.png, docs/api_samples.json

To refresh the API samples from a running database first:

    python scripts/build_showcase.py --live        # needs MySQL + the seeded data
    python scripts/build_showcase.py               # offline rebuild

Edit PROJECT_AUTHOR below (or set SHOWCASE_AUTHOR) to change the credit line.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROJECT_AUTHOR = os.environ.get("SHOWCASE_AUTHOR", "S. Prabhath")
REPORTS = ROOT / "reports"
DOCS = ROOT / "docs"
FONTS = DOCS / "assets" / "fonts"
TEMPLATE = Path(__file__).with_name("showcase_template.html")
SAMPLES = DOCS / "api_samples.json"
OUT = DOCS / "project_showcase.html"

CHARTS = [
    ("01_demand_trend.png", "Demand trend"),
    ("02_weekday_demand_heatmap.png", "Weekday rhythm"),
    ("03_inventory_vs_rop.png", "Inventory vs reorder point"),
    ("04_supplier_on_time.png", "Supplier on-time rates"),
    ("05_supplier_delay_spread.png", "Spread of delivery delay"),
    ("06_abc_pareto.png", "ABC Pareto"),
    ("07_forecast_vs_actual.png", "Forecast vs actual"),
    ("08_forecast_horizon.png", "Forecast horizon"),
    ("09_risk_flags_summary.png", "Risk flags"),
    ("10_stock_turnover.png", "Stock turnover"),
]


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def interpretations() -> dict[str, str]:
    """Pull each chart's written interpretation out of the generated report."""
    text = (REPORTS / "analysis_report.md").read_text(encoding="utf-8")
    found: dict[str, str] = {}
    for match in re.finditer(r"!\[[^\]]*\]\(charts/([^)]+)\)\s*\n+\*\*Interpretation\.\*\*\s*(.+?)(?:\n\n|\Z)",
                             text, re.S):
        found[match.group(1)] = " ".join(match.group(2).split())
    return found


def capture_live_samples() -> None:
    from api.app import create_app            # imported lazily: needs a database

    client = create_app().test_client()
    samples = []
    for method, path, label in [
        ("GET", "/api/health", "Service + database health"),
        ("GET", "/api/products?limit=3", "Products (paginated)"),
        ("GET", "/api/analytics/reorder-recommendations?limit=3", "Reorder recommendations"),
        ("GET", "/api/analytics/suppliers", "Supplier delivery summary"),
        ("GET", "/api/analytics/risks", "Shortage / excess risk lists"),
        ("POST", "/api/products", "Validation error (missing fields) -> 422"),
    ]:
        kwargs = {"json": {"sku": "BAD"}} if method == "POST" else {}
        response = getattr(client, method.lower())(path, **kwargs)
        body = response.get_data(as_text=True)
        try:
            body = json.dumps(json.loads(body), indent=2)
        except ValueError:
            pass
        if len(body) > 2600:
            body = body[:2600] + "\n... (truncated for display)"
        samples.append(dict(method=method, path=path, status=response.status_code,
                            label=label, body=body))
    SAMPLES.write_text(json.dumps(samples, indent=2), encoding="utf-8")
    print(f"captured {len(samples)} live API responses")


def build_data() -> dict:
    tables = REPORTS / "tables"
    summary = json.loads((REPORTS / "analysis_summary.json").read_text())
    rec = pd.read_csv(tables / "reorder_recommendations.csv")
    sup = pd.read_csv(tables / "supplier_delivery_summary.csv")
    comp = pd.read_csv(tables / "forecast_comparison.csv")
    fut = pd.read_csv(tables / "future_forecast_lines.csv")
    trend = pd.read_csv(tables / "delivery_trend_monthly.csv")

    products = [dict(
        id=int(r.product_id), sku=r.sku, name=r.product_name, supplier=r.supplier_name,
        category=r.category, abc=r.abc_class, flag=r.risk_flag,
        mu=round(float(r.avg_daily_demand), 3), sd=round(float(r.demand_std_dev), 3),
        lt=round(float(r.lead_time_used), 2), pos=round(float(r.inventory_position), 1),
        rop=round(float(r.reorder_point_calc), 1), cover=9999 if pd.isna(r.days_of_cover) else round(float(r.days_of_cover), 1),  # 9999 = no demand
        qty=round(float(r.suggested_order_qty), 0), value=round(float(r.order_value), 0),
    ) for r in rec.itertuples()]

    names = {p["id"]: p for p in products}
    order = ["naive_mean", "moving_average", "holt_winters", "seasonal_naive"]
    fc: dict[int, list] = {}
    for pid, grp in comp.groupby("product_id"):
        grp = grp.assign(_o=grp.method.map({m: i for i, m in enumerate(order)})).sort_values("_o")
        fc[int(pid)] = [dict(m=r.method, mae=float(r.mae), rmse=float(r.rmse),
                             mase=None if pd.isna(r.mase) else float(r.mase),
                             bias=None if pd.isna(r.bias) else float(r.bias),
                             sel=bool(r.selected)) for r in grp.itertuples()]

    future = {int(pid): [[str(r.date)[:10], round(float(r.forecast), 1),
                          round(float(r.lower_80), 1), round(float(r.upper_80), 1)]
                         for r in grp.itertuples()] for pid, grp in fut.groupby("product_id")}
    future_ids = sorted(future, key=lambda i: -sum(row[1] for row in future[i]))

    interp = interpretations()
    as_of = datetime.strptime(summary["as_of"], "%Y-%m-%d")

    return dict(
        as_of=summary["as_of"], as_of_h=f"{as_of.day} {as_of:%B %Y}",
        params=summary["parameters"], portfolio=summary["portfolio"], forecast=summary["forecast"],
        products=products,
        suppliers=[dict(name=r.supplier_name, orders=int(r.orders_total), open=int(r.open_orders),
                        lead=float(r.avg_lead_time_days), cv=float(r.lead_time_cv),
                        ot=float(r.on_time_rate), fill=float(r.fill_rate),
                        score=float(r.supplier_score), grade=r.grade) for r in sup.itertuples()],
        trend=[dict(month=str(r.month)[:7], ot=float(r.on_time_rate), orders=int(r.orders))
               for r in trend.itertuples()],
        forecast_products=[dict(id=p["id"], sku=p["sku"], name=p["name"]) for p in products],
        fc=fc, future=future, future_ids=future_ids,
        future_skus=[names[i]["sku"] for i in future_ids],
        chart_order=[f for f, _ in CHARTS],
        api=json.loads(SAMPLES.read_text(encoding="utf-8")),
    ), interp


def safe_json(obj) -> str:
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="re-capture API samples from the live app")
    args = parser.parse_args()
    if args.live:
        capture_live_samples()

    data, interp = build_data()
    charts = {}
    for filename, title in CHARTS:
        path = REPORTS / "charts" / filename
        if path.exists():
            charts[filename] = dict(
                title=title, src="data:image/png;base64," + b64(path),
                text=interp.get(filename, "Generated by viz/charts.py."))
    data["chart_order"] = [f for f, _ in CHARTS if f in charts]

    html = TEMPLATE.read_text(encoding="utf-8")
    for key, value in {
        "__FONT_FRAUNCES_I__": b64(FONTS / "Fraunces-var-italic.woff2"),   # before the shorter key
        "__FONT_FRAUNCES__": b64(FONTS / "Fraunces-var.woff2"),
        "__FONT_INTER__": b64(FONTS / "InterTight-var.woff2"),
        "__DATA__": safe_json(data),
        "__CHARTS__": safe_json(charts),
        "__AUTHOR__": PROJECT_AUTHOR,
        "__GENERATED__": f"{date.today():%d %B %Y}",
    }.items():
        html = html.replace(key, value)
    OUT.write_text(html, encoding="utf-8")
    print(f"written {OUT} ({OUT.stat().st_size / 1024 / 1024:.1f} MB, {len(charts)} charts embedded)")


if __name__ == "__main__":
    main()
