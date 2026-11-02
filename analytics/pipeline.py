"""End-to-end analysis pipeline (descriptive -> reorder -> forecast).

One call to `AnalysisPipeline.run()` produces every table the report and the
API endpoints need, so the numbers shown in the charts, the CSV exports and
the JSON API can never disagree with each other.

Order of work
-------------
1. resolve parameters (env defaults overridden by request arguments)
2. load master data, demand, ledger, purchase orders
3. demand statistics (pandas) + supplier KPIs + inventory position
4. reorder point / safety stock / risk flags (NumPy formulas)
5. simple forecasting with hold-out evaluation against a naive baseline
6. optional persistence of the recommendation snapshot (analysis_runs)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd

from analytics import data_access, demand as demand_mod, forecast as fc, inventory, supplier
from analytics.demand import date_windows
from config import config
from db import Database, db as default_db


@dataclass
class AnalysisResult:
    params: object
    windows: dict
    as_of: date
    products: pd.DataFrame = field(default_factory=pd.DataFrame)
    demand_stats: pd.DataFrame = field(default_factory=pd.DataFrame)
    supplier_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    raw_pos: pd.DataFrame = field(default_factory=pd.DataFrame)
    lead_times: pd.DataFrame = field(default_factory=pd.DataFrame)
    positions: pd.DataFrame = field(default_factory=pd.DataFrame)
    demand_series: pd.DataFrame = field(default_factory=pd.DataFrame)
    category_demand: pd.DataFrame = field(default_factory=pd.DataFrame)
    recommendations: pd.DataFrame = field(default_factory=pd.DataFrame)
    turnover: pd.DataFrame = field(default_factory=pd.DataFrame)
    forecast_compare: pd.DataFrame = field(default_factory=pd.DataFrame)
    forecast_table: pd.DataFrame = field(default_factory=pd.DataFrame)
    forecast_lines: pd.DataFrame = field(default_factory=pd.DataFrame)
    delivery_trend: pd.DataFrame = field(default_factory=pd.DataFrame)
    portfolio: dict = field(default_factory=dict)
    forecast_summary: dict = field(default_factory=dict)
    run_id: int | None = None

    # convenience views -------------------------------------------------
    def flags(self, flag: str) -> pd.DataFrame:
        if self.recommendations.empty:
            return self.recommendations
        return self.recommendations[self.recommendations["risk_flag"] == flag]


class AnalysisPipeline:
    def __init__(self, params=None, database: Database | None = None,
                 with_forecast: bool = True, persist: bool = False):
        self.params = params or _default_params()
        self.db = database or default_db
        self.with_forecast = with_forecast
        self.persist = persist

    # ------------------------------------------------------------------
    def run(self) -> AnalysisResult:
        params = self.params
        products = data_access.load_products(self.db)
        suppliers = data_access.load_suppliers(self.db)
        as_of = data_access.default_window_end(self.db)
        windows = date_windows(as_of, params.window_days,
                              config.FORECAST_HOLDOUT_DAYS, params.forecast_horizon_days)
        windows["supplier_start"] = as_of - timedelta(days=config.SUPPLIER_WINDOW_DAYS - 1)
        start, end = windows["start"], windows["end"]

        demand = data_access.load_demand(start, end, database=self.db)
        # the whole ledger is needed: turnover averages the daily balance, which
        # depends on every movement before the window as well
        txns = data_access.load_stock_transactions(start=None, end=end, database=self.db)
        # supplier delivery KPIs use a longer trailing window than demand:
        # 365 days of POs gives every supplier enough deliveries to judge.
        supplier_start = end - timedelta(days=config.SUPPLIER_WINDOW_DAYS - 1)
        pos = data_access.load_purchase_orders(start=supplier_start, end=end,
                                               database=self.db)
        positions = data_access.load_inventory_position(self.db)

        result = AnalysisResult(params=params, windows=windows, as_of=as_of,
                                products=products, positions=positions)

        # ---- demand & supplier descriptive analysis --------------------
        result.demand_stats = demand_mod.demand_stats(demand, products, start, end)
        result.supplier_summary = supplier.supplier_summary(pos, suppliers)
        result.raw_pos = supplier.prepare_pos(pos)
        result.lead_times = supplier.product_lead_times(pos, products)
        result.delivery_trend = supplier.delivery_trend(pos)
        result.demand_series = demand_mod.daily_series(demand, start, end,
                                                       products["product_id"].tolist())
        result.category_demand = demand_mod.category_demand(demand, products, start, end)

        # ---- reorder engine -------------------------------------------
        result.recommendations = inventory.build_recommendations(
            result.demand_stats, result.lead_times, positions, products, params)
        result.turnover = inventory.stock_turnover_table(txns, products, start, end)
        result.portfolio = inventory.portfolio_summary(result.recommendations, params)

        # ---- forecasting ----------------------------------------------
        if self.with_forecast:
            self._forecast(demand, products, windows, result)

        # ---- persistence ----------------------------------------------
        if self.persist:
            result.run_id = self.save_run(result)
        return result

    # ------------------------------------------------------------------
    def _forecast(self, demand: pd.DataFrame, products: pd.DataFrame,
                  windows: dict, result: AnalysisResult) -> None:
        if products.empty:
            return
        series_all = result.demand_series
        series_map: dict[int, np.ndarray] = {}
        if not series_all.empty:
            for pid, part in series_all.groupby("product_id"):
                series_map[int(pid)] = part.sort_values("demand_date")["quantity"] \
                    .to_numpy(dtype=float)

        compare = fc.comparison_table(series_map, config.FORECAST_HOLDOUT_DAYS,
                                      config.FORECAST_BASELINE_WINDOW)
        result.forecast_compare = compare
        table = fc.forecast_all(series_map, self.params.forecast_horizon_days,
                                config.FORECAST_HOLDOUT_DAYS,
                                config.FORECAST_BASELINE_WINDOW)
        result.forecast_table = table

        # aggregated lines for the chart (top products by forecast volume)
        lines = []
        future = pd.date_range(windows["future_start"], windows["future_end"], freq="D")
        if not table.empty:
            top = table.sort_values("total_forecast", ascending=False).head(4)
            for row in top.itertuples():
                series = series_map.get(int(row.product_id))
                if series is None:
                    continue
                res = fc.forecast_product(series, self.params.forecast_horizon_days,
                                          config.FORECAST_HOLDOUT_DAYS,
                                          config.FORECAST_BASELINE_WINDOW)
                frame = fc.forecast_frame(future, np.asarray(res["forecast"]),
                                          res["residual_std"])
                frame["product_id"] = int(row.product_id)
                lines.append(frame)
        result.forecast_lines = pd.concat(lines, ignore_index=True) if lines else pd.DataFrame()

        result.forecast_summary = summarise_forecast(compare)

    # ------------------------------------------------------------------
    def save_run(self, result: AnalysisResult) -> int:
        """Persist the parameter set and the recommendation snapshot."""
        p = result.params
        method = (result.forecast_table["method"].mode().iloc[0]
                  if not result.forecast_table.empty else "not_run")
        with self.db.transaction() as cur:
            cur.execute(
                """
                INSERT INTO analysis_runs
                    (window_days, service_level, safety_stock_days,
                     review_period_days, forecast_method, notes)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (p.window_days, p.service_level, p.safety_stock_days,
                 p.review_period_days, method,
                 f"policy={p.safety_stock_policy}; as_of={result.as_of.isoformat()}"))
            run_id = cur.lastrowid
            rec = result.recommendations
            if not rec.empty:
                rows = [(
                    run_id, int(r.product_id), float(r.avg_daily_demand),
                    float(r.demand_std_dev), float(r.lead_time_used),
                    float(r.safety_stock), float(r.reorder_point_calc),
                    int(r.reorder_point), float(r.on_hand), float(r.on_order),
                    float(r.inventory_position),
                    None if pd.isna(r.days_of_cover) else float(r.days_of_cover),
                    float(r.suggested_order_qty), float(r.order_value), str(r.risk_flag),
                ) for r in rec.itertuples()]
                cur.executemany(
                    """
                    INSERT INTO reorder_recommendations
                        (run_id, product_id, avg_daily_demand, demand_std_dev,
                         lead_time_days_used, safety_stock, reorder_point_calc,
                         reorder_point_current, on_hand, on_order, inventory_position,
                         days_of_cover, suggested_order_qty, order_value, risk_flag)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE suggested_order_qty = VALUES(suggested_order_qty)
                    """, rows)
        return int(run_id)


# ------------------------------------------------------------------ helpers
def _default_params():
    from api.validators import AnalysisParams
    return AnalysisParams(
        window_days=config.WINDOW_DAYS,
        service_level=config.SERVICE_LEVEL,
        safety_stock_days=config.SAFETY_STOCK_DAYS,
        safety_stock_policy=config.SAFETY_STOCK_POLICY,
        review_period_days=config.REVIEW_PERIOD_DAYS,
        excess_cover_days=config.EXCESS_COVER_DAYS,
        forecast_horizon_days=config.FORECAST_HORIZON_DAYS,
    )


def summarise_forecast(compare: pd.DataFrame) -> dict:
    """Model-vs-baseline summary used in the report and API."""
    if compare.empty:
        return {"products_evaluated": 0, "models": {}, "improved_over_baseline": 0}
    baseline = compare[compare["method"] == "naive_mean"].set_index("product_id")["mae"]
    best = compare[compare["selected"]].set_index("product_id")["mae"]
    common = baseline.index.intersection(best.index)
    improved = int((best.loc[common] < baseline.loc[common]).sum()) if len(common) else 0
    returns = {}
    for (pid, base) in baseline.items():
        if pid in best.index and base and base > 0:
            returns[pid] = 100.0 * (base - best.loc[pid]) / base
    selected_counts = compare[compare["selected"]]["method"].value_counts().to_dict()
    return {
        "products_evaluated": int(compare["product_id"].nunique()),
        "holdout_days": config.FORECAST_HOLDOUT_DAYS,
        "improved_over_baseline": improved,
        "models_selected": {str(k): int(v) for k, v in selected_counts.items()},
        "mean_mae_baseline": round(float(baseline.mean()), 3) if len(baseline) else None,
        "mean_mae_selected": round(float(best.mean()), 3) if len(best) else None,
        "mean_improvement_pct": round(float(np.mean(list(returns.values()))), 2)
        if returns else None,
    }


def to_records(df: pd.DataFrame, limit: int | None = None, digits: int = 3) -> list[dict]:
    """JSON-safe records: numpy scalars, dates, NaN and inf handled."""
    if df is None or df.empty:
        return []
    frame = df.head(limit) if limit else df
    records = []
    for row in frame.to_dict(orient="records"):
        clean = {}
        for key, value in row.items():
            if value is None or value is pd.NaT:
                clean[key] = None
            elif isinstance(value, (pd.Timestamp, date)):
                clean[key] = value.strftime("%Y-%m-%d")
            elif isinstance(value, (np.integer,)):
                clean[key] = int(value)
            elif isinstance(value, (np.floating, float)):
                fv = float(value)
                clean[key] = None if (np.isnan(fv) or np.isinf(fv)) else round(fv, digits)
            elif isinstance(value, (np.bool_, bool)):
                clean[key] = bool(value)
            elif value is None or (isinstance(value, float) and pd.isna(value)):
                clean[key] = None
            else:
                clean[key] = value
        records.append(clean)
    return records
