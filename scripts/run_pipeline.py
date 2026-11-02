"""End-to-end analysis run: tables -> charts -> Markdown report -> Excel workbook.

Usage
-----
    python scripts/run_pipeline.py                  # analyse + write everything
    python scripts/run_pipeline.py --persist        # also store the run in MySQL
    python scripts/run_pipeline.py --window 120 --service-level 0.99
    python scripts/run_pipeline.py --csv-only       # skip charts (fast, CI)

Outputs (all under reports/)
---------------------------
charts/*.png                 ten labelled charts
tables/*.csv                 every summary table used in the report
tables/analysis_workbook.xlsx  all tables in one workbook (one sheet each)
analysis_report.md           generated report with numbers, formulas, charts
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from analytics import formulas                                                       # noqa: E402
from analytics.pipeline import AnalysisPipeline, to_records                          # noqa: E402
from api.validators import AnalysisParams                                            # noqa: E402
from config import config                                                            # noqa: E402

REPORT_DIR = Path(config.REPORT_DIR)
TABLE_DIR = REPORT_DIR / "tables"
CHART_DIR = REPORT_DIR / "charts"


# ------------------------------------------------------------------ helpers
def money(value, digits=0) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value:,.{digits}f}"


def pct(value, digits=1) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    return f"{value * 100:.{digits}f}%"


def md_table(df: pd.DataFrame, columns: dict[str, str] | None = None,
             max_rows: int = 30, digits: int = 2) -> str:
    """Render a DataFrame as a GitHub-flavoured Markdown table."""
    if df is None or len(df) == 0:
        return "_No rows for this selection._\n"
    frame = df.head(max_rows).copy()
    if columns:
        frame = frame[[c for c in columns if c in frame.columns]]
        frame = frame.rename(columns={k: v for k, v in columns.items() if k in df.columns})
    header = "| " + " | ".join(str(c) for c in frame.columns) + " |"
    divider = "|" + "|".join(["---"] * len(frame.columns)) + "|"
    lines = [header, divider]
    for _, row in frame.iterrows():
        cells = []
        for value in row:
            if isinstance(value, float):
                if pd.isna(value):
                    cells.append("n/a")
                elif abs(value) >= 1000:
                    cells.append(f"{value:,.{digits}f}")
                else:
                    cells.append(f"{value:.{digits}f}")
            elif value is None or (isinstance(value, (pd.Timestamp,)) and pd.isna(value)):
                cells.append("n/a")
            else:
                cells.append(str(value))
        lines.append("| " + " | ".join(cells) + " |")
    if len(df) > max_rows:
        lines.append(f"| _... {len(df) - max_rows} more rows in the CSV export_ |"
                     + "|" * (len(frame.columns) - 1))
    return "\n".join(lines) + "\n"


def write_csvs(result) -> dict[str, Path]:
    TABLE_DIR.mkdir(parents=True, exist_ok=True)
    tables = {
        "reorder_recommendations": result.recommendations,
        "supplier_delivery_summary": result.supplier_summary,
        "demand_statistics": result.demand_stats,
        "inventory_turnover": result.turnover,
        "forecast_comparison": result.forecast_compare,
        "forecast_summary": result.forecast_table,
        "future_forecast_lines": result.forecast_lines,
        "delivery_trend_monthly": result.delivery_trend,
        "category_demand": result.category_demand,
    }
    written = {}
    for name, frame in tables.items():
        path = TABLE_DIR / f"{name}.csv"
        frame.to_csv(path, index=False)
        written[name] = path
    return written


def write_excel(result, path: Path) -> Path:
    """All tables in one workbook, plus a documentation sheet."""
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame([
            ["Project", "Supply Chain Optimization and Inventory Forecasting System"],
            ["Generated", date.today().isoformat()],
            ["Analysis as-of date", result.as_of.isoformat()],
            ["Demand window", f"{result.windows['start']} .. {result.windows['end']}"],
            ["Window days", result.params.window_days],
            ["Service level", result.params.service_level],
            ["Safety-stock policy", result.params.safety_stock_policy],
            ["Safety-stock days", result.params.safety_stock_days],
            ["Review period (days)", result.params.review_period_days],
            ["Excess cover threshold (days)", result.params.excess_cover_days],
            ["Forecast horizon (days)", result.params.forecast_horizon_days],
            ["Data", "Synthetic dataset generated by scripts/generate_seed_data.py"],
            ["Disclaimer", "Decision support only - no purchase orders are placed"],
        ], columns=["Item", "Value"]).to_excel(writer, sheet_name="00_parameters", index=False)
        sheets = {
            "01_reorder_recommendations": result.recommendations,
            "02_supplier_summary": result.supplier_summary,
            "03_demand_statistics": result.demand_stats,
            "04_inventory_turnover": result.turnover,
            "05_forecast_comparison": result.forecast_compare,
            "06_forecast_summary": result.forecast_table,
            "07_delivery_trend": result.delivery_trend,
            "08_category_demand": result.category_demand,
        }
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name[:31], index=False)
    return path


def sensitivity_table(base_params) -> pd.DataFrame:
    """How the flags move when the two policy levers change.

    Runs the analysis (without forecasting) for a grid of service levels and
    safety-stock policies so the report can show that the thresholds, not the
    code, decide what is flagged.
    """
    rows = []
    for policy in ("zscore", "days", "max"):
        for level in (0.90, 0.95, 0.99):
            if policy == "days" and level != 0.95:
                continue          # service level does not enter the 'days' policy
            params = AnalysisParams(window_days=base_params.window_days,
                                    service_level=level,
                                    safety_stock_days=base_params.safety_stock_days,
                                    safety_stock_policy=policy,
                                    review_period_days=base_params.review_period_days,
                                    excess_cover_days=base_params.excess_cover_days,
                                    forecast_horizon_days=base_params.forecast_horizon_days)
            run = AnalysisPipeline(params=params, with_forecast=False).run()
            rec = run.recommendations
            rows.append({
                "policy": policy,
                "service_level": level,
                "total_safety_stock_units": round(float(rec["safety_stock"].sum()), 1),
                "total_rop_units": round(float(rec["reorder_point_calc"].sum()), 1),
                "stockout": int((rec["risk_flag"] == "STOCKOUT").sum()),
                "at_risk": int((rec["risk_flag"] == "AT_RISK").sum()),
                "excess": int((rec["risk_flag"] == "EXCESS").sum()),
                "ok": int((rec["risk_flag"] == "OK").sum()),
                "suggested_order_value": round(float(rec["order_value"].sum()), 2),
            })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ report
def build_report(result, charts: list[dict], csv_paths: dict[str, Path],
                 sensitivity: pd.DataFrame | None = None) -> str:
    rec = result.recommendations
    portfolio = result.portfolio
    fsum = result.forecast_summary
    w = result.windows
    params = result.params

    stockout = result.flags("STOCKOUT")
    at_risk = result.flags("AT_RISK")
    excess = result.flags("EXCESS")

    doc = []
    doc.append("# Supply Chain Optimization and Inventory Forecasting System\n")
    doc.append("### Automated analysis report\n")
    doc.append(f"*Generated on {date.today():%d %B %Y} · data as of "
               f"{result.as_of:%d %B %Y} · demo/synthetic dataset*\n")
    doc.append(
        "> **Decision support only.** Every recommendation below is a calculation for "
        "a human planner to review. The system never creates, approves or sends a "
        "purchase order.\n")

    # ---- 1. summary
    doc.append("\n## 1. Executive summary\n")
    doc.append(f"- **Portfolio:** {portfolio['products']} SKUs across "
               f"{len(result.supplier_summary)} suppliers; inventory on hand worth "
               f"INR {money(portfolio['inventory_value'])}.")
    doc.append(f"- **Shortage risk:** {portfolio['stockout']} SKU(s) at stockout risk and "
               f"{portfolio['at_risk']} below the calculated reorder point; reviewing "
               f"them now would require INR {money(portfolio['suggested_order_value'])} "
               f"of purchases (suggestion only).")
    doc.append(f"- **Excess risk:** {portfolio['excess']} SKU(s) hold more than "
               f"{params.excess_cover_days:.0f} days of cover.")
    doc.append(f"- **Forecasting:** the selected models average "
               f"{money(fsum.get('mean_mae_selected'), 2)} units/day error versus "
               f"{money(fsum.get('mean_mae_baseline'), 2)} for the naive baseline, "
               f"beating the baseline on {fsum.get('improved_over_baseline', 0)} of "
               f"{fsum.get('products_evaluated', 0)} SKUs.")
    doc.append(f"- **Suppliers:** lowest on-time rate "
               f"{pct(result.supplier_summary['on_time_rate'].min())}, highest "
               f"{pct(result.supplier_summary['on_time_rate'].max())}.\n")

    # ---- 2. data & method
    doc.append("## 2. Data and method\n")
    doc.append(f"| Item | Value |\n|---|---|\n"
               f"| Demand window | {w['start']} to {w['end']} ({params.window_days} days) |\n"
               f"| Supplier PO window | {w.get('supplier_start')} to {w['end']} "
               f"({config.SUPPLIER_WINDOW_DAYS} days) |\n"
               f"| Target cycle service level | {params.service_level:.3f} "
               f"(z = {formulas.z_from_service_level(params.service_level):.4f}) |\n"
               f"| Safety-stock policy | `{params.safety_stock_policy}` "
               f"(days policy value: {params.safety_stock_days} days) |\n"
               f"| Review period | {params.review_period_days} days |\n"
               f"| Excess cover threshold | {params.excess_cover_days:.0f} days of cover |\n"
               f"| Forecast horizon | {params.forecast_horizon_days} days |\n"
               f"| Hold-out period for scoring | {config.FORECAST_HOLDOUT_DAYS} days |\n")
    doc.append("\n**Formulas (units in brackets):**\n")
    doc.append("```text\n"
               "average daily demand  mu_d     = total demand / calendar days in window      [units/day]\n"
               "demand variability    sigma_d  = sample std dev of daily demand (0-filled)   [units/day]\n"
               "lead time             L        = mean observed PO lead time if >= 3 deliveries,\n"
               "                                 else products.lead_time_days                  [days]\n"
               "safety stock (z)      SS_z     = z(service level) * sigma_d * sqrt(L)          [units]\n"
               "safety stock (days)   SS_d     = safety_stock_days * mu_d                      [units]\n"
               "safety stock used     SS       = max(SS_z, SS_d)   [policy = max, default]     [units]\n"
               "reorder point         ROP      = mu_d * L + SS                                [units]\n"
               "inventory position    IP       = on_hand + open PO quantity                   [units]\n"
               "order-up-to level     T        = ROP + mu_d * review_period_days              [units]\n"
               "suggested order qty   Q        = max(0, T - IP)                               [units]\n"
               "days of cover         DoC      = on_hand / mu_d                               [days]\n"
               "stock turnover        TO       = (units issued * unit_cost) / average stock value [turns]\n"
               "supplier score        Score    = 100 * (0.6*on_time_rate + 0.4*max(0,1-CV))   [0-100]\n"
               "MASE                           = MAE(model) / MAE(naive in-sample)            [ratio]\n"
               "```\n")

    # ---- 3. shortage & excess
    doc.append("\n## 3. Reorder recommendations, shortage and excess flags\n")
    doc.append(f"Classification: **STOCKOUT** = out of stock or inventory position below "
               f"lead-time demand; **AT_RISK** = position below ROP but lead-time demand "
               f"covered; **EXCESS** = above ROP with more than "
               f"{params.excess_cover_days:.0f} days of cover; **OK** otherwise.\n")
    doc.append(md_table(
        rec, {
            "sku": "SKU", "product_name": "Product", "supplier_name": "Supplier",
            "avg_daily_demand": "Avg demand (units/day)",
            "lead_time_used": "Lead time (days)",
            "safety_stock": "Safety stock (units)",
            "reorder_point_calc": "ROP calc (units)",
            "reorder_point": "ROP master (units)",
            "on_hand": "On hand (units)", "on_order": "On order (units)",
            "inventory_position": "Position (units)",
            "days_of_cover": "Cover (days)", "risk_flag": "Flag",
            "suggested_order_qty": "Suggested qty (units)",
            "order_value": "Value (INR)",
        }, max_rows=24))
    doc.append(f"\n*Full table: `reports/tables/reorder_recommendations.csv`*\n")

    if not stockout.empty:
        doc.append("\n### 3.1 Stockout risk (act first)\n")
        doc.append(md_table(stockout, {
            "sku": "SKU", "product_name": "Product",
            "avg_daily_demand": "Avg demand (units/day)",
            "lead_time_used": "Lead time (days)",
            "on_hand": "On hand (units)", "on_order": "On order (units)",
            "reorder_point_calc": "ROP (units)", "days_of_cover": "Cover (days)",
            "suggested_order_qty": "Suggested qty (units)",
            "order_value": "Value (INR)"}))
    if not at_risk.empty:
        doc.append("\n### 3.2 At / below reorder point\n")
        doc.append(md_table(at_risk, {
            "sku": "SKU", "avg_daily_demand": "Avg demand (units/day)",
            "inventory_position": "Position (units)", "reorder_point_calc": "ROP (units)",
            "suggested_order_qty": "Suggested qty (units)",
            "order_value": "Value (INR)"}))
    if not excess.empty:
        doc.append("\n### 3.3 Excess stock\n")
        doc.append(md_table(excess, {
            "sku": "SKU", "product_name": "Product", "on_hand": "On hand (units)",
            "on_hand_value": "Value (INR)", "days_of_cover": "Cover (days)",
            "risk_flag": "Flag"}))

    # ---- 4. demand
    doc.append("\n## 4. Demand analysis\n")
    doc.append(md_table(result.demand_stats, {
        "sku": "SKU", "category": "Category",
        "avg_daily_demand": "Avg demand (units/day)",
        "demand_std_dev": "Std dev (units/day)", "demand_cv": "CV",
        "demand_total": "Total demand (units)",
        "avg_daily_recent": "Last 28d avg (units/day)",
        "demand_change_pct": "Change (%)", "trend_slope": "Trend slope (units/day per day)",
        "abc_class": "ABC"}, max_rows=24))

    # ---- 5. suppliers
    doc.append("\n## 5. Supplier delivery performance\n")
    doc.append(md_table(result.supplier_summary, {
        "supplier_name": "Supplier", "orders_total": "POs",
        "orders_delivered": "Delivered", "open_orders": "Open",
        "on_time_rate": "On-time rate", "fill_rate": "Fill rate",
        "avg_lead_time_days": "Avg lead time (days)",
        "lead_time_cv": "Lead-time CV", "avg_delay_days": "Avg delay (days)",
        "supplier_score": "Score (0-100)", "grade": "Grade"}, max_rows=10))
    doc.append("\n*Definitions: lead time = received_date - order_date; delay = "
               "received_date - expected_date (positive = late). Open orders are "
               "excluded from lead-time statistics.*\n")

    # ---- 6. turnover
    doc.append("\n## 6. Inventory turnover\n")
    doc.append(md_table(result.turnover.sort_values("annualised_turns",
                                                   ascending=False),
                        {"sku": "SKU", "units_issued": "Units issued",
                         "opening_stock": "Opening (units)",
                         "closing_stock": "Closing (units)",
                         "average_inventory_value": "Avg stock value (INR)",
                         "turns": "Turns (period)", "days_of_inventory": "Days of stock",
                         "annualised_turns": "Turns (annualised)"}, max_rows=24))

    # ---- 7. forecast
    doc.append("\n## 7. Demand forecasting and evaluation\n")
    doc.append(f"Models: naive mean (baseline), seasonal naive (last week), 14-day "
               f"moving average and Holt-Winters (additive trend + weekly seasonality). "
               f"Each model is fitted on everything except the last "
               f"{config.FORECAST_HOLDOUT_DAYS} days and scored on those days; the "
               f"baseline is kept unless another model is more than 5% better.\n")
    doc.append(f"- Products evaluated: **{fsum.get('products_evaluated')}**")
    doc.append(f"- Models chosen: {json.dumps(fsum.get('models_selected', {}))}")
    doc.append(f"- Mean hold-out MAE — baseline: "
               f"**{money(fsum.get('mean_mae_baseline'), 3)}**, selected: "
               f"**{money(fsum.get('mean_mae_selected'), 3)}** "
               f"(mean improvement {money(fsum.get('mean_improvement_pct'), 1)}%)")
    doc.append(f"- SKUs where the selected model beat the baseline: "
               f"**{fsum.get('improved_over_baseline')} / "
               f"{fsum.get('products_evaluated')}**\n")
    doc.append(md_table(result.forecast_table, {
        "product_id": "ID", "method": "Selected method",
        "avg_daily_forecast": "Forecast (units/day)",
        "total_forecast": "Forecast total (units)",
        "residual_std": "Residual std (units/day)"}, max_rows=24))

    # ---- 7b. sensitivity
    if sensitivity is not None and not sensitivity.empty:
        doc.append("\n### 7.1 Parameter sensitivity of the reorder flags\n")
        doc.append("The same data scored under different assumptions. This is the "
                   "clearest evidence that the flags are *threshold-driven decision "
                   "support*, not fixed truth:\n")
        doc.append(md_table(sensitivity, {
            "policy": "Safety-stock policy", "service_level": "Service level",
            "total_safety_stock_units": "Total safety stock (units)",
            "total_rop_units": "Total ROP (units)",
            "stockout": "STOCKOUT", "at_risk": "AT_RISK", "excess": "EXCESS",
            "ok": "OK", "suggested_order_value": "Suggested order value (INR)"},
            max_rows=12))

    # ---- 8. charts
    doc.append("\n## 8. Charts and interpretation\n")
    for i, chart in enumerate(charts, start=1):
        doc.append(f"\n### 8.{i} {chart['title']}\n")
        doc.append(f"![{chart['title']}](charts/{chart['file']})\n")
        doc.append(f"**Interpretation.** {chart['interpretation']}\n")

    # ---- 9. assumptions & limits
    doc.append("\n## 9. Assumptions and limitations\n")
    doc.append("""
1. **Synthetic data.** The dataset is simulated (see `scripts/generate_seed_data.py`);
   results demonstrate the method, not a real business.
2. **Zero-demand days.** Days with no row in `demand_records` are treated as zero
   demand, so averages use calendar days, not "days with a sale".
3. **Lead time is calendar-based** and demand is treated as independent between
   days (`sqrt(L)` scaling). Strongly correlated demand would need a different model.
4. **Observed vs master lead time.** After 3 deliveries the mean observed lead time
   replaces `products.lead_time_days`; small samples are noisy.
5. **Safety stock ignores supplier variability** (no `sigma_L` term) unless the
   z-score policy is combined with a longer lead time; a `sigma_L` extension is
   listed as future work.
6. **Order quantity is a simple order-up-to level**, not an EOQ or MOQ-constrained
   quantity.
7. **On-order quantity trusts open POs**; an open PO that will never be delivered
   (see SKU with a stalled order) overstates the inventory position.
8. **Forecasts** are simple and univariate: no promotions, price or weather inputs,
   and the evaluation uses a single hold-out period.
9. **Costs** use `unit_cost` only; holding, ordering and stockout penalty costs are
   not modelled.
10. **Timezone/date edge cases** at month ends and partial weeks are handled by
    excluding incomplete weeks from weekly totals.
""")

    # ---- 10. how to reproduce
    doc.append("\n## 10. Reproducing this report\n")
    doc.append("```bash\n"
               "python scripts/generate_seed_data.py --load   # build synthetic data\n"
               "python scripts/run_pipeline.py                # tables + charts + report\n"
               "python -m pytest tests -q                     # 197 automated tests\n"
               "python run.py                                 # REST API on :5000\n"
               "```\n")
    doc.append("\n---\n*Generated by `scripts/run_pipeline.py`. Numbers in this "
               "report are recomputed from the database on every run.*\n")
    return "\n".join(doc)


# ------------------------------------------------------------------ main
def main() -> None:
    parser = argparse.ArgumentParser(description="Run the full analysis pipeline")
    parser.add_argument("--window", type=int, default=config.WINDOW_DAYS)
    parser.add_argument("--service-level", type=float, default=config.SERVICE_LEVEL)
    parser.add_argument("--safety-stock-days", type=float, default=config.SAFETY_STOCK_DAYS)
    parser.add_argument("--policy", default=config.SAFETY_STOCK_POLICY,
                        choices=["zscore", "days", "max"])
    parser.add_argument("--review-period", type=float, default=config.REVIEW_PERIOD_DAYS)
    parser.add_argument("--excess-cover-days", type=float, default=config.EXCESS_COVER_DAYS)
    parser.add_argument("--horizon", type=int, default=config.FORECAST_HORIZON_DAYS)
    parser.add_argument("--persist", action="store_true",
                        help="store the run in analysis_runs / reorder_recommendations")
    parser.add_argument("--csv-only", action="store_true", help="skip chart rendering")
    parser.add_argument("--no-forecast", action="store_true", help="skip forecasting")
    parser.add_argument("--no-sensitivity", action="store_true",
                        help="skip the service-level / policy sensitivity grid")
    args = parser.parse_args()

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    TABLE_DIR.mkdir(parents=True, exist_ok=True)

    params = AnalysisParams(window_days=args.window, service_level=args.service_level,
                            safety_stock_days=args.safety_stock_days,
                            safety_stock_policy=args.policy,
                            review_period_days=args.review_period,
                            excess_cover_days=args.excess_cover_days,
                            forecast_horizon_days=args.horizon)
    print(f"Running analysis: window={params.window_days}d "
          f"service_level={params.service_level} policy={params.safety_stock_policy}")
    result = AnalysisPipeline(params=params, with_forecast=not args.no_forecast,
                              persist=args.persist).run()

    csv_paths = write_csvs(result)
    print(f"tables  : {len(csv_paths)} CSV files in {TABLE_DIR}")
    workbook = write_excel(result, TABLE_DIR / "analysis_workbook.xlsx")
    print(f"workbook: {workbook.name}")

    charts: list[dict] = []
    if not args.csv_only:
        from viz import charts as chart_module
        charts = chart_module.build_all(result)
        print(f"charts  : {len(charts)} PNG files in {CHART_DIR}")

    sensitivity = None
    if not args.no_sensitivity:
        print("computing parameter sensitivity (6 extra runs, no forecasting) ...")
        sensitivity = sensitivity_table(params)
        sensitivity.to_csv(TABLE_DIR / "parameter_sensitivity.csv", index=False)

    report = build_report(result, charts, csv_paths, sensitivity)
    report_path = REPORT_DIR / "analysis_report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"report  : {report_path}")

    summary = {
        "generated": date.today().isoformat(),
        "as_of": result.as_of.isoformat(),
        "parameters": result.params.as_dict(),
        "portfolio": result.portfolio,
        "forecast": result.forecast_summary,
        "run_id": result.run_id,
        "charts": [c["file"] for c in charts],
        "tables": [p.name for p in csv_paths.values()],
    }
    (REPORT_DIR / "analysis_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print(f"summary : {REPORT_DIR / 'analysis_summary.json'}")
    print(json.dumps({"stockout": result.portfolio["stockout"],
                      "at_risk": result.portfolio["at_risk"],
                      "excess": result.portfolio["excess"],
                      "ok": result.portfolio["ok"]}))


if __name__ == "__main__":
    main()
