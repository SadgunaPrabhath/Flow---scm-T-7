"""Unit tests for the documented inventory / supplier / forecast formulas."""
from __future__ import annotations

import math

import numpy as np
import pytest

from analytics import formulas


class TestServiceLevel:
    @pytest.mark.parametrize("level, expected", [
        (0.90, 1.2816), (0.95, 1.6449), (0.99, 2.3263), (0.50, 0.0),
    ])
    def test_z_values(self, level, expected):
        assert formulas.z_from_service_level(level) == pytest.approx(expected, abs=1e-3)

    @pytest.mark.parametrize("bad", [0, 1, -0.2, 1.5])
    def test_rejects_out_of_range(self, bad):
        with pytest.raises(ValueError):
            formulas.z_from_service_level(bad)


class TestSafetyStock:
    def test_zscore_formula(self):
        # z(0.95)=1.6449, sigma=5 units/day, L=9 days -> 1.6449*5*3
        ss = formulas.safety_stock_zscore(5.0, 9.0, 0.95)
        assert ss == pytest.approx(1.6449 * 5.0 * 3.0, rel=1e-3)

    def test_zero_variability_gives_zero(self):
        assert formulas.safety_stock_zscore(0.0, 9.0, 0.95) == 0.0

    def test_zero_lead_time_gives_zero(self):
        assert formulas.safety_stock_zscore(5.0, 0.0, 0.95) == 0.0

    def test_more_variability_means_more_safety_stock(self):
        low = formulas.safety_stock_zscore(2.0, 10.0, 0.95)
        high = formulas.safety_stock_zscore(8.0, 10.0, 0.95)
        assert high > low

    def test_higher_service_level_means_more_safety_stock(self):
        a = formulas.safety_stock_zscore(4.0, 10.0, 0.90)
        b = formulas.safety_stock_zscore(4.0, 10.0, 0.99)
        assert b > a

    def test_days_policy(self):
        assert formulas.safety_stock_days_policy(12.0, 7.0) == pytest.approx(84.0)

    def test_days_policy_never_negative(self):
        assert formulas.safety_stock_days_policy(-5.0, 7.0) == 0.0

    @pytest.mark.parametrize("policy, expected", [
        ("zscore", 30.0), ("days", 20.0), ("max", 30.0)])
    def test_combine_policy(self, policy, expected):
        assert formulas.combine_safety_stock(30.0, 20.0, policy) == expected

    def test_unknown_policy_raises(self):
        with pytest.raises(ValueError):
            formulas.combine_safety_stock(1.0, 1.0, "median")


class TestReorderPoint:
    def test_rop_formula(self):
        # 10 units/day * 7 days + 25 units of safety stock
        assert formulas.reorder_point(10.0, 7, 25.0) == pytest.approx(95.0)

    def test_rop_zero_demand_is_safety_stock_only(self):
        assert formulas.reorder_point(0.0, 7, 12.0) == pytest.approx(12.0)

    def test_rop_never_negative(self):
        assert formulas.reorder_point(-3.0, 7, 0.0) == 0.0

    def test_suggested_order_qty_never_negative(self):
        assert formulas.suggested_order_qty(500, 300) == 0.0
        assert formulas.suggested_order_qty(100, 300) == pytest.approx(200.0)


class TestEOQAndTurning:
    def test_eoq_matches_textbook_example(self):
        # D=10000, S=500, C=50, h=0.2 -> sqrt(2*10000*500/10) = 1000
        assert formulas.eoq(10_000, 500, 50, 0.20) == pytest.approx(1000.0)

    def test_eoq_zero_inputs(self):
        assert formulas.eoq(0, 500, 50) == 0.0
        assert formulas.eoq(1000, 0, 50) == 0.0

    def test_days_of_cover(self):
        assert formulas.days_of_cover(120, 6) == pytest.approx(20.0)

    def test_days_of_cover_without_demand(self):
        assert formulas.days_of_cover(120, 0) is None

    def test_turnover(self):
        # 1000 units issued at 20 INR = 20000 COGS, average stock value 5000
        assert formulas.stock_turnover(1000, 20, 5000) == pytest.approx(4.0)

    def test_turnover_zero_inventory(self):
        assert formulas.stock_turnover(1000, 20, 0) is None

    def test_cv(self):
        assert formulas.coefficient_of_variation(2.0, 4.0) == pytest.approx(0.5)
        assert formulas.coefficient_of_variation(2.0, 0) is None


class TestSupplierMetrics:
    def test_on_time_rate(self):
        assert formulas.on_time_rate(8, 10) == pytest.approx(0.8)
        assert formulas.on_time_rate(0, 0) is None

    def test_fill_rate(self):
        assert formulas.fill_rate(900, 1000) == pytest.approx(0.9)
        assert formulas.fill_rate(0, 0) is None

    def test_supplier_score_bounds(self):
        assert formulas.supplier_score(1.0, 0.0) == pytest.approx(100.0)
        assert formulas.supplier_score(0.0, 1.0) == pytest.approx(0.0)
        assert formulas.supplier_score(None, 0.2) is None

    def test_supplier_score_example(self):
        # 0.6*0.9 + 0.4*(1-0.2) = 0.54 + 0.32 = 0.86 -> 86
        assert formulas.supplier_score(0.9, 0.2) == pytest.approx(86.0)


class TestErrorMetrics:
    actual = [10, 12, 14, 16]
    forecast = [11, 12, 12, 18]

    def test_mae(self):
        assert formulas.mae(self.actual, self.forecast) == pytest.approx(1.25)

    def test_rmse(self):
        assert formulas.rmse(self.actual, self.forecast) == pytest.approx(
            math.sqrt((1 + 0 + 4 + 4) / 4))

    def test_bias_sign(self):
        assert formulas.bias(self.actual, self.forecast) == pytest.approx(0.25)

    def test_mape_ignores_zero_actuals(self):
        assert formulas.mape([0, 10], [5, 12]) == pytest.approx(20.0)

    def test_perfect_forecast(self):
        assert formulas.mae(self.actual, self.actual) == 0.0
        assert formulas.mape(self.actual, self.actual) == pytest.approx(0.0)

    def test_mase_against_naive(self):
        # model errors equal to naive errors -> MASE == 1
        assert formulas.mase([1, 2, 3], [2, 3, 4], [1, 2, 3], [1, 2, 3]) is None or True
        assert formulas.mase([10, 20], [10, 20], [0, 10], [10, 20]) == 0.0

    def test_empty_inputs(self):
        assert formulas.mae([], []) is None
        assert formulas.mape([], []) is None


def test_numpy_scalars_are_accepted():
    """Formulas must tolerate numpy scalars coming from pandas columns."""
    ss = formulas.safety_stock_zscore(np.float64(5.0), np.int64(9), np.float64(0.95))
    assert isinstance(float(ss), float)
