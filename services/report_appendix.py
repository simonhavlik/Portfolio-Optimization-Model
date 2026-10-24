"""Appendix tables for the printable report."""

from datetime import datetime

import numpy as np


def _annual_returns(monthly):
    years = monthly.get("years") or []
    data = monthly.get("data") or []
    result = []
    for index, year in enumerate(years):
        if index >= len(data):
            continue
        values = [v for v in data[index] if isinstance(v, (int, float))]
        if not values:
            continue
        compounded = float(np.prod([1 + v for v in values]) - 1)
        result.append({"year": year, "return": compounded, "months": len(values)})
    return result


def _drawdown_episodes(drawdown, count=5, spacing=20):
    values = drawdown.get("drawdown") or []
    dates = drawdown.get("dates") or []
    if not values:
        return []
    order = np.argsort(np.array(values, dtype=float))
    episodes = []
    used = []
    for index in order:
        if all(abs(int(index) - other) >= spacing for other in used):
            used.append(int(index))
            episodes.append({
                "date": dates[index] if index < len(dates) else "",
                "drawdown": float(values[index]),
            })
        if len(episodes) >= count:
            break
    return episodes


def _top_correlations(matrix, limit=8):
    pairs = []
    names = sorted(matrix)
    for i, first in enumerate(names):
        for second in names[i + 1:]:
            value = matrix.get(first, {}).get(second)
            if isinstance(value, (int, float)):
                pairs.append({"pair": f"{first}/{second}", "value": float(value)})
    pairs.sort(key=lambda item: abs(item["value"]), reverse=True)
    return pairs[:limit]


def build_appendix(example_data):
    charts = example_data.get("charts") or {}
    monthly = charts.get("monthly_returns") or {}
    return {
        "annual_returns": _annual_returns(monthly),
        "monthly": monthly,
        "drawdowns": _drawdown_episodes(charts.get("drawdown") or {}),
        "correlations": _top_correlations(example_data.get("correlation_matrix") or {}),
        "generated": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }
