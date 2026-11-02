"""Inventory & supplier formulas -- single source of truth.

All functions are pure (NumPy/Python only) so they can be unit-tested and
re-used by the API, the CLI pipeline and the charts.

Units used everywhere
---------------------
* demand            : units / day
* lead time L       : days (calendar days, weekends included -- A6)
* safety stock SS   : units
* reorder point ROP : units
* days of cover     : days
* money             : INR (matches products.unit_cost currency)
"""
from __future__ import annotations

import math
from statistics import NormalDist


# --------------------------------------------------------------- z-factor
def z_from_service_level(service_level: float) -> float:
    """Standard-normal quantile for a target cycle service level.

    service_level 0.90 -> 1.2816, 0.95 -> 1.6449, 0.99 -> 2.3263.
    """
    if not 0 < service_level < 1:
        raise ValueError("service_level must be between 0 and 1 (exclusive).")
    return NormalDist().inv_cdf(service_level)


# ----------------------------------------------------------- safety stock
def safety_stock_zscore(demand_std: float, lead_time_days: float,
                        service_level: float) -> float:
    """SS = z * sigma_d * sqrt(L).

    sigma_d = standard deviation of DAILY demand (units/day).
    The sqrt(L) term comes from assuming independent daily demand, so the
    standard deviation of demand over the lead time is sigma_d*sqrt(L).
    """
    if lead_time_days <= 0 or demand_std <= 0:
        return 0.0
    return z_from_service_level(service_level) * demand_std * math.sqrt(lead_time_days)


def safety_stock_days_policy(avg_daily_demand: float, safety_stock_days: float) -> float:
    """SS = safety_stock_days * average daily demand  (days-of-cover policy)."""
    return max(0.0, avg_daily_demand * safety_stock_days)


def combine_safety_stock(ss_zscore: float, ss_days: float, policy: str) -> float:
    """policy: 'zscore' | 'days' | 'max' (default: the more conservative)."""
    policy = (policy or "max").lower()
    if policy == "zscore":
        return ss_zscore
    if policy == "days":
        return ss_days
    if policy == "max":
        return max(ss_zscore, ss_days)
    raise ValueError(f"Unknown safety-stock policy: {policy}")


# ----------------------------------------------------------- reorder point
def reorder_point(avg_daily_demand: float, lead_time_days: float,
                  safety_stock: float) -> float:
    """ROP = average demand during lead time + safety stock (units)."""
    return max(0.0, avg_daily_demand * lead_time_days + safety_stock)


def suggested_order_qty(inventory_position: float, target_level: float) -> float:
    """How much would be needed to reach `target_level` (units, >= 0).

    This is a *suggestion* only -- the system never creates a purchase order.
    """
    return max(0.0, target_level - inventory_position)


def eoq(annual_demand_units: float, order_cost: float, unit_cost: float,
        holding_rate: float = 0.20) -> float:
    """Classic EOQ = sqrt(2*D*S / (h*C)); shown for reference in the report."""
    denominator = holding_rate * unit_cost
    if annual_demand_units <= 0 or order_cost <= 0 or denominator <= 0:
        return 0.0
    return math.sqrt(2 * annual_demand_units * order_cost / denominator)


# ------------------------------------------------------------- inventory
def days_of_cover(on_hand: float, avg_daily_demand: float) -> float | None:
    """on_hand / avg_daily_demand (days). None when there is no demand."""
    if avg_daily_demand is None or avg_daily_demand <= 0:
        return None
    return on_hand / avg_daily_demand


def stock_turnover(units_issued: float, unit_cost: float,
                   average_inventory_value: float) -> float | None:
    """COGS / average inventory value (turns per period).

    Both numerator and denominator must use the SAME period.
    """
    if average_inventory_value is None or average_inventory_value <= 0:
        return None
    return (units_issued * unit_cost) / average_inventory_value


def coefficient_of_variation(std: float, mean: float) -> float | None:
    """CV = sigma / mu (dimensionless). > 1 means very erratic demand."""
    if mean is None or mean <= 0:
        return None
    return std / mean


# -------------------------------------------------------------- supplier
def on_time_rate(on_time_pos: int, delivered_pos: int) -> float | None:
    """Share of delivered POs whose received_date <= expected_date."""
    if not delivered_pos:
        return None
    return on_time_pos / delivered_pos


def fill_rate(units_received: float, units_ordered: float) -> float | None:
    """Units received / units ordered on delivered POs."""
    if not units_ordered:
        return None
    return units_received / units_ordered


def supplier_score(on_time: float | None, lead_time_cv: float | None,
                   *, on_time_weight: float = 0.6) -> float | None:
    """Composite 0-100 reliability score.

    score = 100 * (0.6 * on_time_rate + 0.4 * consistency)
    consistency = max(0, 1 - lead_time_cv)   (CV >= 1 scores zero)
    """
    if on_time is None:
        return None
    consistency = 0.0 if lead_time_cv is None else max(0.0, 1.0 - lead_time_cv)
    return 100.0 * (on_time_weight * on_time + (1 - on_time_weight) * consistency)


# ------------------------------------------------------------ forecasting
def mae(actual, forecast) -> float | None:
    pairs = [(a, f) for a, f in zip(actual, forecast) if a is not None and f is not None]
    if not pairs:
        return None
    return sum(abs(a - f) for a, f in pairs) / len(pairs)


def rmse(actual, forecast) -> float | None:
    pairs = [(a, f) for a, f in zip(actual, forecast) if a is not None and f is not None]
    if not pairs:
        return None
    return math.sqrt(sum((a - f) ** 2 for a, f in pairs) / len(pairs))


def mape(actual, forecast, *, epsilon: float = 1e-9) -> float | None:
    pairs = [(a, f) for a, f in zip(actual, forecast) if a is not None and f is not None]
    nonzero = [(a, f) for a, f in pairs if abs(a) > epsilon]
    if not nonzero:
        return None
    return 100.0 * sum(abs((a - f) / a) for a, f in nonzero) / len(nonzero)


def mase(actual, forecast, naive_actual, naive_forecast) -> float | None:
    """MASE = MAE(model) / MAE(naive in-sample). < 1 beats the naive model."""
    model_err = mae(actual, forecast)
    naive_err = mae(naive_actual, naive_forecast)
    if model_err is None or not naive_err:
        return None
    return model_err / naive_err


def bias(actual, forecast) -> float | None:
    """Mean(forecast - actual): > 0 means systematic over-forecasting."""
    pairs = [(a, f) for a, f in zip(actual, forecast) if a is not None and f is not None]
    if not pairs:
        return None
    return sum(f - a for a, f in pairs) / len(pairs)
