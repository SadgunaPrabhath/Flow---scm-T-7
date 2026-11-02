"""Reorder-point engine and shortage / excess-stock flagging.

DECISION SUPPORT ONLY
---------------------
Nothing in this module writes to purchase_orders. It produces a ranked
recommendation table that a planner reviews; ordering stays a human or
separately-authorised action (acceptance criterion: "suggestions do not
automatically place orders").

Formulas (units documented in analytics/formulas.py)
---------------------------------------------------
avg_daily_demand  mu_d = total demand / number of calendar days in window
sigma_d                = sample standard deviation of daily demand
lead time L            = observed mean lead time if >= 3 deliveries,
                         else products.lead_time_days
SS (z-score policy)    = z(service_level) * sigma_d * sqrt(L)
SS (days policy)       = safety_stock_days * mu_d
SS (max policy)        = max(of the two)                      [default]
ROP                    = mu_d * L + SS
inventory position IP  = on_hand + on_order (open POs)
target level           = ROP + mu_d * review_period_days
suggested order qty    = max(0, target - IP)
days of cover          = on_hand / mu_d
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from analytics import formulas

FLAG_ORDER = {"STOCKOUT": 0, "AT_RISK": 1, "EXCESS": 2, "OK": 3}


def build_recommendations(demand_stats: pd.DataFrame,
                          lead_times: pd.DataFrame,
                          positions: pd.DataFrame,
                          products: pd.DataFrame,
                          params) -> pd.DataFrame:
    """Join demand statistics, lead times and stock position -> recommendations."""
    if products.empty:
        return empty_recommendations()

    rec = products[["product_id", "sku", "product_name", "category", "unit_cost",
                    "lead_time_days", "reorder_point", "supplier_id",
                    "supplier_name"]].copy()
    rec = rec.merge(demand_stats[["product_id", "avg_daily_demand", "demand_std_dev",
                                 "demand_cv", "demand_total", "abc_class",
                                 "avg_daily_recent", "trend_slope"]],
                    on="product_id", how="left")
    rec = rec.merge(lead_times[["product_id", "lead_time_used", "lead_time_source"]],
                    on="product_id", how="left")
    rec = rec.merge(positions[["product_id", "on_hand", "on_order"]],
                    on="product_id", how="left")

    rec["avg_daily_demand"] = pd.to_numeric(rec["avg_daily_demand"], errors="coerce").fillna(0.0)
    rec["demand_std_dev"] = pd.to_numeric(rec["demand_std_dev"], errors="coerce").fillna(0.0)
    # fall back to the master-data lead time if no observed value is available
    rec["lead_time_used"] = (pd.to_numeric(rec["lead_time_used"], errors="coerce")
                             .fillna(pd.to_numeric(rec["lead_time_days"], errors="coerce"))
                             .fillna(0.0))
    rec["lead_time_source"] = rec["lead_time_source"].fillna("master_data")
    rec["on_hand"] = pd.to_numeric(rec["on_hand"], errors="coerce").fillna(0.0)
    rec["on_order"] = pd.to_numeric(rec["on_order"], errors="coerce").fillna(0.0)
    rec["inventory_position"] = rec["on_hand"] + rec["on_order"]

    ss_days_param = params.safety_stock_days
    service_level = params.service_level
    rec["ss_zscore"] = [formulas.safety_stock_zscore(sigma_d, lt, service_level)
                        for sigma_d, lt in zip(rec["demand_std_dev"], rec["lead_time_used"])]
    rec["ss_days"] = [formulas.safety_stock_days_policy(mu, ss_days_param)
                      for mu in rec["avg_daily_demand"]]
    rec["safety_stock"] = [formulas.combine_safety_stock(z, d, params.safety_stock_policy)
                           for z, d in zip(rec["ss_zscore"], rec["ss_days"])]
    rec["reorder_point_calc"] = [formulas.reorder_point(mu, lt, ss)
                                 for mu, lt, ss in zip(rec["avg_daily_demand"],
                                                       rec["lead_time_used"],
                                                       rec["safety_stock"])]
    rec["target_level"] = rec["reorder_point_calc"] + \
        rec["avg_daily_demand"] * params.review_period_days
    rec["days_of_cover"] = [formulas.days_of_cover(oh, mu)
                            for oh, mu in zip(rec["on_hand"], rec["avg_daily_demand"])]
    rec["gap_vs_master_rop"] = rec["reorder_point_calc"] - rec["reorder_point"]

    rec["risk_flag"] = [classify(r, params) for r in rec.itertuples()]

    rec["suggested_order_qty"] = [
        float(np.ceil(max(0.0, t - ip))) if flag in ("STOCKOUT", "AT_RISK") else 0.0
        for t, ip, flag in zip(rec["target_level"], rec["inventory_position"],
                               rec["risk_flag"])
    ]
    rec["order_value"] = (rec["suggested_order_qty"] * rec["unit_cost"]).round(2)
    rec["on_hand_value"] = (rec["on_hand"] * rec["unit_cost"]).round(2)
    rec["demand_value_at_risk"] = (rec["avg_daily_demand"] * rec["lead_time_used"]
                                   * rec["unit_cost"]).round(2)
    rec["_flag_order"] = rec["risk_flag"].map(FLAG_ORDER)
    rec = rec.sort_values(["_flag_order", "order_value"], ascending=[True, False])

    numeric_cols = ["avg_daily_demand", "demand_std_dev", "demand_cv", "lead_time_used",
                    "ss_zscore", "ss_days", "safety_stock", "reorder_point_calc",
                    "target_level", "on_hand", "on_order", "inventory_position",
                    "days_of_cover", "gap_vs_master_rop", "suggested_order_qty",
                    "order_value", "on_hand_value", "trend_slope", "avg_daily_recent"]
    for col in numeric_cols:
        rec[col] = pd.to_numeric(rec[col], errors="coerce").round(3)
    return rec.drop(columns=["_flag_order"]).reset_index(drop=True)


def classify(row, params) -> str:
    """Rule-based risk label -- thresholds are configurable (config.py).

    STOCKOUT : already out of stock (on_hand <= 0), or the inventory position
               cannot even cover expected demand during the lead time
               (IP < mu_d * L)  -> a stockout is expected before the next
               delivery could arrive.
    AT_RISK  : IP is below the reorder point but still covers lead-time demand
               -> place the recommended order now.
    EXCESS   : IP is above the ROP and on-hand cover exceeds
               EXCESS_COVER_DAYS -> cash tied up in stock.
    OK       : everything else.
    """
    rop = row.reorder_point_calc
    ip = row.inventory_position
    on_hand = row.on_hand
    cover = row.days_of_cover
    mu = row.avg_daily_demand
    lead_time = row.lead_time_used if row.lead_time_used else 0.0

    zero_demand = (mu is None or mu <= 0)
    if zero_demand:
        # No demand in the window: can only be flagged as excess stock.
        return "EXCESS" if on_hand > 0 else "OK"

    if on_hand <= 0:
        return "STOCKOUT"
    if ip < mu * lead_time:                  # cannot cover lead-time demand
        return "STOCKOUT"
    if ip < rop:                             # below reorder point
        return "AT_RISK"
    if cover is not None and cover > params.excess_cover_days:
        return "EXCESS"
    return "OK"


def empty_recommendations() -> pd.DataFrame:
    return pd.DataFrame(columns=[
        "product_id", "sku", "product_name", "category", "unit_cost", "reorder_point",
        "supplier_id", "supplier_name", "avg_daily_demand", "demand_std_dev", "demand_cv",
        "demand_total", "abc_class", "avg_daily_recent", "trend_slope", "lead_time_used",
        "lead_time_source", "on_hand", "on_order", "inventory_position", "ss_zscore",
        "ss_days", "safety_stock", "reorder_point_calc", "target_level", "days_of_cover",
        "gap_vs_master_rop", "risk_flag", "suggested_order_qty", "order_value",
        "on_hand_value", "demand_value_at_risk"])


# ------------------------------------------------------------------ summary
def portfolio_summary(rec: pd.DataFrame, params) -> dict:
    """Headline numbers for the dashboard / report cover page."""
    if rec.empty:
        return {
            "products": 0, "stockout": 0, "at_risk": 0, "excess": 0, "ok": 0,
            "inventory_value": 0.0, "suggested_order_value": 0.0,
            "avg_days_of_cover": None, "parameters": params.as_dict(),
        }
    counts = rec["risk_flag"].value_counts().to_dict()
    cover = rec["days_of_cover"].dropna()
    return {
        "products": int(len(rec)),
        "stockout": int(counts.get("STOCKOUT", 0)),
        "at_risk": int(counts.get("AT_RISK", 0)),
        "excess": int(counts.get("EXCESS", 0)),
        "ok": int(counts.get("OK", 0)),
        "inventory_value": round(float(rec["on_hand_value"].sum()), 2),
        "suggested_order_value": round(float(rec["order_value"].sum()), 2),
        "avg_days_of_cover": round(float(cover.mean()), 2) if len(cover) else None,
        "products_below_master_rop": int((rec["gap_vs_master_rop"] > 0).sum()),
        "abc_counts": rec["abc_class"].value_counts().to_dict() if "abc_class" in rec else {},
        "parameters": params.as_dict(),
    }


# -------------------------------------------------------------- turnover
def inventory_at(transactions: pd.DataFrame, product_ids, as_of) -> pd.Series:
    """Ledger balance per product on a given date (units)."""
    if transactions.empty:
        return pd.Series(0.0, index=list(product_ids))
    upto = transactions[transactions["transaction_date"] <= pd.Timestamp(as_of)]
    balance = upto.groupby("product_id")["quantity"].sum()
    return balance.reindex(list(product_ids), fill_value=0).astype(float)


def daily_balances(transactions: pd.DataFrame, product_ids, start, end) -> pd.DataFrame:
    """Running ledger balance per product per day (units), zero-filled.

    The balance is the cumulative sum of every movement up to and including
    each day, so it is the quantity that would have been on the shelf.
    """
    calendar = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="D")
    if transactions.empty:
        return pd.DataFrame(0.0, index=calendar, columns=list(product_ids))
    upto = transactions[transactions["transaction_date"] <= pd.Timestamp(end)]
    if upto.empty:
        return pd.DataFrame(0.0, index=calendar, columns=list(product_ids))
    pivot = (upto.groupby(["transaction_date", "product_id"])["quantity"].sum()
             .unstack(fill_value=0)
             .reindex(columns=list(product_ids), fill_value=0)
             .sort_index()
             .cumsum())
    return pivot.reindex(calendar, method="ffill").fillna(0.0).clip(lower=0)


def stock_turnover_table(transactions: pd.DataFrame, products: pd.DataFrame,
                         start, end) -> pd.DataFrame:
    """Turns per product for the period.

    COGS proxy          = units ISSUED in the period * unit_cost
    average inventory   = mean of the DAILY ledger balance in the period
                          (a 2-point start/end average is too sensitive to the
                          day a delivery happens to arrive on)
    turnover            = COGS / average inventory value        [turns per period]
    annualised turnover = turnover * 365 / period_days          [turns per year]
    days of inventory   = period_days / turnover                [days]
    """
    ids = products["product_id"].tolist()
    if not ids:
        return pd.DataFrame(columns=["product_id", "sku", "turns", "annualised_turns"])

    period_days = max(1, (pd.Timestamp(end) - pd.Timestamp(start)).days + 1)
    balances = daily_balances(transactions, ids, start, end)
    beginning = balances.iloc[0] if len(balances) else pd.Series(0.0, index=ids)
    ending = balances.iloc[-1] if len(balances) else pd.Series(0.0, index=ids)
    average = balances.mean() if len(balances) else pd.Series(0.0, index=ids)

    if transactions.empty:
        issued = pd.Series(0.0, index=ids)
    else:
        window = transactions[(transactions["transaction_date"] >= pd.Timestamp(start))
                              & (transactions["transaction_date"] <= pd.Timestamp(end))
                              & (transactions["transaction_type"] == "ISSUE")]
        issued = window.groupby("product_id")["quantity"].sum().abs() \
            .reindex(ids, fill_value=0).astype(float)

    frame = pd.DataFrame({
        "product_id": ids,
        "units_issued": issued.to_numpy(),
        "opening_stock": beginning.to_numpy(),
        "closing_stock": ending.to_numpy(),
        "average_inventory_units": average.to_numpy(),
    }).merge(products[["product_id", "sku", "product_name", "category", "unit_cost"]],
             on="product_id", how="left")
    frame["cogs"] = (frame["units_issued"] * frame["unit_cost"]).round(2)
    frame["average_inventory_value"] = (frame["average_inventory_units"]
                                        * frame["unit_cost"]).round(2)
    frame["turns"] = [formulas.stock_turnover(u, c, v) for u, c, v
                      in zip(frame["units_issued"], frame["unit_cost"],
                             frame["average_inventory_value"])]
    frame["days_of_inventory"] = [None if not t else round(period_days / t, 1)
                                  for t in frame["turns"]]
    frame["annualised_turns"] = [None if not t else round(t * 365 / period_days, 2)
                                 for t in frame["turns"]]
    frame["period_days"] = period_days
    for col in ("units_issued", "opening_stock", "closing_stock",
                "average_inventory_units", "turns"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce").round(2)
    return frame.sort_values("annualised_turns", ascending=False,
                             na_position="last").reset_index(drop=True)
