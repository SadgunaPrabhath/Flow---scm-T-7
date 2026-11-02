"""Integration tests for the CRUD REST API (normal, invalid, conflict, missing)."""
from __future__ import annotations

import pytest

from tests.conftest import requires_db
from tests.conftest import unique_suffix

pytestmark = requires_db


class TestHealthAndConfig:
    def test_health_ok(self, client):
        response = client.get("/api/health")
        assert response.status_code == 200
        body = response.get_json()
        assert body["connected"] is True
        assert body["checks"]["reference_data"]["products"] > 0

    def test_config_hides_credentials(self, client):
        body = client.get("/api/config").get_json()["data"]
        assert "DB_PASSWORD" not in body
        assert "SECRET_KEY" not in body
        assert body["SERVICE_LEVEL"] in (0.9, 0.95, 0.99)

    def test_api_index_lists_endpoints(self, client):
        body = client.get("/api").get_json()
        assert "endpoints" in body
        assert "order" in body["notice"].lower()

    def test_explorer_page_served(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert response.mimetype == "text/html"
        assert b"Decision support only" in response.data


class TestProducts:
    def test_list_paginates(self, client):
        body = client.get("/api/products?limit=5&offset=0").get_json()
        assert body["count"] == 5
        assert body["total"] >= 24
        assert len(body["data"]) == 5

    def test_list_filters_by_category(self, client):
        body = client.get("/api/products?category=Filtration").get_json()
        assert body["count"] >= 1
        assert all(p["category"] == "Filtration" for p in body["data"])

    def test_list_search(self, client):
        body = client.get("/api/products?search=Brake").get_json()
        assert body["count"] >= 1

    def test_invalid_limit_rejected(self, client):
        response = client.get("/api/products?limit=0")
        assert response.status_code == 422
        assert response.get_json()["error"]["code"] == "VALIDATION_ERROR"

    def test_limit_above_max_rejected(self, client):
        assert client.get("/api/products?limit=999").status_code == 422

    def test_get_one(self, client):
        body = client.get("/api/products/1").get_json()["data"]
        assert body["product_id"] == 1
        assert "open_order_qty" in body

    def test_get_missing_returns_404(self, client):
        response = client.get("/api/products/999999")
        assert response.status_code == 404
        assert response.get_json()["error"]["code"] == "NOT_FOUND"

    def test_get_invalid_id_type(self, client):
        assert client.get("/api/products/abc").status_code == 404

    def test_create_update_delete_roundtrip(self, client, db):
        sku = f"API-{unique_suffix()}"
        payload = {"sku": sku, "product_name": "API test product",
                   "category": "Testing", "unit_cost": 250.5,
                   "lead_time_days": 12, "supplier_id": 1, "reorder_point": 10}
        created = client.post("/api/products", json=payload)
        assert created.status_code == 201
        new_id = created.get_json()["data"]["product_id"]
        assert created.headers["Location"].endswith(f"/products/{new_id}")

        updated = client.patch(f"/api/products/{new_id}", json={"reorder_point": 99})
        assert updated.status_code == 200
        assert updated.get_json()["data"]["reorder_point"] == 99

        deleted = client.delete(f"/api/products/{new_id}")
        assert deleted.status_code == 200
        assert client.get(f"/api/products/{new_id}").status_code == 404

    def test_create_missing_fields(self, client):
        response = client.post("/api/products", json={"sku": "ONLY-SKU"})
        assert response.status_code == 422
        fields = {d["field"] for d in response.get_json()["error"]["details"]}
        assert "product_name" in fields

    def test_create_duplicate_sku_conflict(self, client):
        payload = {"sku": "AUTO-BRK-0012", "product_name": "dupe", "category": "X",
                   "unit_cost": 10, "lead_time_days": 5, "supplier_id": 1}
        response = client.post("/api/products", json=payload)
        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "DUPLICATE_RECORD"

    def test_create_with_unknown_supplier(self, client):
        payload = {"sku": f"API-{unique_suffix()}", "product_name": "orphan",
                   "category": "X", "unit_cost": 10, "lead_time_days": 5,
                   "supplier_id": 99999}
        assert client.post("/api/products", json=payload).status_code == 409

    def test_create_invalid_lead_time(self, client):
        payload = {"sku": f"API-{unique_suffix()}", "product_name": "bad",
                   "category": "X", "unit_cost": 10, "lead_time_days": 500,
                   "supplier_id": 1}
        assert client.post("/api/products", json=payload).status_code == 422

    def test_empty_body_rejected(self, client):
        assert client.post("/api/products", data="").status_code == 400

    def test_wrong_content_type(self, client):
        response = client.post("/api/products", data="sku=A", content_type="text/plain")
        assert response.status_code == 415

    def test_malformed_json(self, client):
        response = client.post("/api/products", data="{not json",
                               content_type="application/json")
        assert response.status_code == 400

    def test_delete_product_with_history_conflict(self, client):
        response = client.delete("/api/products/1")
        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "RECORD_IN_USE"


class TestSuppliers:
    def test_list(self, client):
        body = client.get("/api/suppliers").get_json()
        assert body["total"] >= 6

    def test_get_with_related_counts(self, client):
        body = client.get("/api/suppliers/1").get_json()["data"]
        assert body["product_count"] >= 1
        assert "open_orders" in body

    def test_create_delete(self, client, db):
        name = f"API Supplier {unique_suffix()}"
        created = client.post("/api/suppliers",
                              json={"supplier_name": name,
                                    "promised_lead_time_days": 15,
                                    "contact_email": "api@example.com"})
        assert created.status_code == 201
        supplier_id = created.get_json()["data"]["supplier_id"]
        assert client.delete(f"/api/suppliers/{supplier_id}").status_code == 200

    def test_invalid_email(self, client):
        response = client.post("/api/suppliers",
                               json={"supplier_name": f"Bad {unique_suffix()}",
                                     "promised_lead_time_days": 10,
                                     "contact_email": "not-an-email"})
        assert response.status_code == 422

    def test_delete_supplier_in_use(self, client):
        response = client.delete("/api/suppliers/1")
        assert response.status_code == 409


class TestPurchaseOrders:
    def test_list_and_filter(self, client):
        body = client.get("/api/purchase-orders?status=RECEIVED&limit=5").get_json()
        assert all(po["status"] == "RECEIVED" for po in body["data"])
        assert body["total"] >= 1

    def test_overdue_filter(self, client):
        body = client.get("/api/purchase-orders?overdue=true").get_json()
        for po in body["data"]:
            assert po["status"] in ("OPEN", "PARTIAL")

    def test_invalid_status(self, client):
        assert client.get("/api/purchase-orders?status=LOST").status_code == 422

    def test_create_and_receive_updates_ledger(self, client, temp_product):
        pid = temp_product["product_id"]
        created = client.post("/api/purchase-orders", json={
            "supplier_id": temp_product["supplier_id"], "product_id": pid,
            "quantity": 40, "order_date": "2026-10-01",
            "expected_date": "2026-10-12"})
        assert created.status_code == 201
        po_id = created.get_json()["data"]["po_id"]

        partial = client.post(f"/api/purchase-orders/{po_id}/receive",
                              json={"received_qty": 15, "received_date": "2026-10-11"})
        assert partial.status_code == 200
        assert partial.get_json()["data"]["status"] == "PARTIAL"

        final = client.post(f"/api/purchase-orders/{po_id}/receive",
                            json={"received_date": "2026-10-13"})
        assert final.get_json()["data"]["status"] == "RECEIVED"
        assert final.get_json()["data"]["received_qty"] == 40

        inventory = client.get(f"/api/inventory?limit=500").get_json()["data"]
        row = next(r for r in inventory if r["product_id"] == pid)
        assert row["on_hand"] == 40        # two RECEIPT rows in the ledger

    def test_receive_more_than_ordered(self, client, temp_product):
        created = client.post("/api/purchase-orders", json={
            "supplier_id": temp_product["supplier_id"],
            "product_id": temp_product["product_id"], "quantity": 10,
            "order_date": "2026-10-01", "expected_date": "2026-10-08"})
        po_id = created.get_json()["data"]["po_id"]
        response = client.post(f"/api/purchase-orders/{po_id}/receive",
                               json={"received_qty": 11})
        assert response.status_code == 422

    def test_expected_date_before_order_date(self, client, temp_product):
        response = client.post("/api/purchase-orders", json={
            "supplier_id": temp_product["supplier_id"],
            "product_id": temp_product["product_id"], "quantity": 5,
            "order_date": "2026-10-10", "expected_date": "2026-10-01"})
        assert response.status_code == 422

    def test_get_missing_po(self, client):
        assert client.get("/api/purchase-orders/999999").status_code == 404


class TestStockTransactions:
    def test_list_filter_by_type(self, client):
        body = client.get("/api/stock-transactions?transaction_type=ISSUE&limit=3").get_json()
        assert all(t["transaction_type"] == "ISSUE" for t in body["data"])
        assert all(t["quantity"] < 0 for t in body["data"])

    def test_issue_sign_enforced(self, client, temp_product):
        response = client.post("/api/stock-transactions", json={
            "product_id": temp_product["product_id"],
            "transaction_date": "2026-10-19", "transaction_type": "ISSUE",
            "quantity": 5})
        assert response.status_code == 422

    def test_receipt_updates_balance(self, client, temp_product):
        pid = temp_product["product_id"]
        response = client.post("/api/stock-transactions", json={
            "product_id": pid, "transaction_date": "2026-10-19",
            "transaction_type": "RECEIPT", "quantity": 25, "reference": "TEST"})
        assert response.status_code == 201
        assert response.get_json()["stock_balance"] == 25

    def test_negative_balance_blocked(self, client, temp_product):
        pid = temp_product["product_id"]
        response = client.post("/api/stock-transactions", json={
            "product_id": pid, "transaction_date": "2026-10-19",
            "transaction_type": "ISSUE", "quantity": -500})
        assert response.status_code == 409
        assert response.get_json()["error"]["code"] == "CONFLICT"

    def test_invalid_date_format(self, client, temp_product):
        response = client.post("/api/stock-transactions", json={
            "product_id": temp_product["product_id"],
            "transaction_date": "19-10-2026", "transaction_type": "RECEIPT",
            "quantity": 1})
        assert response.status_code == 422


class TestDemandRecords:
    def test_list(self, client):
        body = client.get("/api/demand-records?limit=3").get_json()
        assert body["count"] == 3
        assert body["total"] > 1000

    def test_create_and_upsert(self, client, temp_product):
        payload = {"product_id": temp_product["product_id"],
                   "demand_date": "2026-10-01", "quantity": 12}
        first = client.post("/api/demand-records", json=payload)
        assert first.status_code == 201
        second = client.post("/api/demand-records",
                             json={**payload, "quantity": 30})
        assert second.status_code == 201
        assert second.get_json()["data"]["quantity"] == 30

    def test_bulk_upload(self, client, temp_product):
        records = [{"product_id": temp_product["product_id"],
                    "demand_date": f"2026-09-{day:02d}", "quantity": day}
                   for day in range(1, 6)]
        response = client.post("/api/demand-records/bulk", json={"records": records})
        assert response.status_code == 201
        assert response.get_json()["inserted"] == 5

    def test_bulk_upload_rejects_bad_rows(self, client, temp_product):
        records = [{"product_id": temp_product["product_id"],
                    "demand_date": "2026-09-01", "quantity": 5},
                   {"product_id": temp_product["product_id"],
                    "demand_date": "bad-date", "quantity": 5}]
        response = client.post("/api/demand-records/bulk", json={"records": records})
        assert response.status_code == 422
        assert response.get_json()["error"]["details"][0]["row"] == 1

    def test_bulk_upload_empty(self, client):
        assert client.post("/api/demand-records/bulk",
                           json={"records": []}).status_code == 400

    def test_negative_demand(self, client, temp_product):
        response = client.post("/api/demand-records", json={
            "product_id": temp_product["product_id"],
            "demand_date": "2026-10-02", "quantity": -3})
        assert response.status_code == 422


class TestInventoryView:
    def test_inventory_lists_positions(self, client):
        body = client.get("/api/inventory?limit=500").get_json()
        assert body["total"] >= 24
        for row in body["data"]:
            assert row["inventory_position"] == row["on_hand"] + row["on_order"]
