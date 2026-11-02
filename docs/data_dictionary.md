# Data Dictionary

*Generated from `INFORMATION_SCHEMA` on 21 October 2026 — database `supplychain`. Regenerate with `python scripts/dump_data_dictionary.py`; do not edit by hand.*


## Tables

| Table | Rows | Description |
|---|---|---|

| `suppliers` | 6 | Vendor master data (name, country, promised lead time). |
| `products` | 24 | Item master data (SKU, cost, planned lead time, manual reorder point). |
| `purchase_orders` | 377 | Single-line purchase orders with expected and actual receipt dates. |
| `stock_transactions` | 11,359 | Signed perpetual inventory ledger (the only source of on-hand stock). |
| `demand_records` | 12,411 | Observed daily demand per product; a missing day means zero demand. |
| `analysis_runs` | 1 | Audit record of each explicit analysis run and its parameters. |
| `reorder_recommendations` | 24 | Snapshot of computed reorder points, risk flags and suggestions per run. |

## `suppliers`

| Column | Type | Null | Default | Key | Extra | Notes |
|---|---|---|---|---|---|---|
| `supplier_id` | `int(10) unsigned` | NO | — | PK | auto_increment |  |
| `supplier_name` | `varchar(120)` | NO | — | UNIQUE | — |  |
| `contact_email` | `varchar(160)` | YES | NULL |  | — |  |
| `country` | `varchar(60)` | NO | 'India' |  | — |  |
| `promised_lead_time_days` | `smallint(5) unsigned` | NO | — |  | — |  |
| `is_active` | `tinyint(1)` | NO | 1 |  | — |  |
| `created_at` | `timestamp` | NO | current_timestamp() |  | — |  |
| `updated_at` | `timestamp` | NO | current_timestamp() |  | on update current_timestamp() |  |

**Constraints**

| Name | Type | Column(s) | References |
|---|---|---|---|
| `chk_suppliers_email` | CHECK | — | ``contact_email` is null or `contact_email` like '%_@_%._%'` |
| `chk_suppliers_lead_time` | CHECK | — | ``promised_lead_time_days` between 1 and 180` |
| `PRIMARY` | PRIMARY KEY | `supplier_id` | — |
| `uq_suppliers_name` | UNIQUE | `supplier_name` | — |

## `products`

| Column | Type | Null | Default | Key | Extra | Notes |
|---|---|---|---|---|---|---|
| `product_id` | `int(10) unsigned` | NO | — | PK | auto_increment |  |
| `sku` | `varchar(32)` | NO | — | UNIQUE | — |  |
| `product_name` | `varchar(150)` | NO | — |  | — |  |
| `category` | `varchar(60)` | NO | — | INDEX | — |  |
| `unit_cost` | `decimal(12,2)` | NO | — |  | — |  |
| `lead_time_days` | `smallint(5) unsigned` | NO | — |  | — |  |
| `reorder_point` | `int(10) unsigned` | NO | 0 |  | — |  |
| `supplier_id` | `int(10) unsigned` | NO | — | INDEX | — |  |
| `is_active` | `tinyint(1)` | NO | 1 |  | — |  |
| `created_at` | `timestamp` | NO | current_timestamp() |  | — |  |
| `updated_at` | `timestamp` | NO | current_timestamp() |  | on update current_timestamp() |  |

**Constraints**

| Name | Type | Column(s) | References |
|---|---|---|---|
| `chk_products_lead_time` | CHECK | — | ``lead_time_days` between 1 and 180` |
| `chk_products_unit_cost` | CHECK | — | ``unit_cost` > 0` |
| `fk_products_supplier` | FOREIGN KEY | `supplier_id` | `suppliers(supplier_id)` |
| `PRIMARY` | PRIMARY KEY | `product_id` | — |
| `uq_products_sku` | UNIQUE | `sku` | — |

## `purchase_orders`

| Column | Type | Null | Default | Key | Extra | Notes |
|---|---|---|---|---|---|---|
| `po_id` | `int(10) unsigned` | NO | — | PK | auto_increment |  |
| `po_number` | `varchar(24)` | NO | — | UNIQUE | — |  |
| `supplier_id` | `int(10) unsigned` | NO | — | INDEX | — |  |
| `product_id` | `int(10) unsigned` | NO | — | INDEX | — |  |
| `quantity` | `int(10) unsigned` | NO | — |  | — |  |
| `received_qty` | `int(10) unsigned` | YES | NULL |  | — |  |
| `order_date` | `date` | NO | — |  | — |  |
| `expected_date` | `date` | NO | — |  | — |  |
| `received_date` | `date` | YES | NULL |  | — |  |
| `status` | `enum('OPEN','PARTIAL','RECEIVED','CANCELLED')` | NO | 'OPEN' | INDEX | — |  |
| `notes` | `varchar(255)` | YES | NULL |  | — |  |
| `created_at` | `timestamp` | NO | current_timestamp() |  | — |  |
| `updated_at` | `timestamp` | NO | current_timestamp() |  | on update current_timestamp() |  |

**Constraints**

| Name | Type | Column(s) | References |
|---|---|---|---|
| `chk_po_dates` | CHECK | — | ``expected_date` >= `order_date`` |
| `chk_po_qty` | CHECK | — | ``quantity` > 0` |
| `chk_po_received_date` | CHECK | — | ``received_date` is null or `received_date` >= `order_date`` |
| `chk_po_received_qty` | CHECK | — | ``received_qty` is null or `received_qty` <= `quantity`` |
| `fk_po_product` | FOREIGN KEY | `product_id` | `products(product_id)` |
| `fk_po_supplier` | FOREIGN KEY | `supplier_id` | `suppliers(supplier_id)` |
| `PRIMARY` | PRIMARY KEY | `po_id` | — |
| `uq_po_number` | UNIQUE | `po_number` | — |

## `stock_transactions`

| Column | Type | Null | Default | Key | Extra | Notes |
|---|---|---|---|---|---|---|
| `transaction_id` | `bigint(20) unsigned` | NO | — | PK | auto_increment |  |
| `product_id` | `int(10) unsigned` | NO | — | INDEX | — |  |
| `transaction_date` | `date` | NO | — |  | — |  |
| `transaction_type` | `enum('RECEIPT','ISSUE','ADJUSTMENT','RETURN')` | NO | — | INDEX | — |  |
| `quantity` | `int(11)` | NO | — |  | — |  |
| `reference` | `varchar(64)` | YES | NULL |  | — |  |
| `notes` | `varchar(255)` | YES | NULL |  | — |  |
| `created_at` | `timestamp` | NO | current_timestamp() |  | — |  |

**Constraints**

| Name | Type | Column(s) | References |
|---|---|---|---|
| `chk_st_quantity` | CHECK | — | ``quantity` <> 0` |
| `fk_st_product` | FOREIGN KEY | `product_id` | `products(product_id)` |
| `PRIMARY` | PRIMARY KEY | `transaction_id` | — |

## `demand_records`

| Column | Type | Null | Default | Key | Extra | Notes |
|---|---|---|---|---|---|---|
| `demand_id` | `bigint(20) unsigned` | NO | — | PK | auto_increment |  |
| `product_id` | `int(10) unsigned` | NO | — | INDEX | — |  |
| `demand_date` | `date` | NO | — | INDEX | — |  |
| `quantity` | `int(10) unsigned` | NO | — |  | — |  |
| `created_at` | `timestamp` | NO | current_timestamp() |  | — |  |

**Constraints**

| Name | Type | Column(s) | References |
|---|---|---|---|
| `fk_demand_product` | FOREIGN KEY | `product_id` | `products(product_id)` |
| `PRIMARY` | PRIMARY KEY | `demand_id` | — |
| `uq_demand_product_date` | UNIQUE | `product_id,demand_date` | — |

## `analysis_runs`

| Column | Type | Null | Default | Key | Extra | Notes |
|---|---|---|---|---|---|---|
| `run_id` | `int(10) unsigned` | NO | — | PK | auto_increment |  |
| `run_at` | `timestamp` | NO | current_timestamp() |  | — |  |
| `window_days` | `smallint(5) unsigned` | NO | — |  | — |  |
| `service_level` | `decimal(4,3)` | NO | — |  | — |  |
| `safety_stock_days` | `decimal(5,2)` | NO | — |  | — |  |
| `review_period_days` | `decimal(5,2)` | NO | — |  | — |  |
| `forecast_method` | `varchar(40)` | NO | — |  | — |  |
| `notes` | `varchar(255)` | YES | NULL |  | — |  |

**Constraints**

| Name | Type | Column(s) | References |
|---|---|---|---|
| `chk_run_service_level` | CHECK | — | ``service_level` > 0 and `service_level` < 1` |
| `PRIMARY` | PRIMARY KEY | `run_id` | — |

## `reorder_recommendations`

| Column | Type | Null | Default | Key | Extra | Notes |
|---|---|---|---|---|---|---|
| `rec_id` | `int(10) unsigned` | NO | — | PK | auto_increment |  |
| `run_id` | `int(10) unsigned` | NO | — | INDEX | — |  |
| `product_id` | `int(10) unsigned` | NO | — | INDEX | — |  |
| `avg_daily_demand` | `decimal(12,3)` | NO | — |  | — |  |
| `demand_std_dev` | `decimal(12,3)` | NO | — |  | — |  |
| `lead_time_days_used` | `decimal(6,2)` | NO | — |  | — |  |
| `safety_stock` | `decimal(12,3)` | NO | — |  | — |  |
| `reorder_point_calc` | `decimal(12,3)` | NO | — |  | — |  |
| `reorder_point_current` | `int(10) unsigned` | NO | — |  | — |  |
| `on_hand` | `decimal(12,3)` | NO | — |  | — |  |
| `on_order` | `decimal(12,3)` | NO | — |  | — |  |
| `inventory_position` | `decimal(12,3)` | NO | — |  | — |  |
| `days_of_cover` | `decimal(8,2)` | YES | NULL |  | — |  |
| `suggested_order_qty` | `decimal(12,3)` | NO | — |  | — |  |
| `order_value` | `decimal(14,2)` | NO | — |  | — |  |
| `risk_flag` | `enum('STOCKOUT','AT_RISK','OK','EXCESS')` | NO | — | INDEX | — |  |

**Constraints**

| Name | Type | Column(s) | References |
|---|---|---|---|
| `fk_rec_product` | FOREIGN KEY | `product_id` | `products(product_id)` |
| `fk_rec_run` | FOREIGN KEY | `run_id` | `analysis_runs(run_id)` |
| `PRIMARY` | PRIMARY KEY | `rec_id` | — |
| `uq_rec_run_product` | UNIQUE | `run_id,product_id` | — |

## Views

| View | Definition |
|---|---|

| `v_product_inventory` | see `sql/schema.sql` (definition stored in INFORMATION_SCHEMA, 588 characters) |
| `v_po_delivery` | see `sql/schema.sql` (definition stored in INFORMATION_SCHEMA, 665 characters) |

## Units used in the analysis

| Quantity | Unit | Where |
|---|---|---|
| Demand | units/day | `demand_records.quantity`, `avg_daily_demand` |
| Lead time | calendar days | `lead_time_days`, `actual_lead_time_days` |
| Safety stock, ROP, quantities | units | `analytics/formulas.py` |
| Days of cover | days | `days_of_cover` |
| Turnover | turns per year | `annualised_turns` |
| Money | INR | `unit_cost`, `order_value`, `on_hand_value` |

