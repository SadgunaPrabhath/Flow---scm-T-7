"""Generate docs/data_dictionary.md straight from the live schema.

Uses INFORMATION_SCHEMA so the document can never drift from the database:

    python scripts/dump_data_dictionary.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db import db                      # noqa: E402
from config import config              # noqa: E402

TABLES = ["suppliers", "products", "purchase_orders", "stock_transactions",
          "demand_records", "analysis_runs", "reorder_recommendations"]
VIEWS = ["v_product_inventory", "v_po_delivery"]


def columns(table: str) -> list[dict]:
    return db.fetch_all(
        """
        SELECT c.COLUMN_NAME      AS column_name,
               c.COLUMN_TYPE      AS column_type,
               c.IS_NULLABLE      AS is_nullable,
               COALESCE(c.COLUMN_DEFAULT, '') AS column_default,
               c.COLUMN_KEY       AS column_key,
               c.EXTRA            AS extra,
               COALESCE(c.COLUMN_COMMENT, '')  AS comment
        FROM INFORMATION_SCHEMA.COLUMNS c
        WHERE c.TABLE_SCHEMA = %s AND c.TABLE_NAME = %s
        ORDER BY c.ORDINAL_POSITION
        """, (config.DB_NAME, table))


def constraints(table: str) -> list[dict]:
    return db.fetch_all(
        """
        SELECT tc.CONSTRAINT_NAME AS name, tc.CONSTRAINT_TYPE AS type,
               GROUP_CONCAT(kcu.COLUMN_NAME ORDER BY kcu.ORDINAL_POSITION) AS cols,
               kcu.REFERENCED_TABLE_NAME AS ref_table,
               GROUP_CONCAT(kcu.REFERENCED_COLUMN_NAME) AS ref_cols
        FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
        LEFT JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE kcu
               ON kcu.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
              AND kcu.TABLE_SCHEMA = tc.TABLE_SCHEMA
              AND kcu.TABLE_NAME = tc.TABLE_NAME
        WHERE tc.TABLE_SCHEMA = %s AND tc.TABLE_NAME = %s
        GROUP BY tc.CONSTRAINT_NAME, tc.CONSTRAINT_TYPE, kcu.REFERENCED_TABLE_NAME
        ORDER BY tc.CONSTRAINT_TYPE, tc.CONSTRAINT_NAME
        """, (config.DB_NAME, table))


def check_clauses(table: str) -> dict[str, str]:
    """MariaDB/MySQL >= 8.0.16 stores CHECK text in CHECK_CONSTRAINTS."""
    try:
        rows = db.fetch_all(
            """
            SELECT tc.CONSTRAINT_NAME AS name, cc.CHECK_CLAUSE AS clause
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS tc
            JOIN INFORMATION_SCHEMA.CHECK_CONSTRAINTS cc
              ON cc.CONSTRAINT_NAME = tc.CONSTRAINT_NAME
             AND cc.CONSTRAINT_SCHEMA = tc.TABLE_SCHEMA
            WHERE tc.TABLE_SCHEMA = %s AND tc.TABLE_NAME = %s
              AND tc.CONSTRAINT_TYPE = 'CHECK'
            """, (config.DB_NAME, table))
        return {row["name"]: row["clause"] for row in rows}
    except Exception:                       # older server without the view
        return {}


def row_count(table: str) -> int:
    return int(db.fetch_scalar(f"SELECT COUNT(*) FROM {table}") or 0)


def main() -> None:
    lines = ["# Data Dictionary\n",
             f"*Generated from `INFORMATION_SCHEMA` on {date.today():%d %B %Y} — "
             f"database `{config.DB_NAME}`. Regenerate with "
             "`python scripts/dump_data_dictionary.py`; do not edit by hand.*\n"]

    lines.append("\n## Tables\n")
    lines.append("| Table | Rows | Description |\n|---|---|---|\n")
    descriptions = {
        "suppliers": "Vendor master data (name, country, promised lead time).",
        "products": "Item master data (SKU, cost, planned lead time, manual reorder point).",
        "purchase_orders": "Single-line purchase orders with expected and actual receipt dates.",
        "stock_transactions": "Signed perpetual inventory ledger (the only source of on-hand stock).",
        "demand_records": "Observed daily demand per product; a missing day means zero demand.",
        "analysis_runs": "Audit record of each explicit analysis run and its parameters.",
        "reorder_recommendations": "Snapshot of computed reorder points, risk flags and suggestions per run.",
    }
    for table in TABLES:
        lines.append(f"| `{table}` | {row_count(table):,} | {descriptions[table]} |")

    for table in TABLES:
        lines.append(f"\n## `{table}`\n")
        lines.append("| Column | Type | Null | Default | Key | Extra | Notes |")
        lines.append("|---|---|---|---|---|---|---|")
        for col in columns(table):
            key = {"PRI": "PK", "UNI": "UNIQUE", "MUL": "INDEX"}.get(col["column_key"], "")
            lines.append(
                f"| `{col['column_name']}` | `{col['column_type']}` | "
                f"{'YES' if col['is_nullable'] == 'YES' else 'NO'} | "
                f"{col['column_default'] or '—'} | {key} | {col['extra'] or '—'} | "
                f"{col['comment'] or ''} |")
        cons = constraints(table)
        if cons:
            lines.append("\n**Constraints**\n")
            lines.append("| Name | Type | Column(s) | References |")
            lines.append("|---|---|---|---|")
            checks = check_clauses(table)
            for row in cons:
                ref = (f"`{row['ref_table']}({row['ref_cols']})`"
                       if row["ref_table"] else "—")
                if row["type"] == "CHECK":
                    clauses = checks.get(row["name"], "")
                    ref = f"`{clauses}`" if clauses else "—"
                    cols = "—"
                else:
                    cols = f"`{row['cols']}`"
                lines.append(f"| `{row['name']}` | {row['type']} | {cols} | {ref} |")

    lines.append("\n## Views\n")
    lines.append("| View | Definition |\n|---|---|\n")
    for view in VIEWS:
        definition = db.fetch_scalar(
            "SELECT VIEW_DEFINITION FROM INFORMATION_SCHEMA.VIEWS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s", (config.DB_NAME, view))
        lines.append(f"| `{view}` | see `sql/schema.sql` (definition stored in "
                     f"INFORMATION_SCHEMA, {len(definition or '')} characters) |")

    lines.append("\n## Units used in the analysis\n")
    lines.append("| Quantity | Unit | Where |\n|---|---|---|\n"
                 "| Demand | units/day | `demand_records.quantity`, `avg_daily_demand` |\n"
                 "| Lead time | calendar days | `lead_time_days`, `actual_lead_time_days` |\n"
                 "| Safety stock, ROP, quantities | units | `analytics/formulas.py` |\n"
                 "| Days of cover | days | `days_of_cover` |\n"
                 "| Turnover | turns per year | `annualised_turns` |\n"
                 "| Money | INR | `unit_cost`, `order_value`, `on_hand_value` |\n")

    target = ROOT / "docs" / "data_dictionary.md"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"written {target} ({target.stat().st_size / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
