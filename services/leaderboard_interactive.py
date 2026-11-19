"""Small, server-rendered portfolio series with an optional browser cursor."""

from bisect import bisect_left
from datetime import date


def comparison_plot(board, focus, peer=None):
    if not focus or not focus.get('curve'):
        return None
    dates = focus['curve_dates']
    traces = [(focus['name'], focus['curve'], '#efc775')]
    if peer and peer.get('curve') and peer['curve_dates'] == dates and peer['slug'] != focus['slug']:
        traces.append((peer['name'], peer['curve'], '#f494a7'))
    benchmarks = dict(board.get('benchmark_curve', []))
    if all(day in benchmarks for day in dates):
        traces.append(('SPY', [benchmarks[day] for day in dates], '#8ed1d8'))
    values = [value for _, line, _ in traces for value in line]
    low, high = min(values) * .97, max(values) * 1.025
    span = max(high - low, 1)
    x = lambda index: round(50 + index * 646 / (len(dates) - 1), 1)
    y = lambda value: round(15 + (high - value) * 160 / span, 1)
    chart = [{'label': label, 'color': color, 'values': line,
              'points': ' '.join(f'{x(i)},{y(value)}' for i, value in enumerate(line))}
             for label, line, color in traces]
    years = []
    for year in range(date.fromisoformat(dates[0]).year + 1, date.fromisoformat(dates[-1]).year + 1):
        i = bisect_left(dates, f'{year}-01-01')
        years.append((x(i), year))
    return {'dates': dates, 'traces': chart, 'years': years,
            'ticks': [(round(low + span * i / 4), y(low + span * i / 4)) for i in range(5)],
            'cursor': x(len(dates) - 1)}
