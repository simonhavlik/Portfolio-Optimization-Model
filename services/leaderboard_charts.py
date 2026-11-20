"""Server-rendered leaderboard charts (dark terminal palette)."""

from __future__ import annotations

from datetime import date
import io

import matplotlib.dates as mdates

import matplotlib

matplotlib.use('Agg')

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter, MaxNLocator

INK = '#06070a'
PANEL = '#10161b'
TEXT = '#f1ede5'
MUTED = '#8f8a80'
BRASS = '#c8a15a'
BRASS_LIGHT = '#e8cd97'
UP = '#79b58c'
DOWN = '#c97f6e'
GRID = '#1b1f25'

plt.rcParams.update({
    'font.family': 'DejaVu Sans',
    'font.size': 9,
    'text.color': TEXT,
    'axes.labelcolor': MUTED,
    'xtick.color': MUTED,
    'ytick.color': MUTED,
    'axes.edgecolor': GRID,
    'figure.facecolor': INK,
    'axes.facecolor': INK,
})

CHARTS = {
    'score': {'title': 'Composite score', 'sub': 'Mean percentile across ten risk and return measures'},
    'sharpe': {'title': 'Sharpe ratio', 'sub': 'Excess return per unit of volatility'},
    'sortino': {'title': 'Sortino ratio', 'sub': 'Excess return per unit of downside deviation'},
    'alpha': {'title': 'Alpha vs benchmark', 'sub': 'Annualised return not explained by beta'},
    'drawdown': {'title': 'Maximum drawdown', 'sub': 'Worst peak-to-trough loss in the window'},
    'volatility': {'title': 'Annualised volatility', 'sub': 'Standard deviation of daily returns × √252'},
    'beta': {'title': 'Beta to benchmark', 'sub': 'Sensitivity to the S&P 500'},
    'crisis': {'title': 'Crisis behaviour', 'sub': 'Total return through COVID 2020 and the 2022 bear market'},
    'growth': {'title': 'Growth of $100', 'sub': 'Cumulative return of each fund over the window'},
    'riskreturn': {'title': 'Risk versus return', 'sub': 'Volatility against annualised return; bubble = composite score'},
    'pe': {'title': 'Weighted P/E of the book', 'sub': 'Value-weighted trailing multiple of disclosed holdings; dashed line = median across funds'},
}


def _decorate(ax, board: dict, title: str, sub: str) -> None:
    ax.set_facecolor(INK)
    for spine in ('top', 'right'):
        ax.spines[spine].set_visible(False)
    for spine in ('left', 'bottom'):
        ax.spines[spine].set_color(GRID)
    ax.grid(axis='x', color=GRID, linewidth=0.6, alpha=0.9)
    ax.set_axisbelow(True)
    ax.tick_params(colors=MUTED, labelsize=8, length=0)
    fig = ax.figure
    fig.text(.02, .973, title, color=TEXT, fontsize=17, fontweight='bold', va='top')
    fig.text(.02, .907, sub, color=MUTED, fontsize=9.5, va='top')
    fig.text(.02, .026, f"{board.get('start', '')} – {board.get('as_of', '')} · SEC Form 13F",
             color=MUTED, fontsize=8)
    if not isinstance(ax.xaxis.get_major_locator(), mdates.DateLocator):
        ax.xaxis.set_major_locator(MaxNLocator(8))


def _save(fig, *, bottom=.085, right=.985):
    out = io.BytesIO()
    try:
        fig.tight_layout(rect=(.012, bottom, right, .864), pad=.35)
        fig.savefig(out, format='png', facecolor=INK)
        out.seek(0)
        return out
    finally:
        plt.close(fig)


def _hbar(board: dict, key: str, *, metric: str | None = None, pct: bool = False,
          signed: bool = True) -> io.BytesIO:
    metric = metric or key

    def measure(row):
        # the composite lives on the row, the rest inside the metrics block
        if metric == 'score':
            return row.get('score')
        return (row.get('metrics') or {}).get(metric)

    rows = [r for r in board['rows'] if measure(r) is not None]
    rows.sort(key=measure, reverse=True)
    names = [r['name'] for r in rows][::-1]
    values = [measure(r) for r in rows][::-1]

    fig, ax = plt.subplots(figsize=(8.4, max(4.6, 1.44 + .245 * len(names))), dpi=250)
    fig.patch.set_facecolor(INK)
    colours = [BRASS if v >= 0 else DOWN for v in values]
    bars = ax.barh(names, values, color=colours, height=0.62)
    if values and min(values) < 0:
        ax.axvline(0, color='#3a3f47', linewidth=0.8)

    for bar, value in zip(bars, values):
        if pct:
            label = f'{value * 100:+.1f}%' if signed else f'{value * 100:.1f}%'
        else:
            label = f'{value:+.2f}' if signed else f'{value:.2f}'
        # labels live in a fixed column to the right of the plot, so long bars
        # and long fund names can never collide with them
        ax.text(1.015, bar.get_y() + bar.get_height() / 2, label,
                transform=ax.get_yaxis_transform(), va='center', ha='left',
                color=TEXT, fontsize=8)

    if pct:
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v * 100:.0f}%'))
    _decorate(ax, board, CHARTS[key]['title'], CHARTS[key]['sub'])
    return _save(fig)


def _crisis(board: dict) -> io.BytesIO:
    def present(row, key):
        value = (row.get('crisis') or {}).get(key)
        return float('nan') if value is None else value * 100

    rows = [r for r in board['rows']
            if (r.get('crisis') or {}).get('covid') is not None
            or (r.get('crisis') or {}).get('y2022') is not None]
    def resilience(row):
        values = [v for v in ((row.get('crisis') or {}).get('covid'), (row.get('crisis') or {}).get('y2022')) if v is not None]
        return sum(values) / len(values) if values else -9

    rows.sort(key=resilience, reverse=True)
    names = [r['name'] for r in rows]
    covid = [present(r, 'covid') for r in rows]
    bear = [present(r, 'y2022') for r in rows]

    fig, ax = plt.subplots(figsize=(9.6, max(4.4, 1.45 + .26 * len(names))), dpi=250)
    fig.patch.set_facecolor(INK)
    import numpy as np
    y = np.arange(len(names))
    h = 0.36
    ax.barh(y + h / 2, covid, height=h, color=BRASS, label='COVID 2020')
    ax.barh(y - h / 2, bear, height=h, color='#7f8a94', label='2022 bear')
    ax.set_yticks(y)
    ax.set_yticklabels(names)
    ax.axvline(0, color='#3a3f47', linewidth=0.8)
    ax.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, fontsize=8, loc='upper right')
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:.0f}%'))
    _decorate(ax, board, CHARTS['crisis']['title'], CHARTS['crisis']['sub'])
    return _save(fig)


def _growth(board: dict) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(9.6, 4.6), dpi=250)
    fig.patch.set_facecolor(INK)
    palette = ['#e8cd97', '#8fa3b8', '#c07a6a', '#7fa08a', '#a99bc4',
               '#d9c9a8', '#7f8a94', '#b98f62', '#9aa7b8', '#8c8272']
    if board.get('benchmark_curve'):
        dates, levels = zip(*board['benchmark_curve'])
        ax.plot([date.fromisoformat(d) for d in dates], levels, color=MUTED, linestyle='--',
                linewidth=1, label=board['benchmark'])
    for index, row in enumerate(board['rows']):
        curve = row.get('curve') or []
        if len(curve) < 3:
            continue
        dates = [date.fromisoformat(d) for d in row['curve_dates']]
        ax.plot(dates, curve, linewidth=1.4 if index < 4 else 1,
                color=palette[index % len(palette)], alpha=0.95 if index < 5 else 0.55,
                label=row['name'] if index < 6 else None)
    locator = mdates.AutoDateLocator(minticks=4, maxticks=9)
    ax.xaxis.set_major_locator(locator)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
    if board.get('start') and board.get('as_of'):
        ax.set_xlim(date.fromisoformat(board['start']), date.fromisoformat(board['as_of']))
    ax.axhline(100, color='#3a3f47', linewidth=0.7, linestyle='--')
    ax.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, fontsize=7.5, loc='upper left', framealpha=0.9)
    ax.set_ylabel('Index (start = 100)')
    _decorate(ax, board, CHARTS['growth']['title'], CHARTS['growth']['sub'])
    ax.grid(axis='x', visible=False)
    ax.grid(axis='y', color=GRID, linewidth=0.6, alpha=0.8)
    return _save(fig)


def _riskreturn(board: dict) -> io.BytesIO:
    rows = [row for row in board['rows'] if row['metrics'].get('volatility') is not None
            and row['metrics'].get('annualized_return') is not None]
    legend_rows = (len(rows) + 2) // 3
    height = 4.3 + .14 * legend_rows
    fig, ax = plt.subplots(figsize=(8.4, max(4.6, height)), dpi=250)
    legend_top = (0.43 + .14 * legend_rows) / fig.get_figheight()
    for i, row in enumerate(rows, 1):
        m = row['metrics']
        x, y = m['volatility'] * 100, m['annualized_return'] * 100
        size = 45 + (row.get('score') or 0) * 2.4
        ax.scatter(x, y, s=size, color=BRASS, alpha=.85, edgecolors=BRASS_LIGHT, linewidths=.6)
        ax.text(x, y, str(i), ha='center', va='center', fontsize=6.5, color=INK, fontweight='bold')
        line, column = divmod(i - 1, 3)
        fig.text(.020 + column * .329, legend_top - line * .14 / fig.get_figheight(), f"{i:02d}  {row['name']}",
                 color=TEXT, fontsize=7.8, va='top')
    ax.margins(.08)
    ax.set_xlabel('Volatility (%)')
    ax.set_ylabel('Annualised return (%)')
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:.0f}%'))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:.0f}%'))
    _decorate(ax, board, CHARTS['riskreturn']['title'], CHARTS['riskreturn']['sub'])
    ax.grid(axis='x', visible=True)
    ax.grid(axis='y', color=GRID, linewidth=0.6, alpha=0.8)
    return _save(fig, bottom=legend_top + .055)


def _pe(board: dict) -> io.BytesIO:
    rows = [r for r in board['rows'] if (r.get('pe') or {}).get('weighted')]
    rows.sort(key=lambda r: r['pe']['weighted'])
    names = [r['name'] for r in rows]
    values = [r['pe']['weighted'] for r in rows]
    median = board.get('pe_median')

    fig, ax = plt.subplots(figsize=(8.4, max(4.6, 1.44 + .245 * len(names))), dpi=250)
    fig.patch.set_facecolor(INK)
    bars = ax.barh(names, values, color=BRASS, height=0.62)
    if median:
        ax.axvline(median, color=BRASS_LIGHT, linewidth=0.9, linestyle='--', alpha=0.7)
        ax.text(median, len(names) - 0.35, f'  median {median:.1f}x', color=BRASS_LIGHT, fontsize=7)
    for bar, value in zip(bars, values):
        ax.text(1.015, bar.get_y() + bar.get_height() / 2, f'{value:.1f}x',
                transform=ax.get_yaxis_transform(), va='center', ha='left', color=TEXT, fontsize=8)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:.0f}x'))
    _decorate(ax, board, CHARTS['pe']['title'], CHARTS['pe']['sub'])
    return _save(fig)


def render(board: dict, key: str) -> io.BytesIO:
    if key == 'crisis':
        return _crisis(board)
    if key == 'pe':
        return _pe(board)
    if key == 'growth':
        return _growth(board)
    if key == 'riskreturn':
        return _riskreturn(board)
    if key == 'score':
        return _hbar(board, 'score', metric='score')
    if key == 'alpha':
        return _hbar(board, 'alpha', pct=True)
    if key == 'drawdown':
        return _hbar(board, 'drawdown', metric='max_drawdown', pct=True, signed=False)
    if key == 'volatility':
        return _hbar(board, 'volatility', metric='volatility', pct=True, signed=False)
    if key == 'sharpe':
        return _hbar(board, 'sharpe', metric='sharpe_ratio')
    if key == 'sortino':
        return _hbar(board, 'sortino', metric='sortino_ratio')
    if key == 'beta':
        return _hbar(board, 'beta', metric='beta', signed=False)
    raise KeyError(key)
