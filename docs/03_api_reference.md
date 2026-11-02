# D3 — API Reference

Base URL: `http://localhost:5000` (see `FLASK_HOST` / `FLASK_PORT` in `.env`).
All request and response bodies are JSON (`Content-Type: application/json`).
Every endpoint is read-only **except** the CRUD writes and `POST /api/analytics/run`.

**Decision support note:** no endpoint creates, approves or transmits a purchase
order. `/api/analytics/*` only computes and explains suggestions.

---

## 1. Conventions

### Success envelope

List endpoints:

```json
{"count": 5, "total": 24, "limit": 5, "offset": 0, "data": [ ... ]}
```

Single-resource endpoints: `{"data": { ... }}` (plus `message` for writes).

Pagination: `?limit=` (1–500, default 50) and `?offset=` (≥ 0).
Invalid values are rejected with `422`, never silently clamped.

### Error envelope

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "details": [{"field": "lead_time_days", "message": "Must be <= 180.", "value": "500"}]
  }
}
```

| HTTP | `code` | When |
|---|---|---|
| 400 | `EMPTY_BODY`, `INVALID_JSON`, `MISSING_PARAMETER`, `EMPTY_UPDATE`, `INVALID_PATH` | malformed request |
| 404 | `NOT_FOUND` | unknown id / chart / report |
| 409 | `DUPLICATE_RECORD`, `RECORD_IN_USE`, `CONFLICT` | unique key, FK parent with children, negative stock balance |
| 413 | `BATCH_TOO_LARGE` | bulk upload over 5 000 rows |
| 415 | `UNSUPPORTED_MEDIA_TYPE` | body is not JSON |
| 422 | `VALIDATION_ERROR`, `CONSTRAINT_VIOLATION` | field-level or DB CHECK failure |
| 500 | `INTERNAL_ERROR`, `DATABASE_ERROR` | unexpected fault (logged to `reports/logs/api.log`) |
| 503 | `DATABASE_UNAVAILABLE` | MySQL unreachable — the API stays up and reports it |

---

## 2. Service endpoints

### `GET /`

Service banner and endpoint map.

### `GET /api/health`

```json
{
  "database": "supplychain", "host": "127.0.0.1", "port": 3306,
  "connected": true, "server_version": "11.8.6-MariaDB",
  "status": "ok",
  "checks": {"database_connection": true,
             "reference_data": {"products": 24, "suppliers": 6, "...": "..."}}
}
```

Returns **503** with `"status": "degraded"` when the database is down.

### `GET /api/config`

Non-secret configuration and the documented analysis parameters. Never returns
`DB_PASSWORD`, `DB_USER` or `SECRET_KEY`.

---

## 3. Master data and transactions

### Products

| Method | Path | Notes |
|---|---|---|
| GET | `/api/products` | filters: `category`, `supplier_id`, `search`, `is_active` |
| GET | `/api/products/{id}` | adds `open_order_qty`, `last_demand_date` |
| POST | `/api/products` | 201 + `Location` header |
| PUT | `/api/products/{id}` | full replacement |
| PATCH | `/api/products/{id}` | partial update |
| DELETE | `/api/products/{id}` | 409 `RECORD_IN_USE` if history exists |

```bash
curl -X POST http://localhost:5000/api/products \
  -H 'Content-Type: application/json' \
  -d '{"sku":"auto-new-0001","product_name":"Test Bracket","category":"Braking",
       "unit_cost":349.99,"lead_time_days":10,"supplier_id":1,"reorder_point":40}'
```

```json
{"data": {"product_id": 25, "sku": "AUTO-NEW-0001", "reorder_point": 40, "...": "..."},
 "message": "Product created."}
```

Notes: `sku` is upper-cased and must match `[A-Z0-9][A-Z0-9-_/]{1,31}`;
`unit_cost > 0`; `1 ≤ lead_time_days ≤ 180`; an unknown `supplier_id` returns 409
before the database is touched.

### Suppliers

| Method | Path | Notes |
|---|---|---|
| GET | `/api/suppliers` | `search` on name/country |
| GET | `/api/suppliers/{id}` | adds `product_count`, `open_orders` |
| POST / PUT / PATCH / DELETE | `/api/suppliers[/{id}]` | same patterns as products |

### Purchase orders

| Method | Path | Notes |
|---|---|---|
| GET | `/api/purchase-orders` | filters: `status`, `supplier_id`, `product_id`, `overdue=true` |
| GET | `/api/purchase-orders/{id}` | adds `actual_lead_time_days`, `delay_days` |
| POST | `/api/purchase-orders` | `po_number` optional — generated when omitted |
| PUT / PATCH | `/api/purchase-orders/{id}` | business rules re-checked after merge |
| POST | `/api/purchase-orders/{id}/receive` | **goods receipt**: updates PO **and** writes a `RECEIPT` ledger row in one transaction |
| DELETE | `/api/purchase-orders/{id}` | 409 if goods were received |

Receipt example (partial, then remainder):

```bash
curl -X POST http://localhost:5000/api/purchase-orders/12/receive \
  -H 'Content-Type: application/json' -d '{"received_qty":15,"received_date":"2026-10-11"}'
# -> status PARTIAL, stock_transaction {quantity: 15}
curl -X POST http://localhost:5000/api/purchase-orders/12/receive -H 'Content-Type: application/json'
# -> status RECEIVED, received_qty = ordered quantity
```

Validation: `expected_date ≥ order_date`, `received_date ≥ order_date`,
`received_qty ≤ quantity`, `status` ∈ {OPEN, PARTIAL, RECEIVED, CANCELLED},
`RECEIVED` requires `received_date`, `OPEN`/`CANCELLED` must not have one.

### Stock transactions

| Method | Path | Notes |
|---|---|---|
| GET | `/api/stock-transactions` | filters: `product_id`, `transaction_type`, `start_date`, `end_date` |
| GET | `/api/stock-transactions/{id}` | |
| POST | `/api/stock-transactions` | sign rules enforced; 409 if the balance would go negative |

```bash
curl -X POST http://localhost:5000/api/stock-transactions \
  -H 'Content-Type: application/json' \
  -d '{"product_id":1,"transaction_date":"2026-10-19","transaction_type":"RECEIPT",
       "quantity":50,"reference":"GRN-1001"}'
# -> 201 {"data":{...},"stock_balance":231,...}
```

Sign convention: `RECEIPT`/`RETURN` must be positive, `ISSUE` must be negative,
`ADJUSTMENT` may be either; zero is always rejected.

### Demand records

| Method | Path | Notes |
|---|---|---|
| GET | `/api/demand-records` | filters: `product_id`, `start_date`, `end_date` |
| POST | `/api/demand-records` | upsert on (`product_id`,`demand_date`) |
| POST | `/api/demand-records/bulk` | `{"records":[...]}`, ≤ 5 000 rows, all-or-nothing |

### Inventory view

`GET /api/inventory` — ledger-derived `on_hand`, `on_order`, `inventory_position`,
`on_hand_value` per product. This is the raw position; the **analytics** live under
`/api/analytics/reorder-recommendations`.

---

## 4. Analytics endpoints

All analytics endpoints accept these query overrides (validated, 422 on bad values):

| Parameter | Default | Range |
|---|---|---|
| `window_days` | 90 | 14–730 |
| `service_level` | 0.95 | 0.5–0.999 |
| `safety_stock_days` | 7 | 0–90 |
| `safety_stock_policy` | `max` | `zscore` \| `days` \| `max` |
| `review_period_days` | 7 | 0–90 |
| `excess_cover_days` | 90 | 7–730 |
| `horizon` | 28 | 1–180 |
| `product_id` | – | ≥ 1 (single-SKU view) |

### `GET /api/analytics/reorder-recommendations`

Reorder points, safety stock and risk flags per SKU. Extra parameters:
`flag` = STOCKOUT | AT_RISK | EXCESS | OK, `limit`.

```json
{
  "generated_at": "2026-10-19",
  "parameters": {"window_days": 90, "service_level": 0.95, "safety_stock_days": 7.0,
                 "safety_stock_policy": "max", "review_period_days": 7.0,
                 "excess_cover_days": 90.0, "forecast_horizon_days": 28},
  "window": {"start": "2026-07-22", "end": "2026-10-19", "...": "..."},
  "summary": {"products": 24, "stockout": 2, "at_risk": 1, "excess": 4, "ok": 17,
              "inventory_value": 7151593.0, "suggested_order_value": 477900.0},
  "count": 2, "total": 2,
  "data": [{
      "sku": "ELEC-HAR-1120", "product_name": "Wiring Harness 24V",
      "supplier_name": "NorthStar Electronics",
      "avg_daily_demand": 2.844, "demand_std_dev": 2.06, "lead_time_used": 24.6,
      "lead_time_source": "observed_pos", "safety_stock": 19.91,
      "reorder_point_calc": 89.87, "reorder_point": 86, "gap_vs_master_rop": 3.87,
      "on_hand": 41.0, "on_order": 0.0, "inventory_position": 41.0,
      "days_of_cover": 14.42, "risk_flag": "STOCKOUT",
      "suggested_order_qty": 69.0, "order_value": 234600.0, "abc_class": "B"}],
  "formulas": {"reorder_point": "avg_daily_demand * lead_time_days + safety_stock", "...": "..."},
  "disclaimer": "Decision support only. No purchase order is created automatically."
}
```

### `GET /api/analytics/suppliers`

On-time rate, fill rate, observed lead time, lead-time CV, P90, delay statistics,
composite score and A–D grade per supplier, plus the KPI definitions.
`GET /api/analytics/suppliers/{id}/deliveries` lists the individual POs.

### `GET /api/analytics/demand`

Per-SKU demand statistics (mean, std dev, CV, totals, recent-vs-previous change,
trend slope, ABC class) plus `category_totals`.
`GET /api/analytics/demand/series?product_id=1` returns the zero-filled daily series.

### `GET /api/analytics/risks`

```json
{
  "thresholds": {"excess_cover_days": 90.0,
                 "stockout": "on_hand <= 0, or inventory_position < avg_daily_demand * lead_time",
                 "at_risk": "inventory_position < reorder_point (but covers lead-time demand)",
                 "excess": "inventory_position >= reorder_point and days_of_cover > excess_cover_days"},
  "stockout": {"count": 2, "value_at_stake_inr": 742800.0, "items": [ ... ]},
  "at_risk":  {"count": 1, "...": "..."},
  "excess":   {"count": 4, "value_at_stake_inr": 0.0, "items": [ ... ]}
}
```

### `GET /api/analytics/forecast`

All-SKU forecast summary + per-SKU hold-out metrics:

```json
{
  "horizon_days": 28, "holdout_days": 28,
  "methods": ["naive_mean","seasonal_naive","moving_average","holt_winters"],
  "baseline": "naive_mean (28-day mean of the training window)",
  "evaluation": {"products_evaluated": 24, "improved_over_baseline": 15,
                 "models_selected": {"holt_winters": 12, "naive_mean": 9, "...": 3},
                 "mean_mae_baseline": 7.809, "mean_mae_selected": 5.861,
                 "mean_improvement_pct": 12.03},
  "forecasts": [{"product_id": 1, "method": "holt_winters",
                 "avg_daily_forecast": 6.63, "total_forecast": 185.65,
                 "residual_std": 2.368}],
  "disclaimer": "Forecasts are statistical decision support, not commitments..."
}
```

With `?product_id=1` the response also contains `selected_method`,
`evaluation_detail` (all four methods' MAE/RMSE/MAPE/MASE/bias) and a 28-row
`forecast` array with an approximate 80% band (`lower_80`, `upper_80`).

### `POST /api/analytics/run`

Runs the analysis and **stores** the parameter set and recommendation snapshot
(`analysis_runs` + `reorder_recommendations`) for audit. Returns `201` with `run_id`.

> It stores what the planner was shown. It creates **no** purchase orders —
> asserted by `tests/test_api_analytics.py::test_run_stores_snapshot_without_creating_orders`.

### `GET /api/analytics/runs`

Last 50 stored runs with their parameters and recommendation counts.

### `GET /api/analytics/dashboard`

One call for the demo: portfolio summary, forecast summary, top risks, supplier
grades and the list of chart URLs.

---

## 5. Charts and reports

| Method | Path | Notes |
|---|---|---|
| GET | `/api/charts` | catalogue: key, file, URL and the written interpretation |
| GET | `/api/charts/{file}.png` | PNG image (`image/png`); built on demand if missing |
| GET | `/api/reports/{path}` | serves generated CSV/Markdown reports; path traversal blocked |

---

## 6. Quick verification script

```bash
BASE=http://localhost:5000
curl -s $BASE/api/health | python -m json.tool | head -20
curl -s "$BASE/api/analytics/reorder-recommendations?flag=STOCKOUT" | python -m json.tool
curl -s "$BASE/api/analytics/forecast?product_id=1" | python -m json.tool | head -40
curl -s -o chart03.png -w "%{http_code} %{content_type}\n" $BASE/api/charts/03_inventory_vs_rop.png
```
