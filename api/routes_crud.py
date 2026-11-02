"""CRUD REST endpoints for the five domain entities + inventory view.

Conventions
-----------
* GET list endpoints support `limit`, `offset` and entity-specific filters.
* POST returns 201 with a Location header.
* PUT is a full replacement, PATCH a partial update.
* DELETE refuses to remove a parent row that children still reference
  (HTTP 409 with a human explanation) instead of relying on the DB error.
* Every SQL statement is parameterised.
"""
from __future__ import annotations

from datetime import date, timedelta

from flask import Blueprint, jsonify, request

from api.errors import ApiError, ConflictError, NotFoundError
from api.validators import (as_date, as_enum, as_int, as_text, json_body, pagination,
                            require_fields, validate_demand, validate_po,
                            validate_product, validate_stock_txn, validate_supplier)
from db import db

crud_bp = Blueprint("crud", __name__, url_prefix="/api")


def _iso(value):
    return value.strftime("%Y-%m-%d") if isinstance(value, (date,)) else value


def _paged(items: list, total: int, limit: int, offset: int, extra: dict | None = None):
    body = {"count": len(items), "total": total, "limit": limit, "offset": offset,
            "data": items}
    if extra:
        body.update(extra)
    return jsonify(body)


# =====================================================================
# health / config
# =====================================================================
@crud_bp.get("/health")
def health():
    report = db.health()
    status = 200 if report["connected"] else 503
    report["status"] = "ok" if report["connected"] else "degraded"
    report["checks"] = {
        "database_connection": report["connected"],
        "reference_data": None,
    }
    if report["connected"]:
        try:
            counts = db.fetch_one(
                """SELECT (SELECT COUNT(*) FROM products) AS products,
                          (SELECT COUNT(*) FROM suppliers) AS suppliers,
                          (SELECT COUNT(*) FROM purchase_orders) AS purchase_orders,
                          (SELECT COUNT(*) FROM stock_transactions) AS stock_transactions,
                          (SELECT COUNT(*) FROM demand_records) AS demand_records""")
            report["checks"]["reference_data"] = counts
            report["checks"]["reference_data_loaded"] = bool(counts and counts["products"])
        except Exception:                                  # pragma: no cover
            report["checks"]["reference_data_loaded"] = False
    return jsonify(report), status


@crud_bp.get("/config")
def get_config():
    """Non-secret configuration, including the documented analysis assumptions."""
    return jsonify({"data": request.app_cfg.public() if hasattr(request, "app_cfg")
                    else _cfg().public()})


def _cfg():
    from flask import current_app
    return current_app.config["APP_CFG"]


# =====================================================================
# suppliers
# =====================================================================
@crud_bp.get("/suppliers")
def list_suppliers():
    limit, offset = pagination()
    search = request.args.get("search")
    sql = "SELECT * FROM suppliers WHERE 1=1"
    params: list = []
    if search:
        sql += " AND (supplier_name LIKE %s OR country LIKE %s)"
        params += [f"%{search}%", f"%{search}%"]
    total = db.fetch_scalar(f"SELECT COUNT(*) FROM ({sql}) t", params)
    sql += " ORDER BY supplier_id LIMIT %s OFFSET %s"
    params += [limit, offset]
    rows = db.fetch_all(sql, params)
    for row in rows:
        row["created_at"] = _iso(row.get("created_at"))
        row["updated_at"] = _iso(row.get("updated_at"))
    return _paged(rows, int(total or 0), limit, offset)


@crud_bp.get("/suppliers/<int:supplier_id>")
def get_supplier(supplier_id: int):
    row = db.fetch_one("SELECT * FROM suppliers WHERE supplier_id = %s", (supplier_id,))
    if not row:
        raise NotFoundError(f"Supplier {supplier_id} does not exist.")
    row["product_count"] = db.fetch_scalar(
        "SELECT COUNT(*) FROM products WHERE supplier_id = %s", (supplier_id,))
    row["open_orders"] = db.fetch_scalar(
        "SELECT COUNT(*) FROM purchase_orders WHERE supplier_id = %s "
        "AND status IN ('OPEN','PARTIAL')", (supplier_id,))
    return jsonify({"data": row})


@crud_bp.post("/suppliers")
def create_supplier():
    payload = validate_supplier(json_body())
    new_id = db.execute(
        """INSERT INTO suppliers (supplier_name, contact_email, country,
                                  promised_lead_time_days, is_active)
           VALUES (%s, %s, %s, %s, %s)""",
        (payload["supplier_name"], payload.get("contact_email"), payload["country"],
         payload["promised_lead_time_days"], payload["is_active"]))
    row = db.fetch_one("SELECT * FROM suppliers WHERE supplier_id = %s", (new_id,))
    return jsonify({"data": row, "message": "Supplier created."}), 201, \
        {"Location": f"/api/suppliers/{new_id}"}


@crud_bp.put("/suppliers/<int:supplier_id>")
@crud_bp.patch("/suppliers/<int:supplier_id>")
def update_supplier(supplier_id: int):
    if not db.fetch_one("SELECT supplier_id FROM suppliers WHERE supplier_id = %s",
                        (supplier_id,)):
        raise NotFoundError(f"Supplier {supplier_id} does not exist.")
    payload = validate_supplier(json_body(), partial=request.method == "PATCH")
    if not payload:
        raise ApiError("No updatable fields supplied.", status=400, code="EMPTY_UPDATE")
    assignments = ", ".join(f"{col} = %s" for col in payload)
    db.execute(f"UPDATE suppliers SET {assignments} WHERE supplier_id = %s",
               list(payload.values()) + [supplier_id])
    row = db.fetch_one("SELECT * FROM suppliers WHERE supplier_id = %s", (supplier_id,))
    return jsonify({"data": row, "message": "Supplier updated."})


@crud_bp.delete("/suppliers/<int:supplier_id>")
def delete_supplier(supplier_id: int):
    if not db.fetch_one("SELECT supplier_id FROM suppliers WHERE supplier_id = %s",
                        (supplier_id,)):
        raise NotFoundError(f"Supplier {supplier_id} does not exist.")
    products = db.fetch_scalar("SELECT COUNT(*) FROM products WHERE supplier_id = %s",
                               (supplier_id,))
    orders = db.fetch_scalar("SELECT COUNT(*) FROM purchase_orders WHERE supplier_id = %s",
                             (supplier_id,))
    if products or orders:
        raise ConflictError(
            f"Supplier {supplier_id} still has {products} product(s) and {orders} "
            f"purchase order(s). Re-assign or delete them first.", code="RECORD_IN_USE")
    db.execute("DELETE FROM suppliers WHERE supplier_id = %s", (supplier_id,))
    return jsonify({"message": f"Supplier {supplier_id} deleted."})


# =====================================================================
# products
# =====================================================================
PRODUCT_SELECT = """
    SELECT p.*, s.supplier_name,
           COALESCE((SELECT SUM(st.quantity) FROM stock_transactions st
                     WHERE st.product_id = p.product_id), 0) AS on_hand
    FROM products p JOIN suppliers s ON s.supplier_id = p.supplier_id
"""


@crud_bp.get("/products")
def list_products():
    limit, offset = pagination()
    category = request.args.get("category")
    supplier_id = request.args.get("supplier_id")
    search = request.args.get("search")
    active = request.args.get("is_active")
    sql = PRODUCT_SELECT + " WHERE 1=1"
    params: list = []
    if category:
        sql += " AND p.category = %s"
        params.append(category)
    if supplier_id:
        sql += " AND p.supplier_id = %s"
        params.append(as_int(supplier_id, "supplier_id", minimum=1))
    if search:
        sql += " AND (p.sku LIKE %s OR p.product_name LIKE %s)"
        params += [f"%{search}%", f"%{search}%"]
    if active is not None and active != "":
        sql += " AND p.is_active = %s"
        params.append(1 if as_enum(active, "is_active", ("0", "1", "TRUE", "FALSE",
                                                         "YES", "NO")) in
                      ("1", "TRUE", "YES") else 0)
    total = db.fetch_scalar(f"SELECT COUNT(*) FROM ({sql}) t", params)
    sql += " ORDER BY p.product_id LIMIT %s OFFSET %s"
    params += [limit, offset]
    return _paged(db.fetch_all(sql, params), int(total or 0), limit, offset)


@crud_bp.get("/products/<int:product_id>")
def get_product(product_id: int):
    row = db.fetch_one(PRODUCT_SELECT + " WHERE p.product_id = %s", (product_id,))
    if not row:
        raise NotFoundError(f"Product {product_id} does not exist.")
    row["open_order_qty"] = db.fetch_scalar(
        """SELECT COALESCE(SUM(quantity - COALESCE(received_qty,0)),0)
           FROM purchase_orders WHERE product_id = %s AND status IN ('OPEN','PARTIAL')""",
        (product_id,))
    row["last_demand_date"] = _iso(db.fetch_scalar(
        "SELECT MAX(demand_date) FROM demand_records WHERE product_id = %s", (product_id,)))
    return jsonify({"data": row})


@crud_bp.post("/products")
def create_product():
    payload = validate_product(json_body())
    if not db.fetch_one("SELECT supplier_id FROM suppliers WHERE supplier_id = %s",
                        (payload["supplier_id"],)):
        raise ConflictError(f"supplier_id {payload['supplier_id']} does not exist.")
    new_id = db.execute(
        """INSERT INTO products (sku, product_name, category, unit_cost, lead_time_days,
                                 reorder_point, supplier_id, is_active)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (payload["sku"], payload["product_name"], payload["category"],
         float(payload["unit_cost"]), payload["lead_time_days"],
         payload["reorder_point"], payload["supplier_id"], payload["is_active"]))
    row = db.fetch_one("SELECT * FROM products WHERE product_id = %s", (new_id,))
    return jsonify({"data": row, "message": "Product created."}), 201, \
        {"Location": f"/api/products/{new_id}"}


@crud_bp.put("/products/<int:product_id>")
@crud_bp.patch("/products/<int:product_id>")
def update_product(product_id: int):
    if not db.fetch_one("SELECT product_id FROM products WHERE product_id = %s",
                        (product_id,)):
        raise NotFoundError(f"Product {product_id} does not exist.")
    payload = validate_product(json_body(), partial=request.method == "PATCH")
    if not payload:
        raise ApiError("No updatable fields supplied.", status=400, code="EMPTY_UPDATE")
    if "supplier_id" in payload and not db.fetch_one(
            "SELECT supplier_id FROM suppliers WHERE supplier_id = %s",
            (payload["supplier_id"],)):
        raise ConflictError(f"supplier_id {payload['supplier_id']} does not exist.")
    values = [float(v) if key == "unit_cost" else v for key, v in payload.items()]
    assignments = ", ".join(f"{col} = %s" for col in payload)
    db.execute(f"UPDATE products SET {assignments} WHERE product_id = %s",
               values + [product_id])
    row = db.fetch_one("SELECT * FROM products WHERE product_id = %s", (product_id,))
    return jsonify({"data": row, "message": "Product updated."})


@crud_bp.delete("/products/<int:product_id>")
def delete_product(product_id: int):
    if not db.fetch_one("SELECT product_id FROM products WHERE product_id = %s",
                        (product_id,)):
        raise NotFoundError(f"Product {product_id} does not exist.")
    txns = db.fetch_scalar("SELECT COUNT(*) FROM stock_transactions WHERE product_id = %s",
                           (product_id,))
    demand = db.fetch_scalar("SELECT COUNT(*) FROM demand_records WHERE product_id = %s",
                             (product_id,))
    orders = db.fetch_scalar("SELECT COUNT(*) FROM purchase_orders WHERE product_id = %s",
                             (product_id,))
    if txns or demand or orders:
        raise ConflictError(
            f"Product {product_id} has {txns} stock transaction(s), {demand} demand "
            f"record(s) and {orders} purchase order(s); history cannot be deleted. "
            f"Set is_active = false instead to retire the SKU.", code="RECORD_IN_USE")
    db.execute("DELETE FROM products WHERE product_id = %s", (product_id,))
    return jsonify({"message": f"Product {product_id} deleted."})


# =====================================================================
# purchase orders
# =====================================================================
@crud_bp.get("/purchase-orders")
def list_purchase_orders():
    limit, offset = pagination()
    status = request.args.get("status")
    supplier_id = request.args.get("supplier_id")
    product_id = request.args.get("product_id")
    overdue = request.args.get("overdue")

    sql = """SELECT po.*, s.supplier_name, p.sku
             FROM purchase_orders po
             JOIN suppliers s ON s.supplier_id = po.supplier_id
             JOIN products  p ON p.product_id = po.product_id
             WHERE 1=1"""
    params: list = []
    if status:
        sql += " AND po.status = %s"
        params.append(as_enum(status, "status",
                              ("OPEN", "PARTIAL", "RECEIVED", "CANCELLED")))
    if supplier_id:
        sql += " AND po.supplier_id = %s"
        params.append(as_int(supplier_id, "supplier_id", minimum=1))
    if product_id:
        sql += " AND po.product_id = %s"
        params.append(as_int(product_id, "product_id", minimum=1))
    if overdue in ("1", "true", "TRUE", "yes"):
        sql += " AND po.status IN ('OPEN','PARTIAL') AND po.expected_date < %s"
        params.append(date.today())
    total = db.fetch_scalar(f"SELECT COUNT(*) FROM ({sql}) t", params)
    sql += " ORDER BY po.order_date DESC, po.po_id DESC LIMIT %s OFFSET %s"
    params += [limit, offset]
    rows = db.fetch_all(sql, params)
    for row in rows:
        for key in ("order_date", "expected_date", "received_date"):
            row[key] = _iso(row.get(key))
    return _paged(rows, int(total or 0), limit, offset)


@crud_bp.get("/purchase-orders/<int:po_id>")
def get_purchase_order(po_id: int):
    row = db.fetch_one(
        """SELECT po.*, s.supplier_name, p.sku, p.product_name,
                  DATEDIFF(po.received_date, po.order_date) AS actual_lead_time_days,
                  DATEDIFF(po.received_date, po.expected_date) AS delay_days
           FROM purchase_orders po
           JOIN suppliers s ON s.supplier_id = po.supplier_id
           JOIN products  p ON p.product_id = po.product_id
           WHERE po.po_id = %s""", (po_id,))
    if not row:
        raise NotFoundError(f"Purchase order {po_id} does not exist.")
    for key in ("order_date", "expected_date", "received_date"):
        row[key] = _iso(row.get(key))
    return jsonify({"data": row})


@crud_bp.post("/purchase-orders")
def create_purchase_order():
    payload = validate_po(json_body())
    product = db.fetch_one("SELECT product_id, supplier_id FROM products "
                           "WHERE product_id = %s", (payload["product_id"],))
    if not product:
        raise ConflictError(f"product_id {payload['product_id']} does not exist.")
    if not db.fetch_one("SELECT supplier_id FROM suppliers WHERE supplier_id = %s",
                        (payload["supplier_id"],)):
        raise ConflictError(f"supplier_id {payload['supplier_id']} does not exist.")
    po_number = payload.get("po_number") or _next_po_number(payload["supplier_id"])
    new_id = db.execute(
        """INSERT INTO purchase_orders (po_number, supplier_id, product_id, quantity,
                                        received_qty, order_date, expected_date,
                                        received_date, status, notes)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (po_number, payload["supplier_id"], payload["product_id"], payload["quantity"],
         payload.get("received_qty"), payload["order_date"], payload["expected_date"],
         payload.get("received_date"), payload["status"], payload.get("notes")))
    row = db.fetch_one("SELECT * FROM purchase_orders WHERE po_id = %s", (new_id,))
    return jsonify({"data": row, "message": "Purchase order created."}), 201, \
        {"Location": f"/api/purchase-orders/{new_id}"}


@crud_bp.put("/purchase-orders/<int:po_id>")
@crud_bp.patch("/purchase-orders/<int:po_id>")
def update_purchase_order(po_id: int):
    existing = db.fetch_one("SELECT * FROM purchase_orders WHERE po_id = %s", (po_id,))
    if not existing:
        raise NotFoundError(f"Purchase order {po_id} does not exist.")
    payload = validate_po(json_body(), partial=request.method == "PATCH")
    if not payload:
        raise ApiError("No updatable fields supplied.", status=400, code="EMPTY_UPDATE")
    merged = {**{k: existing[k] for k in ("quantity", "order_date", "expected_date",
                                         "received_date", "received_qty", "status")},
              **payload}
    if merged["expected_date"] < merged["order_date"]:
        raise ApiError("expected_date cannot be before order_date.", status=422,
                       code="VALIDATION_ERROR",
                       details=[{"field": "expected_date",
                                 "message": "Must be on or after order_date."}])
    assignments = ", ".join(f"{col} = %s" for col in payload)
    db.execute(f"UPDATE purchase_orders SET {assignments} WHERE po_id = %s",
               list(payload.values()) + [po_id])
    row = db.fetch_one("SELECT * FROM purchase_orders WHERE po_id = %s", (po_id,))
    return jsonify({"data": row, "message": "Purchase order updated."})


@crud_bp.post("/purchase-orders/<int:po_id>/receive")
def receive_purchase_order(po_id: int):
    """Record a goods receipt: updates the PO and writes a RECEIPT transaction.

    Body: {"received_qty": 40, "received_date": "2026-10-18"}
    Both fields are optional (defaults: full remaining quantity, today).
    The stock ledger and the PO status are updated in ONE transaction.
    """
    existing = db.fetch_one("SELECT * FROM purchase_orders WHERE po_id = %s", (po_id,))
    if not existing:
        raise NotFoundError(f"Purchase order {po_id} does not exist.")
    if existing["status"] == "CANCELLED":
        raise ConflictError("A cancelled purchase order cannot be received.")
    payload = json_body() if request.data else {}
    remaining = existing["quantity"] - (existing["received_qty"] or 0)
    qty = as_int(payload.get("received_qty", remaining), "received_qty",
                 minimum=1, maximum=remaining)
    received_date = as_date(payload.get("received_date") or date.today(), "received_date")
    new_received = (existing["received_qty"] or 0) + qty
    status = "RECEIVED" if new_received >= existing["quantity"] else "PARTIAL"

    with db.transaction() as cur:
        cur.execute(
            """UPDATE purchase_orders
               SET received_qty = %s, received_date = %s, status = %s
               WHERE po_id = %s""", (new_received, received_date, status, po_id))
        cur.execute(
            """INSERT INTO stock_transactions
                   (product_id, transaction_date, transaction_type, quantity,
                    reference, notes)
               VALUES (%s, %s, 'RECEIPT', %s, %s, %s)""",
            (existing["product_id"], received_date, qty, existing["po_number"],
             f"Goods receipt for PO {existing['po_number']}"))
    row = db.fetch_one("SELECT * FROM purchase_orders WHERE po_id = %s", (po_id,))
    return jsonify({
        "data": row,
        "message": f"Recorded receipt of {qty} unit(s); status is now {status}.",
        "stock_transaction": {"product_id": existing["product_id"], "quantity": qty,
                              "transaction_type": "RECEIPT",
                              "transaction_date": received_date.strftime("%Y-%m-%d")},
    })


@crud_bp.delete("/purchase-orders/<int:po_id>")
def delete_purchase_order(po_id: int):
    existing = db.fetch_one("SELECT * FROM purchase_orders WHERE po_id = %s", (po_id,))
    if not existing:
        raise NotFoundError(f"Purchase order {po_id} does not exist.")
    if (existing["received_qty"] or 0) > 0:
        raise ConflictError("A purchase order with goods receipts cannot be deleted; "
                            "cancel it instead.", code="RECORD_IN_USE")
    db.execute("DELETE FROM purchase_orders WHERE po_id = %s", (po_id,))
    return jsonify({"message": f"Purchase order {po_id} deleted."})


def _next_po_number(supplier_id: int) -> str:
    count = db.fetch_scalar("SELECT COUNT(*) FROM purchase_orders WHERE supplier_id = %s",
                            (supplier_id,)) or 0
    return f"PO-S{supplier_id:02d}-{int(count) + 1:05d}"


# =====================================================================
# stock transactions
# =====================================================================
@crud_bp.get("/stock-transactions")
def list_stock_transactions():
    limit, offset = pagination()
    product_id = request.args.get("product_id")
    txn_type = request.args.get("transaction_type")
    start = request.args.get("start_date")
    end = request.args.get("end_date")
    sql = """SELECT st.*, p.sku FROM stock_transactions st
             JOIN products p ON p.product_id = st.product_id WHERE 1=1"""
    params: list = []
    if product_id:
        sql += " AND st.product_id = %s"
        params.append(as_int(product_id, "product_id", minimum=1))
    if txn_type:
        sql += " AND st.transaction_type = %s"
        params.append(as_enum(txn_type, "transaction_type",
                              ("RECEIPT", "ISSUE", "ADJUSTMENT", "RETURN")))
    if start:
        sql += " AND st.transaction_date >= %s"
        params.append(as_date(start, "start_date"))
    if end:
        sql += " AND st.transaction_date <= %s"
        params.append(as_date(end, "end_date"))
    total = db.fetch_scalar(f"SELECT COUNT(*) FROM ({sql}) t", params)
    sql += " ORDER BY st.transaction_date DESC, st.transaction_id DESC LIMIT %s OFFSET %s"
    params += [limit, offset]
    rows = db.fetch_all(sql, params)
    for row in rows:
        row["transaction_date"] = _iso(row.get("transaction_date"))
    return _paged(rows, int(total or 0), limit, offset)


@crud_bp.get("/stock-transactions/<int:transaction_id>")
def get_stock_transaction(transaction_id: int):
    row = db.fetch_one("SELECT * FROM stock_transactions WHERE transaction_id = %s",
                       (transaction_id,))
    if not row:
        raise NotFoundError(f"Stock transaction {transaction_id} does not exist.")
    row["transaction_date"] = _iso(row.get("transaction_date"))
    return jsonify({"data": row})


@crud_bp.post("/stock-transactions")
def create_stock_transaction():
    payload = validate_stock_txn(json_body())
    if not db.fetch_one("SELECT product_id FROM products WHERE product_id = %s",
                        (payload["product_id"],)):
        raise ConflictError(f"product_id {payload['product_id']} does not exist.")
    with db.transaction() as cur:
        cur.execute(
            """INSERT INTO stock_transactions
                   (product_id, transaction_date, transaction_type, quantity,
                    reference, notes)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (payload["product_id"], payload["transaction_date"],
             payload["transaction_type"], payload["quantity"],
             payload.get("reference"), payload.get("notes")))
        new_id = cur.lastrowid
        cur.execute("SELECT COALESCE(SUM(quantity),0) AS bal FROM stock_transactions "
                    "WHERE product_id = %s", (payload["product_id"],))
        balance = cur.fetchone()["bal"]
    if balance < 0:
        raise ConflictError(
            "This movement would push the product's stock balance below zero "
            f"(resulting balance: {balance}). Correct the ledger first.",
            details=[{"field": "quantity", "message": "Stock balance cannot be negative."}])
    row = db.fetch_one("SELECT * FROM stock_transactions WHERE transaction_id = %s",
                       (new_id,))
    row["transaction_date"] = _iso(row.get("transaction_date"))
    return jsonify({"data": row, "stock_balance": int(balance),
                    "message": "Stock transaction recorded."}), 201, \
        {"Location": f"/api/stock-transactions/{new_id}"}


# =====================================================================
# demand records
# =====================================================================
@crud_bp.get("/demand-records")
def list_demand_records():
    limit, offset = pagination()
    product_id = request.args.get("product_id")
    start = request.args.get("start_date")
    end = request.args.get("end_date")
    sql = """SELECT d.*, p.sku FROM demand_records d
             JOIN products p ON p.product_id = d.product_id WHERE 1=1"""
    params: list = []
    if product_id:
        sql += " AND d.product_id = %s"
        params.append(as_int(product_id, "product_id", minimum=1))
    if start:
        sql += " AND d.demand_date >= %s"
        params.append(as_date(start, "start_date"))
    if end:
        sql += " AND d.demand_date <= %s"
        params.append(as_date(end, "end_date"))
    total = db.fetch_scalar(f"SELECT COUNT(*) FROM ({sql}) t", params)
    sql += " ORDER BY d.demand_date DESC, d.demand_id DESC LIMIT %s OFFSET %s"
    params += [limit, offset]
    rows = db.fetch_all(sql, params)
    for row in rows:
        row["demand_date"] = _iso(row.get("demand_date"))
    return _paged(rows, int(total or 0), limit, offset)


@crud_bp.post("/demand-records")
def create_demand_record():
    payload = validate_demand(json_body())
    if not db.fetch_one("SELECT product_id FROM products WHERE product_id = %s",
                        (payload["product_id"],)):
        raise ConflictError(f"product_id {payload['product_id']} does not exist.")
    new_id = db.execute(
        """INSERT INTO demand_records (product_id, demand_date, quantity)
           VALUES (%s, %s, %s)
           ON DUPLICATE KEY UPDATE quantity = VALUES(quantity), demand_id = LAST_INSERT_ID(demand_id)""",
        (payload["product_id"], payload["demand_date"], payload["quantity"]))
    row = db.fetch_one("SELECT * FROM demand_records WHERE demand_id = %s", (new_id,))
    row["demand_date"] = _iso(row.get("demand_date"))
    return jsonify({"data": row,
                    "message": "Demand recorded (existing product/date row updated)."}), 201


@crud_bp.post("/demand-records/bulk")
def bulk_upload_demand():
    """Upload a list of {product_id, demand_date, quantity} rows (CSV import path)."""
    body = json_body()
    if not isinstance(body, dict) or not isinstance(body.get("records"), list):
        raise ApiError("Body must be {\"records\": [ {...}, ... ]}.", status=400,
                       code="INVALID_JSON")
    records = body["records"]
    if not records:
        raise ApiError("records must not be empty.", status=400, code="EMPTY_BATCH")
    if len(records) > 5000:
        raise ApiError("A batch may contain at most 5000 rows.", status=413,
                       code="BATCH_TOO_LARGE")
    errors, rows = [], []
    for index, record in enumerate(records):
        try:
            item = validate_demand(record)
            rows.append((item["product_id"], item["demand_date"], item["quantity"]))
        except ApiError as exc:
            errors.append({"row": index, "details": exc.details or exc.message})
    if errors:
        raise ApiError("Some rows failed validation; nothing was inserted.",
                       status=422, code="VALIDATION_ERROR", details=errors[:50])
    db.execute_many(
        """INSERT INTO demand_records (product_id, demand_date, quantity)
           VALUES (%s, %s, %s)
           ON DUPLICATE KEY UPDATE quantity = VALUES(quantity)""", rows)
    return jsonify({"inserted": len(rows), "message": f"{len(rows)} demand rows stored."}), 201


# =====================================================================
# inventory view
# =====================================================================
@crud_bp.get("/inventory")
def inventory():
    limit, offset = pagination()
    rows = db.fetch_all(
        """SELECT p.product_id, p.sku, p.product_name, p.category, p.unit_cost,
                  p.lead_time_days, p.reorder_point, p.supplier_id, s.supplier_name,
                  COALESCE(SUM(st.quantity), 0) AS on_hand,
                  COALESCE((SELECT SUM(po.quantity - COALESCE(po.received_qty,0))
                            FROM purchase_orders po
                            WHERE po.product_id = p.product_id
                              AND po.status IN ('OPEN','PARTIAL')), 0) AS on_order
           FROM products p
           JOIN suppliers s ON s.supplier_id = p.supplier_id
           LEFT JOIN stock_transactions st ON st.product_id = p.product_id
           GROUP BY p.product_id, p.sku, p.product_name, p.category, p.unit_cost,
                    p.lead_time_days, p.reorder_point, p.supplier_id, s.supplier_name
           ORDER BY p.product_id
           LIMIT %s OFFSET %s""", (limit, offset))
    total = db.fetch_scalar("SELECT COUNT(*) FROM products")
    for row in rows:
        row["inventory_position"] = (row["on_hand"] or 0) + (row["on_order"] or 0)
        row["on_hand_value"] = round(float(row["on_hand"] or 0) * float(row["unit_cost"]), 2)
    return _paged(rows, int(total or 0), limit, offset,
                  extra={"note": "Ledger-based view. Reorder analytics live under "
                                 "/api/analytics/reorder-recommendations."})
