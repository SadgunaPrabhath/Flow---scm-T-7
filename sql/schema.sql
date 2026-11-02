-- =====================================================================
-- Project 24 : Supply Chain Optimization and Inventory Forecasting System
-- File       : sql/schema.sql
-- Purpose    : MySQL 8.x / MariaDB 10.6+ schema (InnoDB, utf8mb4)
-- Notes      : Run with  mysql -u root -p < sql/schema.sql
--              Every table has a primary key, declared foreign keys,
--              CHECK constraints (MySQL 8.0.16+ enforces them) and
--              indexes on the columns used by the analytics queries.
-- =====================================================================

DROP DATABASE IF EXISTS supplychain;
CREATE DATABASE supplychain
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
USE supplychain;

-- ---------------------------------------------------------------------
-- 1. suppliers  -- vendor master data
-- ---------------------------------------------------------------------
CREATE TABLE suppliers (
    supplier_id             INT UNSIGNED    NOT NULL AUTO_INCREMENT,
    supplier_name           VARCHAR(120)    NOT NULL,
    contact_email           VARCHAR(160)    NULL,
    country                 VARCHAR(60)     NOT NULL DEFAULT 'India',
    promised_lead_time_days SMALLINT UNSIGNED NOT NULL,
    is_active               TINYINT(1)      NOT NULL DEFAULT 1,
    created_at              TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                                            ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (supplier_id),
    UNIQUE KEY uq_suppliers_name (supplier_name),
    CONSTRAINT chk_suppliers_lead_time
        CHECK (promised_lead_time_days BETWEEN 1 AND 180),
    CONSTRAINT chk_suppliers_email
        CHECK (contact_email IS NULL OR contact_email LIKE '%_@_%._%')
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------
-- 2. products -- item master data
--   lead_time_days = master-data (planned) replenishment lead time.
--   reorder_point  = legacy / manually maintained ROP. The analytics
--                    layer recalculates a documented ROP and reports
--                    the difference instead of overwriting this value.
-- ---------------------------------------------------------------------
CREATE TABLE products (
    product_id      INT UNSIGNED    NOT NULL AUTO_INCREMENT,
    sku             VARCHAR(32)     NOT NULL,
    product_name    VARCHAR(150)    NOT NULL,
    category        VARCHAR(60)     NOT NULL,
    unit_cost       DECIMAL(12,2)   NOT NULL,
    lead_time_days  SMALLINT UNSIGNED NOT NULL,
    reorder_point   INT UNSIGNED    NOT NULL DEFAULT 0,
    supplier_id     INT UNSIGNED    NOT NULL,
    is_active       TINYINT(1)      NOT NULL DEFAULT 1,
    created_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP
                                    ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (product_id),
    UNIQUE KEY uq_products_sku (sku),
    KEY idx_products_supplier (supplier_id),
    KEY idx_products_category (category),
    CONSTRAINT chk_products_unit_cost CHECK (unit_cost > 0),
    CONSTRAINT chk_products_lead_time
        CHECK (lead_time_days BETWEEN 1 AND 180),
    CONSTRAINT fk_products_supplier
        FOREIGN KEY (supplier_id) REFERENCES suppliers (supplier_id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------
-- 3. purchase_orders -- one row per single-line purchase order
--   Simplification (documented in docs/03_database_design.md):
--   each PO carries one product line instead of a separate
--   purchase_order_lines table.
-- ---------------------------------------------------------------------
CREATE TABLE purchase_orders (
    po_id         INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    po_number     VARCHAR(24)   NOT NULL,
    supplier_id   INT UNSIGNED  NOT NULL,
    product_id    INT UNSIGNED  NOT NULL,
    quantity      INT UNSIGNED  NOT NULL,
    received_qty  INT UNSIGNED  NULL,
    order_date    DATE          NOT NULL,
    expected_date DATE          NOT NULL,
    received_date DATE          NULL,
    status        ENUM('OPEN','PARTIAL','RECEIVED','CANCELLED')
                                NOT NULL DEFAULT 'OPEN',
    notes         VARCHAR(255)  NULL,
    created_at    TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (po_id),
    UNIQUE KEY uq_po_number (po_number),
    KEY idx_po_supplier (supplier_id),
    KEY idx_po_product (product_id),
    KEY idx_po_status_dates (status, order_date, received_date),
    CONSTRAINT chk_po_qty CHECK (quantity > 0),
    CONSTRAINT chk_po_received_qty
        CHECK (received_qty IS NULL OR received_qty <= quantity),
    CONSTRAINT chk_po_dates CHECK (expected_date >= order_date),
    CONSTRAINT chk_po_received_date
        CHECK (received_date IS NULL OR received_date >= order_date),
    CONSTRAINT fk_po_supplier
        FOREIGN KEY (supplier_id) REFERENCES suppliers (supplier_id)
        ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_po_product
        FOREIGN KEY (product_id) REFERENCES products (product_id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------
-- 4. stock_transactions -- perpetual inventory ledger
--   quantity is SIGNED:  positive = stock in, negative = stock out.
--   RECEIPT / RETURN must be positive, ISSUE must be negative.
--   The API validates the sign; the CHECK below blocks the easiest error.
-- ---------------------------------------------------------------------
CREATE TABLE stock_transactions (
    transaction_id   BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    product_id       INT UNSIGNED    NOT NULL,
    transaction_date DATE            NOT NULL,
    transaction_type ENUM('RECEIPT','ISSUE','ADJUSTMENT','RETURN') NOT NULL,
    quantity         INT             NOT NULL,
    reference        VARCHAR(64)     NULL,
    notes            VARCHAR(255)    NULL,
    created_at       TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (transaction_id),
    KEY idx_st_product_date (product_id, transaction_date),
    KEY idx_st_type (transaction_type),
    CONSTRAINT chk_st_quantity CHECK (quantity <> 0),
    CONSTRAINT fk_st_product
        FOREIGN KEY (product_id) REFERENCES products (product_id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------
-- 5. demand_records -- one row per product per day with observed demand
--   Days with no row are treated as zero demand by the analytics layer
--   (documented assumption A2 in docs/04_analysis_methodology.md).
-- ---------------------------------------------------------------------
CREATE TABLE demand_records (
    demand_id    BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    product_id   INT UNSIGNED    NOT NULL,
    demand_date  DATE            NOT NULL,
    quantity     INT UNSIGNED    NOT NULL,
    created_at   TIMESTAMP       NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (demand_id),
    UNIQUE KEY uq_demand_product_date (product_id, demand_date),
    KEY idx_demand_date (demand_date),
    CONSTRAINT fk_demand_product
        FOREIGN KEY (product_id) REFERENCES products (product_id)
        ON UPDATE CASCADE ON DELETE RESTRICT
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------
-- 6. analysis_runs -- audit trail of decision-support runs
--    Every recommendation report is stored with the parameters used,
--    so a recommendation can always be traced back to its assumptions.
-- ---------------------------------------------------------------------
CREATE TABLE analysis_runs (
    run_id             INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    run_at             TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    window_days        SMALLINT UNSIGNED NOT NULL,
    service_level      DECIMAL(4,3)  NOT NULL,
    safety_stock_days  DECIMAL(5,2)  NOT NULL,
    review_period_days DECIMAL(5,2)  NOT NULL,
    forecast_method    VARCHAR(40)   NOT NULL,
    notes              VARCHAR(255)  NULL,
    PRIMARY KEY (run_id),
    CONSTRAINT chk_run_service_level
        CHECK (service_level > 0 AND service_level < 1)
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------
-- 7. reorder_recommendations -- snapshot of the latest scored run.
--    DECISION SUPPORT ONLY: no order is ever created from this table.
-- ---------------------------------------------------------------------
CREATE TABLE reorder_recommendations (
    rec_id                INT UNSIGNED  NOT NULL AUTO_INCREMENT,
    run_id                INT UNSIGNED  NOT NULL,
    product_id            INT UNSIGNED  NOT NULL,
    avg_daily_demand      DECIMAL(12,3) NOT NULL,
    demand_std_dev        DECIMAL(12,3) NOT NULL,
    lead_time_days_used   DECIMAL(6,2)  NOT NULL,
    safety_stock          DECIMAL(12,3) NOT NULL,
    reorder_point_calc    DECIMAL(12,3) NOT NULL,
    reorder_point_current INT UNSIGNED  NOT NULL,
    on_hand               DECIMAL(12,3) NOT NULL,
    on_order              DECIMAL(12,3) NOT NULL,
    inventory_position    DECIMAL(12,3) NOT NULL,
    days_of_cover         DECIMAL(8,2)  NULL,
    suggested_order_qty   DECIMAL(12,3) NOT NULL,
    order_value           DECIMAL(14,2) NOT NULL,
    risk_flag             ENUM('STOCKOUT','AT_RISK','OK','EXCESS') NOT NULL,
    PRIMARY KEY (rec_id),
    UNIQUE KEY uq_rec_run_product (run_id, product_id),
    KEY idx_rec_flag (risk_flag),
    CONSTRAINT fk_rec_run
        FOREIGN KEY (run_id) REFERENCES analysis_runs (run_id)
        ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT fk_rec_product
        FOREIGN KEY (product_id) REFERENCES products (product_id)
        ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE = InnoDB;

-- ---------------------------------------------------------------------
-- 8. Convenience views used by the reporting layer
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW v_product_inventory AS
SELECT p.product_id,
       p.sku,
       p.product_name,
       p.category,
       p.unit_cost,
       p.lead_time_days,
       p.reorder_point,
       p.supplier_id,
       COALESCE(SUM(st.quantity), 0) AS on_hand
FROM products p
LEFT JOIN stock_transactions st ON st.product_id = p.product_id
GROUP BY p.product_id, p.sku, p.product_name, p.category,
         p.unit_cost, p.lead_time_days, p.reorder_point, p.supplier_id;

CREATE OR REPLACE VIEW v_po_delivery AS
SELECT po.po_id,
       po.po_number,
       po.supplier_id,
       s.supplier_name,
       po.product_id,
       po.quantity,
       po.received_qty,
       po.order_date,
       po.expected_date,
       po.received_date,
       po.status,
       DATEDIFF(po.received_date, po.order_date)  AS actual_lead_time_days,
       DATEDIFF(po.received_date, po.expected_date) AS delay_days
FROM purchase_orders po
JOIN suppliers s ON s.supplier_id = po.supplier_id;
