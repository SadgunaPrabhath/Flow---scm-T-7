# D8 — Final Demonstration Plan (8–12 minutes)

Target length: **11 minutes** + Q&A. Every step has a prepared command that works on a
clean machine after the setup in `README.md` §2.

---

## 0. Before you enter the room (T-10 minutes checklist)

```bash
# 1. database up?
sudo service mariadb status          # or: netstat -an | grep 3306

# 2. environment file present?
cat .env | grep -v PASSWORD          # never show the password on screen

# 3. data loaded?
python - <<'PY'
from db import db
print(db.fetch_one("SELECT (SELECT COUNT(*) FROM products) p, (SELECT COUNT(*) FROM demand_records) d, (SELECT COUNT(*) FROM purchase_orders) po"))
PY

# 4. charts/report already generated (so nothing must be built live)
ls reports/charts reports/tables

# 5. API running in a second terminal
python run.py

# 6. tests green (optional, 1 minute)
python -m pytest tests -q
```

If the venue database is unavailable, start the API with `DB_PORT=65001 python run.py`
and demonstrate instead: (a) `/api/health` returning **503 degraded**, (b) the
pre-generated charts, report and test results. This is a feature, not an excuse.

---

## 1. Run sheet

| Time | Section | What you show | What you say |
|---|---|---|---|
| 0:00–0:40 | **Problem** | Title slide | "Reorder points live in spreadsheets: stale, unmeasured suppliers, no audit trail. We built a system that computes them from documented assumptions and flags risk — as decision support, never as automatic ordering." |
| 0:40–1:40 | **Architecture** | Slide 3 | "MySQL 6 tables + 2 views; Flask REST API with validation and a uniform error envelope; pandas/NumPy analysis; Matplotlib charts; statsmodels for Holt-Winters. Credentials only in `.env`." |
| 1:40–2:40 | **Database** | `sql/schema.sql` + ER slide + `mysql` prompt | Show one table with PK/FK/CHECK; run `SHOW CREATE TABLE purchase_orders\G`; point at CHECK constraints and indexes. |
| 2:40–4:00 | **APIs** | Terminal 1 | `GET /api/health` → products list → `POST /api/purchase-orders` with a **deliberately invalid** body (422 + field detail) → `POST /purchase-orders/{id}/receive` (PO + ledger in one transaction). |
| 4:00–5:20 | **Reorder engine** | `GET /api/analytics/reorder-recommendations?limit=5` + chart 03 | Explain μ_d, L, SS (z·σ·√L), ROP = μ·L + SS, IP = on hand + on order. Point at `lead_time_source` and `gap_vs_master_rop`. |
| 5:20–6:30 | **Supplier performance** | `GET /api/analytics/suppliers` + charts 04 & 05 | "On-time rate, fill rate, lead-time CV, score, grade. Variability is what forces safety stock — Krishna Plastics is 43 % on time with CV 0.33." |
| 6:30–7:40 | **Shortage / excess flags** | `GET /api/analytics/risks` + chart 09 | Name the actual SKUs: 2 stockouts, 1 at risk, 4 excess. Convert units to INR value at stake. |
| 7:40–9:10 | **Forecasting + baseline** | `GET /api/analytics/forecast?product_id=1` + chart 07 | "Four methods, 28-day hold-out, MAE/RMSE/MAPE/MASE. Selected models average 5.86 units/day error vs 7.81 for the naive baseline; the baseline is kept whenever the gain is under 5 %." |
| 9:10–10:10 | **Reports** | `reports/analysis_report.md` + `tables/analysis_workbook.xlsx` | Generated, reproducible, ends with assumptions and limitations. Mention `POST /api/analytics/run` as the audit trail and that it creates **no orders**. |
| 10:10–11:00 | **Testing + wrap-up** | `pytest -q` output + test report slide | "199 tests: formulas, validation, empty data, boundaries, database-down. Known limitations and future work are documented." |

---

## 2. Live commands (copy-paste ready)

```bash
# A. health and configuration
curl -s localhost:5000/api/health | python -m json.tool
curl -s localhost:5000/api/config | python -m json.tool | head -30

# B. validation catches a bad order (dates reversed, quantity 0)
curl -s -X POST localhost:5000/api/purchase-orders \
  -H 'Content-Type: application/json' \
  -d '{"supplier_id":1,"product_id":1,"quantity":0,
       "order_date":"2026-10-10","expected_date":"2026-10-01"}' | python -m json.tool

# C. a valid order, then a two-step goods receipt (PO + ledger in one transaction)
curl -s -X POST localhost:5000/api/purchase-orders \
  -H 'Content-Type: application/json' \
  -d '{"supplier_id":1,"product_id":1,"quantity":40,
       "order_date":"2026-10-01","expected_date":"2026-10-12"}' | python -m json.tool
curl -s -X POST localhost:5000/api/purchase-orders/<ID>/receive \
  -H 'Content-Type: application/json' -d '{"received_qty":15}' | python -m json.tool

# D. the analysis
curl -s "localhost:5000/api/analytics/reorder-recommendations?flag=STOCKOUT" | python -m json.tool
curl -s localhost:5000/api/analytics/suppliers | python -m json.tool | head -60
curl -s localhost:5000/api/analytics/risks | python -m json.tool | head -60
curl -s "localhost:5000/api/analytics/forecast?product_id=1" | python -m json.tool | head -50

# E. persistence + audit ("stored, not ordered")
curl -s -X POST localhost:5000/api/analytics/run | python -m json.tool
curl -s localhost:5000/api/analytics/runs | python -m json.tool

# F. charts over HTTP
curl -s localhost:5000/api/charts | python -m json.tool | head -30
```

---

## 3. Screens to have open before you start

1. Terminal A — API running (`python run.py`), response visible.
2. Terminal B — ready to paste the `curl` commands.
3. File manager / editor with `reports/charts/` thumbnails visible.
4. `reports/analysis_report.md` rendered (charts inline).
5. `docs/05_test_report.md` (test evidence) and `sql/schema.sql`.

---

## 4. Plain-language explanations (use these exact lines if nervous)

* **Reorder point** — "The stock level at which we must order again so that the
  delivery lands before we run out. It is average daily demand × lead time, plus a
  safety buffer for the days demand is higher than average."
* **Safety stock (z-score)** — "z × σ × √L: the buffer grows with how unpredictable
  demand is, and with the square root of how long the supplier takes."
* **Inventory position** — "On-hand plus what is already on order. On-hand alone
  would order again for goods already in transit."
* **Days of cover** — "How many days the stock on hand would last at current demand;
  more than 90 days means cash is sitting in the warehouse."
* **Baseline** — "The dumbest forecast: repeat the last 28 days' average. A model is
  only adopted if it beats that by more than 5 % on data it has never seen."
* **Decision support** — "The system does not order. It ranks and explains; the buyer
  decides."

---

## 5. Anticipated questions (with answers)

| Question | Answer |
|---|---|
| Why not store `on_hand` in `products`? | A stored balance drifts from history. The signed ledger is the single source of truth and can be reconciled at any date. |
| Why is `sqrt(L)` in the safety-stock formula? | If daily demand is independent, variances add: σ² over L days = L·σ_d², hence σ·√L. It is an assumption (A7) — with correlated demand you would need a different model. |
| Why is the manual `reorder_point` column still there? | To make the correction visible: `gap_vs_master_rop` shows how far the planner's value is from the computed one, without destroying the original. |
| Why keep the naive baseline at all? | To be honest about added complexity. On 15 of 24 SKUs a model wins; on the rest the baseline is kept, and the report says so. |
| What happens if a supplier never delivers an open PO? | The open quantity still counts in the inventory position (assumption A5), so risk is *understated* — it is listed as a limitation, and one SKU in the data has exactly this case. |
| How do you know the tests are meaningful? | The suite injects real failure modes: dead database port, FK violations, negative stock, empty datasets, boundary parameters, plus a regression test that the analysis never creates a purchase order. |
| Where do credentials live? | `.env`, excluded from version control; `/api/config` exposes only non-secret parameters. |
| How would you scale this? | Materialised daily snapshots, incremental demand aggregation, caching, and moving the forecast to a job queue; multi-warehouse would need a location dimension on the ledger. |
| Why is your data synthetic? | No real company data was available; synthetic data lets us plant the edge cases (dead stock, stalled PO, lumpy demand) that make the analysis testable. |
| What would you do next? | Lead-time variability in safety stock (σ_L), rolling-origin backtest, EOQ/MOQ-constrained quantities, and a planner UI. |

---

## 6. Expo checklist (from the brief) — where each item is satisfied

| Requirement | Evidence |
|---|---|
| Source code, SQL schema/seed, README, final report submitted | repository + `sql/` + `README.md` + `docs/Project24_Final_Report.docx` |
| Application starts on the demo machine, connects with env credentials | `python run.py`; `/api/health` shows the connection |
| One end-to-end use case demonstrated | PO create → receive → ledger → inventory position → analytics (run sheet 2:40–5:20) |
| Charts and metrics explained in plain language incl. limitations | §4 above, charts carry written interpretations, report §9 |
| Team can answer schema, routes, cleaning, formulas, testing, work division | §5 above, `docs/02`, `docs/03`, `docs/04`, `docs/05`, `docs/01` §10 |
| Only synthetic/anonymised data, no secrets or personal data | `scripts/generate_seed_data.py`, `.example.com` addresses, `.env` excluded |

---

## 7. Rehearsal protocol

1. Dry run **twice** with a timer; cut from §1 middle rows if over 12 minutes.
2. Each member speaks for their part only (division of labour in `docs/01` §10).
3. One member drives the keyboard, one narrates; never debug live — if a command
   fails, switch to the pre-generated report and continue.
4. Print/keep the run sheet and the command list on paper.
