"""Shared pytest fixtures.

Two test families
-----------------
* pure tests (formulas, validators, forecast maths) -- no database needed;
* integration tests (API + SQL) -- skipped automatically with a clear reason
  when MySQL is unreachable, so `pytest` always runs on a marker machine.
"""
from __future__ import annotations

import itertools
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.app import create_app          # noqa: E402
from config import config               # noqa: E402
from db import Database, db as default_db  # noqa: E402


def _database_available() -> tuple[bool, str]:
    try:
        report = default_db.health()
        if report.get("connected"):
            return True, "ok"
        return False, report.get("error", "not connected")
    except Exception as exc:                                  # pragma: no cover
        return False, str(exc)


_UNIQUE = itertools.count(1)


def unique_suffix() -> str:
    """Short unique token so parallel/repeat runs never collide on unique keys."""
    return f"{int(time.time() * 1000) % 10_000_000}{next(_UNIQUE)}"


pytest.timestamp = unique_suffix()      # simple namespace used by the fixtures


AVAILABLE, REASON = _database_available()
requires_db = pytest.mark.skipif(
    not AVAILABLE, reason=f"MySQL not reachable ({REASON}) - integration tests skipped")


@pytest.fixture(scope="session")
def app():
    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def db():
    return default_db


@pytest.fixture()
def broken_app():
    """App pointed at a dead port -- used to prove graceful 503 handling.

    The app factory re-points the shared database singleton at the broken
    config, so the fixture restores the real one afterwards (otherwise every
    later test in the session would see a dead database).
    """
    original = default_db.cfg
    bad = replace(config, DB_PORT=65001, DB_CONNECT_TIMEOUT=1, DB_POOL_SIZE=1)
    application = create_app(bad)
    application.config.update(TESTING=True)
    try:
        yield application
    finally:
        default_db.configure(original)
        default_db.close()


@pytest.fixture()
def temp_product(db):
    """Create a throw-away product (+ supplier if needed) and clean up after."""
    supplier_id = db.execute(
        """INSERT INTO suppliers (supplier_name, contact_email, country,
                                  promised_lead_time_days)
           VALUES (%s, %s, 'India', 7)""",
        (f"TEST Supplier {pytest.timestamp}", "test@example.com"))
    product_id = db.execute(
        """INSERT INTO products (sku, product_name, category, unit_cost,
                                 lead_time_days, reorder_point, supplier_id)
           VALUES (%s, %s, 'Testing', 100.00, 7, 50, %s)""",
        (f"TST-{pytest.timestamp}", "Temporary test product", supplier_id))
    yield {"product_id": product_id, "supplier_id": supplier_id,
           "sku": f"TST-{pytest.timestamp}"}
    for table in ("demand_records", "stock_transactions", "purchase_orders"):
        db.execute(f"DELETE FROM {table} WHERE product_id = %s", (product_id,))
    db.execute("DELETE FROM products WHERE product_id = %s", (product_id,))
    db.execute("DELETE FROM purchase_orders WHERE supplier_id = %s", (supplier_id,))
    db.execute("DELETE FROM products WHERE supplier_id = %s", (supplier_id,))
    db.execute("DELETE FROM suppliers WHERE supplier_id = %s", (supplier_id,))


@pytest.fixture()
def temp_supplier(db):
    name = f"TEST Supplier {pytest.timestamp}-s"
    supplier_id = db.execute(
        """INSERT INTO suppliers (supplier_name, contact_email, country,
                                  promised_lead_time_days)
           VALUES (%s, 'sup@example.com', 'India', 14)""", (name,))
    yield {"supplier_id": supplier_id, "supplier_name": name}
    db.execute("DELETE FROM products WHERE supplier_id = %s", (supplier_id,))
    db.execute("DELETE FROM suppliers WHERE supplier_id = %s", (supplier_id,))
