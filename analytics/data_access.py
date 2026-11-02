"""Read-only SQL used by the analysis layer, returned as pandas DataFrames.

Keeping the SQL in one module means the API endpoints and the CLI
pipeline always analyse exactly the same data with the same filters.
All statements are parameterised.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from db import Database, db as default_db


def _frame(rows: list[dict], columns: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=columns)
    return df.reindex(columns=columns)


PRODUCT_COLUMNS = ["product_id", "sku", "product_name", "category", "unit_cost",
                   "lead_time_days", "reorder_point", "supplier_id", "supplier_name",
                   "is_active"]


def load_products(database: Database | None = None) -> pd.DataFrame:
    database = database or default_db
    rows = database.fetch_all(
        """
        SELECT p.product_id, p.sku, p.product_name, p.category, p.unit_cost,
               p.lead_time_days, p.reorder_point, p.supplier_id,
               s.supplier_name, p.is_active
        FROM products p
        JOIN suppliers s ON s.supplier_id = p.supplier_id
        ORDER BY p.product_id
        """)
    df = _frame(rows, PRODUCT_COLUMNS)
    if not df.empty:
        for col in ("unit_cost", "lead_time_days", "reorder_point"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_suppliers(database: Database | None = None) -> pd.DataFrame:
    database = database or default_db
    rows = database.fetch_all(
        """
        SELECT supplier_id, supplier_name, contact_email, country,
               promised_lead_time_days, is_active
        FROM suppliers ORDER BY supplier_id
        """)
    return _frame(rows, ["supplier_id", "supplier_name", "contact_email", "country",
                         "promised_lead_time_days", "is_active"])


def load_demand(start: date | None = None, end: date | None = None,
                product_id: int | None = None,
                database: Database | None = None) -> pd.DataFrame:
    database = database or default_db
    sql = ["SELECT product_id, demand_date, quantity FROM demand_records WHERE 1=1"]
    params: list = []
    if start:
        sql.append("AND demand_date >= %s")
        params.append(start)
    if end:
        sql.append("AND demand_date <= %s")
        params.append(end)
    if product_id:
        sql.append("AND product_id = %s")
        params.append(product_id)
    sql.append("ORDER BY product_id, demand_date")
    df = _frame(database.fetch_all(" ".join(sql), params),
                ["product_id", "demand_date", "quantity"])
    if not df.empty:
        df["demand_date"] = pd.to_datetime(df["demand_date"])
        df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
        df["product_id"] = df["product_id"].astype(int)
    return df


def load_stock_transactions(start: date | None = None, end: date | None = None,
                            database: Database | None = None) -> pd.DataFrame:
    database = database or default_db
    sql = ["SELECT transaction_id, product_id, transaction_date, transaction_type, quantity",
           "FROM stock_transactions WHERE 1=1"]
    params: list = []
    if start:
        sql.append("AND transaction_date >= %s")
        params.append(start)
    if end:
        sql.append("AND transaction_date <= %s")
        params.append(end)
    sql.append("ORDER BY product_id, transaction_date, transaction_id")
    df = _frame(database.fetch_all(" ".join(sql), params),
                ["transaction_id", "product_id", "transaction_date",
                 "transaction_type", "quantity"])
    if not df.empty:
        df["transaction_date"] = pd.to_datetime(df["transaction_date"])
        df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0)
        df["product_id"] = df["product_id"].astype(int)
    return df


def load_purchase_orders(start: date | None = None, end: date | None = None,
                         supplier_id: int | None = None,
                         database: Database | None = None) -> pd.DataFrame:
    database = database or default_db
    sql = ["""
        SELECT po.po_id, po.po_number, po.supplier_id, s.supplier_name, po.product_id,
               p.sku, po.quantity, po.received_qty, po.order_date, po.expected_date,
               po.received_date, po.status
        FROM purchase_orders po
        JOIN suppliers s ON s.supplier_id = po.supplier_id
        JOIN products  p ON p.product_id = po.product_id
        WHERE 1=1
    """]
    params: list = []
    if start:
        sql.append("AND po.order_date >= %s")
        params.append(start)
    if end:
        sql.append("AND po.order_date <= %s")
        params.append(end)
    if supplier_id:
        sql.append("AND po.supplier_id = %s")
        params.append(supplier_id)
    sql.append("ORDER BY po.order_date, po.po_id")
    df = _frame(database.fetch_all(" ".join(sql), params),
                ["po_id", "po_number", "supplier_id", "supplier_name", "product_id",
                 "sku", "quantity", "received_qty", "order_date", "expected_date",
                 "received_date", "status"])
    if not df.empty:
        for col in ("order_date", "expected_date", "received_date"):
            df[col] = pd.to_datetime(df[col])
        for col in ("quantity", "received_qty"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_inventory_position(database: Database | None = None,
                            as_of: date | None = None) -> pd.DataFrame:
    """On-hand (ledger sum) and on-order (open POs) quantity per product."""
    database = database or default_db
    as_of_clause_tx = ""
    params_tx: list = []
    if as_of:
        as_of_clause_tx = "AND st.transaction_date <= %s"
        params_tx.append(as_of)

    on_hand = _frame(database.fetch_all(
        "SELECT p.product_id, COALESCE(SUM(st.quantity), 0) AS on_hand "
        "FROM products p LEFT JOIN stock_transactions st "
        f"  ON st.product_id = p.product_id {as_of_clause_tx} "
        "GROUP BY p.product_id", params_tx),
        ["product_id", "on_hand"])

    on_order = _frame(database.fetch_all(
        """
        SELECT product_id,
               COALESCE(SUM(quantity - COALESCE(received_qty, 0)), 0) AS on_order
        FROM purchase_orders
        WHERE status IN ('OPEN', 'PARTIAL')
        GROUP BY product_id
        """),
        ["product_id", "on_order"])

    if on_hand.empty:
        out = pd.DataFrame(columns=["product_id", "on_hand", "on_order"])
    else:
        out = on_hand.merge(on_order, on="product_id", how="left")
        out["on_order"] = out["on_order"].fillna(0)
        for col in ("on_hand", "on_order"):
            out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0)
    return out


def data_bounds(database: Database | None = None) -> dict:
    """First/last dates present in demand and PO data (used for defaults)."""
    database = database or default_db
    row = database.fetch_one(
        """
        SELECT (SELECT MIN(demand_date) FROM demand_records) AS demand_min,
               (SELECT MAX(demand_date) FROM demand_records) AS demand_max,
               (SELECT COUNT(*) FROM demand_records)        AS demand_rows,
               (SELECT COUNT(*) FROM products)              AS product_rows,
               (SELECT COUNT(*) FROM purchase_orders)       AS po_rows
        """) or {}
    return {k: (str(v) if isinstance(v, (date,)) else v) for k, v in row.items()}


def default_window_end(database: Database | None = None) -> date:
    """Latest demand date in the database, else yesterday."""
    database = database or default_db
    value = database.fetch_scalar("SELECT MAX(demand_date) FROM demand_records")
    if isinstance(value, date):
        return value
    return date.today() - timedelta(days=1)
