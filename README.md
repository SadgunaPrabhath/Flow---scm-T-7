# Project 24 — Supply Chain Optimization and Inventory Forecasting System

A MySQL + Flask + Pandas/NumPy/Matplotlib system that analyses demand and supplier
performance, computes **reorder points from documented assumptions**, compares a
simple forecast against a naive baseline, and flags **stockout and excess-stock
risk** — as *decision support for a planner*, never as an automatic ordering system.

> **Decision support only.** No endpoint in this project creates, approves, sends or
> schedules a purchase order. Suggestions are computed, ranked and explained so a
> human can decide.

---

## 1. What it does

| Layer | What it delivers |
|---|---|
| **MySQL schema** | 6 tables + 2 views, primary/foreign keys, CHECK constraints, indexes, an audit table for analysis runs |
| **Flask REST API** | CRUD for products, suppliers, purchase orders, stock transactions, demand records; analytics endpoints; chart images served over HTTP |
| **Pandas / NumPy analysis** | Zero-filled daily demand, descriptive statistics, ABC (Pareto) classes, reorder points, safety stock, days of cover, inventory turnover, supplier KPIs |
| **Forecasting** | Naive-mean baseline, seasonal naive, 14-day moving average, Holt-Winters — each scored on a 28-day hold-out with MAE/RMSE/MAPE/MASE |
| **Matplotlib** | 10 labelled charts (units on every axis, legends, written interpretation per chart) saved as PNG |
| **Testing** | 199 automated tests: formulas, validators, API contracts, boundary values, empty datasets, DB-down behaviour |
| **Reports** | Markdown analysis report, 9 CSV tables, one multi-sheet Excel workbook |

Screenshot-free demo path: run the API, call `/api/analytics/dashboard`, open the
generated `reports/analysis_report.md`.

---

## 2. Quick start

### 2.1 Prerequisites

* Python 3.10+ (tested on 3.13)
* MySQL 8.0+ or MariaDB 10.6+ (tested on MariaDB 11.8)

### 2.2 Install

```bash
git clone <your-repo-url> project24_scm
cd project24_scm

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2.3 Create the database and an application user

```sql
-- run once as an administrator
CREATE DATABASE supplychain CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'scm_app'@'localhost' IDENTIFIED BY 'choose-a-strong-password';
CREATE USER 'scm_app'@'127.0.0.1' IDENTIFIED BY 'choose-a-strong-password';
GRANT ALL PRIVILEGES ON supplychain.* TO 'scm_app'@'localhost';
GRANT ALL PRIVILEGES ON supplychain.* TO 'scm_app'@'127.0.0.1';
FLUSH PRIVILEGES;
```

### 2.4 Configure credentials (never hard-coded)

```bash
cp .env.example .env
# edit .env: DB_USER, DB_PASSWORD, and the analysis parameters if you wish
```

`.env` is in `.gitignore`. `GET /api/config` exposes the non-secret parameters and
never the password.

### 2.5 Load schema + synthetic data

```bash
# option A - one command (uses the .env credentials)
python scripts/load_sql.py sql/schema.sql            # tables, constraints, views
python scripts/generate_seed_data.py --load          # regenerate + load sample data

# option B - classic mysql client
mysql -u scm_app -p < sql/schema.sql
mysql -u scm_app -p supplychain < sql/seed_data.sql
```

The committed `sql/seed_data.sql` reproduces exactly the numbers in
`reports/analysis_report.md` (seed `20261021`, window ending `2026-10-19`).

### 2.6 Run the analysis and build the charts/report

```bash
python scripts/run_pipeline.py            # tables + charts + report
python scripts/run_pipeline.py --persist  # also store the run in MySQL (audit trail)
```

Outputs:

```
reports/analysis_report.md          generated report (numbers, formulas, charts)
reports/tables/*.csv                9 summary tables
reports/tables/analysis_workbook.xlsx
reports/charts/*.png                10 charts
```

### 2.7 Start the API

```bash
python run.py
#  * Supply Chain API on http://0.0.0.0:5000
curl http://localhost:5000/api/health
```

### 2.8 Run the tests

```bash
python -m pytest tests -q            # 199 tests
```

Integration tests need a reachable database; if MySQL is down they are **skipped with
an explicit reason** so the pure-logic suite still runs on any machine.

---

## 3. Project layout

```
project24_scm/
├── config.py                  # all settings, from environment variables
├── db.py                      # connection pool, parameterised SQL, error mapping
├── run.py                     # development server entry point
├── api/
│   ├── app.py                 # application factory, JSON rules, logging
│   ├── errors.py              # uniform JSON error envelope
│   ├── validators.py          # request validation + analysis parameters
│   ├── routes_crud.py         # CRUD endpoints for the 5 entities
│   └── routes_analytics.py    # reorder, suppliers, demand, risks, forecast, charts
├── analytics/
│   ├── formulas.py            # every documented formula, pure functions
│   ├── data_access.py         # read-only SQL -> DataFrames
│   ├── demand.py              # zero-filled series, stats, ABC
│   ├── supplier.py            # lead-time / on-time / fill-rate KPIs
│   ├── inventory.py           # reorder engine, risk flags, turnover
│   ├── forecast.py            # 4 models + hold-out evaluation
│   └── pipeline.py            # end-to-end orchestration + persistence
├── viz/charts.py              # 10 Matplotlib charts + interpretations
├── sql/
│   ├── schema.sql             # DDL: tables, constraints, indexes, views
│   └── seed_data.sql          # generated synthetic dataset (12k+ rows)
├── scripts/
│   ├── generate_seed_data.py  # day-by-day inventory simulation
│   ├── load_sql.py            # executes .sql files with .env credentials
│   └── run_pipeline.py        # tables -> charts -> report -> Excel
├── tests/                     # 199 pytest tests
├── docs/                      # synopsis, DB design, API reference, methodology,
│                              # test report, demo plan, limitations
└── reports/                   # generated artefacts (charts, tables, report)
```

---

## 4. The ten-minute tour of the API

| # | Call | What the examiner sees |
|---|---|---|
| 1 | `GET /api/health` | service + database + row counts |
| 2 | `GET /api/products?limit=5` | paginated master data |
| 3 | `POST /api/purchase-orders` | validation (dates, quantities, foreign keys) |
| 4 | `POST /api/purchase-orders/{id}/receive` | PO + ledger updated in one transaction |
| 5 | `GET /api/analytics/reorder-recommendations` | ROP, safety stock, flags, suggested quantity |
| 6 | `GET /api/analytics/suppliers` | on-time rate, fill rate, lead-time CV, grade |
| 7 | `GET /api/analytics/risks` | shortage and excess lists with values |
| 8 | `GET /api/analytics/forecast?product_id=1` | forecast + hold-out metrics vs baseline |
| 9 | `POST /api/analytics/run` | stores an auditable snapshot (creates **no** orders) |
| 10 | `GET /api/charts` | chart catalogue with interpretations; PNGs at `/api/charts/<file>` |

Full request/response examples: [`docs/03_api_reference.md`](docs/03_api_reference.md).

---

## 5. Configuration

Everything is environment-driven (see `.env.example` and `config.py`):

| Variable | Default | Meaning |
|---|---|---|
| `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME` | localhost / 3306 / — / — / supplychain | database connection |
| `WINDOW_DAYS` | 90 | rolling demand window (calendar days) |
| `SERVICE_LEVEL` | 0.95 | target cycle service level → z factor |
| `SAFETY_STOCK_POLICY` | `max` | `zscore`, `days` or `max` of both |
| `SAFETY_STOCK_DAYS` | 7 | safety stock as days of average demand |
| `REVIEW_PERIOD_DAYS` | 7 | order-up-to level = ROP + μ·review period |
| `EXCESS_COVER_DAYS` | 90 | days of cover above which stock is "excess" |
| `FORECAST_HORIZON_DAYS` | 28 | forward forecast length |
| `FORECAST_HOLDOUT_DAYS` | 28 | hold-out period used for scoring |
| `SUPPLIER_WINDOW_DAYS` | 365 | trailing PO window for supplier KPIs |

Every analytics endpoint accepts the analysis parameters as query string overrides,
e.g. `/api/analytics/reorder-recommendations?service_level=0.99&window_days=120`.

---

## 6. Documented assumptions (summary)

Full detail in [`docs/04_analysis_methodology.md`](docs/04_analysis_methodology.md).

1. Days with no demand row = **zero demand** (calendar-based averages).
2. Lead time is in **calendar days**; daily demand is assumed independent (`√L`).
3. Observed lead time replaces master data after **3 deliveries**.
4. Safety stock combines a **z-score** and a **days-of-demand** rule (default: max).
5. Inventory position = on-hand + open-PO quantity (open POs are trusted).
6. Risk labels are **threshold rules**, configurable in `.env`.
7. Forecasts are univariate and evaluated on a single hold-out period.
8. The data is **synthetic** (simulated demand + replenishment cycle) — no real
   company, customer or personal data is used.
9. Unit costs are in **INR**; only `unit_cost` is modelled (no ordering/holding costs).

---

## 7. Limitations

* Univariate forecasts; no promotions, pricing, weather or lead-time regressors.
* Safety stock ignores lead-time variability (`σ_L` extension listed as future work).
* Suggested quantity is an order-up-to level, not an EOQ/MOQ-constrained quantity.
* Open POs are trusted; a stalled open order overstates the inventory position.
* Single-warehouse model: no multi-location allocation or transfer logic.

See [`docs/07_limitations_and_future_work.md`](docs/07_limitations_and_future_work.md).

---

## 8. Deliverables map

| Deliverable | Where |
|---|---|
| D1 Proposal & requirements | `docs/01_synopsis_and_proposal.md` |
| D2 Database design (ER, schema, data dictionary) | `docs/02_database_design.md`, `sql/schema.sql` |
| D3 Flask backend | `api/`, `run.py`, `docs/03_api_reference.md` |
| D4 Data analysis | `analytics/`, `docs/04_analysis_methodology.md`, `reports/tables/` |
| D5 Visualisations | `viz/charts.py`, `reports/charts/` |
| D6 Testing | `tests/`, `docs/05_test_report.md` |
| D7 Documentation | this README, `docs/`, `reports/analysis_report.md` |
| D8 Final demonstration | `docs/06_demo_plan.md`, `presentation/Project24_Demo.pptx` |

---

## 9. Academic-integrity and data notes

* All data is **synthetic**, produced by `scripts/generate_seed_data.py`; product
  names and supplier names are fictional and `.example.com` addresses are reserved
  for documentation.
* No passwords, personal, banking or medical data are stored. Credentials live in
  `.env` only.
* Outputs are labelled **decision support**; they are not professional supply-chain
  advice and no order is ever placed automatically.
