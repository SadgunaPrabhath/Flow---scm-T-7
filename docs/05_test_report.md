# D6 — Test Report

Test framework: **pytest 9.0.3**, Python 3.13, MariaDB 11.8.6.
Suite: [`tests/`](../tests) · run with `python -m pytest tests -q`.

---

## 1. Summary of results

| Run | Command | Result |
|---|---|---|
| Full suite (database available) | `python -m pytest tests` | **199 passed** in 55.9 s |
| Suite on a machine without MySQL | `DB_PORT=65001 python -m pytest tests` | **113 passed, 85 skipped** in 1.5 s (skips carry an explicit reason) |
| Formula unit tests only | `python -m pytest tests/test_formulas.py` | 42 passed |
| Validation unit tests only | `python -m pytest tests/test_validators.py` | 38 passed |
| Analysis unit tests only | `python -m pytest tests/test_analysis.py` | 33 passed |
| CRUD API tests only | `python -m pytest tests/test_api_crud.py` | 45 passed |
| Analytics API tests only | `python -m pytest tests/test_api_analytics.py` | 41 passed |

Raw console evidence:

```text
$ python -m pytest tests
........................................................................ [ 36%]
........................................................................ [ 72%]
......................................................                   [100%]
199 passed in 55.89s

$ DB_PORT=65001 python -m pytest tests
...sssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssssss
ssssssssssssssssssssssssssssssssssssssssssssssss..........................
......................................................                   [100%]
113 passed, 85 skipped in 1.45s

SKIPPED [1] tests/test_api_crud.py:221: MySQL not reachable (Cannot connect to the
  MySQL server. Check DB_HOST/DB_PORT/DB_USER/DB_PASSWORD in your .env file and that
  the server is running.) - integration tests skipped
```

---

## 2. Test strategy

| Level | What it proves | Files |
|---|---|---|
| **Unit — formulas** | the arithmetic is correct and matches hand calculations | `test_formulas.py` |
| **Unit — validation** | bad input never reaches SQL | `test_validators.py` |
| **Unit — analysis** | pandas/NumPy pipelines behave on clean, empty and pathological data | `test_analysis.py` |
| **Integration — CRUD** | endpoints, status codes, DB constraints, transaction behaviour | `test_api_crud.py` |
| **Integration — analytics** | parameter handling, boundaries, empty results, DB failure, "no orders placed" | `test_api_analytics.py` |

Design choices:

* **Deterministic.** No random seeds inside the tests; the forecast tests use a fixed
  `numpy.random.default_rng(3)` generator.
* **Non-destructive.** Integration tests create their own supplier/product/PO rows with
  unique suffixes and delete them in fixture teardown; no seeded row is modified.
  Deleting seeded data is asserted to be *refused* rather than performed.
* **Skippable.** Integration tests are marked with `requires_db`; when MySQL is
  unreachable they skip with the driver's message, so the pure-logic suite runs on any
  checker's machine.
* **State-restoring.** The `broken_app` fixture re-points the shared connection pool at
  a dead port and then restores the real configuration, so one failure-injection test
  cannot poison the rest of the session.

---

## 3. Coverage matrix (required categories)

### 3.1 Normal input

| ID | Case | Expected | Test |
|---|---|---|---|
| N1 | `GET /api/health` with DB up | 200, `connected: true`, row counts | `test_health_ok` |
| N2 | list with pagination | `count`, `total`, `limit`, `offset` correct | `test_list_paginates` |
| N3 | create → patch → delete product | 201 + Location, 200, 200, then 404 | `test_create_update_delete_roundtrip` |
| N4 | receive a PO in two parts | PO becomes PARTIAL then RECEIVED, ledger gets 2 RECEIPT rows | `test_create_and_receive_updates_ledger` |
| N5 | issue a stock transaction | 201 and `stock_balance` returned | `test_receipt_updates_balance` |
| N6 | bulk demand upload | 201, `inserted: 5` | `test_bulk_upload` |
| N7 | reorder recommendations | all four flags appear; flags partition the portfolio | `test_flags_partition_the_portfolio` |
| N8 | supplier summary | on-time rate ∈ [0,1], score present, grade assigned | `test_summary` |
| N9 | forecast for one product | 28 rows, band contains the point forecast | `test_single_product` |
| N11 | explorer page + /api index | 200 text/html and JSON index | `test_explorer_page_served`, `test_api_index_lists_endpoints` |
| N10 | chart catalogue + PNG download | ≥ 3 charts, `image/png` magic bytes | `test_chart_catalogue`, `test_chart_png_served` |

### 3.2 Invalid input

| ID | Case | Expected | Test |
|---|---|---|---|
| I1 | missing required fields | 422 + per-field details | `test_missing_required_fields`, `test_create_missing_fields` |
| I2 | unknown field in body | 422 (`hacker`) | `test_unknown_field_rejected` |
| I3 | wrong content type | 415 | `test_wrong_content_type` |
| I4 | malformed JSON | 400 `INVALID_JSON` | `test_malformed_json` |
| I5 | empty body | 400 `EMPTY_BODY` | `test_empty_body_rejected` |
| I6 | duplicate SKU | 409 `DUPLICATE_RECORD` | `test_create_duplicate_sku_conflict` |
| I7 | unknown FK (supplier 99999) | 409 before touching SQL | `test_create_with_unknown_supplier` |
| I8 | non-numeric id in path | 404 | `test_get_invalid_id_type` |
| I9 | missing resource | 404 `NOT_FOUND` | `test_get_missing_returns_404`, `test_get_missing_po` |
| I10 | bad email, bad SKU, bad date, bad enum | 422 each | `test_invalid_email`, `test_as_sku`, `test_as_date`, `test_invalid_status` |
| I11 | ISSUE with positive quantity | 422 | `test_issue_sign_enforced` |
| I12 | zero quantity movement | 422 | `test_zero_quantity_rejected` |
| I13 | received_qty > quantity | 422 | `test_receive_more_than_ordered` |
| I14 | expected_date before order_date | 422 | `test_expected_date_before_order_date` |
| I15 | invalid analytics flag | 422 | `test_invalid_flag` |
| I16 | bad rows in a bulk batch | 422, nothing inserted, row index reported | `test_bulk_upload_rejects_bad_rows` |
| I17 | path traversal on reports | 400/404 | `test_report_path_traversal_blocked` |
| I18 | delete parent with children | 409 `RECORD_IN_USE` | `test_delete_product_with_history_conflict`, `test_delete_supplier_in_use` |
| I19 | stock movement that would go negative | 409 | `test_negative_balance_blocked` |

### 3.3 Empty datasets

| ID | Case | Expected | Test |
|---|---|---|---|
| E1 | no demand rows for any product | zero-filled series, stats with mean 0, no exception | `test_empty_demand_input`, `test_stats_empty_input` |
| E2 | no products at all | empty recommendation frame **with the expected columns** | `test_empty_products_returns_empty_frame` |
| E3 | no purchase orders | empty summary when no suppliers given; `NO_DATA` grade when suppliers given | `test_empty_purchase_orders` |
| E4 | no ledger history | turnover row with no turns instead of a crash | `test_empty_ledger_is_safe` |
| E5 | demand series for a product outside the window | 404 with a message | `test_series_unknown_product` |
| E6 | reorder query for a product with no data | `count: 0`, `data: []` (not an error) | `test_unknown_product_gives_empty_result_not_error` |
| E7 | supplier without deliveries asked for its POs | 404 | `test_supplier_without_orders_returns_404` |
| E8 | bulk upload of an empty list | 400 `EMPTY_BATCH` | `test_bulk_upload_empty` |

### 3.4 Boundary values

| ID | Case | Expected | Test |
|---|---|---|---|
| B1 | `window_days` 14 (min) / 730 (max) | accepted | `test_boundary_parameters_accepted` |
| B2 | `window_days` 5 / 5000 | 422 | `test_boundary_parameters_rejected` |
| B3 | `service_level` 0.5 / 0.999 | accepted | `test_boundary_parameters_accepted` |
| B4 | `service_level` 0 / 1.5 | 422 | `test_boundary_parameters_rejected` |
| B5 | `safety_stock_days` 0 / −1 | 0 accepted, −1 rejected | `test_boundary_parameters_*` |
| B6 | `review_period_days` 0 / 200 | 0 accepted, 200 rejected | `test_boundary_parameters_*` |
| B7 | `horizon` 1 / 180 / 0 / 500 | 1 and 180 accepted, 0 and 500 rejected | `test_boundary_parameters_*` |
| B8 | pagination `limit=1`, `limit=500`, `limit=0`, `limit=999` | first two accepted, last two 422 | `test_invalid_limit_rejected`, `test_limit_above_max_rejected` |
| B9 | `lead_time_days` 1 / 180 / 181 | 1 and 180 accepted, 181 rejected | `test_lead_time_upper_boundary` |
| B10 | `supplier_id` = 0 | 422 | `test_supplier_id_boundary` |
| B11 | `unit_cost` = −1 / 0.01 | −1 rejected, minimum accepted | `test_negative_cost_rejected`, `test_as_decimal` |
| B12 | z-factor at 0.50 / 0.99 / invalid 0 or 1 | 0.0 / 2.3263 / `ValueError` | `test_z_values`, `test_rejects_out_of_range` |
| B13 | safety stock with σ=0, L=0 | 0 (no division by zero) | `test_zero_variability_gives_zero`, `test_zero_lead_time_gives_zero` |
| B14 | forecast with 3 observations | falls back to naive, still returns a horizon | `test_short_history_falls_back_to_naive` |
| B15 | PO with `received_qty` exactly equal to `quantity` | accepted, status RECEIVED | `test_create_and_receive_updates_ledger` |

### 3.5 Database errors / failure injection

| ID | Case | Expected | Test |
|---|---|---|---|
| D1 | health check with MySQL down (dead port 65001) | 503, `connected: false`, `status: degraded` | `test_health_reports_degraded_when_db_down` |
| D2 | GET on a CRUD route with MySQL down | 503 `DATABASE_UNAVAILABLE` | `test_crud_returns_503_when_db_down` |
| D3 | analytics route with MySQL down | 503 (cache cleared first) | `test_analytics_returns_503_when_db_down` |
| D4 | POST with MySQL down | 503, never a 500 traceback | `test_post_returns_503_when_db_down` |
| D5 | FK violation attempted directly in SQL | mapped to 409 `CONFLICT` | `test_create_with_unknown_supplier` (API pre-check) + `db.DatabaseError.is_referenced` |
| D6 | CHECK-constraint violation | mapped to 422 `CONSTRAINT_VIOLATION` | `test_zero_quantity_rejected` (validator) + `_human_message()` |

### 3.6 Business / acceptance-criterion tests

| ID | Criterion | Test |
|---|---|---|
| A1 | **Suggestions never create purchase orders** | `test_run_stores_snapshot_without_creating_orders` (row count before == after) |
| A2 | Higher service level ⇒ more safety stock | `test_service_level_changes_safety_stock` |
| A3 | Selected model is never worse than the baseline by more than 5 % | `test_selected_model_is_not_worse_than_baseline` |
| A4 | Flags partition the portfolio (no SKU unclassified) | `test_flags_partition_the_portfolio` |
| A5 | Reorder point equals the hand calculation | `test_rop_equals_hand_calculation` |
| A6 | Open orders count towards the inventory position | `test_on_order_counts_towards_position` |
| A7 | Suggested quantity is zero unless flagged | `test_suggested_quantity_is_zero_unless_flagged` |
| A8 | No-demand SKU is never a stockout | `test_no_demand_product_is_not_stockout` |
| A9 | Turnover uses the daily average balance | `test_turnover_computation`, `test_daily_balances_use_the_whole_ledger` |
| A10 | Configuration endpoint never leaks credentials | `test_config_hides_credentials` |

---

## 4. Defects found and fixed during testing

| # | Defect found | Root cause | Fix |
|---|---|---|---|
| 1 | `GET /api/analytics/*` returned 500 | cache key built with `sorted()` over mixed tuple/str | key = `(params tuple, sorted args)` |
| 2 | Every analytics endpoint recomputed the portfolio (4 s each) | cache keyed by URL, not by parameter set | cache keyed on the analysis parameters |
| 3 | Decimal money values serialised as strings in JSON | default Flask JSON provider | custom `JSONProvider` converting `Decimal → float`, dates → ISO |
| 4 | Analytics answered from cache while the DB was down | cache hit before any DB access | test clears the cache first; documented behaviour |
| 5 | ABC class gave a single dominant SKU a "C" | cumulative share evaluated *after* the item | class from the share *before* the item |
| 6 | Turnover reported 4 810 turns/year for a lean SKU | average inventory as a 2-point (start/end) average | mean **daily** ledger balance; ledger loaded from the beginning of history |
| 7 | Charts written to `api/reports/...` depending on the working directory | relative `REPORT_DIR` | absolute path derived from the project root |
| 8 | `supplier_summary` crashed on a supplier with no delivered POs | `strftime` on `NaT` in the JSON converter | `NaT` handled before date formatting |
| 9 | DB-failure tests poisoned later tests | the shared pool was re-pointed and never restored | `broken_app` fixture restores the original config |
| 10 | `matplotlib` boxplot `labels=` deprecation | new matplotlib API | `tick_labels` with a `TypeError` fallback |

Each fix is covered by the test that exposed it; the full suite is green afterwards.

---

## 5. What is *not* tested (and why)

* **Concurrency / load.** The pool size is 5 and the app is single-analyst; no load
  test was in scope. `pool_reset_session` is enabled.
* **Browser/UI.** The project is backend-focused; charts and reports are the output.
* **Real supplier data.** All data is synthetic, so no test validates real-world
  demand distributions.
* **Model quality on other regimes.** The forecast is evaluated on one 28-day
  hold-out; a rolling-origin backtest is listed as future work.

---

## 6. Reproducing this report

```bash
python -m pytest tests -q            # 199 passed
python -m pytest tests -q -rs         # include skip reasons (DB down)
DB_PORT=65001 python -m pytest tests # prove the suite survives a dead database
```
