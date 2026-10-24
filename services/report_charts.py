"""Static chart images for the printable report.

Rendered with matplotlib from the same cached analysis payload the
interactive dashboard uses, so the PDF never depends on browser timing.
"""

import logging
import os
from datetime import datetime, timedelta

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

logger = logging.getLogger(__name__)

OUT_DIR = os.path.join("static", "report")
PAPER = "#fbfaf7"
INK = "#14171b"
MUTED = "#6d7480"
GRID = "#e3ded2"
BRASS = "#a8824a"
SERIES = ["#0e1116", "#a8824a", "#4b5a6a", "#8c5f4d", "#6b7f6a", "#c9b18a", "#7f8a94", "#b98f62"]
GREEN = "#4f7a5c"
RED = "#b5544a"
RED_GREEN = ["#7f1d1d", "#2f2f2f", "#14532d"]

CHART_FILES = [
    ("allocation.png", "Sector allocation"),
    ("performance.png", "Cumulative performance"),
    ("drawdown.png", "Drawdown"),
    ("distribution.png", "Return distribution"),
    ("monte_carlo.png", "Monte Carlo"),
    ("volatility.png", "Rolling volatility"),
    ("monthly_returns.png", "Monthly returns"),
    ("efficient_frontier.png", "Efficient frontier"),
    ("risk_contribution.png", "Risk contribution"),
    ("pca.png", "PCA variance"),
    ("rolling_correlation.png", "Rolling correlation"),
    ("correlation.png", "Asset correlation"),
]


def _style(ax, title=None):
    ax.set_facecolor(PAPER)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=7, length=0)
    ax.grid(color=GRID, linewidth=0.5, alpha=0.7)
    ax.set_axisbelow(True)
    if title:
        ax.set_title(title, color=INK, fontsize=9, loc="left", pad=6)


def _save(fig, name):
    fig.patch.set_facecolor(PAPER)
    fig.tight_layout(pad=0.4)
    fig.savefig(os.path.join(OUT_DIR, name), dpi=190, facecolor=PAPER)
    plt.close(fig)


def _allocation(charts):
    pie = charts.get("allocation", {}).get("pie") or {}
    labels, values = pie.get("labels") or [], pie.get("values") or []
    if not values:
        return
    order = np.argsort(values)[::-1]
    labels = [labels[i] for i in order]
    values = [values[i] for i in order]
    fig, ax = plt.subplots(figsize=(5.4, 3.2))
    wedges, _ = ax.pie(values, colors=SERIES[: len(values)], startangle=90,
                       wedgeprops={"width": 0.42, "edgecolor": PAPER, "linewidth": 1})
    ax.legend(wedges, [f"{l} {v * 100:.0f}%" for l, v in zip(labels, values)],
              loc="center left", bbox_to_anchor=(1.0, 0.5), frameon=False,
              fontsize=5.6, labelcolor=INK)
    ax.set_aspect("equal")
    _save(fig, "allocation.png")


def _performance(charts):
    perf = charts.get("performance") or {}
    portfolio = perf.get("portfolio") or {}
    if not portfolio.get("values"):
        return
    fig, ax = plt.subplots(figsize=(6.0, 2.5))
    _style(ax)
    for name, series in (perf.get("benchmarks") or {}).items():
        values = series.get("values") or []
        if len(values) > 1:
            ax.plot(range(len(values)), values, color=GRID, linewidth=1.0, label=name)
    ax.plot(range(len(portfolio["values"])), portfolio["values"], color=BRASS, linewidth=1.8,
            label="Portfolio")
    ax.axhline(1.0, color=GRID, linewidth=0.8)
    ax.legend(frameon=False, fontsize=6, ncol=3, loc="upper left", labelcolor=INK)
    _save(fig, "performance.png")


def _drawdown(charts):
    dd = charts.get("drawdown") or {}
    values = dd.get("drawdown") or []
    if not values:
        return
    fig, ax = plt.subplots(figsize=(6.0, 2.1))
    _style(ax)
    x = np.arange(len(values))
    ax.fill_between(x, np.array(values) * 100, 0, color=RED, alpha=0.25)
    ax.plot(x, np.array(values) * 100, color=RED, linewidth=1.0)
    ax.set_ylabel("Drawdown %", color=MUTED, fontsize=7)
    _save(fig, "drawdown.png")


def _distribution(charts):
    rd = charts.get("return_distribution") or {}
    kde = rd.get("kde") or {}
    hist = rd.get("histogram") or {}
    if not kde.get("x"):
        return
    fig, ax = plt.subplots(figsize=(5.4, 2.3))
    _style(ax)
    if hist.get("bins") and hist.get("counts"):
        bins = np.array(hist["bins"])
        counts = np.array(hist["counts"], dtype=float)
        width = np.diff(bins)
        ax.bar(bins[:-1], counts, width=width, align="edge", color=GRID, alpha=0.8)
    ax.plot(kde["x"], kde["y"], color=INK, linewidth=1.4)
    ax.fill_between(kde["x"], kde["y"], color=INK, alpha=0.05)
    _save(fig, "distribution.png")


def _monte_carlo(charts):
    mc = charts.get("monte_carlo") or {}
    pct = mc.get("percentiles") or {}
    if not pct.get("p50"):
        return
    fig, ax = plt.subplots(figsize=(6.0, 2.3))
    _style(ax)
    days = np.arange(len(pct["p50"]))
    ax.fill_between(days, np.array(pct["p5"]), np.array(pct["p95"]), color=GRID, alpha=0.55,
                    label="5-95%")
    ax.fill_between(days, np.array(pct["p25"]), np.array(pct["p75"]), color=BRASS, alpha=0.25,
                    label="25-75%")
    ax.plot(days, pct["p50"], color=INK, linewidth=1.2, label="Median")
    ax.legend(frameon=False, fontsize=6, loc="upper left", labelcolor=INK)
    _save(fig, "monte_carlo.png")


def _volatility(charts):
    vol = charts.get("volatility") or {}
    returns = vol.get("returns") or []
    if len(returns) < 40:
        return
    series = np.array(returns, dtype=float)
    window = 21
    rolling = np.array([series[max(0, i - window):i + 1].std() for i in range(len(series))])
    rolling = rolling * np.sqrt(252) * 100
    fig, ax = plt.subplots(figsize=(6.0, 2.1))
    _style(ax)
    ax.plot(np.arange(len(rolling)), rolling, color=BRASS, linewidth=1.2)
    ax.set_ylabel("Vol % annualised", color=MUTED, fontsize=7)
    _save(fig, "volatility.png")


def _heat(ax, matrix, title=None):
    _style(ax, title)
    image = ax.imshow(matrix, cmap=matplotlib.colors.LinearSegmentedColormap.from_list("rg", RED_GREEN),
                      aspect="auto")
    return image


def _monthly(charts):
    mr = charts.get("monthly_returns") or {}
    data = mr.get("data") or []
    if not data:
        return
    matrix = np.array([[np.nan if v is None else v * 100 for v in row] for row in data])
    fig, ax = plt.subplots(figsize=(6.0, 2.2))
    image = _heat(ax, matrix)
    ax.set_xticks(range(len(mr.get("months") or [])))
    ax.set_xticklabels(mr.get("months") or [], fontsize=6)
    ax.set_yticks(range(len(mr.get("years") or [])))
    ax.set_yticklabels(mr.get("years") or [], fontsize=6)
    ax.grid(False)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            if not np.isnan(matrix[i, j]):
                ax.text(j, i, f"{matrix[i, j]:.1f}", ha="center", va="center", fontsize=5.2,
                        color="#ffffff")
    fig.colorbar(image, ax=ax, fraction=0.025, pad=0.01).ax.tick_params(labelsize=5.5, colors=MUTED)
    _save(fig, "monthly_returns.png")


def _correlation(example_data):
    matrix_map = example_data.get("correlation_matrix") or {}
    if not matrix_map:
        return
    tickers = sorted(matrix_map)
    matrix = np.array([[matrix_map.get(a, {}).get(b, np.nan) for b in tickers] for a in tickers])
    fig, ax = plt.subplots(figsize=(5.6, 3.4))
    image = _heat(ax, matrix)
    ax.set_xticks([])
    ax.set_yticks(range(len(tickers)))
    ax.set_yticklabels(tickers, fontsize=6)
    ax.grid(False)
    fig.colorbar(image, ax=ax, fraction=0.03, pad=0.01).ax.tick_params(labelsize=5.5, colors=MUTED)
    _save(fig, "correlation.png")




def _efficient_frontier(charts):
    ef = charts.get("efficient_frontier") or {}
    frontier = ef.get("frontier") or []
    if not frontier:
        return
    fig, ax = plt.subplots(figsize=(6.0, 2.4))
    _style(ax)
    assets = ef.get("individual_assets") or []
    if assets:
        ax.scatter([a.get("volatility", 0) * 100 for a in assets],
                   [a.get("return_annualized", 0) * 100 for a in assets],
                   s=12, color=GRID, edgecolor=MUTED, linewidth=0.3, zorder=2)
    ax.plot([p.get("volatility", 0) * 100 for p in frontier],
            [p.get("return_annualized", 0) * 100 for p in frontier],
            color=BRASS, linewidth=1.4, zorder=3)
    current = ef.get("current_portfolio") or {}
    if current:
        ax.scatter([current.get("volatility", 0) * 100], [current.get("return", 0) * 100],
                   s=34, color=INK, zorder=4, label="Portfolio")
        ax.legend(frameon=False, fontsize=6, loc="lower right", labelcolor=INK)
    ax.set_xlabel("Volatility %", color=MUTED, fontsize=7)
    ax.set_ylabel("Return %", color=MUTED, fontsize=7)
    _save(fig, "efficient_frontier.png")


def _risk_contribution(charts):
    rc = charts.get("risk_contribution") or {}
    tickers = rc.get("tickers") or []
    values = rc.get("risk_contrib_pct") or []
    if not tickers or not values:
        return
    order = np.argsort(values)
    fig, ax = plt.subplots(figsize=(5.6, 2.8))
    _style(ax)
    ax.barh([tickers[i] for i in order], [values[i] for i in order], color=BRASS, height=0.6)
    ax.tick_params(axis="y", labelsize=6)
    ax.set_xlabel("% of total risk", color=MUTED, fontsize=7)
    _save(fig, "risk_contribution.png")


def _pca(charts):
    pca = charts.get("pca") or {}
    explained = pca.get("explained_variance") or []
    cumulative = pca.get("cumulative_variance") or []
    if not explained:
        return
    fig, ax = plt.subplots(figsize=(5.6, 2.3))
    _style(ax)
    x = np.arange(1, len(explained) + 1)
    ax.bar(x, np.array(explained) * 100, color=GRID, width=0.6, label="Explained")
    ax.plot(x, np.array(cumulative) * 100, color=INK, linewidth=1.2, marker="o", markersize=2,
            label="Cumulative")
    ax.legend(frameon=False, fontsize=6, labelcolor=INK)
    ax.set_xlabel("Component", color=MUTED, fontsize=7)
    ax.set_ylabel("% variance", color=MUTED, fontsize=7)
    _save(fig, "pca.png")


def _rolling_correlation(charts):
    rc = charts.get("rolling_correlation") or {}
    series = rc.get("correlations") or {}
    dates = rc.get("dates") or []
    if not series or not dates:
        return
    arrays = [np.array([np.nan if x is None else x for x in v], dtype=float)
              for v in series.values() if isinstance(v, list) and v]
    arrays = [a for a in arrays if a.size]
    if not arrays:
        return
    length = min(a.size for a in arrays)
    values = np.vstack([a[:length] for a in arrays])
    average = np.nanmean(values, axis=0)
    dates = dates[:length]
    fig, ax = plt.subplots(figsize=(6.0, 2.1))
    _style(ax)
    n = min(len(average), len(dates))
    ax.plot(np.arange(n), average[:n], color=BRASS, linewidth=1.2)
    ax.axhline(0, color=GRID, linewidth=0.8)
    ax.set_ylabel("Avg correlation", color=MUTED, fontsize=7)
    _save(fig, "rolling_correlation.png")

def generate(example_data, force=False):
    """Render every report image; skip when the set is fresh."""
    os.makedirs(OUT_DIR, exist_ok=True)
    charts = example_data.get("charts") or {}
    if not force and all(
        os.path.exists(os.path.join(OUT_DIR, name))
        and datetime.utcnow() - datetime.utcfromtimestamp(os.path.getmtime(os.path.join(OUT_DIR, name)))
        < timedelta(days=7)
        for name, _ in CHART_FILES
    ):
        return OUT_DIR

    jobs = [
        (_allocation, (charts,)),
        (_performance, (charts,)),
        (_drawdown, (charts,)),
        (_distribution, (charts,)),
        (_monte_carlo, (charts,)),
        (_volatility, (charts,)),
        (_monthly, (charts,)),
        (_correlation, (example_data,)),
        (_efficient_frontier, (charts,)),
        (_risk_contribution, (charts,)),
        (_pca, (charts,)),
        (_rolling_correlation, (charts,)),
    ]
    for func, args in jobs:
        try:
            func(*args)
        except Exception as exc:
            logger.warning("Report chart %s failed: %s", func.__name__, exc)
    return OUT_DIR
