# D1 — Project Synopsis and Proposal

**Project 24 · Supply Chain Optimization and Inventory Forecasting System**
Difficulty: Advanced · Core stack: Python, Flask, MySQL, Pandas, NumPy, Matplotlib
Optional: statsmodels (Holt-Winters forecasting)

---

## 1. Title

Design and implementation of a supply chain analytics and inventory forecasting
system that computes reorder points from documented assumptions and flags stockout
and excess-stock risk as decision support.

---

## 2. Problem statement

Small and mid-sized distributors plan replenishment in spreadsheets. Three problems
follow:

1. **Reorder points are stale.** A single "minimum level" is typed once and never
   recalculated, so a product whose demand doubled last quarter still reorders at the
   old quantity, while a declining product keeps piling up stock.
2. **Supplier performance is anecdotal.** Late deliveries are remembered, not
   measured. Because the *variability* of lead time (not just its average) drives the
   safety stock you need, unmeasured variability shows up as either stockouts or
   excess inventory.
3. **There is no audit trail.** When someone asks "why was 300 units ordered?", the
   answer is in a deleted spreadsheet tab.

The project builds a small, auditable system that answers three questions with
reproducible arithmetic: **what is the reorder point now, which suppliers cause the
risk, and which SKUs are about to run out or are holding too much cash?**

---

## 3. Objectives

| # | Objective | Evidence in the submission |
|---|---|---|
| O1 | Model products, suppliers, purchase orders, stock movements and demand in a normalised MySQL schema with keys and constraints | `sql/schema.sql`, `docs/02_database_design.md` |
| O2 | Expose the data through validated REST APIs with correct HTTP semantics and meaningful error messages | `api/`, 199 tests, `docs/03_api_reference.md` |
| O3 | Compute lead-time and supplier delivery summaries (on-time rate, fill rate, lead-time CV, composite score) | `analytics/supplier.py`, `/api/analytics/suppliers` |
| O4 | Estimate average demand, safety stock and reorder points from **documented, configurable** assumptions | `analytics/formulas.py`, `docs/04_analysis_methodology.md` |
| O5 | Compare historical demand with a simple forecast and **evaluate it against a baseline** | `analytics/forecast.py`, chart 07, §7 of the report |
| O6 | Flag stockout and excess-stock risk with explicit thresholds | `analytics/inventory.py::classify`, `/api/analytics/risks` |
| O7 | Produce a reorder recommendation report, supplier summary and demand trends as tables and charts | `reports/` |
| O8 | Prove the system behaves correctly on invalid input, empty data and database failure | `tests/`, `docs/05_test_report.md` |

**Non-objective (explicit boundary).** The system does **not** place, approve or
transmit purchase orders. It produces ranked, explained suggestions; procurement
stays a human action. This boundary is enforced by design (no write endpoint is
called by any analytics code) and tested (`test_run_stores_snapshot_without_creating_orders`).

---

## 4. Scope

### In scope

* Single-warehouse inventory ledger (units in, units out, adjustments).
* Product master with category, unit cost, planned lead time, manual reorder point.
* Supplier master with promised lead time.
* Single-line purchase orders (order → expected → received, with partial receipts).
* Daily demand records.
* Descriptive statistics, ABC analysis, reorder-point calculation under three
  safety-stock policies, risk classification, turnover, supplier KPIs.
* Four forecasting methods with hold-out evaluation, including a naive baseline.
* REST API, generated charts, generated Markdown/CSV/Excel reports.

### Out of scope

* Multi-warehouse or multi-echelon (supplier → DC → store) allocation.
* Automatic order submission to any supplier/ERP.
* Pricing, promotions, market or weather data.
* User authentication/authorisation (single-analyst tool; stated as future work).
* Real-time streaming (batch analysis on demand).
* Mobile/web front end (the project is backend-focused; charts and reports are the UI).

---

## 5. Users / roles

| Role | Needs | Interacts through |
|---|---|---|
| **Inventory planner** (primary) | Which SKUs need action now, how much to order, why | `GET /api/analytics/reorder-recommendations`, `/api/analytics/risks`, report |
| **Procurement / buyer** | Which suppliers are late and by how much | `GET /api/analytics/suppliers`, chart 04 and 05 |
| **Supply-chain manager** | Portfolio-level risk, value at stake, trends | `GET /api/analytics/dashboard`, report §1 |
| **Analyst / student** | Reproduce and audit the numbers | `scripts/run_pipeline.py`, `analysis_runs` table |
| **System** (integrator) | Write data, validate it, receive errors | CRUD endpoints, `POST /api/analytics/run` |

No authentication layer: the API is intended to run on a trusted machine or behind
an existing gateway (see limitations).

---

## 6. Features (mapped to suggested scope)

| Suggested feature | Implemented as |
|---|---|
| REST APIs for products, suppliers, purchase orders, stock movements | `api/routes_crud.py` — full CRUD + `POST /purchase-orders/{id}/receive` (PO and ledger in one transaction) |
| Calculate lead-time and supplier delivery summaries | `analytics/supplier.py`: actual lead time, delay, on-time rate, fill rate, lead-time CV, P90, composite score, A–D grade |
| Estimate average demand and reorder point using lead time and configurable safety stock | `analytics/formulas.py` + `analytics/inventory.py`; parameters via `.env` or query string |
| Compare historical demand with a simple forecast | `analytics/forecast.py`: naive mean vs seasonal naive vs moving average vs Holt-Winters, 28-day hold-out |
| Flag potential stockout and excess-stock risks | `classify()` rules + `/api/analytics/risks` + charts 03 and 09 |
| *(extra)* ABC/Pareto analysis | `demand.py::demand_stats`, chart 06 |
| *(extra)* Inventory turnover | `inventory.py::stock_turnover_table`, chart 10 |
| *(extra)* Audit trail of runs | `analysis_runs`, `reorder_recommendations`, `POST /api/analytics/run` |
| *(extra)* Parameter-sensitivity grid | `scripts/run_pipeline.py::sensitivity_table`, report §7.1 |

---

## 7. Assumptions (business and analytical)

Business/data assumptions:

* **A1.** One warehouse; a product is bought from exactly one supplier.
* **A2.** Demand is recorded per product per day; **a missing row means zero demand**.
* **A3.** Purchase orders have one product line each (documented simplification of a
  `purchase_order_lines` design).
* **A4.** `on_hand` is derived from the transaction ledger, never stored as a mutable
  balance, so it can always be reconstructed and audited.
* **A5.** Open POs (`OPEN`, `PARTIAL`) are included in the inventory position and are
  assumed to arrive.
* **A6.** Lead times are **calendar** days (weekends included).

Analytical assumptions (all configurable, all documented in
`docs/04_analysis_methodology.md`):

* **A7.** Daily demand is independent between days → σ over L days = σ_d·√L.
* **A8.** After 3 delivered POs the mean observed lead time replaces master data.
* **A9.** Safety stock = max(z-score policy, days-of-demand policy) by default.
* **A10.** Suggested order quantity is an order-up-to level, not an EOQ.
* **A11.** A 90-day demand window and a 365-day PO window represent "current"
  behaviour; both are parameters.
* **A12.** Forecast selection is only allowed to beat the naive baseline by more than
  5% before switching — otherwise the simpler model wins.

---

## 8. Data

| Aspect | Decision |
|---|---|
| Source | **Synthetic** dataset generated by `scripts/generate_seed_data.py` (no real, personal or institution-confidential data) |
| Volume | 24 products, 6 suppliers, 377 purchase orders, ~11.4k stock movements, ~12.4k demand rows, 540 days of history |
| Why simulated | Demand, POs and the ledger must be mutually consistent (an ISSUE only happens if stock exists; a RECEIPT only happens when a PO arrives) — random rows cannot guarantee that |
| Edge cases planted | discontinued SKU with dead stock, SKU with no deliveries for 70 days, brand-new SKU with short history, lumpy/intermittent demand, missing zero-demand rows, inconsistent supplier, supplier with no deliveries, late/partial/cancelled/overdue orders |
| Currency / units | INR for money, units for quantity, calendar days for time |

---

## 9. Deliverable plan and timeline

Week 1 (first review) — **complete**

* Synopsis, objectives, scope, difficulty ✅ (this document)
* Team schema/ER diagram, SQL script, sample dataset, data dictionary ✅
* Application structure, connection configuration, health check, initial routes ✅
* CRUD/API operations, validation, error handling, test evidence for core workflows ✅
* Role division proposal ✅ (see §10)

Week 2 (final submission) — **complete**

* Pandas/NumPy analysis scripts, formulas, summary tables ✅
* Matplotlib visualisations + interpretations + integrated end-to-end run ✅
* Test report, README, API documentation, final synopsis, demonstration plan ✅
* Working demonstration, final report, source code, SQL file, charts, presentation ✅

---

## 10. Work division (5 members — adjust names to your team)

| Member | Responsibility | Files |
|---|---|---|
| M1 — Data & database | Schema, constraints, synthetic generator, SQL loading, data dictionary | `sql/`, `scripts/generate_seed_data.py`, `docs/02_database_design.md` |
| M2 — Backend/API | Flask app factory, CRUD routes, validators, error envelope, health/config | `api/`, `run.py`, `db.py`, `config.py` |
| M3 — Analytics | Reorder engine, supplier KPIs, ABC, turnover, formulas documentation | `analytics/`, `docs/04_analysis_methodology.md` |
| M4 — Forecasting & visuals | Forecast models, hold-out evaluation, all Matplotlib charts | `analytics/forecast.py`, `viz/` |
| M5 — QA & documentation | Test suite, test report, README, demo script, presentation | `tests/`, `docs/05`, `docs/06`, `presentation/` |

Everyone: end-to-end review, demo rehearsal, Q&A preparation.

---

## 11. Success criteria (self-assessment)

| Acceptance criterion (from the brief) | How this submission satisfies it |
|---|---|
| Formula assumptions and units are documented | `docs/04_analysis_methodology.md` + formula block printed in every report and in `/api/analytics/reorder-recommendations` |
| Forecasts are evaluated against a baseline | 28-day hold-out; naive-mean baseline; MAE/RMSE/MAPE/MASE per method; chart 07; §7.1 sensitivity |
| Suggestions do not automatically place orders | No write path from analytics; tested; disclaimer in API, report and charts |
| MySQL entities with keys/constraints | `sql/schema.sql` (PK, FK, CHECK, UNIQUE, indexes, 2 views) |
| Pandas/NumPy analysis with explained formulas | `analytics/*`, §3–§6 of the generated report |
| At least three meaningful charts with title/axes/units/interpretation | 10 charts, each with units on both axes, legend and a written interpretation returned by the API too |
| Testing: normal, invalid, empty, boundary, DB errors | 199 tests, 5 groups, evidence in `docs/05_test_report.md` |
| README + synopsis + API list + schema + methodology + limitations | README + `docs/` |
