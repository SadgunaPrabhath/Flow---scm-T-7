"""Analytics REST endpoints (read-only decision support + chart images).

Every endpoint documents the parameters it accepts in its JSON payload
(`parameters` block) so a reviewer can see exactly which assumptions
produced the numbers.
"""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from flask import Blueprint, jsonify, request, send_file, send_from_directory

from analytics import forecast as fc
from analytics.pipeline import AnalysisPipeline, summarise_forecast, to_records
from api.errors import ApiError, NotFoundError
from api.validators import analysis_params, clean_number
from config import config

analytics_bp = Blueprint("analytics", __name__, url_prefix="/api")

# Small in-process cache keyed by the request arguments; the dataset is static
# during a demo, so this keeps repeated calls fast without hiding changes
# (any write invalidates nothing here because analytics endpoints are read-only).
_CACHE: dict[tuple, object] = {}
_CACHE_LIMIT = 8


def _cfg():
    from flask import current_app
    return current_app.config["APP_CFG"]


def _run():
    """Run the pipeline with validated query parameters.

    The cache key is the *parameter set*, not the URL, so every analytics
    endpoint called with the same assumptions reuses one run instead of
    recomputing the portfolio (the forecast step is the expensive part).
    """
    cfg = _cfg()
    params = analysis_params(cfg)
    key = (cfg.DB_NAME, params.window_days, params.service_level,
           params.safety_stock_days, params.safety_stock_policy,
           params.review_period_days, params.excess_cover_days,
           params.forecast_horizon_days)
    if key in _CACHE:
        return _CACHE[key]
    result = AnalysisPipeline(params=params, with_forecast=True).run()
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = result
    return result


def _window_payload(result) -> dict:
    w = result.windows
    return {k: (v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else v)
            for k, v in w.items()}


# =====================================================================
# 1. reorder recommendations
# =====================================================================
@analytics_bp.get("/analytics/reorder-recommendations")
def reorder_recommendations():
    """ROP, safety stock, risk flag and suggested quantity per SKU."""
    result = _run()
    flag = (request.args.get("flag") or "").upper() or None
    limit = min(int(request.args.get("limit", 200)), 1000)
    rec = result.recommendations
    product_id = request.args.get("product_id", type=int)
    if product_id:
        rec = rec[rec["product_id"] == product_id]
    if flag:
        if flag not in ("STOCKOUT", "AT_RISK", "EXCESS", "OK"):
            raise ApiError("flag must be one of STOCKOUT, AT_RISK, EXCESS, OK.",
                           status=422, code="VALIDATION_ERROR",
                           details=[{"field": "flag", "message": "Invalid flag."}])
        rec = rec[rec["risk_flag"] == flag]
    columns = ["sku", "product_name", "category", "supplier_name", "avg_daily_demand",
               "demand_std_dev", "lead_time_used", "lead_time_source", "safety_stock",
               "reorder_point_calc", "reorder_point", "gap_vs_master_rop", "on_hand",
               "on_order", "inventory_position", "days_of_cover", "risk_flag",
               "suggested_order_qty", "order_value", "abc_class"]
    page = rec.head(limit)
    return jsonify({
        "generated_at": result.as_of.strftime("%Y-%m-%d"),
        "parameters": result.params.as_dict(),
        "window": _window_payload(result),
        "summary": result.portfolio,
        "count": int(len(page)),
        "total": int(len(rec)),
        "data": to_records(page[columns]),
        "formulas": {
            "avg_daily_demand": "total demand in window / number of calendar days",
            "safety_stock_zscore": "z(service_level) * std_dev(daily demand) * sqrt(lead_time_days)",
            "safety_stock_days": "safety_stock_days * avg_daily_demand",
            "reorder_point": "avg_daily_demand * lead_time_days + safety_stock",
            "inventory_position": "on_hand + quantity on open purchase orders",
            "suggested_order_qty": "max(0, reorder_point + avg_daily_demand * review_period_days - inventory_position)",
        },
        "disclaimer": "Decision support only. No purchase order is created automatically.",
    })


# =====================================================================
# 2. supplier delivery summary
# =====================================================================
@analytics_bp.get("/analytics/suppliers")
def supplier_summary():
    result = _run()
    supplier_id = request.args.get("supplier_id")
    summary = result.supplier_summary
    if supplier_id:
        summary = summary[summary["supplier_id"] == int(supplier_id)]
    return jsonify({
        "window": _window_payload(result),
        "window_days": config.SUPPLIER_WINDOW_DAYS,
        "definitions": {
            "actual_lead_time_days": "received_date - order_date (calendar days)",
            "delay_days": "received_date - expected_date (positive = late)",
            "on_time_rate": "delivered POs received on or before expected_date / delivered POs",
            "fill_rate": "units received / units ordered on delivered POs",
            "lead_time_cv": "standard deviation / mean of actual lead time (0 = perfectly consistent)",
            "supplier_score": "100 * (0.6 * on-time rate + 0.4 * max(0, 1 - lead_time_cv))",
        },
        "count": int(len(summary)),
        "data": to_records(summary),
    })


@analytics_bp.get("/analytics/suppliers/<int:supplier_id>/deliveries")
def supplier_deliveries(supplier_id: int):
    result = _run()
    pos = result.raw_pos
    rows = pos[pos["supplier_id"] == supplier_id] if not pos.empty else pos
    if rows is None or (hasattr(rows, "empty") and rows.empty):
        raise NotFoundError(f"No purchase orders found for supplier {supplier_id}.")
    columns = ["po_number", "sku", "order_date", "expected_date", "received_date",
               "status", "quantity", "received_qty", "actual_lead_time_days",
               "promised_lead_time_days", "delay_days", "on_time"]
    return jsonify({"supplier_id": supplier_id, "count": int(len(rows)),
                    "data": to_records(rows[columns])})


# =====================================================================
# 3. demand analysis
# =====================================================================
@analytics_bp.get("/analytics/demand")
def demand_summary():
    result = _run()
    stats = result.demand_stats
    product_id = request.args.get("product_id")
    if product_id:
        stats = stats[stats["product_id"] == int(product_id)]
    return jsonify({
        "window": _window_payload(result),
        "note": ("Days without a demand row count as zero demand (assumption A2). "
                 "ABC class is based on demand value (units x unit cost) in the window."),
        "count": int(len(stats)),
        "data": to_records(stats),
        "category_totals": to_records(result.category_demand),
    })


@analytics_bp.get("/analytics/demand/series")
def demand_series():
    """Daily zero-filled series for one product (chart / drill-down data)."""
    result = _run()
    product_id = request.args.get("product_id", type=int)
    if not product_id:
        raise ApiError("product_id is required.", status=400, code="MISSING_PARAMETER")
    series = result.demand_series
    series = series[series["product_id"] == product_id]
    if series.empty:
        raise NotFoundError(f"No demand series for product {product_id} in the window.")
    return jsonify({
        "product_id": product_id,
        "window": _window_payload(result),
        "count": int(len(series)),
        "data": to_records(series.rename(columns={"demand_date": "date"}),
                           digits=0),
    })


# =====================================================================
# 4. risk flags
# =====================================================================
@analytics_bp.get("/analytics/risks")
def risk_flags():
    result = _run()
    rec = result.recommendations
    payload = {
        "generated_at": result.as_of.strftime("%Y-%m-%d"),
        "parameters": result.params.as_dict(),
        "thresholds": {
            "excess_cover_days": result.params.excess_cover_days,
            "stockout": "on_hand <= 0, or inventory_position < avg_daily_demand * lead_time",
            "at_risk": "inventory_position < reorder_point (but covers lead-time demand)",
            "excess": "inventory_position >= reorder_point and days_of_cover > excess_cover_days",
        },
    }
    for flag in ("STOCKOUT", "AT_RISK", "EXCESS"):
        subset = rec[rec["risk_flag"] == flag]
        payload[flag.lower()] = {
            "count": int(len(subset)),
            "value_at_stake_inr": round(float(
                subset["order_value"].sum()
                if flag != "EXCESS" else (subset["on_hand"] * subset["unit_cost"]).sum()), 2),
            "items": to_records(subset[["sku", "product_name", "supplier_name",
                                        "on_hand", "on_order", "reorder_point_calc",
                                        "days_of_cover", "suggested_order_qty",
                                        "order_value"]].head(20)),
        }
    return jsonify(payload)


# =====================================================================
# 5. forecasting
# =====================================================================
@analytics_bp.get("/analytics/forecast")
def forecast_endpoint():
    """Forecast + hold-out evaluation against the naive baseline."""
    result = _run()
    product_id = request.args.get("product_id", type=int)
    horizon = result.params.forecast_horizon_days
    payload = {
        "generated_at": result.as_of.strftime("%Y-%m-%d"),
        "horizon_days": horizon,
        "holdout_days": config.FORECAST_HOLDOUT_DAYS,
        "methods": list(fc.METHODS),
        "baseline": "naive_mean (28-day mean of the training window)",
        "evaluation": result.forecast_summary,
        "comparison": to_records(result.forecast_compare),
        "forecasts": to_records(result.forecast_table),
        "disclaimer": ("Forecasts are statistical decision support, not commitments. "
                       "The selected method per SKU is the one with the lowest hold-out "
                       "MAE, and the naive baseline is kept whenever it is within 5%."),
    }
    if product_id:
        series = result.demand_series
        series = series[series["product_id"] == product_id]
        if series.empty:
            raise NotFoundError(f"No demand history for product {product_id}.")
        values = series.sort_values("demand_date")["quantity"].to_numpy(dtype=float)
        res = fc.forecast_product(values, horizon, config.FORECAST_HOLDOUT_DAYS,
                                  config.FORECAST_BASELINE_WINDOW)
        import pandas as pd
        dates = pd.date_range(result.windows["future_start"],
                              result.windows["future_end"], freq="D")
        frame = fc.forecast_frame(dates, np.asarray(res["forecast"]), res["residual_std"])
        payload["product_id"] = product_id
        payload["selected_method"] = res["method"]
        payload["evaluation_detail"] = to_records(res["evaluation"])
        payload["forecast"] = to_records(frame)
    return jsonify(payload)


# =====================================================================
# 6. run + persist (explicit, human-triggered)
# =====================================================================
@analytics_bp.post("/analytics/run")
def run_analysis():
    """Run the full analysis and store the recommendation snapshot.

    Writing a snapshot is NOT ordering: it records what the planner was
    shown, with the parameters used, for auditability.
    """
    cfg = _cfg()
    params = analysis_params(cfg)
    pipeline = AnalysisPipeline(params=params, with_forecast=True, persist=True)
    result = pipeline.run()
    return jsonify({
        "run_id": result.run_id,
        "generated_at": result.as_of.strftime("%Y-%m-%d"),
        "parameters": result.params.as_dict(),
        "summary": result.portfolio,
        "forecast": result.forecast_summary,
        "stored": True,
        "message": ("Analysis run stored in analysis_runs / reorder_recommendations. "
                    "No purchase orders were created."),
    }), 201


@analytics_bp.get("/analytics/runs")
def list_runs():
    from db import db
    rows = db.fetch_all(
        """SELECT r.run_id, r.run_at, r.window_days, r.service_level,
                  r.safety_stock_days, r.review_period_days, r.forecast_method,
                  r.notes, COUNT(rr.rec_id) AS recommendations
           FROM analysis_runs r
           LEFT JOIN reorder_recommendations rr ON rr.run_id = r.run_id
           GROUP BY r.run_id ORDER BY r.run_id DESC LIMIT 50""")
    for row in rows:
        row["run_at"] = str(row["run_at"])
        row["service_level"] = clean_number(row["service_level"], 3)
    return jsonify({"count": len(rows), "data": rows})


# =====================================================================
# 7. charts
# =====================================================================
@analytics_bp.get("/charts")
def list_charts():
    from viz import charts
    result = _run()
    items = charts.build_all(result)
    return jsonify({
        "count": len(items),
        "data": [{"key": c["key"], "file": c["file"],
                  "url": f"/api/charts/{c['file']}",
                  "interpretation": c["interpretation"]} for c in items],
    })


@analytics_bp.get("/charts/<path:name>")
def get_chart(name: str):
    from viz import charts
    target = Path(charts.CHART_DIR) / name
    if target.exists() and target.suffix == ".png":
        return send_file(target, mimetype="image/png", max_age=0)
    # not built yet -> build all charts once, then retry
    result = _run()
    charts.build_all(result)
    if target.exists():
        return send_file(target, mimetype="image/png", max_age=0)
    raise NotFoundError(f"Chart '{name}' does not exist. See GET /api/charts.")


@analytics_bp.get("/reports/<path:name>")
def get_report(name: str):
    """Serve generated CSV / markdown reports from the reports directory."""
    base = Path(config.REPORT_DIR).resolve()
    target = (base / name).resolve()
    if base not in target.parents and target != base:
        raise ApiError("Invalid report path.", status=400, code="INVALID_PATH")
    if not target.exists():
        raise NotFoundError(f"Report '{name}' not found. Run scripts/run_pipeline.py.")
    return send_from_directory(base, name, as_attachment=False)


# =====================================================================
# 8. dashboard (one call for the demo)
# =====================================================================
@analytics_bp.get("/analytics/dashboard")
def dashboard():
    result = _run()
    return jsonify({
        "generated_at": result.as_of.strftime("%Y-%m-%d"),
        "window": _window_payload(result),
        "parameters": result.params.as_dict(),
        "portfolio": result.portfolio,
        "forecast": result.forecast_summary,
        "top_risks": to_records(
            result.recommendations[
                result.recommendations["risk_flag"] != "OK"
            ][["sku", "product_name", "risk_flag", "inventory_position",
               "reorder_point_calc", "days_of_cover", "suggested_order_qty",
               "order_value", "supplier_name"]].head(10)),
        "supplier_grades": to_records(
            result.supplier_summary[["supplier_name", "on_time_rate", "lead_time_cv",
                                     "grade", "orders_delivered"]]),
        "charts": [f"/api/charts/{p.name}" for p in
                   sorted((Path(config.REPORT_DIR) / "charts").glob("*.png"))],
    })
