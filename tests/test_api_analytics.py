"""Integration tests for the analytics endpoints.

Covers: parameter handling, empty result sets, boundary values, DB failures
and the "no order is placed" guarantee.
"""
from __future__ import annotations

import pytest

from db import db as shared_db
from tests.conftest import requires_db

pytestmark = requires_db


class TestReorderRecommendations:
    def test_default_run(self, client):
        body = client.get("/api/analytics/reorder-recommendations?limit=5").get_json()
        assert body["count"] == 5
        assert body["parameters"]["window_days"] >= 14
        assert body["summary"]["products"] >= 24
        assert "formulas" in body
        assert "no purchase order is created" in body["disclaimer"].lower()

    def test_flags_partition_the_portfolio(self, client):
        body = client.get("/api/analytics/reorder-recommendations?limit=200").get_json()
        flags = [row["risk_flag"] for row in body["data"]]
        assert set(flags) <= {"STOCKOUT", "AT_RISK", "EXCESS", "OK"}
        summary = body["summary"]
        assert summary["stockout"] + summary["at_risk"] + summary["excess"] + summary["ok"] \
            == summary["products"]

    def test_filter_by_flag(self, client):
        body = client.get("/api/analytics/reorder-recommendations?flag=STOCKOUT").get_json()
        assert all(row["risk_flag"] == "STOCKOUT" for row in body["data"])

    def test_invalid_flag(self, client):
        response = client.get("/api/analytics/reorder-recommendations?flag=MAYBE")
        assert response.status_code == 422

    def test_service_level_changes_safety_stock(self, client):
        low = client.get("/api/analytics/reorder-recommendations?limit=50&service_level=0.80"
                         ).get_json()
        high = client.get("/api/analytics/reorder-recommendations?limit=50&service_level=0.99"
                          ).get_json()
        low_ss = sum(r["safety_stock"] for r in low["data"])
        high_ss = sum(r["safety_stock"] for r in high["data"])
        assert high_ss > low_ss

    @pytest.mark.parametrize("query", [
        "service_level=1.5", "service_level=0", "window_days=5", "window_days=5000",
        "safety_stock_days=-1", "review_period_days=200", "excess_cover_days=1",
        "safety_stock_policy=median", "horizon=0", "horizon=500",
    ])
    def test_boundary_parameters_rejected(self, client, query):
        response = client.get(f"/api/analytics/reorder-recommendations?{query}")
        assert response.status_code == 422, query

    def test_boundary_parameters_accepted(self, client):
        for query in ("window_days=14", "window_days=730", "service_level=0.5",
                      "service_level=0.999", "safety_stock_days=0",
                      "review_period_days=0", "horizon=1", "horizon=180"):
            response = client.get(f"/api/analytics/reorder-recommendations?{query}&limit=3")
            assert response.status_code == 200, query

    def test_product_filter_narrows_results(self, client):
        body = client.get("/api/analytics/reorder-recommendations?product_id=1&limit=50"
                          ).get_json()
        assert body["count"] == 1
        assert body["data"][0]["sku"] == "AUTO-BRK-0012"

    def test_unknown_product_gives_empty_result_not_error(self, client):
        body = client.get("/api/analytics/reorder-recommendations?product_id=999999"
                          ).get_json()
        assert body["count"] == 0
        assert body["data"] == []


class TestSupplierAnalytics:
    def test_summary(self, client):
        body = client.get("/api/analytics/suppliers").get_json()
        assert body["count"] >= 6
        assert "definitions" in body
        graded = [row for row in body["data"] if row["grade"] != "NO_DATA"]
        assert graded, "at least one supplier should have deliveries"
        for row in graded:
            assert 0 <= row["on_time_rate"] <= 1
            assert row["supplier_score"] is not None

    def test_supplier_deliveries(self, client):
        body = client.get("/api/analytics/suppliers/1/deliveries").get_json()
        assert body["count"] >= 1
        assert "delay_days" in body["data"][0]

    def test_supplier_without_orders_returns_404(self, client):
        assert client.get("/api/analytics/suppliers/99999/deliveries").status_code == 404


class TestDemandAnalytics:
    def test_summary(self, client):
        body = client.get("/api/analytics/demand").get_json()
        assert body["count"] >= 24
        assert len(body["category_totals"]) >= 5
        row = body["data"][0]
        assert row["avg_daily_demand"] is not None
        assert row["abc_class"] in ("A", "B", "C")

    def test_series_is_zero_filled(self, client):
        body = client.get("/api/analytics/demand/series?product_id=1").get_json()
        window = body["window"]
        assert body["count"] >= 60
        assert all("date" in row and "quantity" in row for row in body["data"])
        assert window["start"] <= window["end"]

    def test_series_requires_product_id(self, client):
        assert client.get("/api/analytics/demand/series").status_code == 400

    def test_series_unknown_product(self, client):
        assert client.get("/api/analytics/demand/series?product_id=999999").status_code == 404


class TestRiskEndpoint:
    def test_structure_and_thresholds(self, client):
        body = client.get("/api/analytics/risks").get_json()
        assert set(body) >= {"stockout", "at_risk", "excess", "thresholds"}
        assert body["thresholds"]["excess_cover_days"] > 0
        assert isinstance(body["stockout"]["items"], list)

    def test_stockout_items_have_suggestions(self, client):
        body = client.get("/api/analytics/risks").get_json()
        for item in body["stockout"]["items"]:
            assert item["suggested_order_qty"] > 0


class TestForecastEndpoint:
    def test_all_products(self, client):
        body = client.get("/api/analytics/forecast").get_json()
        assert body["horizon_days"] >= 1
        assert body["evaluation"]["products_evaluated"] >= 20
        assert body["evaluation"]["mean_mae_baseline"] is not None
        methods = {row["method"] for row in body["forecasts"]}
        assert methods <= set(body["methods"])

    def test_single_product(self, client):
        body = client.get("/api/analytics/forecast?product_id=1").get_json()
        assert body["product_id"] == 1
        assert len(body["forecast"]) == body["horizon_days"]
        assert body["selected_method"] in body["methods"]
        row = body["forecast"][0]
        assert row["lower_80"] <= row["forecast"] <= row["upper_80"]

    def test_unknown_product(self, client):
        assert client.get("/api/analytics/forecast?product_id=999999").status_code == 404


class TestRunAndAudit:
    def test_run_stores_snapshot_without_creating_orders(self, client, db):
        before = db.fetch_scalar("SELECT COUNT(*) FROM purchase_orders")
        response = client.post("/api/analytics/run")
        assert response.status_code == 201
        body = response.get_json()
        assert body["run_id"]
        assert body["stored"] is True
        after = db.fetch_scalar("SELECT COUNT(*) FROM purchase_orders")
        assert before == after, "the analysis must never create purchase orders"

        stored = db.fetch_scalar(
            "SELECT COUNT(*) FROM reorder_recommendations WHERE run_id = %s",
            (body["run_id"],))
        assert stored >= 24

    def test_runs_list(self, client):
        body = client.get("/api/analytics/runs").get_json()
        assert body["count"] >= 1
        assert "service_level" in body["data"][0]


class TestChartsAndReports:
    def test_chart_catalogue(self, client):
        body = client.get("/api/charts").get_json()
        assert body["count"] >= 3
        for item in body["data"]:
            assert item["file"].endswith(".png")
            assert len(item["interpretation"]) > 40

    def test_chart_png_served(self, client):
        response = client.get("/api/charts/01_demand_trend.png")
        assert response.status_code == 200
        assert response.mimetype == "image/png"
        assert response.data[:8] == b"\x89PNG\r\n\x1a\n"

    def test_missing_chart_404(self, client):
        assert client.get("/api/charts/does-not-exist.png").status_code == 404

    def test_report_path_traversal_blocked(self, client):
        assert client.get("/api/reports/../../etc/passwd").status_code in (400, 404)


class TestDashboard:
    def test_dashboard_payload(self, client):
        body = client.get("/api/analytics/dashboard").get_json()
        assert body["portfolio"]["products"] >= 24
        assert isinstance(body["top_risks"], list)
        assert isinstance(body["charts"], list)


class TestDatabaseFailure:
    def test_health_reports_degraded_when_db_down(self, broken_app):
        response = broken_app.test_client().get("/api/health")
        assert response.status_code == 503
        body = response.get_json()
        assert body["connected"] is False
        assert body["status"] == "degraded"

    def test_crud_returns_503_when_db_down(self, broken_app):
        response = broken_app.test_client().get("/api/products")
        assert response.status_code == 503
        assert response.get_json()["error"]["code"] == "DATABASE_UNAVAILABLE"

    def test_analytics_returns_503_when_db_down(self, broken_app, clear_analytics_cache):
        # cache cleared first, otherwise the endpoint could (legitimately)
        # answer from a previously computed in-process result
        response = broken_app.test_client().get("/api/analytics/reorder-recommendations")
        assert response.status_code == 503

    def test_post_returns_503_when_db_down(self, broken_app):
        response = broken_app.test_client().post(
            "/api/products", json={"sku": "X-1", "product_name": "x", "category": "x",
                                   "unit_cost": 1, "lead_time_days": 1, "supplier_id": 1})
        assert response.status_code == 503


@pytest.fixture()
def clear_analytics_cache():
    """Explicit cache reset, used by tests that inspect the cache itself.

    (Not autouse: the cache key contains the parameter set, so tests with
    different parameters never read each other's results, and re-running the
    portfolio for every test would make the suite unnecessarily slow.)
    """
    from api import routes_analytics
    routes_analytics._CACHE.clear()
    yield
    routes_analytics._CACHE.clear()
