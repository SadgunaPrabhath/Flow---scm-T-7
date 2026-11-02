"""Matplotlib visualisations for the Supply Chain Optimization project.

Every chart
-----------
* has a title, labelled axes WITH UNITS, a legend where more than one
  series is plotted;
* is saved as a PNG under reports/charts/ (served by the API through
  /api/charts/<name>);
* returns a plain-language interpretation string that is reused in the
  report and in the JSON payload -- so the chart and its written
  interpretation can never drift apart.

Colour convention used throughout
---------------------------------
red = danger / late / stockout, orange = warning / at risk,
green = healthy / on time, grey = informational.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")                      # headless: no display needed
import matplotlib.pyplot as plt            # noqa: E402
import numpy as np                         # noqa: E402
import pandas as pd                        # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402

from analytics import forecast as fc       # noqa: E402
from config import config                  # noqa: E402

CHART_DIR = Path(config.REPORT_DIR) / "charts"
CHART_DIR.mkdir(parents=True, exist_ok=True)

# ---- house style -----------------------------------------------------
plt.rcParams.update({
    "figure.dpi": 140,
    "savefig.dpi": 140,
    "font.size": 10,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.labelsize": 10,
    "axes.grid": True,
    "grid.alpha": 0.25,
    "grid.linestyle": "--",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "legend.frameon": False,
    "figure.autolayout": False,
})

FLAG_COLORS = {"STOCKOUT": "#c0392b", "AT_RISK": "#e67e22",
               "EXCESS": "#8e44ad", "OK": "#27ae60"}
PALETTE = ["#1f4e79", "#c0392b", "#2e8b57", "#e6a817", "#6c5ce7",
           "#16a085", "#d35400", "#7f8c8d"]
FOOTER = ("Source: synthetic dataset (Project 24 demo) | "
          "decision support only - no orders are placed automatically")


def _save(fig, name: str) -> Path:
    path = CHART_DIR / name
    fig.savefig(path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def _footer(fig, y: float = -0.035) -> None:
    """Provenance footer placed below the axes (included by bbox_inches='tight')."""
    fig.text(0.5, y, FOOTER, ha="center", fontsize=7.5, color="#555555")


def _thousands(value, _pos=None) -> str:
    return f"{value:,.0f}"


def _empty_chart(name: str, title: str, message: str) -> tuple[str, str]:
    """Placeholder used when a dataset slice is empty (tested behaviour)."""
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.axis("off")
    ax.set_title(title)
    ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=11, color="#7f8c8d")
    _footer(fig)
    return str(_save(fig, name)), f"{title}: no data available for the selected filters."


# ------------------------------------------------------------------ 1
def chart_demand_trend(result) -> tuple[str, str]:
    """Daily demand (7-day moving average) and complete-week totals."""
    products, start, end = result.products, result.windows["start"], result.windows["end"]
    series = result.demand_series
    if series.empty:
        return _empty_chart("01_demand_trend.png", "Weekly Demand Trend",
                            "No demand records in the selected window.")

    totals = series.groupby("product_id")["quantity"].sum().sort_values(ascending=False)
    top = totals.head(5).index.tolist()
    sku_map = dict(zip(products["product_id"], products["sku"]))

    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(11, 8),
                                  gridspec_kw={"height_ratios": [3, 1.2]})
    for i, pid in enumerate(top):
        part = series[series["product_id"] == pid].sort_values("demand_date")
        smooth = part["quantity"].rolling(7, min_periods=7).mean()
        ax.plot(part["demand_date"], smooth, linewidth=1.9, color=PALETTE[i % len(PALETTE)],
                label=f"{sku_map.get(pid, pid)}")
    ax.set_title("Demand Trend of the Five Highest-Volume SKUs (7-day moving average)")
    ax.set_xlabel("Date")
    ax.set_ylabel("Demand (units per day, 7-day moving average)")
    ax.yaxis.set_major_formatter(FuncFormatter(_thousands))
    ax.legend(title="SKU", ncol=3, loc="lower left", fontsize=8)
    ax.set_ylim(bottom=0)

    # total demand per calendar week -- only weeks fully inside the window
    weekly = (series.assign(week_start=series["demand_date"] - pd.to_timedelta(
        series["demand_date"].dt.weekday, unit="D"))
        .groupby("week_start")["quantity"].agg(total="sum", n_days="count"))
    # keep only weeks whose calendar days inside the window are all present
    weekly = weekly[weekly["n_days"] == 7 * series["product_id"].nunique()]
    ax2.plot(weekly.index, weekly["total"], color="#1f4e79", linewidth=1.8, marker="o",
             markersize=3.5, label="All 24 SKUs (complete weeks only)")
    ax2.fill_between(weekly.index, weekly["total"].values, color="#1f4e79", alpha=0.25)
    ax2.set_title("Total Weekly Demand, All 24 SKUs")
    ax2.set_xlabel("Week starting (date) -- partial first/last weeks excluded")
    ax2.set_ylabel("Demand (units per week)")
    ax2.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f"{v/1000:,.1f}k"))
    ax2.set_ylim(bottom=0)
    ax2.legend(loc="lower left")
    _footer(fig)
    fig.tight_layout()
    path = _save(fig, "01_demand_trend.png")

    growth = "rising" if weekly["total"].iloc[-4:].mean() > weekly["total"].iloc[:4].mean() \
        else "flat or falling"
    text = (
        f"Demand is shown from {start:%d %b %Y} to {end:%d %b %Y}. The five largest "
        f"SKUs (packaging boxes, seal rings, oil and air filters) carry most of the "
        f"volume; their moving averages are {growth} across the window. The lower "
        f"panel shows total weekly demand for all 24 SKUs. Because a handful of SKUs "
        f"dominate, they deserve the closest review of reorder points and supplier "
        f"reliability.")
    return str(path), text


# ------------------------------------------------------------------ 2
def chart_demand_heatmap(result) -> tuple[str, str]:
    """Average demand by weekday and SKU -- shows the weekly demand shape."""
    series = result.demand_series
    if series.empty:
        return _empty_chart("02_weekday_demand_heatmap.png", "Weekday Demand Profile",
                            "No demand records in the selected window.")
    frame = series.copy()
    frame["weekday"] = frame["demand_date"].dt.dayofweek
    pivot = (frame.groupby(["product_id", "weekday"])["quantity"].mean()
             .unstack(fill_value=0))
    pivot.index = [result.products.set_index("product_id")["sku"].get(i, str(i))
                   for i in pivot.index]
    # show the 12 busiest SKUs so labels stay readable
    order = pivot.sum(axis=1).sort_values(ascending=False).head(12).index
    pivot = pivot.loc[order]
    pivot.columns = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    fig, ax = plt.subplots(figsize=(9, 6.2))
    im = ax.imshow(pivot.values, aspect="auto", cmap="YlGnBu")
    ax.set_title("Average Demand by Weekday and SKU (12 highest-volume SKUs)")
    ax.set_xlabel("Day of week")
    ax.set_ylabel("SKU")
    ax.set_yticks(range(len(pivot.index)))
    ax.set_yticklabels(pivot.index, fontsize=8.5)
    ax.set_xticks(range(7))
    ax.set_xticklabels(pivot.columns)
    ax.grid(False)
    cbar = fig.colorbar(im, ax=ax, pad=0.02)
    cbar.set_label("Average demand (units per day)")
    for i in range(pivot.shape[0]):
        for j in range(pivot.shape[1]):
            value = pivot.values[i, j]
            ax.text(j, i, f"{value:,.0f}", ha="center", va="center", fontsize=7,
                    color="white" if value > pivot.values.max() * 0.55 else "#222222")
    _footer(fig)
    fig.tight_layout()
    path = _save(fig, "02_weekday_demand_heatmap.png")

    fri = pivot["Fri"].mean()
    sun = pivot["Sun"].mean()
    text = (
        f"Each cell is the average daily demand for one SKU and weekday "
        f"(units/day). Demand peaks mid-week and on Friday (average "
        f"{fri:,.1f} units/day) and falls sharply on Sunday "
        f"({sun:,.1f} units/day). This weekly pattern is why the forecast adds a "
        f"weekly seasonal component; it also means weekend stock levels are not "
        f"comparable with weekday levels.")
    return str(path), text


# ------------------------------------------------------------------ 3
def chart_inventory_position(result) -> tuple[str, str]:
    """Coverage ratio vs ROP and days of cover -- the two planner views."""
    rec = result.recommendations
    if rec.empty:
        return _empty_chart("03_inventory_vs_rop.png", "Inventory Position vs Reorder Point",
                            "No products to plot.")

    frame = rec.copy()
    frame["ip_ratio_pct"] = np.where(
        frame["reorder_point_calc"] > 0,
        100.0 * frame["inventory_position"] / frame["reorder_point_calc"],
        np.nan)
    frame = frame.sort_values("ip_ratio_pct", na_position="first")

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(14, 6.4),
                                  gridspec_kw={"width_ratios": [1.15, 1]})
    y = np.arange(len(frame))
    colors = [FLAG_COLORS.get(f, "#7f8c8d") for f in frame["risk_flag"]]
    ratio = frame["ip_ratio_pct"].fillna(0).to_numpy()
    ax.barh(y, np.clip(ratio, 0, 400), color=colors)
    for i, (value, flag) in enumerate(zip(ratio, frame["risk_flag"])):
        label = f"{value:,.0f}%"
        if np.isnan(frame["ip_ratio_pct"].iloc[i]):
            label = "no demand"
        ax.text(min(value, 400) + 8, i, label, va="center", fontsize=7.5,
                color=FLAG_COLORS.get(flag, "#333333"))
    ax.axvline(100, color="#c0392b", linestyle="--", linewidth=1.5,
               label="Reorder point reached (100% of ROP)")
    ax.set_yticks(y)
    ax.set_yticklabels(frame["sku"], fontsize=8)
    ax.set_xlim(0, 470)
    ax.set_title("Inventory Position as % of Calculated Reorder Point")
    ax.set_xlabel("Inventory position / reorder point (%)\n"
                  "below 100% = reorder needed; bars cut at 400%")
    ax.set_ylabel("SKU")
    handles = [plt.Rectangle((0, 0), 1, 1, color=FLAG_COLORS[f])
               for f in ("STOCKOUT", "AT_RISK", "EXCESS", "OK")]
    line_handle, line_label = ax.get_legend_handles_labels()
    ax.legend(handles + line_handle, ["STOCKOUT", "AT_RISK", "EXCESS", "OK"] + line_label,
              title="Risk flag", loc="lower right", fontsize=8)

    cover = frame.dropna(subset=["days_of_cover"]).sort_values("days_of_cover")
    yy = np.arange(len(cover))
    ax2.barh(yy, cover["days_of_cover"],
             color=[FLAG_COLORS.get(f, "#7f8c8d") for f in cover["risk_flag"]])
    ax2.axvline(config.EXCESS_COVER_DAYS, color="#8e44ad", linestyle="--", linewidth=1.5,
                label=f"Excess threshold ({config.EXCESS_COVER_DAYS:.0f} days)")
    ax2.set_yticks(yy)
    ax2.set_yticklabels(cover["sku"], fontsize=8)
    ax2.set_title("Days of Cover on Hand (on-hand / average daily demand)")
    ax2.set_xlabel("Days of cover (days of demand held in stock)")
    ax2.set_ylabel("SKU")
    ax2.legend(loc="lower right", fontsize=8)
    _footer(fig)
    fig.tight_layout()
    path = _save(fig, "03_inventory_vs_rop.png")

    below = frame[frame["ip_ratio_pct"].notna()
                  & (frame["ip_ratio_pct"] < 100)].sort_values("ip_ratio_pct")
    worst = below.iloc[0] if not below.empty else None
    text = (
        f"Left: inventory position (on hand + open orders) divided by the calculated "
        f"reorder point. {len(below)} of {len(frame)} SKUs sit below the 100% line"
        + (f", the lowest being {worst.sku} at {worst.ip_ratio_pct:.0f}%"
           if worst is not None else "")
        + f". Right: days of cover on hand; bars beyond the "
        f"{config.EXCESS_COVER_DAYS:.0f}-day line are excess stock. The two views "
        f"answer different questions -- 'do I need to order now?' and 'am I holding "
        f"too much?' -- and a SKU can be fine on one while flagged on the other.")
    return str(path), text


# ------------------------------------------------------------------ 4
def chart_supplier_performance(result) -> tuple[str, str]:
    """On-time rate and fill rate per supplier."""
    summary = result.supplier_summary
    if summary.empty:
        return _empty_chart("04_supplier_on_time.png", "Supplier Delivery Performance",
                            "No supplier data.")
    frame = summary.dropna(subset=["on_time_rate"]).sort_values("on_time_rate")
    if frame.empty:
        return _empty_chart("04_supplier_on_time.png", "Supplier Delivery Performance",
                            "No delivered purchase orders to evaluate.")
    fig, ax = plt.subplots(figsize=(10.5, 5.0))
    y = np.arange(len(frame))
    ax.barh(y + 0.19, frame["fill_rate"].fillna(0) * 100, height=0.36, color="#8fc7a5",
            label="Fill rate (%)")
    ax.barh(y - 0.19, frame["on_time_rate"] * 100, height=0.36, color="#1f4e79",
            label="On-time delivery rate (%)")
    ax.axvline(90, color="#c0392b", linestyle="--", linewidth=1.4,
               label="Target on-time rate (90%)")
    for i, row in enumerate(frame.itertuples()):
        ax.text(row.on_time_rate * 100 + 1.2, i - 0.19, f"{row.on_time_rate*100:.0f}%",
                va="center", fontsize=8, color="#1f4e79")
        fill = 0 if pd.isna(row.fill_rate) else row.fill_rate * 100
        ax.text(fill + 1.2, i + 0.19, f"{fill:.0f}%", va="center", fontsize=7.5,
                color="#2e7d5b")
    ax.set_yticks(y)
    ax.set_yticklabels([f"{name[:30]}  (grade {grade})"
                        for name, grade in zip(frame["supplier_name"], frame["grade"])],
                       fontsize=8.5)
    ax.set_xlim(0, 112)
    ax.set_title(f"Supplier Delivery Performance ({len(frame)} suppliers, "
                 f"{config.SUPPLIER_WINDOW_DAYS}-day PO window)")
    ax.set_xlabel("Rate (%)  --  on-time = received on or before the expected date")
    ax.set_ylabel("Supplier")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.17), ncol=3, fontsize=8.5)
    _footer(fig, -0.16)
    fig.tight_layout()
    path = _save(fig, "04_supplier_on_time.png")

    best = frame.iloc[-1]
    worst = frame.iloc[0]
    text = (
        f"{best.supplier_name} is the most reliable supplier "
        f"({best.on_time_rate*100:.0f}% on time, grade {best.grade}), while "
        f"{worst.supplier_name} delivers only {worst.on_time_rate*100:.0f}% of orders "
        f"on time (grade {worst.grade}). A low on-time rate forces either a longer "
        f"planning lead time or more safety stock, so these two suppliers need "
        f"different replenishment assumptions.")
    return str(path), text


# ------------------------------------------------------------------ 5
def chart_supplier_delay(result) -> tuple[str, str]:
    """Distribution of delivery delay per supplier."""
    pos = result.raw_pos
    if pos is None or pos.empty or "delay_days" not in pos:
        return _empty_chart("05_supplier_delay_spread.png", "Delivery Delay Spread",
                            "No delivered purchase orders.")
    frame = pos[pos["is_delivered"]].dropna(subset=["delay_days"])
    if frame.empty:
        return _empty_chart("05_supplier_delay_spread.png", "Delivery Delay Spread",
                            "No delivered purchase orders.")
    groups, labels = [], []
    for name, part in frame.groupby("supplier_name"):
        if len(part) >= 2:
            groups.append(part["delay_days"].to_numpy())
            labels.append(name[:28])
    if not groups:
        return _empty_chart("05_supplier_delay_spread.png", "Delivery Delay Spread",
                            "Fewer than two deliveries per supplier.")

    fig, ax = plt.subplots(figsize=(10.5, 4.8))
    try:                                  # matplotlib >= 3.9 renamed the argument
        bp = ax.boxplot(groups, tick_labels=labels, patch_artist=True, showmeans=True,
                        meanprops=dict(marker="D", markerfacecolor="#c0392b",
                                       markeredgecolor="#c0392b", markersize=6))
    except TypeError:                     # older matplotlib
        bp = ax.boxplot(groups, labels=labels, patch_artist=True, showmeans=True,
                        meanprops=dict(marker="D", markerfacecolor="#c0392b",
                                       markeredgecolor="#c0392b", markersize=6))
    for patch, color in zip(bp["boxes"], PALETTE):
        patch.set_facecolor(color)
        patch.set_alpha(0.45)
    ax.axhline(0, color="#c0392b", linestyle="--", linewidth=1.3,
               label="Zero delay (delivered on expected date)")
    ax.set_title("Spread of Delivery Delay by Supplier (delivered POs only)")
    ax.set_xlabel("Supplier")
    ax.set_ylabel("Delay (days; positive = late, negative = early)")
    plt.setp(ax.get_xticklabels(), rotation=18, ha="right", fontsize=8)
    ax.legend(loc="upper left")
    _footer(fig)
    fig.tight_layout()
    path = _save(fig, "05_supplier_delay_spread.png")

    widest = max(zip(labels, [np.ptp(g) for g in groups]), key=lambda t: t[1])
    text = (
        f"Boxes show the median, quartiles and range of delay in days; diamonds are "
        f"means. {widest[0]} has the widest spread ({widest[1]:.0f} days between best "
        f"and worst delivery), which is exactly the variability that drives the "
        f"z-score safety stock term. A supplier who is late only occasionally still "
        f"hurts if the delay is large.")
    return str(path), text


# ------------------------------------------------------------------ 6
def chart_abc_pareto(result) -> tuple[str, str]:
    """Pareto of demand value with cumulative share."""
    stats = result.demand_stats
    if stats.empty or stats["demand_value"].sum() <= 0:
        return _empty_chart("06_abc_pareto.png", "ABC Pareto of Demand Value",
                            "No demand value to plot.")
    frame = stats.sort_values("demand_value", ascending=False)
    fig, ax = plt.subplots(figsize=(11, 5))
    x = np.arange(len(frame))
    colors = [{"A": "#1f4e79", "B": "#5dade2", "C": "#aab7b8"}.get(c, "#aab7b8")
              for c in frame["abc_class"]]
    ax.bar(x, frame["demand_value"], color=colors, label="Demand value (INR, window total)")
    ax.set_ylabel("Demand value in window (INR)")
    ax.set_xlabel("SKU (sorted by demand value)")
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f"{v/1e6:,.1f}M"))

    ax2 = ax.twinx()
    ax2.plot(x, frame["cum_share"] * 100, color="#c0392b", marker="o", markersize=4,
             linewidth=1.8, label="Cumulative share of demand value (%)")
    ax2.axhline(70, color="#e67e22", linestyle=":", linewidth=1.3,
                label="Class A cut-off (70%)")
    ax2.axhline(90, color="#8e44ad", linestyle=":", linewidth=1.3,
                label="Class B cut-off (90%)")
    ax2.set_ylabel("Cumulative share of demand value (%)")
    ax2.set_ylim(0, 105)
    ax2.grid(False)
    ax.set_xticks(x)
    ax.set_xticklabels(frame["sku"], rotation=75, fontsize=7.5)
    ax.set_title("ABC (Pareto) Analysis of Demand Value")
    handles, labels = ax.get_legend_handles_labels()
    handles2, labels2 = ax2.get_legend_handles_labels()
    ax.legend(handles + handles2, labels + labels2, loc="upper center", fontsize=8)
    _footer(fig, -0.05)
    fig.tight_layout()
    path = _save(fig, "06_abc_pareto.png")

    a_count = int((frame["abc_class"] == "A").sum())
    a_share = frame.loc[frame["abc_class"] == "A", "demand_value"].sum() \
        / frame["demand_value"].sum() * 100
    text = (
        f"{a_count} SKUs (class A) generate {a_share:.0f}% of demand value, so they "
        f"should be counted most often and their reorder points reviewed first. "
        f"Class C SKUs are numerous but contribute little value: simpler, larger "
        f"orders are acceptable there.")
    return str(path), text


# ------------------------------------------------------------------ 7
def chart_forecast_vs_actual(result) -> tuple[str, str]:
    """Hold-out comparison of the selected model against the naive baseline."""
    series_frame, products = result.demand_series, result.products
    compare = result.forecast_compare
    if series_frame.empty or compare.empty:
        return _empty_chart("07_forecast_vs_actual.png", "Forecast vs Baseline",
                            "Forecast evaluation not available.")
    volume = series_frame.groupby("product_id")["quantity"].sum().sort_values(ascending=False)
    pid = int(volume.index[0])
    sku = products.set_index("product_id")["sku"].get(pid, str(pid))
    series = series_frame[series_frame["product_id"] == pid] \
        .sort_values("demand_date")["quantity"].to_numpy(dtype=float)

    holdout = config.FORECAST_HOLDOUT_DAYS
    train, test = series[:-holdout], series[-holdout:]
    table, best = fc.evaluate_series(series, holdout, config.FORECAST_BASELINE_WINDOW)
    pred_best = fc.run_method(best, train, len(test), config.FORECAST_BASELINE_WINDOW)
    pred_base = fc.run_method("naive_mean", train, len(test), config.FORECAST_BASELINE_WINDOW)

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(13, 4.8),
                                  gridspec_kw={"width_ratios": [1.45, 1]})
    x = np.arange(len(test))
    ax.plot(x, test, color="#1f4e79", linewidth=2, label="Actual demand (units/day)")
    ax.plot(x, pred_best, color="#e67e22", linewidth=1.8,
            label=f"Selected model: {best}")
    ax.plot(x, pred_base, color="#7f8c8d", linewidth=1.5, linestyle="--",
            label="Baseline: 28-day mean")
    ax.set_title(f"Hold-out Forecast vs Actual -- {sku} (last {len(test)} days)")
    ax.set_xlabel(f"Day of hold-out period (0 = {holdout} days before window end)")
    ax.set_ylabel("Demand (units per day)")
    ax.legend(fontsize=8)
    ax.set_ylim(bottom=0)

    avg = (table.groupby("method")["mae"].mean().sort_values())
    colors = ["#27ae60" if m == best else "#95a5a6" for m in avg.index]
    ax2.barh(avg.index, avg.values, color=colors)
    for i, v in enumerate(avg.values):
        ax2.text(v * 1.01, i, f"{v:.2f}", va="center", fontsize=8)
    ax2.set_title("Mean Absolute Error by Method (all 24 SKUs)")
    ax2.set_xlabel("MAE (units per day, lower is better)")
    ax2.set_ylabel("Forecast method")
    ax2.set_xlim(0, avg.values.max() * 1.18)
    _footer(fig)
    fig.tight_layout()
    path = _save(fig, "07_forecast_vs_actual.png")

    summary = result.forecast_summary
    text = (
        f"Left: the last {len(test)} days are held back, every model is fitted on the "
        f"earlier data only, and the winner for {sku} is '{best}'. Right: average MAE "
        f"across all SKUs. The selected models average "
        f"{summary.get('mean_mae_selected', float('nan')):.2f} units/day error against "
        f"{summary.get('mean_mae_baseline', float('nan')):.2f} for the naive baseline, "
        f"improving on the baseline for {summary.get('improved_over_baseline', 0)} of "
        f"{summary.get('products_evaluated', 0)} SKUs. Where the baseline wins, the "
        f"simpler method is kept on purpose.")
    return str(path), text


# ------------------------------------------------------------------ 8
def chart_forecast_horizon(result) -> tuple[str, str]:
    """History plus forward forecast with an ~80% band, for the top SKU."""
    lines = result.forecast_lines
    series_frame = result.demand_series
    if lines.empty or series_frame.empty:
        return _empty_chart("08_forecast_horizon.png", "Forward Forecast",
                            "Forecast horizon not available.")
    pid = int(lines["product_id"].iloc[0])
    sku = result.products.set_index("product_id")["sku"].get(pid, str(pid))
    hist = series_frame[series_frame["product_id"] == pid].sort_values("demand_date")
    hist = hist.tail(120)
    future = lines[lines["product_id"] == pid]

    fig, ax = plt.subplots(figsize=(12, 4.8))
    ax.plot(hist["demand_date"], hist["quantity"], color="#95a5a6", linewidth=0.9,
            label="Daily demand (units/day)")
    ax.plot(hist["demand_date"],
            hist["quantity"].rolling(7, min_periods=1).mean(), color="#1f4e79",
            linewidth=2, label="7-day moving average (units/day)")
    ax.plot(future["date"], future["forecast"], color="#c0392b", linewidth=2.2,
            label="Forecast (units/day)")
    ax.fill_between(future["date"], future["lower_80"], future["upper_80"],
                    color="#c0392b", alpha=0.16, label="~80% prediction band")
    ax.axvline(future["date"].iloc[0], color="#7f8c8d", linestyle="--", linewidth=1.2,
               label="Forecast start")
    ax.set_title(f"{sku}: History and {len(future)}-Day Forward Forecast")
    ax.set_xlabel("Date")
    ax.set_ylabel("Demand (units per day)")
    ax.legend(fontsize=8, loc="upper left")
    ax.set_ylim(bottom=0)
    _footer(fig)
    fig.tight_layout()
    path = _save(fig, "08_forecast_horizon.png")

    avg_fc = future["forecast"].mean()
    text = (
        f"The red line is the forward forecast for {sku} over the next {len(future)} "
        f"days (average {avg_fc:.1f} units/day) and the shaded area is an approximate "
        f"80% band built from the hold-out error. The band widens with the horizon "
        f"because uncertainty grows; treat the centre line as a planning figure and "
        f"the band as the range a safety buffer must cover.")
    return str(path), text


# ------------------------------------------------------------------ 9
def chart_flags_summary(result) -> tuple[str, str]:
    """Count of SKUs and value of recommended orders per risk flag."""
    rec = result.recommendations
    if rec.empty:
        return _empty_chart("09_risk_flags_summary.png", "Risk Flag Summary",
                            "No products to classify.")
    flags = ["STOCKOUT", "AT_RISK", "EXCESS", "OK"]
    counts = [int((rec["risk_flag"] == f).sum()) for f in flags]
    values = [float(rec.loc[rec["risk_flag"] == f, "order_value"].sum()) for f in flags]

    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11, 4.4))
    bars = ax.bar(flags, counts, color=[FLAG_COLORS[f] for f in flags])
    for bar, count in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width() / 2, count + 0.15, str(count),
                ha="center", fontsize=10, fontweight="bold")
    ax.set_title("SKU Count by Risk Flag")
    ax.set_xlabel("Risk flag")
    ax.set_ylabel("Number of SKUs")
    ax.set_ylim(0, max(counts) + 2 if max(counts) else 2)
    ax.yaxis.set_major_locator(MaxNLocator(integer=True))

    colors = [FLAG_COLORS[f] for f in flags if values[flags.index(f)] > 0] or ["#95a5a6"]
    shown = [f for f in flags if values[flags.index(f)] > 0]
    ax2.bar(shown or ["none"], [v for v in values if v > 0] or [0], color=colors)
    ax2.set_title("Value of Suggested Orders by Flag")
    ax2.set_xlabel("Risk flag")
    ax2.set_ylabel("Suggested order value (INR)")
    ax2.yaxis.set_major_formatter(FuncFormatter(lambda v, p: f"{v/1e5:,.1f}L"))
    for i, v in enumerate([v for v in values if v > 0]):
        ax2.text(i, v, f"{v/1e5:,.1f}L", ha="center", va="bottom", fontsize=8)
    _footer(fig)
    fig.tight_layout()
    path = _save(fig, "09_risk_flags_summary.png")

    total_value = sum(values)
    text = (
        f"{counts[0]} SKU(s) are at critical stockout risk and {counts[1]} need "
        f"reordering at the next review. The suggested orders together are worth "
        f"about INR {total_value/1e5:,.1f} lakh -- a suggestion for the planner, "
        f"not an order. {counts[2]} SKU(s) hold more than "
        f"{config.EXCESS_COVER_DAYS:.0f} days of cover, which is working capital that "
        f"could be released.")
    return str(path), text


# ------------------------------------------------------------------ 10
def chart_stock_turnover(result) -> tuple[str, str]:
    """Annualised inventory turns per SKU."""
    turnover = result.turnover
    if turnover.empty or turnover["annualised_turns"].notna().sum() == 0:
        return _empty_chart("10_stock_turnover.png", "Stock Turnover",
                            "Not enough ledger history for turnover.")
    frame = turnover.dropna(subset=["annualised_turns"]).sort_values(
        "annualised_turns", ascending=False).head(16)
    fig, ax = plt.subplots(figsize=(11, 4.8))
    bars = ax.bar(frame["sku"], frame["annualised_turns"], color="#1f4e79")
    avg = frame["annualised_turns"].mean()
    ax.axhline(avg, color="#c0392b", linestyle="--", linewidth=1.3,
               label=f"Mean of shown SKUs ({avg:.1f} turns/year)")
    for bar, v in zip(bars, frame["annualised_turns"]):
        ax.text(bar.get_x() + bar.get_width() / 2, v, f"{v:.0f}", ha="center",
                va="bottom", fontsize=7.5)
    ax.set_title("Annualised Stock Turnover by SKU (top 16)")
    ax.set_xlabel("SKU")
    ax.set_ylabel("Turns per year (COGS / average inventory value)")
    plt.setp(ax.get_xticklabels(), rotation=70, ha="right", fontsize=7.5)
    ax.legend()
    _footer(fig)
    fig.tight_layout()
    path = _save(fig, "10_stock_turnover.png")

    slow = turnover.dropna(subset=["annualised_turns"]).sort_values("annualised_turns").head(3)
    text = (
        f"Turnover = units issued x unit cost divided by average inventory value for "
        f"the window, annualised. Low-turn SKUs ({', '.join(slow['sku'])}) hold stock "
        f"for a long time and are the first candidates for order-size reductions, "
        f"while high-turn SKUs need dependable suppliers because a late delivery hurts "
        f"immediately.")
    return str(path), text


# ------------------------------------------------------------------ all
def build_all(result, only: list[str] | None = None) -> list[dict]:
    """Render every chart; returns metadata + interpretation for the report."""
    builders = [
        ("demand_trend", "01_demand_trend.png", chart_demand_trend),
        ("weekday_demand", "02_weekday_demand_heatmap.png", chart_demand_heatmap),
        ("inventory_vs_rop", "03_inventory_vs_rop.png", chart_inventory_position),
        ("supplier_on_time", "04_supplier_on_time.png", chart_supplier_performance),
        ("supplier_delay", "05_supplier_delay_spread.png", chart_supplier_delay),
        ("abc_pareto", "06_abc_pareto.png", chart_abc_pareto),
        ("forecast_vs_actual", "07_forecast_vs_actual.png", chart_forecast_vs_actual),
        ("forecast_horizon", "08_forecast_horizon.png", chart_forecast_horizon),
        ("risk_flags", "09_risk_flags_summary.png", chart_flags_summary),
        ("stock_turnover", "10_stock_turnover.png", chart_stock_turnover),
    ]
    out = []
    for key, filename, fn in builders:
        if only and key not in only:
            continue
        try:
            path, interpretation = fn(result)
        except Exception as exc:                      # a chart must never break the run
            path, interpretation = _empty_chart(
                filename, key.replace("_", " ").title(), f"Chart could not be drawn: {exc}")
        out.append({
            "key": key,
            "file": Path(path).name,
            "path": str(path),
            "title": key.replace("_", " ").title(),
            "interpretation": interpretation,
        })
    return out
