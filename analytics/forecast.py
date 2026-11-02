"""Simple, explainable demand forecasting + honest evaluation.

Methods implemented
-------------------
1. `naive_mean`          baseline: repeat the mean of the last N days  (N=28)
2. `seasonal_naive`      baseline: repeat the same weekday from last week
3. `moving_average`      last 14 days, flat forecast
4. `holt_winters`        additive trend + additive weekly seasonality
                         (statsmodels ExponentialSmoothing, damped trend)

Evaluation protocol (no leaking of the hold-out period)
------------------------------------------------------
* train  = window start .. (end - holdout_days)
* test   = the final `holdout_days` days of the window
* every model is fitted on train only and scored on test
* MAE / RMSE / MAPE are reported in units; MASE compares the model with
  the in-sample naive (last value) forecast, so MASE < 1 means
  "better than naive".
* the winner (lowest test MAE, never worse than the baseline by more than
  a 5% tolerance) is refitted on the whole window to produce the
  forward forecast that feeds the recommendation report.

All forecasts are decision support: the API returns the numbers plus the
method that produced them, and never writes an order.
"""
from __future__ import annotations

import warnings

import numpy as np
import pandas as pd

from analytics import formulas

METHODS = ("naive_mean", "seasonal_naive", "moving_average", "holt_winters")
MIN_HISTORY_DAYS = 21


# --------------------------------------------------------------- methods
def _naive_mean(train: np.ndarray, horizon: int, window: int) -> np.ndarray:
    tail = train[-window:] if len(train) >= window else train
    value = float(np.mean(tail)) if len(tail) else 0.0
    return np.full(horizon, value)


def _seasonal_naive(train: np.ndarray, horizon: int, period: int = 7) -> np.ndarray:
    if len(train) < period:
        return _naive_mean(train, horizon, 4)
    last_week = train[-period:]
    return np.array([last_week[i % period] for i in range(horizon)], dtype=float)


def _moving_average(train: np.ndarray, horizon: int, window: int = 14) -> np.ndarray:
    tail = train[-window:] if len(train) >= window else train
    return np.full(horizon, float(np.mean(tail)) if len(tail) else 0.0)


def _holt_winters(train: np.ndarray, horizon: int, period: int = 7):
    """Additive damped trend + additive weekly seasonality."""
    try:
        from statsmodels.tsa.holtwinters import ExponentialSmoothing
    except ImportError:                                    # optional dependency
        return None
    if len(train) < 2 * period:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            model = ExponentialSmoothing(
                np.asarray(train, dtype=float),
                trend="add",
                damped_trend=True,
                seasonal="add",
                seasonal_periods=period,
                initialization_method="estimated",
            ).fit(optimized=True)
            return np.clip(np.asarray(model.forecast(horizon), dtype=float), 0, None)
    except Exception:
        return None


def run_method(method: str, train: np.ndarray, horizon: int,
               baseline_window: int = 28) -> np.ndarray | None:
    if method == "naive_mean":
        return _naive_mean(train, horizon, baseline_window)
    if method == "seasonal_naive":
        return _seasonal_naive(train, horizon)
    if method == "moving_average":
        return _moving_average(train, horizon, 14)
    if method == "holt_winters":
        return _holt_winters(train, horizon)
    raise ValueError(f"Unknown method: {method}")


# ------------------------------------------------------------ evaluation
def evaluate_series(series: np.ndarray, holdout_days: int,
                    baseline_window: int = 28) -> tuple[pd.DataFrame, str]:
    """Score every method on the hold-out tail. Returns (table, best_method)."""
    n = len(series)
    if n < MIN_HISTORY_DAYS + 1:
        return pd.DataFrame(columns=["method", "mae", "rmse", "mape", "mase", "bias"]), \
            "naive_mean"
    holdout = min(holdout_days, max(1, n - MIN_HISTORY_DAYS))
    train, test = series[:-holdout], series[-holdout:]

    # in-sample naive errors for MASE
    naive_insample = formulas.mae(train[1:], train[:-1])

    rows = []
    for method in METHODS:
        pred = run_method(method, train, holdout, baseline_window)
        if pred is None:
            continue
        rows.append({
            "method": method,
            "mae": formulas.mae(test, pred),
            "rmse": formulas.rmse(test, pred),
            "mape": formulas.mape(test, pred),
            "mase": formulas.mase(test, pred, train[1:], train[:-1]),
            "bias": formulas.bias(test, pred),
        })
    table = pd.DataFrame(rows)
    if table.empty:
        return table, "naive_mean"
    table = table.sort_values("mae").reset_index(drop=True)
    baseline_mae = float(table.loc[table["method"] == "naive_mean", "mae"].iloc[0]) \
        if (table["method"] == "naive_mean").any() else np.inf
    best = table.iloc[0]
    # only switch away from the baseline if the gain is real (> 5%)
    if best["method"] != "naive_mean" and best["mae"] > baseline_mae * 0.95:
        best_method = "naive_mean"
    else:
        best_method = str(best["method"])
    table["selected"] = table["method"] == best_method
    for col in ("mae", "rmse", "mape", "bias", "mase"):
        table[col] = pd.to_numeric(table[col], errors="coerce").round(3)
    _ = naive_insample
    return table, best_method


def forecast_product(series: np.ndarray, horizon: int, holdout_days: int = 28,
                     baseline_window: int = 28) -> dict:
    """Full pipeline for one product's daily series."""
    series = np.nan_to_num(np.asarray(series, dtype=float), nan=0.0)
    if series.size == 0:
        return {"method": "none", "forecast": [], "evaluation": pd.DataFrame(),
                "avg_forecast": 0.0, "residual_std": 0.0, "note": "no demand history"}

    eval_table, best = evaluate_series(series, holdout_days, baseline_window)
    pred = run_method(best, series, horizon, baseline_window)
    if pred is None:
        best, pred = "naive_mean", _naive_mean(series, horizon, baseline_window)
    pred = np.clip(pred, 0, None)

    # residual standard deviation of the chosen method on the hold-out tail
    if len(eval_table):
        chosen_rmse = float(eval_table.loc[eval_table["method"] == best, "rmse"].iloc[0]) \
            if (eval_table["method"] == best).any() else float(np.std(series))
    else:
        chosen_rmse = float(np.std(series)) if series.size > 1 else 0.0
    if not np.isfinite(chosen_rmse):
        chosen_rmse = 0.0

    return {
        "method": best,
        "forecast": pred.round(2).tolist(),
        "evaluation": eval_table,
        "avg_forecast": float(np.mean(pred)),
        "total_forecast": float(np.sum(pred)),
        "residual_std": chosen_rmse,
        "note": "",
    }


def forecast_all(series_map: dict[int, np.ndarray], horizon: int,
                 holdout_days: int = 28, baseline_window: int = 28) -> pd.DataFrame:
    """Run forecast_product for every product and return a tidy table."""
    rows = []
    for pid, series in series_map.items():
        result = forecast_product(series, horizon, holdout_days, baseline_window)
        rows.append({
            "product_id": pid,
            "method": result["method"],
            "avg_daily_forecast": round(result["avg_forecast"], 3),
            "total_forecast": round(result.get("total_forecast", 0.0), 2),
            "residual_std": round(result["residual_std"], 3),
            "note": result["note"],
        })
    return pd.DataFrame(rows)


def comparison_table(series_map: dict[int, np.ndarray], holdout_days: int,
                     baseline_window: int = 28) -> pd.DataFrame:
    """Long table: product x method x metrics (used in the report and tests)."""
    frames = []
    for pid, series in series_map.items():
        table, best = evaluate_series(np.asarray(series, dtype=float), holdout_days,
                                      baseline_window)
        if table.empty:
            continue
        table = table.copy()
        table.insert(0, "product_id", pid)
        table["best_method"] = best
        frames.append(table)
    if not frames:
        return pd.DataFrame(columns=["product_id", "method", "mae", "rmse", "mape",
                                     "mase", "bias", "selected", "best_method"])
    return pd.concat(frames, ignore_index=True)


def forecast_frame(dates: pd.DatetimeIndex, forecast: np.ndarray,
                   residual_std: float, level: float = 1.28) -> pd.DataFrame:
    """Add an ~80% prediction band: forecast +/- 1.28 * residual std."""
    band = level * max(0.0, residual_std) * np.sqrt(np.arange(1, len(forecast) + 1) / 7)
    return pd.DataFrame({
        "date": dates,
        "forecast": np.round(forecast, 2),
        "lower_80": np.round(np.clip(forecast - band, 0, None), 2),
        "upper_80": np.round(forecast + band, 2),
    })
