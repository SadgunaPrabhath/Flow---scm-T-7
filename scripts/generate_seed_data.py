"""Synthetic data generator for the Supply Chain Optimization project.

Why simulate instead of using random rows?
------------------------------------------
Demand, purchase orders and the stock ledger must tell the same story:
an ISSUE only happens if stock exists, a RECEIPT only happens when a PO
arrives, and the closing balance of the ledger is the on-hand quantity
that the reorder report uses. A day-by-day inventory simulation gives us
that consistency, plus deliberate edge cases for the test matrix.

Outputs
-------
* sql/seed_data.sql   (idempotent INSERT script for MySQL/MariaDB)
* optionally loads it straight into the database (--load)

Edge cases deliberately planted (documented in docs/05_test_report.md)
---------------------------------------------------------------------
* SKU-DEAD      demand stopped 150 days ago, stock still high   -> EXCESS
* SKU-STARVE    no receipts for the last 70 days                -> STOCKOUT
* SKU-NEW       only 45 days of history                         -> master lead time
* SKU-LUMPY     intermittent / lumpy demand, huge CV
* SKU-GAP       ~20% of zero-demand days have no row            -> zero-fill test
* supplier 5    highly inconsistent lead times (CV > 0.5)
* supplier 6    only open orders, never delivered               -> NO_DATA grade
* POs           late, partial, cancelled and overdue-open orders

Usage
-----
    python scripts/generate_seed_data.py                # writes the SQL file
    python scripts/generate_seed_data.py --load         # writes + loads it
    python scripts/generate_seed_data.py --days 540 --seed 7 --end 2026-10-19
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_SEED = 20261021
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# --------------------------------------------------------------- reference
@dataclass
class SupplierSpec:
    name: str
    country: str
    promised_lead_time: int
    delay_mean: float          # days late (+) or early (-) vs expected date
    delay_sd: float
    email: str = ""


SUPPLIERS = [
    SupplierSpec("Precision Auto Components Pvt Ltd", "India", 10, -2.5, 0.9,
                 "orders@precisionauto.example.com"),
    SupplierSpec("Shenzhen MotionWorks Ltd", "China", 21, 0.2, 2.2,
                 "sales@motionworks.example.com"),
    SupplierSpec("Bharat Industrial Fasteners", "India", 7, -1.2, 1.2,
                 "support@bharatfasteners.example.com"),
    SupplierSpec("EuroDrive Systems GmbH", "Germany", 30, 1.2, 3.2,
                 "kontakt@eurodrive.example.com"),
    SupplierSpec("Krishna Plastics & Packaging", "India", 12, 2.8, 7.0,
                 "hello@krishnaplastics.example.com"),
    SupplierSpec("NorthStar Electronics", "Taiwan", 25, -0.5, 0.6,
                 "po@northstar.example.com"),
]


@dataclass
class ProductSpec:
    sku: str
    name: str
    category: str
    unit_cost: float
    supplier_idx: int
    base_daily: float
    trend: float = 0.0             # total relative change over the horizon
    weekly_pattern: tuple = (1.0, 0.95, 1.0, 1.05, 1.2, 0.75, 0.55)
    noise_sd: float = 0.18         # multiplicative noise on the daily mean
    lumpiness: float = 0.0         # 0..1 share of "no sale" days
    promo_factor: float = 0.0      # extra demand in promo weeks
    holiday_factor: float = 0.0    # extra demand in a specific month
    stop_after: int | None = None  # demand stops N days before the end
    start_after: int | None = None # demand starts N days after the beginning
    drop_demand_rows: float = 0.0  # share of zero days with no DB row
    block_receipts_after: int | None = None  # supplier stops shipping
    surge_after: int | None = None           # demand jump (days from start)
    surge_factor: float = 1.0
    stalled_po_before_end: int | None = None # open PO that never arrives
    stalled_po_days_of_demand: float = 20.0
    initial_cover_days: float = 35.0
    opening_units: int | None = None
    legacy_rop_factor: float = 1.0  # multiplies the "manual" reorder point
    reorder_qty_days: float = 30.0
    sim_cover_days: float = 10.0    # planner's buffer days above lead time
                                    # (negative = aggressive lean policy)


PRODUCTS = [
    ProductSpec("AUTO-BRK-0012", "Brake Caliper Assembly 12in", "Braking", 2450.00, 0,
                6.5, trend=0.25, promo_factor=0.3, sim_cover_days=9, initial_cover_days=30),
    ProductSpec("AUTO-BRK-0021", "Brake Pad Set (Front)", "Braking", 780.00, 0,
                18.0, trend=0.10, weekly_pattern=(1.0, 1.0, 1.05, 1.1, 1.25, 0.7, 0.5),
                surge_after=25, surge_factor=1.9),
    ProductSpec("AUTO-SUS-0104", "Shock Absorber Rear", "Suspension", 1620.00, 2,
                9.0, trend=-0.05),
    ProductSpec("AUTO-SUS-0112", "Coil Spring Heavy Duty", "Suspension", 980.00, 2,
                4.5, promo_factor=0.2, sim_cover_days=0, initial_cover_days=18),
    ProductSpec("AUTO-FLT-0201", "Oil Filter Premium", "Filtration", 320.00, 2,
                46.0, trend=0.15, noise_sd=0.12),
    ProductSpec("AUTO-FLT-0207", "Air Filter Element", "Filtration", 410.00, 2,
                27.0, noise_sd=0.15),
    ProductSpec("ELEC-SEN-1001", "Crankshaft Position Sensor", "Electronics", 1180.00, 1,
                7.5, trend=0.40, promo_factor=0.25, surge_after=30, surge_factor=1.8),
    ProductSpec("ELEC-SEN-1008", "Oxygen Sensor Universal", "Electronics", 1650.00, 1,
                5.0, trend=0.05, noise_sd=0.25, sim_cover_days=-6, initial_cover_days=20),
    ProductSpec("ELEC-ECU-1102", "Engine Control Module v3", "Electronics", 9800.00, 1,
                1.6, trend=0.30, noise_sd=0.35, lumpiness=0.15),
    ProductSpec("ELEC-HAR-1120", "Wiring Harness 24V", "Electronics", 3400.00, 5,
                2.2, trend=0.55, noise_sd=0.30, sim_cover_days=-8, initial_cover_days=15),
    ProductSpec("ELEC-CAM-1130", "Rear Camera Module", "Electronics", 2750.00, 5,
                3.0, trend=0.65, noise_sd=0.28, reorder_qty_days=25),
    ProductSpec("DRV-UNT-2001", "Drive Unit Assembly", "Drivetrain", 14500.00, 3,
                1.1, trend=0.20, noise_sd=0.30, lumpiness=0.20, sim_cover_days=-12,
                initial_cover_days=20),
    ProductSpec("DRV-BRG-2015", "Bearing Kit Heavy Duty", "Drivetrain", 1240.00, 3,
                6.0, noise_sd=0.20),
    ProductSpec("DRV-CLP-2020", "Clutch Plate 240mm", "Drivetrain", 3100.00, 3,
                2.4, trend=-0.30, noise_sd=0.25, sim_cover_days=45, reorder_qty_days=120),
    ProductSpec("PKG-BOX-3001", "Corrugated Box 600x400", "Packaging", 45.00, 4,
                210.0, trend=0.12, noise_sd=0.10, weekly_pattern=(1, 1, 1, 1, 1.1, 0.6, 0.4)),
    ProductSpec("PKG-BOX-3007", "Corrugated Box 400x300", "Packaging", 32.00, 4,
                160.0, noise_sd=0.12, sim_cover_days=35, reorder_qty_days=90),
    ProductSpec("PKG-FLM-3010", "Stretch Wrap Roll", "Packaging", 210.00, 4,
                24.0, noise_sd=0.18, lumpiness=0.10),
    ProductSpec("PKG-PLT-3020", "Wooden Pallet ISPM-15", "Packaging", 640.00, 4,
                11.0, trend=0.08, noise_sd=0.20, stalled_po_before_end=22),
    ProductSpec("CON-SEAL-4001", "Gasket Seal Ring", "Consumables", 95.00, 2,
                58.0, trend=0.05, noise_sd=0.14, surge_after=28, surge_factor=1.6),
    ProductSpec("CON-GRS-4004", "Lithium Grease Cartridge", "Consumables", 260.00, 2,
                19.0, noise_sd=0.16, sim_cover_days=40, reorder_qty_days=100),
    # --- planted edge cases -------------------------------------------------
    ProductSpec("AUTO-CAB-0099", "Cabin Air Filter (Discontinued)", "Filtration", 290.00, 2,
                14.0, stop_after=150, initial_cover_days=210, legacy_rop_factor=0.4),
    ProductSpec("ELEC-MOD-1150", "Telematics Module LTE", "Electronics", 6200.00, 1,
                2.5, trend=0.15, noise_sd=0.25, block_receipts_after=70,
                initial_cover_days=25),
    ProductSpec("ELEC-MOD-1160", "Telematics Module 5G (new)", "Electronics", 8400.00, 5,
                2.0, start_after=495, noise_sd=0.30, initial_cover_days=30,
                sim_cover_days=30, reorder_qty_days=90),
    ProductSpec("CON-ADH-4010", "Structural Adhesive (lumpy)", "Consumables", 520.00, 4,
                6.0, noise_sd=0.45, lumpiness=0.45, drop_demand_rows=0.20),
]


# --------------------------------------------------------------- helpers
def working_day_factor(d: date, pattern: tuple) -> float:
    return pattern[d.weekday()]


def seasonal_factor(d: date, product: ProductSpec) -> float:
    """Yearly seasonality: a gentle annual wave plus a festive boost in Oct-Nov."""
    annual = 1.0 + 0.12 * np.sin(2 * np.pi * (d.timetuple().tm_yday / 365.0))
    festive = 1.0 + 0.25 * product.holiday_factor if d.month in (10, 11) else 1.0
    return float(annual * festive)


def sim_daily_mean(day_idx: int, total_days: int, d: date, product: ProductSpec) -> float:
    """Expected demand for a day (before noise): trend * weekday * season * surge."""
    progress = day_idx / max(1, total_days - 1)
    trend_mult = 1.0 + product.trend * progress
    if product.surge_after is not None and day_idx >= total_days - product.surge_after:
        trend_mult *= product.surge_factor
    base = product.base_daily * trend_mult
    base *= working_day_factor(d, product.weekly_pattern)
    base *= seasonal_factor(d, product)
    # promotional weeks: day 3 of every 6th week
    if product.promo_factor and (day_idx // 7) % 6 == 3:
        base *= (1.0 + product.promo_factor)
    return max(0.0, base)


def draw_demand(mu: float, product: ProductSpec, rng: np.random.Generator) -> int:
    """Negative-binomial style demand: Poisson mean with multiplicative noise."""
    if mu <= 0:
        return 0
    if product.lumpiness and rng.random() < product.lumpiness:
        return 0
    noisy = mu * max(0.0, rng.normal(1.0, product.noise_sd))
    if noisy > 60:                     # bulk orders for high-volume items
        noisy *= max(0.0, rng.normal(1.0, 0.05))
    return int(rng.poisson(noisy))


# ------------------------------------------------------------ simulation
@dataclass
class ProductResult:
    demand_rows: list = field(default_factory=list)     # (date, qty)
    txn_rows: list = field(default_factory=list)        # dict
    po_rows: list = field(default_factory=list)         # dict
    on_hand_final: float = 0.0
    demand_total: int = 0


def simulate_product(pid: int, product: ProductSpec, supplier: SupplierSpec,
                     start: date, days: int, rng: np.random.Generator) -> ProductResult:
    result = ProductResult()
    dates = [start + timedelta(days=i) for i in range(days)]

    # initial stock comes from an opening RECEIPT
    opening = product.opening_units
    if opening is None:
        opening = int(round(product.base_daily * product.initial_cover_days))
    opening = max(0, opening)
    if opening:
        result.txn_rows.append({
            "product_id": pid, "transaction_date": start - timedelta(days=5),
            "transaction_type": "RECEIPT", "quantity": opening,
            "reference": "OPENING", "notes": "Opening balance load",
        })

    on_hand = float(opening)
    inbound: list[dict] = []     # scheduled receipts: {date, qty, po}
    po_seq = 0
    last_promo_adjust = 0

    # reorder policy used by the *simulated planner* (not the analyst)
    sim_rop_days = supplier.promised_lead_time + product.sim_cover_days
    sim_rop = product.base_daily * sim_rop_days
    order_qty = max(1, int(round(product.base_daily * product.reorder_qty_days)))

    for i, d in enumerate(dates):
        # ---- receipts due today (or overdue, processed in order) -----------
        due = [r for r in inbound if r["date"] <= d]
        due.sort(key=lambda r: r["date"])
        received_today = 0
        for r in due:
            inbound.remove(r)
            qty = r["qty"]
            status = "RECEIVED"
            received_qty = qty
            delay = (d - r["expected"]).days
            if rng.random() < 0.06 and delay == 0:
                # partial delivery: the balance arrives 6 days later as PO-...B
                received_qty = int(qty * rng.uniform(0.5, 0.85))
                status = "PARTIAL"
                inbound.append({**r, "po": r["po"] + "B", "qty": qty - received_qty,
                                "date": d + timedelta(days=6),
                                "expected": r["expected"]})
            result.txn_rows.append({
                "product_id": pid, "transaction_date": d,
                "transaction_type": "RECEIPT", "quantity": received_qty,
                "reference": r["po"], "notes": f"Against {r['po']}",
            })
            on_hand += received_qty
            received_today += received_qty
            result.po_rows.append({
                "po_number": r["po"], "product_id": pid,
                "supplier_id": supplier_idx_to_id(product.supplier_idx),
                "quantity": qty, "received_qty": received_qty,
                "order_date": r["ordered"], "expected_date": r["expected"],
                "received_date": d, "status": status,
                "notes": "Part split delivery" if status == "PARTIAL" else None,
            })

        # ---- demand / issue ------------------------------------------------
        inside_window = True
        if product.stop_after is not None and i >= days - product.stop_after:
            inside_window = False
        if product.start_after is not None and i < product.start_after:
            inside_window = False

        # a brand-new SKU has no rows at all before its launch date
        before_launch = (product.start_after is not None and i < product.start_after)

        demand = 0
        if inside_window and not before_launch:
            mu = sim_daily_mean(i, days, d, product)
            demand = draw_demand(mu, product, rng)

        if not before_launch:
            # some zero-demand days are deliberately missing rows (reporting gap)
            drop_row = bool(product.drop_demand_rows) and demand == 0 \
                and rng.random() < product.drop_demand_rows
            if not drop_row:
                result.demand_rows.append((d, int(demand)))
                result.demand_total += demand

            if demand:
                issue = min(demand, int(on_hand))
                if issue > 0:
                    result.txn_rows.append({
                        "product_id": pid, "transaction_date": d,
                        "transaction_type": "ISSUE", "quantity": -issue,
                        "reference": "SALES", "notes": "Customer dispatch",
                    })
                    on_hand -= issue
                # demand above on-hand = lost sale (kept in demand_records only)

        # ---- inventory adjustments (stock counts) --------------------------
        if i % 97 == 40 and on_hand > 0:
            shrink = int(on_hand * rng.uniform(0.005, 0.02))
            if shrink > 0:
                result.txn_rows.append({
                    "product_id": pid, "transaction_date": d,
                    "transaction_type": "ADJUSTMENT", "quantity": -shrink,
                    "reference": "CYCLE-COUNT", "notes": "Shrinkage / cycle count",
                })
                on_hand -= shrink

        # ---- a single PO that the supplier never ships (overdue open PO) --
        if (product.stalled_po_before_end is not None
                and i == days - product.stalled_po_before_end - supplier.promised_lead_time):
            stalled_qty = max(1, int(round(product.base_daily
                                           * product.stalled_po_days_of_demand)))
            result.po_rows.append({
                "po_number": f"PO-{pid:03d}-S001", "product_id": pid,
                "supplier_id": supplier_idx_to_id(product.supplier_idx),
                "quantity": stalled_qty, "received_qty": None, "order_date": d,
                "expected_date": d + timedelta(days=supplier.promised_lead_time),
                "received_date": None, "status": "OPEN",
                "notes": "Supplier confirmed delay - no dispatch yet",
            })
            inbound.append({"po": f"PO-{pid:03d}-S001", "qty": 0, "ordered": d,
                            "expected": d + timedelta(days=supplier.promised_lead_time),
                            "date": d + timedelta(days=900)})   # never arrives

        # ---- replenishment decision ---------------------------------------
        blocked = (product.block_receipts_after is not None
                   and i >= days - product.block_receipts_after)
        position = on_hand + sum(r["qty"] for r in inbound)
        if not blocked and position < sim_rop and i % 3 == 0:
            po_seq += 1
            ordered = d
            planned_lt = supplier.promised_lead_time
            expected = ordered + timedelta(days=planned_lt)
            delay = max(-3, int(round(rng.normal(supplier.delay_mean, supplier.delay_sd))))
            arrival = expected + timedelta(days=delay)
            po_number = f"PO-{pid:03d}-{po_seq:04d}"
            if arrival > dates[-1]:
                # stays open at the end of the simulation window
                result.po_rows.append({
                    "po_number": po_number, "product_id": pid,
                    "supplier_id": supplier_idx_to_id(product.supplier_idx),
                    "quantity": order_qty, "received_qty": None,
                    "order_date": ordered, "expected_date": expected,
                    "received_date": None, "status": "OPEN", "notes": None,
                })
            else:
                inbound.append({"po": po_number, "qty": order_qty, "ordered": ordered,
                                "expected": expected, "date": arrival})
            # cancelled PO (2% of orders)
            if rng.random() < 0.02:
                result.po_rows.append({
                    "po_number": f"PO-{pid:03d}-{po_seq:04d}X", "product_id": pid,
                    "supplier_id": supplier_idx_to_id(product.supplier_idx),
                    "quantity": max(1, order_qty // 2), "received_qty": None,
                    "order_date": ordered, "expected_date": expected,
                    "received_date": None, "status": "CANCELLED",
                    "notes": "Cancelled by planner",
                })

    # POs still in transit when the window closes stay OPEN (qty 0 = stalled PO
    # that was already recorded when it was raised)
    for r in inbound:
        if r["qty"] <= 0:
            continue
        result.po_rows.append({
            "po_number": r["po"], "product_id": pid,
            "supplier_id": supplier_idx_to_id(product.supplier_idx),
            "quantity": r["qty"], "received_qty": None, "order_date": r["ordered"],
            "expected_date": r["expected"], "received_date": None,
            "status": "OPEN", "notes": None,
        })

    result.on_hand_final = on_hand
    return result


def supplier_idx_to_id(idx: int) -> int:
    return idx + 1


# --------------------------------------------------------------- SQL emit
def sql_str(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, float):
        return f"{value:.2f}"
    if isinstance(value, date):
        return "'" + value.isoformat() + "'"
    text = str(value).replace("\\", "\\\\").replace("'", "''")
    return f"'{text}'"


def chunked_insert(table: str, columns: list[str], rows: list[list], batch: int = 200) -> list[str]:
    if not rows:
        return []
    out = []
    cols = ", ".join(columns)
    for i in range(0, len(rows), batch):
        part = rows[i:i + batch]
        values = ",\n  ".join("(" + ", ".join(sql_str(v) for v in r) + ")" for r in part)
        out.append(f"INSERT INTO {table} ({cols}) VALUES\n  {values};")
    return out


def build_sql(days: int, seed: int, end: date) -> str:
    rng = np.random.default_rng(seed)
    start = end - timedelta(days=days - 1)

    supplier_rows = [[i + 1, s.name, s.email, s.country, s.promised_lead_time, 1]
                     for i, s in enumerate(SUPPLIERS)]

    product_rows = []
    demand_rows = []
    txn_rows = []
    po_rows = []
    summary = []

    for idx, product in enumerate(PRODUCTS):
        pid = idx + 1
        supplier = SUPPLIERS[product.supplier_idx]
        # legacy manually-maintained reorder point (stale on purpose for some SKUs)
        legacy_rop = max(0, int(round(product.base_daily * (supplier.promised_lead_time + 14)
                                     * product.legacy_rop_factor)))
        product_rows.append([
            pid, product.sku, product.name, product.category,
            f"{product.unit_cost:.2f}", supplier.promised_lead_time, legacy_rop,
            supplier_idx_to_id(product.supplier_idx), 1,
        ])
        result = simulate_product(pid, product, supplier, start, days, rng)

        for d, q in result.demand_rows:
            demand_rows.append([pid, d, int(q)])
        for t in result.txn_rows:
            txn_rows.append([t["product_id"], t["transaction_date"], t["transaction_type"],
                             t["quantity"], t["reference"], t["notes"]])
        for po in result.po_rows:
            po_rows.append([po["po_number"], po["supplier_id"], po["product_id"],
                            po["quantity"], po["received_qty"], po["order_date"],
                            po["expected_date"], po["received_date"], po["status"],
                            po["notes"]])
        summary.append((product.sku, result.demand_total, result.on_hand_final,
                        len(result.po_rows)))

    lines = [
        "-- =====================================================================",
        "-- Project 24 : seed / sample data (synthetic -- generated, not real data)",
        f"-- Generated by scripts/generate_seed_data.py  seed={seed}  days={days}",
        f"-- Window : {start.isoformat()} .. {end.isoformat()}",
        f"-- Products: {len(PRODUCTS)}   Suppliers: {len(SUPPLIERS)}",
        "-- Every purchase order, stock movement and demand row comes from one",
        "-- day-by-day inventory simulation, so ledger balances are consistent.",
        "-- =====================================================================",
        "USE supplychain;",
        "SET NAMES utf8mb4;",
        "SET @OLD_FK = @@FOREIGN_KEY_CHECKS; SET FOREIGN_KEY_CHECKS = 0;",
        "TRUNCATE TABLE reorder_recommendations;",
        "TRUNCATE TABLE analysis_runs;",
        "TRUNCATE TABLE demand_records;",
        "TRUNCATE TABLE stock_transactions;",
        "TRUNCATE TABLE purchase_orders;",
        "TRUNCATE TABLE products;",
        "TRUNCATE TABLE suppliers;",
        "SET FOREIGN_KEY_CHECKS = @OLD_FK;",
        "",
    ]
    lines += chunked_insert("suppliers",
                            ["supplier_id", "supplier_name", "contact_email", "country",
                             "promised_lead_time_days", "is_active"], supplier_rows)
    lines += chunked_insert("products",
                            ["product_id", "sku", "product_name", "category", "unit_cost",
                             "lead_time_days", "reorder_point", "supplier_id", "is_active"],
                            product_rows)
    lines += chunked_insert("demand_records", ["product_id", "demand_date", "quantity"],
                            demand_rows, batch=400)
    lines += chunked_insert("stock_transactions",
                            ["product_id", "transaction_date", "transaction_type",
                             "quantity", "reference", "notes"], txn_rows, batch=400)
    lines += chunked_insert("purchase_orders",
                            ["po_number", "supplier_id", "product_id", "quantity",
                             "received_qty", "order_date", "expected_date",
                             "received_date", "status", "notes"], po_rows, batch=200)
    lines += [
        "",
        "-- sanity check: ledger balance per product",
        "SELECT p.sku, SUM(st.quantity) AS on_hand",
        "FROM products p LEFT JOIN stock_transactions st ON st.product_id = p.product_id",
        "GROUP BY p.sku ORDER BY on_hand DESC;",
        "",
    ]
    print(f"rows -> demand={len(demand_rows)} transactions={len(txn_rows)} "
          f"purchase_orders={len(po_rows)}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate synthetic seed data for Project 24")
    ap.add_argument("--days", type=int, default=540, help="history length in days")
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--end", type=str, default=None, help="last demand date YYYY-MM-DD")
    ap.add_argument("--out", type=str, default=str(ROOT / "sql" / "seed_data.sql"))
    ap.add_argument("--load", action="store_true", help="also execute the SQL")
    args = ap.parse_args()

    end = date.fromisoformat(args.end) if args.end else (date.today() - timedelta(days=2))
    sql = build_sql(args.days, args.seed, end)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(sql, encoding="utf-8")
    print(f"written {out} ({out.stat().st_size/1024:.0f} KB)")

    if args.load:
        here = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(here))
        from scripts.load_sql import load_sql_file
        load_sql_file(out)


if __name__ == "__main__":
    main()
