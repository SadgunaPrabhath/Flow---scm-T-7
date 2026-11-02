# D4 — Analysis Methodology

Every number the system produces comes from the formulas below. They are implemented
once in [`analytics/formulas.py`](../analytics/formulas.py) (pure functions, unit
tested) and reused by the API, the CLI pipeline and the charts, so the chart, the CSV
and the JSON can never disagree.

**Units used everywhere**

| Quantity | Symbol | Unit |
|---|---|---|
| Average daily demand | μ_d | units / day |
| Std. dev. of daily demand | σ_d | units / day |
| Lead time | L | calendar days |
| Safety stock | SS | units |
| Reorder point | ROP | units |
| Inventory position | IP | units |
| Days of cover | DoC | days |
| Money | — | INR |
| Turnover | TO | turns / period (annualised where stated) |

---

## 1. Data preparation (pandas)

1. **Load** products, suppliers, demand, ledger and purchase orders with parameterised
   SQL (`analytics/data_access.py`). No analysis reads raw CSVs, so the API and the
   report always see the same data.
2. **Zero-fill the calendar.** For each product a complete daily calendar over the
   analysis window is built and missing dates are set to **0 units**
   (`demand.daily_series`). Consequence: averages are per *calendar day*, not per
   *day with a sale*. Without this, a product sold on 20 of 90 days would look five
   times bigger than it is.
3. **Reconcile stock.** `on_hand` is `SUM(quantity)` of the signed ledger. The API
   refuses movements that would make the balance negative, so the ledger is a valid
   inventory history.
4. **Filter the windows.**
   * demand: `window_days` calendar days ending on the latest demand date (default 90)
   * purchase orders for supplier KPIs: trailing `SUPPLIER_WINDOW_DAYS` (default 365)
   * delivery statistics use **delivered** POs only (status `RECEIVED`/`PARTIAL`
     with a `received_date`) — open orders would bias lead time downwards.
5. **Aggregate.** Demand per product per day → statistics; POs per supplier → KPIs;
   ledger per product → position and turnover.

Data-cleaning rules applied: demand quantities coerced to numeric and `NaN < 0`
rejected by the schema; PO dates parsed to `datetime` with `NaT` for undelivered
orders; `received_qty` treated as 0 when `NULL`; duplicate demand rows impossible
(unique key); products with no demand in the window kept in the table but flagged
`EXCESS`/`OK` rather than dropped (a silent drop would hide dead stock).

---

## 2. Descriptive demand statistics

```
μ_d  = Σ demand over window / number of calendar days in window        [units/day]
σ_d  = sqrt( Σ (d_i − μ_d)² / (n − 1) )                                [units/day]
CV   = σ_d / μ_d                                                       [dimensionless]
trend slope = OLS slope of the 7-day moving average over time          [units/day per day]
change %    = (μ_recent − μ_previous) / μ_previous × 100               [% over 28 days]
```

Interpretation: `CV < 0.5` is regular demand, `0.5–1.0` is volatile, `> 1.0` is
intermittent/lumpy (`CON-ADH-4010` in the sample data is deliberately lumpy).

**ABC (Pareto) class** on demand *value* (units × unit cost) within the window:

* class **A** — SKUs while cumulative value share < 70 %
* class **B** — 70 %–90 %
* class **C** — above 90 %

The class is assigned from the cumulative share *before* the SKU, so the SKU that
crosses the 70 % line is still class A (a unit test covers this).

---

## 3. Lead time

```
actual lead time   = received_date − order_date            [calendar days]
promised lead time = expected_date − order_date            [calendar days]
delay              = received_date − expected_date         [days; >0 = late]
L (used)           = mean(actual lead time) if ≥ 3 deliveries for that product
                     else products.lead_time_days           [days]
```

Why calendar days: the dataset has no working-calendar table (assumption A6). Why
switch to observed lead time: the master value is a plan; once real deliveries exist,
reality should drive the reorder point. `lead_time_source` in the output says which
value was used, so the assumption is visible per SKU.

---

## 4. Safety stock and reorder point

Three configurable policies (`.env` or query string):

| Policy | Formula | When to use |
|---|---|---|
`zscore` | `SS_z = z(service_level) · σ_d · √L` | statistically principled; needs stable σ_d |
`days` | `SS_d = safety_stock_days · μ_d` | simple planner rule of thumb |
`max` (default) | `SS = max(SS_z, SS_d)` | conservative: never less than either rule |

`z` comes from the standard normal quantile of the cycle service level:
`z(0.90)=1.2816`, `z(0.95)=1.6449`, `z(0.99)=2.3263` (`statistics.NormalDist.inv_cdf`).

```
ROP = μ_d · L + SS                                  [units]
```

The `√L` term assumes daily demand is independent (assumption A7), so the standard
deviation of demand over L days is `σ_d·√L`.

**Inventory position and suggested quantity**

```
IP = on_hand + Σ(quantity − received_qty) over OPEN/PARTIAL POs      [units]
T  = ROP + μ_d · review_period_days        (order-up-to level)       [units]
Q  = max(0, T − IP)                        (suggested quantity)      [units]
```

`Q` is rounded **up** to a whole unit. It is a suggestion for a human: it ignores
MOQ, pack size, container loads and budget — all listed as limitations. For reference
the classic EOQ is implemented (`formulas.eoq`) but not used for the recommendation,
because EOQ needs ordering and holding costs that this dataset does not have.

**Manual vs computed ROP.** `products.reorder_point` is the planner's legacy value.
The system never overwrites it; it reports `gap_vs_master_rop = ROP_calc − ROP_master`
so the size of the correction is visible (3 SKUs in the sample data have a stale
manual value).

---

## 5. Risk classification (thresholds)

| Flag | Rule | Business meaning |
|---|---|---|
`STOCKOUT` | `on_hand ≤ 0` **or** `IP < μ_d · L` | already out, or cannot cover demand during lead time → a stockout is expected before the next delivery can arrive |
`AT_RISK` | `IP < ROP` (and not above) | covers lead-time demand, but below the reorder point → order at this review |
`EXCESS` | `IP ≥ ROP` **and** `DoC > excess_cover_days` (default 90) | more than three months of cover tied up in cash |
`OK` | otherwise | within policy |

`DoC = on_hand / μ_d` (days). A product with **no demand** in the window is never
flagged `STOCKOUT`; if it still holds stock it is flagged `EXCESS` (dead stock).

All thresholds are parameters, and the report prints a **sensitivity grid** showing
how the flag counts move at other service levels and policies.

---

## 6. Supplier performance

```
on_time_rate = delivered POs with received_date ≤ expected_date / delivered POs
fill_rate    = units received / units ordered            (delivered POs)
lead-time CV = std(actual lead time) / mean(actual lead time)
P90 lead time= 90th percentile of actual lead time
score        = 100 · (0.6 · on_time_rate + 0.4 · max(0, 1 − CV))
grade        = A if on_time ≥ 95 % and CV ≤ 0.15
               B if on_time ≥ 85 % and CV ≤ 0.30
               C if on_time ≥ 70 %
               D otherwise
               NO_DATA if the supplier has no delivered PO
```

The score deliberately weights **both** dimensions: a supplier with a perfect average
lead time but erratic deliveries is not "good", because its variability is what forces
extra safety stock.

---

## 7. Inventory turnover

```
daily balance b_t     = cumulative Σ quantity up to day t        [units]
average inventory     = mean(b_t) over the window                [units]
COGS proxy            = Σ |ISSUE quantity| · unit_cost           [INR]
turns (period)        = COGS / (average inventory · unit_cost)   [turns]
turns (annualised)    = turns · 365 / period_days                [turns/year]
days of inventory     = period_days / turns                      [days]
```

A daily average is used instead of a two-point (start/end) average because a single
delivery on the last day would otherwise distort the result by an order of magnitude.
The COGS proxy excludes adjustments (shrinkage) — it measures customer-driven outflow.

---

## 8. Forecasting and its evaluation

Implemented in [`analytics/forecast.py`](../analytics/forecast.py). All four models
are simple, explainable and reproducible:

| Method | Definition | Role |
|---|---|---|
`naive_mean` | flat forecast = mean of the last 28 days | **baseline** |
`seasonal_naive` | repeat the last 7 days' pattern | weekly-seasonality baseline |
`moving_average` | flat forecast = mean of the last 14 days | smooth baseline |
`holt_winters` | additive damped trend + additive weekly seasonality (statsmodels) | the model under test |

**Evaluation protocol (no leakage)**

```
window  = [start … end]                       (default 90 days)
train   = [start … end − 28]
test    = last 28 days (hold-out)
```

Each model is fitted on `train` only and scored on `test`:

```
MAE  = mean(|actual − forecast|)                              [units/day]
RMSE = sqrt(mean((actual − forecast)²))                       [units/day]
MAPE = mean(|actual − forecast| / |actual|) · 100             [%]  (zero actuals excluded)
MASE = MAE_model(test) / MAE_naive_in-sample                  [ratio; <1 beats naive]
bias = mean(forecast − actual)                                [units/day; >0 = over-forecast]
```

**Selection rule (documented, deliberately conservative).** The method with the
lowest hold-out MAE wins **only if it beats `naive_mean` by more than 5 %**;
otherwise the baseline is used. Rationale: a 2 % gain on a 28-day hold-out is noise,
and a simpler model is easier to defend in a viva. The selected method is then
refitted on the whole window to produce the forward forecast and an approximate 80 %
band (`± 1.28 · σ_residual · √(h/7)`).

Forecasts are **decision support**, not commitments, and are never fed back into the
reorder point automatically (the ROP uses observed demand statistics).

---

## 9. Assumptions (numbered, referenced in the report)

| # | Assumption | Impact if wrong |
|---|---|---|
A1 | One warehouse, one supplier per SKU | multi-sourcing changes ROP and lead-time variability |
A2 | Missing demand row = zero demand | understated demand if the gap is a reporting failure |
A3 | One product per purchase order | mixed POs need allocation logic |
A4 | On-hand is the ledger sum | manual stock counts not recorded break the balance |
A5 | Open POs will arrive (included in IP) | a stalled open PO overstates IP → hidden stockout |
A6 | Lead times in calendar days | working-day calendars change every ROP |
A7 | Independent daily demand (σ·√L) | positively correlated demand understates SS |
A8 | Observed lead time replaces master data after 3 deliveries | small samples are noisy |
A9 | Safety stock = max(z-score, days policy) | a budget-constrained planner may prefer `zscore` |
A10 | Suggested quantity is an order-up-to level | MOQ/pack size/budget constraints ignored |
A11 | 90-day demand window, 365-day PO window | seasonality longer than the window is invisible |
A12 | Model must beat the baseline by > 5 % to be chosen | a genuinely better model with a small gain may be discarded |

---

## 10. Reproducing the numbers

```bash
python scripts/run_pipeline.py                       # all tables, charts and the report
python scripts/run_pipeline.py --window 120 --service-level 0.99 --policy zscore
python -m pytest tests/test_formulas.py -q           # the formulas themselves
```

Every report ends with the parameter table that produced it, and
`POST /api/analytics/run` stores the same parameter set in `analysis_runs` for audit.
