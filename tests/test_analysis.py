"""Tests for the pandas/NumPy analysis layer, including empty-data behaviour."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from analytics import demand as demand_mod, forecast as fc, formulas, inventory, supplier


# ------------------------------------------------------------------ helpers
def make_demand(product_id=1, days=60, level=10, start=date(2026, 1, 1)):
    rows = [{"product_id": product_id,
             "demand_date": pd.Timestamp(start + timedelta(days=i)),
             "quantity": level} for i in range(days)]
    return pd.DataFrame(rows)


def make_products(ids=(1,)):
    return pd.DataFrame({
        "product_id": list(ids),
        "sku": [f"SKU-{i}" for i in ids],
        "product_name": [f"Product {i}" for i in ids],
        "category": ["Testing"] * len(ids),
        "unit_cost": [100.0] * len(ids),
        "lead_time_days": [7] * len(ids),
        "reorder_point": [50] * len(ids),
        "supplier_id": [1] * len(ids),
        "supplier_name": ["Test Supplier"] * len(ids),
        "is_active": [1] * len(ids),
    })


class TestDemandSeries:
    def test_zero_fills_missing_days(self):
        demand = make_demand(days=5, level=3)
        demand = demand[demand["demand_date"] != pd.Timestamp("2026-01-03")]
        series = demand_mod.daily_series(demand, date(2026, 1, 1), date(2026, 1, 5))
        assert len(series) == 5                       # calendar completeness
        assert series["quantity"].sum() == 12         # 4 days * 3 units
        assert (series["quantity"] == 0).sum() == 1

    def test_empty_demand_input(self):
        empty = pd.DataFrame(columns=["product_id", "demand_date", "quantity"])
        series = demand_mod.daily_series(empty, date(2026, 1, 1), date(2026, 1, 10),
                                         product_ids=[1])
        assert len(series) == 10
        assert series["quantity"].sum() == 0

    def test_stats_empty_input(self):
        empty = pd.DataFrame(columns=["product_id", "demand_date", "quantity"])
        stats = demand_mod.demand_stats(empty, make_products(), date(2026, 1, 1),
                                        date(2026, 1, 31))
        # products exist but no demand -> rows with zero means, not an exception
        assert len(stats) == 1
        assert stats.iloc[0]["avg_daily_demand"] == 0


class TestDemandStats:
    def test_constant_demand_has_zero_std(self):
        stats = demand_mod.demand_stats(make_demand(days=30, level=5), make_products(),
                                        date(2026, 1, 1), date(2026, 1, 30))
        row = stats.iloc[0]
        assert row["avg_daily_demand"] == pytest.approx(5.0)
        assert row["demand_std_dev"] == pytest.approx(0.0)
        assert row["demand_total"] == 150

    def test_mean_and_std_match_numpy(self):
        demand = make_demand(days=30, level=0)
        rng = np.random.default_rng(7)
        demand["quantity"] = rng.integers(0, 20, size=len(demand))
        stats = demand_mod.demand_stats(demand, make_products(), date(2026, 1, 1),
                                        date(2026, 1, 30))
        assert stats.iloc[0]["avg_daily_demand"] == pytest.approx(
            round(float(demand["quantity"].mean()), 3), abs=1e-3)
        assert stats.iloc[0]["demand_std_dev"] == pytest.approx(
            round(float(demand["quantity"].std(ddof=1)), 3), abs=1e-3)

    def test_abc_classes_are_a_b_c(self):
        products = make_products(ids=(1, 2, 3))
        products["unit_cost"] = [1000.0, 10.0, 1.0]
        rows = []
        for pid in (1, 2, 3):
            rows += [{"product_id": pid,
                      "demand_date": pd.Timestamp(date(2026, 1, 1) + timedelta(days=i)),
                      "quantity": 10} for i in range(10)]
        stats = demand_mod.demand_stats(pd.DataFrame(rows), products,
                                        date(2026, 1, 1), date(2026, 1, 10))
        assert set(stats["abc_class"]) <= {"A", "B", "C"}
        assert stats.loc[stats["product_id"] == 1, "abc_class"].iloc[0] == "A"


class TestReorderEngine:
    def params(self):
        from api.validators import AnalysisParams
        return AnalysisParams(window_days=90, service_level=0.95, safety_stock_days=7.0,
                              safety_stock_policy="max", review_period_days=7.0,
                              excess_cover_days=90.0, forecast_horizon_days=28)

    def build(self, avg=10.0, std=2.0, lead_time=7.0, on_hand=100.0, on_order=0.0):
        products = make_products()
        demand_stats = pd.DataFrame([{
            "product_id": 1, "avg_daily_demand": avg, "demand_std_dev": std,
            "demand_cv": std / avg if avg else None, "demand_total": avg * 90,
            "abc_class": "A", "avg_daily_recent": avg, "trend_slope": 0.0}])
        lead_times = pd.DataFrame([{"product_id": 1, "lead_time_used": lead_time,
                                    "lead_time_source": "master_data"}])
        positions = pd.DataFrame([{"product_id": 1, "on_hand": on_hand,
                                   "on_order": on_order}])
        return inventory.build_recommendations(demand_stats, lead_times, positions,
                                              products, self.params())

    def test_rop_equals_hand_calculation(self):
        rec = self.build()
        row = rec.iloc[0]
        expected_ss = max(formulas.safety_stock_zscore(2.0, 7.0, 0.95), 7.0 * 10.0)
        assert row["safety_stock"] == pytest.approx(expected_ss, rel=1e-3)
        assert row["reorder_point_calc"] == pytest.approx(10.0 * 7.0 + expected_ss,
                                                          rel=1e-3)

    def test_ok_when_stock_is_high(self):
        assert self.build(on_hand=1000.0).iloc[0]["risk_flag"] in ("OK", "EXCESS")

    def test_stockout_when_out_of_stock(self):
        assert self.build(on_hand=0.0).iloc[0]["risk_flag"] == "STOCKOUT"

    def test_at_risk_below_rop(self):
        # ROP is ~140 units; 100 units on hand covers lead-time demand (70) only
        assert self.build(on_hand=100.0).iloc[0]["risk_flag"] == "AT_RISK"

    def test_excess_when_cover_too_long(self):
        assert self.build(on_hand=1500.0).iloc[0]["risk_flag"] == "EXCESS"

    def test_on_order_counts_towards_position(self):
        rec = self.build(on_hand=10.0, on_order=500.0)
        assert rec.iloc[0]["inventory_position"] == pytest.approx(510.0)
        assert rec.iloc[0]["risk_flag"] != "STOCKOUT"

    def test_suggested_quantity_is_zero_unless_flagged(self):
        for on_hand in (0.0, 100.0, 1000.0):
            row = self.build(on_hand=on_hand).iloc[0]
            if row["risk_flag"] in ("OK", "EXCESS"):
                assert row["suggested_order_qty"] == 0

    def test_no_demand_product_is_not_stockout(self):
        rec = self.build(avg=0.0, std=0.0, on_hand=0.0)
        assert rec.iloc[0]["risk_flag"] == "OK"

    def test_empty_products_returns_empty_frame(self):
        rec = inventory.build_recommendations(
            pd.DataFrame(), pd.DataFrame(), pd.DataFrame(),
            pd.DataFrame(columns=make_products().columns), self.params())
        assert rec.empty
        assert "risk_flag" in rec.columns

    def test_portfolio_summary_counts_all_flags(self):
        rec = self.build(on_hand=0.0)
        summary = inventory.portfolio_summary(rec, self.params())
        assert summary["products"] == 1
        assert summary["stockout"] == 1
        assert summary["parameters"]["service_level"] == 0.95


class TestStockTurnover:
    def test_turnover_computation(self):
        """Average inventory is the mean DAILY balance, not a 2-point average."""
        products = make_products()
        start, end = date(2026, 1, 1), date(2026, 1, 31)      # 31 days
        txns = pd.DataFrame([
            {"transaction_id": 1, "product_id": 1,
             "transaction_date": pd.Timestamp("2025-12-31"),
             "transaction_type": "RECEIPT", "quantity": 1000},
            {"transaction_id": 2, "product_id": 1,
             "transaction_date": pd.Timestamp("2026-01-15"),
             "transaction_type": "ISSUE", "quantity": -500},
        ])
        table = inventory.stock_turnover_table(txns, products, start, end)
        row = table.iloc[0]
        # balance = 1000 for 14 days (1-14 Jan) and 500 for 17 days (15-31 Jan)
        expected_avg = (14 * 1000 + 17 * 500) / 31
        assert row["opening_stock"] == pytest.approx(1000.0)
        assert row["closing_stock"] == pytest.approx(500.0)
        assert row["average_inventory_units"] == pytest.approx(expected_avg, abs=0.01)
        assert row["units_issued"] == pytest.approx(500.0)
        # COGS 500*100 = 50,000 over average stock value 72,580.65
        expected_turns = 500 * 100 / (expected_avg * 100)
        assert row["turns"] == pytest.approx(expected_turns, abs=0.01)
        assert row["annualised_turns"] == pytest.approx(expected_turns * 365 / 31, abs=0.05)

    def test_daily_balances_use_the_whole_ledger(self):
        """A receipt before the window must still be included in the balance."""
        txns = pd.DataFrame([
            {"transaction_id": 1, "product_id": 1,
             "transaction_date": pd.Timestamp("2025-06-01"),
             "transaction_type": "RECEIPT", "quantity": 100},
        ])
        balances = inventory.daily_balances(txns, [1], date(2026, 1, 1), date(2026, 1, 5))
        assert (balances[1] == 100).all()
        assert len(balances) == 5

    def test_empty_ledger_is_safe(self):
        empty = pd.DataFrame(columns=["transaction_id", "product_id", "transaction_date",
                                      "transaction_type", "quantity"])
        table = inventory.stock_turnover_table(empty, make_products(),
                                               date(2026, 1, 1), date(2026, 1, 31))
        assert len(table) == 1
        assert table.iloc[0]["turns"] == 0 or pd.isna(table.iloc[0]["turns"])


class TestSupplierAnalytics:
    def pos(self):
        return pd.DataFrame([
            {"po_id": 1, "po_number": "PO-1", "supplier_id": 1,
             "supplier_name": "OnTime Co", "product_id": 1, "sku": "SKU-1",
             "quantity": 100, "received_qty": 100,
             "order_date": pd.Timestamp("2026-01-01"),
             "expected_date": pd.Timestamp("2026-01-11"),
             "received_date": pd.Timestamp("2026-01-10"), "status": "RECEIVED"},
            {"po_id": 2, "po_number": "PO-2", "supplier_id": 1,
             "supplier_name": "OnTime Co", "product_id": 1, "sku": "SKU-1",
             "quantity": 100, "received_qty": 100,
             "order_date": pd.Timestamp("2026-02-01"),
             "expected_date": pd.Timestamp("2026-02-11"),
             "received_date": pd.Timestamp("2026-02-16"), "status": "RECEIVED"},
            {"po_id": 3, "po_number": "PO-3", "supplier_id": 1,
             "supplier_name": "OnTime Co", "product_id": 1, "sku": "SKU-1",
             "quantity": 50, "received_qty": None,
             "order_date": pd.Timestamp("2026-03-01"),
             "expected_date": pd.Timestamp("2026-03-11"),
             "received_date": pd.NaT, "status": "OPEN"},
        ])

    def test_lead_time_and_delay(self):
        prepared = supplier.prepare_pos(self.pos())
        delivered = prepared[prepared["is_delivered"]]
        assert list(delivered["actual_lead_time_days"]) == [9, 15]
        assert list(delivered["delay_days"]) == [-1, 5]
        assert list(delivered["on_time"]) == [True, False]

    def test_open_orders_are_excluded_from_lead_time(self):
        summary = supplier.supplier_summary(self.pos())
        row = summary.iloc[0]
        assert row["orders_total"] == 3
        assert row["orders_delivered"] == 2
        assert row["open_orders"] == 1
        assert row["avg_lead_time_days"] == pytest.approx(12.0)

    def test_on_time_rate_and_grade(self):
        row = supplier.supplier_summary(self.pos()).iloc[0]
        assert row["on_time_rate"] == pytest.approx(0.5)
        assert row["grade"] in ("C", "D")

    def test_empty_purchase_orders(self):
        empty = pd.DataFrame(columns=["po_id", "supplier_id", "supplier_name", "quantity",
                                      "received_qty", "order_date", "expected_date",
                                      "received_date", "status"])
        # no suppliers supplied -> empty table, no exception
        assert supplier.supplier_summary(empty).empty
        # suppliers supplied but never delivered -> NO_DATA grade instead of a crash
        suppliers = pd.DataFrame({"supplier_id": [1], "supplier_name": ["Idle Co"],
                                  "promised_lead_time_days": [10]})
        summary = supplier.supplier_summary(empty, suppliers)
        assert len(summary) == 1
        assert summary.iloc[0]["grade"] == "NO_DATA"

    def test_product_lead_time_uses_observations_only_after_three_deliveries(self):
        pos = self.pos()
        products = make_products()
        out = supplier.product_lead_times(pos, products)
        assert out.iloc[0]["lead_time_source"] == "master_data"   # only 2 deliveries
        extra = pd.concat([pos, pos.assign(po_id=4, po_number="PO-4")], ignore_index=True)
        out2 = supplier.product_lead_times(extra, products)
        assert out2.iloc[0]["lead_time_source"] == "observed_pos"


class TestForecast:
    def series(self, n=120, level=20):
        rng = np.random.default_rng(3)
        weekday = np.array([1.0, 1.0, 1.05, 1.1, 1.2, 0.7, 0.5])
        values = [level * weekday[i % 7] + rng.normal(0, 1.5) for i in range(n)]
        return np.clip(np.array(values), 0, None)

    def test_constant_series_forecast_matches_mean(self):
        series = np.full(60, 10.0)
        out = fc.forecast_product(series, horizon=7)
        assert out["avg_forecast"] == pytest.approx(10.0, abs=0.5)

    def test_evaluation_returns_all_methods(self):
        table, best = fc.evaluate_series(self.series(), holdout_days=28)
        assert set(table["method"]) == set(fc.METHODS)
        assert best in fc.METHODS
        assert table["selected"].sum() == 1

    def test_selected_model_is_not_worse_than_baseline(self):
        table, best = fc.evaluate_series(self.series(), holdout_days=28)
        baseline = table.loc[table["method"] == "naive_mean", "mae"].iloc[0]
        chosen = table.loc[table["method"] == best, "mae"].iloc[0]
        assert chosen <= baseline * 1.05

    def test_short_history_falls_back_to_naive(self):
        table, best = fc.evaluate_series(np.array([1.0, 2.0, 3.0]), holdout_days=7)
        assert table.empty
        assert best == "naive_mean"
        out = fc.forecast_product(np.array([1.0, 2.0, 3.0]), horizon=3)
        assert len(out["forecast"]) == 3

    def test_empty_series(self):
        out = fc.forecast_product(np.array([]), horizon=5)
        assert out["method"] == "none"
        assert out["forecast"] == []

    def test_forecast_is_never_negative(self):
        series = np.zeros(60)
        out = fc.forecast_product(series, horizon=14)
        assert min(out["forecast"]) >= 0

    def test_comparison_table_shape(self):
        table = fc.comparison_table({1: self.series(), 2: self.series(level=5)},
                                    holdout_days=28)
        assert set(table["product_id"]) == {1, 2}
        assert len(table) <= 2 * len(fc.METHODS)

    def test_prediction_band_contains_point_forecast(self):
        dates = pd.date_range("2026-11-01", periods=10, freq="D")
        frame = fc.forecast_frame(dates, np.full(10, 20.0), residual_std=3.0)
        assert (frame["lower_80"] <= frame["forecast"]).all()
        assert (frame["upper_80"] >= frame["forecast"]).all()

    def test_error_metrics_are_not_nan_for_zero_demand(self):
        series = np.zeros(60)
        table, _ = fc.evaluate_series(series, holdout_days=14)
        assert not table.empty
        assert table["mae"].notna().all()
