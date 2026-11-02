"""Demand analytics with pandas.

Responsibilities
----------------
1. Build a complete daily calendar per product (days without a row = 0
   demand -- assumption A2) so averages are not inflated by only
   counting days on which something happened.
2. Descriptive statistics per product: mean, standard deviation,
   coefficient of variation, total, zero-demand days.
3. Trend: least-squares slope of a 28-day moving average plus a
   recent-vs-previous window comparison.
4. Weekly aggregation for charts and an ABC (Pareto) demand-value class.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from analytics import formulas


# ------------------------------------------------------------------ series
def daily_series(demand: pd.DataFrame, start: date, end: date,
                 product_ids: list[int] | None = None) -> pd.DataFrame:
    """Complete daily calendar for every product between start and end.

    Returns a long frame: product_id, demand_date, quantity (0-filled).
    """
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    calendar = pd.date_range(start_ts, end_ts, freq="D")
    if product_ids is None:
        product_ids = sorted(demand["product_id"].unique().tolist()) if not demand.empty else []

    window = demand[(demand["demand_date"] >= start_ts) & (demand["demand_date"] <= end_ts)] \
        if not demand.empty else demand
    if window.empty:
        return pd.DataFrame({
            "product_id": np.repeat(product_ids, len(calendar)),
            "demand_date": np.tile(calendar, len(product_ids)),
            "quantity": np.zeros(len(product_ids) * len(calendar), dtype=int),
        })

    pivot = (window.groupby(["product_id", "demand_date"])["quantity"].sum()
             .unstack(fill_value=0)
             .reindex(index=product_ids, columns=calendar, fill_value=0))
    out = pivot.stack().reset_index()
    out.columns = ["product_id", "demand_date", "quantity"]
    return out


def demand_stats(demand: pd.DataFrame, products: pd.DataFrame, start: date, end: date,
                 recent_days: int = 28) -> pd.DataFrame:
    """Per-product descriptive statistics over [start, end]."""
    series = daily_series(demand, start, end, product_ids=products["product_id"].tolist())
    if series.empty:
        return pd.DataFrame(columns=["product_id", "avg_daily_demand", "demand_std_dev"])

    grouped = series.groupby("product_id")["quantity"]
    stats = pd.DataFrame({
        "avg_daily_demand": grouped.mean(),
        "demand_std_dev": grouped.std(ddof=1).fillna(0.0),
        "demand_total": grouped.sum(),
        "demand_days": grouped.size(),
        "zero_demand_days": grouped.apply(lambda s: int((s == 0).sum())),
        "max_daily_demand": grouped.max(),
        "median_daily_demand": grouped.median(),
    }).reset_index()

    stats["demand_cv"] = [formulas.coefficient_of_variation(r.demand_std_dev,
                                                           r.avg_daily_demand)
                          for r in stats.itertuples()]

    # recent vs previous window (momentum)
    end_ts = pd.Timestamp(end)
    recent_start = end_ts - pd.Timedelta(days=recent_days - 1)
    prev_start = recent_start - pd.Timedelta(days=recent_days)
    prev_end = recent_start - pd.Timedelta(days=1)
    recent = (series[series["demand_date"] >= recent_start]
              .groupby("product_id")["quantity"].mean().rename("avg_daily_recent"))
    prev = (series[(series["demand_date"] >= prev_start) & (series["demand_date"] <= prev_end)]
            .groupby("product_id")["quantity"].mean().rename("avg_daily_previous"))
    stats = stats.merge(recent, on="product_id", how="left") \
                 .merge(prev, on="product_id", how="left")
    stats["avg_daily_recent"] = stats["avg_daily_recent"].fillna(0.0)
    stats["avg_daily_previous"] = stats["avg_daily_previous"].fillna(0.0)
    stats["demand_change_pct"] = [
        round(100.0 * (r - p) / p, 2) if p > 0 else None
        for r, p in zip(stats["avg_daily_recent"], stats["avg_daily_previous"])
    ]

    # trend slope on the 7-day moving average (units/day per day)
    slopes = {}
    for pid, part in series.groupby("product_id"):
        part = part.sort_values("demand_date")
        smooth = part["quantity"].rolling(7, min_periods=3).mean().to_numpy()
        mask = ~np.isnan(smooth)
        if mask.sum() >= 3:
            x = np.arange(len(smooth))[mask]
            slopes[pid] = float(np.polyfit(x, smooth[mask], 1)[0])
        else:
            slopes[pid] = 0.0
    stats["trend_slope"] = stats["product_id"].map(slopes)

    stats = stats.merge(products[["product_id", "sku", "product_name", "category",
                                  "unit_cost", "supplier_name"]],
                        on="product_id", how="left")
    stats["demand_value"] = stats["demand_total"] * stats["unit_cost"]

    total_value = stats["demand_value"].sum()
    if total_value > 0:
        ordered = stats.sort_values("demand_value", ascending=False).copy()
        ordered["cum_share"] = ordered["demand_value"].cumsum() / total_value
        # Class from the share BEFORE the item: the SKU that crosses the 70%
        # line still belongs to class A (otherwise a single dominant SKU
        # would be graded C, which is wrong).
        previous_share = ordered["cum_share"].shift(1).fillna(0.0)
        ordered["abc_class"] = np.where(previous_share < 0.70, "A",
                                        np.where(previous_share < 0.90, "B", "C"))
        stats = stats.merge(ordered[["product_id", "cum_share", "abc_class"]],
                            on="product_id", how="left")
    else:
        stats["cum_share"] = np.nan
        stats["abc_class"] = "C"

    for col in ("avg_daily_demand", "demand_std_dev", "demand_total", "demand_cv",
                "max_daily_demand", "median_daily_demand", "avg_daily_recent",
                "avg_daily_previous", "trend_slope", "demand_value", "cum_share"):
        stats[col] = pd.to_numeric(stats[col], errors="coerce").round(3)
    return stats.sort_values("product_id").reset_index(drop=True)


# ------------------------------------------------------------------ charts
def weekly_demand(demand: pd.DataFrame, products: pd.DataFrame,
                  start: date, end: date, top_n: int = 6) -> pd.DataFrame:
    """Weekly totals for the top-N products by total demand (chart-friendly)."""
    if demand.empty:
        return pd.DataFrame(columns=["product_id", "sku", "week_start", "quantity"])
    window = demand[(demand["demand_date"] >= pd.Timestamp(start))
                    & (demand["demand_date"] <= pd.Timestamp(end))]
    if window.empty:
        return pd.DataFrame(columns=["product_id", "sku", "week_start", "quantity"])
    totals = window.groupby("product_id")["quantity"].sum().sort_values(ascending=False)
    keep = totals.head(top_n).index.tolist()
    frame = window[window["product_id"].isin(keep)].copy()
    frame["week_start"] = frame["demand_date"] - pd.to_timedelta(
        frame["demand_date"].dt.weekday, unit="D")
    weekly = (frame.groupby(["product_id", "week_start"])["quantity"].sum()
              .reset_index())
    weekly = weekly.merge(products[["product_id", "sku"]], on="product_id", how="left")
    return weekly.sort_values(["product_id", "week_start"]).reset_index(drop=True)


def category_demand(demand: pd.DataFrame, products: pd.DataFrame,
                    start: date, end: date) -> pd.DataFrame:
    """Total demand and demand value per product category."""
    if demand.empty:
        return pd.DataFrame(columns=["category", "quantity", "value"])
    window = demand[(demand["demand_date"] >= pd.Timestamp(start))
                    & (demand["demand_date"] <= pd.Timestamp(end))]
    merged = window.merge(products[["product_id", "category", "unit_cost"]],
                          on="product_id", how="left")
    agg = merged.groupby("category").agg(
        quantity=("quantity", "sum"),
        value=("quantity", lambda s: float((s * merged.loc[s.index, "unit_cost"]).sum())),
    ).reset_index().sort_values("quantity", ascending=False)
    return agg


def moving_average(values, window: int = 7):
    return pd.Series(values).rolling(window, min_periods=1).mean().to_numpy()


def date_windows(as_of: date, window_days: int, forecast_holdout: int,
                 forecast_horizon: int) -> dict:
    """Consistent date ranges for analysis, hold-out evaluation and forecast."""
    end = as_of
    start = end - timedelta(days=window_days - 1)
    holdout_start = end - timedelta(days=forecast_holdout - 1)
    train_end = holdout_start - timedelta(days=1)
    train_start = start
    future_start = end + timedelta(days=1)
    future_end = end + timedelta(days=forecast_horizon)
    return {
        "start": start,
        "end": end,
        "train_start": train_start,
        "train_end": train_end,
        "holdout_start": holdout_start,
        "holdout_end": end,
        "future_start": future_start,
        "future_end": future_end,
    }
