"""Supplier delivery performance analytics (pandas).

Definitions (documented in docs/04_analysis_methodology.md)
----------------------------------------------------------
* actual lead time  = received_date - order_date            (calendar days)
* promised lead time = expected_date - order_date
* delay (days)      = received_date - expected_date          (>0 = late)
* on-time           = received_date <= expected_date
* fill rate         = units received / units ordered, on RECEIVED/PARTIAL POs
* open order qty    = quantity - received_qty for OPEN/PARTIAL POs

Only POs that have actually been delivered (status RECEIVED / PARTIAL with
a received_date) count towards lead-time statistics -- otherwise open
orders would bias the average downwards.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics import formulas

DELIVERED = ("RECEIVED", "PARTIAL")


def prepare_pos(pos: pd.DataFrame) -> pd.DataFrame:
    """Add lead-time / delay / on-time columns to a raw PO frame."""
    if pos.empty:
        return pos.assign(actual_lead_time_days=pd.Series(dtype="float64"),
                          promised_lead_time_days=pd.Series(dtype="float64"),
                          delay_days=pd.Series(dtype="float64"),
                          on_time=pd.Series(dtype="bool"),
                          is_delivered=pd.Series(dtype="bool"))
    df = pos.copy()
    df["is_delivered"] = (df["status"].isin(DELIVERED)
                          & df["received_date"].notna())
    df["actual_lead_time_days"] = (df["received_date"] - df["order_date"]).dt.days
    df["promised_lead_time_days"] = (df["expected_date"] - df["order_date"]).dt.days
    df["delay_days"] = (df["received_date"] - df["expected_date"]).dt.days
    df["on_time"] = df["is_delivered"] & (df["delay_days"] <= 0)
    return df


def supplier_summary(pos: pd.DataFrame, suppliers: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per supplier with delivery KPIs (all suppliers, even idle ones)."""
    if pos.empty:
        base = (suppliers[["supplier_id", "supplier_name"]].copy()
                if suppliers is not None and not suppliers.empty
                else pd.DataFrame(columns=["supplier_id", "supplier_name"]))
        for col in ("orders_total", "orders_delivered", "units_ordered", "units_received",
                    "avg_lead_time_days", "std_lead_time_days", "lead_time_cv",
                    "avg_delay_days", "max_delay_days", "on_time_rate", "fill_rate",
                    "late_orders", "open_orders", "supplier_score", "grade",
                    "expected_lead_time_days", "lead_time_gap_days"):
            base[col] = 0 if col not in ("avg_lead_time_days", "std_lead_time_days",
                                         "lead_time_cv", "avg_delay_days",
                                         "max_delay_days", "on_time_rate", "fill_rate",
                                         "supplier_score", "lead_time_gap_days") else np.nan
        base["grade"] = "NO_DATA"
        return base

    df = prepare_pos(pos)
    rows = []
    for sid, part in df.groupby("supplier_id"):
        delivered = part[part["is_delivered"]]
        open_pos = part[part["status"].isin(("OPEN", "PARTIAL"))]
        lt = delivered["actual_lead_time_days"].dropna()
        mean_lt = float(lt.mean()) if len(lt) else None
        std_lt = float(lt.std(ddof=1)) if len(lt) > 1 else (0.0 if len(lt) == 1 else None)
        cv = (std_lt / mean_lt) if (mean_lt and std_lt is not None) else None
        on_time = formulas.on_time_rate(int(delivered["on_time"].sum()), len(delivered))
        fill = formulas.fill_rate(float(delivered["received_qty"].fillna(0).sum()),
                                  float(delivered["quantity"].sum())) if len(delivered) else None
        rows.append({
            "supplier_id": int(sid),
            "supplier_name": part["supplier_name"].iloc[0],
            "orders_total": int(len(part)),
            "orders_delivered": int(len(delivered)),
            "open_orders": int(len(open_pos)),
            "late_orders": int((delivered["delay_days"] > 0).sum()) if len(delivered) else 0,
            "units_ordered": float(part["quantity"].sum()),
            "units_received": float(delivered["received_qty"].fillna(0).sum()),
            "open_units": float((open_pos["quantity"]
                                 - open_pos["received_qty"].fillna(0)).sum())
                          if len(open_pos) else 0.0,
            "avg_lead_time_days": mean_lt,
            "std_lead_time_days": std_lt,
            "lead_time_cv": cv,
            "p90_lead_time_days": float(np.percentile(lt, 90)) if len(lt) else None,
            "avg_delay_days": float(delivered["delay_days"].dropna().mean())
                              if len(delivered) else None,
            "max_delay_days": float(delivered["delay_days"].dropna().max())
                              if len(delivered) else None,
            "on_time_rate": on_time,
            "fill_rate": fill,
            "supplier_score": formulas.supplier_score(on_time, cv),
        })
    summary = pd.DataFrame(rows)
    if suppliers is not None and not suppliers.empty:
        summary = suppliers[["supplier_id", "supplier_name"]].merge(
            summary.drop(columns=["supplier_name"]), on="supplier_id", how="left")
        summary["supplier_name"] = summary["supplier_name"].fillna("(unknown supplier)")
    summary["expected_lead_time_days"] = summary["supplier_id"].map(
        dict(zip(suppliers["supplier_id"], suppliers["promised_lead_time_days"]))
        if suppliers is not None and not suppliers.empty else {})
    summary["lead_time_gap_days"] = summary["avg_lead_time_days"] \
        - summary["expected_lead_time_days"]
    summary["grade"] = [grade(r) for r in summary.itertuples()]
    numeric = ["units_ordered", "units_received", "open_units", "avg_lead_time_days",
               "std_lead_time_days", "lead_time_cv", "p90_lead_time_days",
               "avg_delay_days", "max_delay_days", "on_time_rate", "fill_rate",
               "supplier_score", "expected_lead_time_days", "lead_time_gap_days"]
    for col in numeric:
        summary[col] = pd.to_numeric(summary[col], errors="coerce").round(3)
    return summary.sort_values("supplier_score", ascending=False,
                               na_position="last").reset_index(drop=True)


def grade(row) -> str:
    """Simple A-D reliability grade from on-time rate + lead-time consistency."""
    if row.on_time_rate is None or (isinstance(row.on_time_rate, float)
                                    and np.isnan(row.on_time_rate)):
        return "NO_DATA"
    cv = row.lead_time_cv if row.lead_time_cv is not None else 0.0
    if np.isnan(cv):
        cv = 0.0
    if row.on_time_rate >= 0.95 and cv <= 0.15:
        return "A"
    if row.on_time_rate >= 0.85 and cv <= 0.30:
        return "B"
    if row.on_time_rate >= 0.70:
        return "C"
    return "D"


def product_lead_times(pos: pd.DataFrame, products: pd.DataFrame) -> pd.DataFrame:
    """Observed lead time per product (used instead of the master value when
    enough deliveries exist -- assumption A4)."""
    if pos.empty:
        out = products[["product_id", "lead_time_days"]].copy()
        out["observed_lead_time_days"] = np.nan
        out["lead_time_source"] = "master_data"
        return out
    delivered = prepare_pos(pos)
    delivered = delivered[delivered["is_delivered"]]
    stats = (delivered.groupby("product_id")["actual_lead_time_days"]
             .agg(observed_lead_time_days="mean", lead_time_observations="count")
             .reset_index()) if not delivered.empty else pd.DataFrame(
        columns=["product_id", "observed_lead_time_days", "lead_time_observations"])
    out = products[["product_id", "lead_time_days"]].merge(stats, on="product_id", how="left")
    min_obs = 3   # need at least 3 deliveries before overriding master data
    out["lead_time_used"] = np.where(
        out["lead_time_observations"].fillna(0) >= min_obs,
        out["observed_lead_time_days"].round(1),
        out["lead_time_days"].astype(float))
    out["lead_time_source"] = np.where(
        out["lead_time_observations"].fillna(0) >= min_obs,
        "observed_pos", "master_data")
    return out


def delivery_trend(pos: pd.DataFrame) -> pd.DataFrame:
    """Monthly on-time rate and average delay, for the delivery chart."""
    if pos.empty:
        return pd.DataFrame(columns=["month", "on_time_rate", "avg_delay_days", "orders"])
    df = prepare_pos(pos)
    df = df[df["is_delivered"]]
    if df.empty:
        return pd.DataFrame(columns=["month", "on_time_rate", "avg_delay_days", "orders"])
    df = df.assign(month=df["received_date"].dt.to_period("M").dt.to_timestamp())
    out = df.groupby("month").agg(
        orders=("po_id", "count"),
        on_time_rate=("on_time", "mean"),
        avg_delay_days=("delay_days", "mean"),
    ).reset_index()
    out["on_time_rate"] = out["on_time_rate"].round(3)
    out["avg_delay_days"] = out["avg_delay_days"].round(2)
    return out
