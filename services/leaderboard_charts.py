"""Server-rendered leaderboard charts (dark terminal palette)."""

from __future__ import annotations

import io

import matplotlib

matplotlib.use('Agg')

import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

INK = '#06070a'
PANEL = '#0b0e12'
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
    'growth': {'title': 'Growth of 100', 'sub': 'Cumulative return of each fund over the window'},
    'riskreturn': {'title': 'Risk versus return', 'sub': 'Volatility against annualised return; bubble = composite score'},
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
    ax.set_title(title, color=TEXT, fontsize=13, fontweight='bold', loc='left', pad=16)
    ax.text(0, 1.035, sub, transform=ax.transAxes, color=MUTED, fontsize=8)
    ax.text(0, -0.16, f"as of {board.get('as_of', '')} · {board.get('window_years', 5)}y window · optimizer.simonhavlik.com/leaderboard",
            transform=ax.transAxes, color='#6b675f', fontsize=6.5)


def _hbar(board: dict, key: str, *, metric: str | None = None, pct: bool = False,
          signed: bool = True) -> io.BytesIO:
    metric = metric or key
    rows = [r for r in board['rows'] if (r.get('metrics') or {}).get(metric) is not None]
    rows.sort(key=lambda r: r['metrics'][metric], reverse=True)
    names = [r['name'] for r in rows][::-1]
    values = [r['metrics'][metric] for r in rows][::-1]

    fig, ax = plt.subplots(figsize=(8.4, 4.6), dpi=150)
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
    fig.tight_layout()
    out = io.BytesIO()
    fig.savefig(out, format='png', facecolor=INK, bbox_inches='tight')
    plt.close(fig)
    out.seek(0)
    return out


def _crisis(board: dict) -> io.BytesIO:
    rows = [r for r in board['rows'] if (r.get('crisis') or {}).get('covid') is not None]
    rows.sort(key=lambda r: r['crisis'].get('y2022') or 0, reverse=True)
    names = [r['name'] for r in rows]
    covid = [(r['crisis'].get('covid') or 0) * 100 for r in rows]
    bear = [(r['crisis'].get('y2022') or 0) * 100 for r in rows]

    fig, ax = plt.subplots(figsize=(9.6, 4.4), dpi=150)
    fig.patch.set_facecolor(INK)
    import numpy as np
    y = np.arange(len(names))
    h = 0.36
    ax.barh(y + h / 2, covid, height=h, color=BRASS, label='COVID 2020')
    ax.barh(y - h / 2, bear, height=h, color='#7f8a94', label='2022 bear')
    ax.set_yticks(y)
    ax.set_yticklabels(names)
    ax.axvline(0, color='#3a3f47', linewidth=0.8)
    ax.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, fontsize=8, loc='lower left')
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:.0f}%'))
    _decorate(ax, board, CHARTS['crisis']['title'], CHARTS['crisis']['sub'])
    fig.tight_layout()
    out = io.BytesIO()
    fig.savefig(out, format='png', facecolor=INK, bbox_inches='tight')
    plt.close(fig)
    out.seek(0)
    return out


def _growth(board: dict) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(9.6, 4.6), dpi=150)
    fig.patch.set_facecolor(INK)
    palette = ['#e8cd97', '#8fa3b8', '#c07a6a', '#7fa08a', '#a99bc4',
               '#d9c9a8', '#7f8a94', '#b98f62', '#9aa7b8', '#8c8272']
    for index, row in enumerate(board['rows']):
        curve = row.get('curve') or []
        if len(curve) < 3:
            continue
        if max(curve) < 10:  # older seeds stored a 1-based index
            curve = [value * 100 for value in curve]
        ax.plot(range(len(curve)), curve, linewidth=1.4 if index == 0 else 1.0,
                color=palette[index % len(palette)], alpha=0.95 if index < 3 else 0.65,
                label=row['name'] if index < 4 else None)
    ax.axhline(100, color='#3a3f47', linewidth=0.7, linestyle='--')
    ax.legend(facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, fontsize=7.5, loc='upper left', framealpha=0.9)
    ax.set_ylabel('Index (start = 100)')
    _decorate(ax, board, CHARTS['growth']['title'], CHARTS['growth']['sub'])
    ax.grid(axis='x', visible=False)
    ax.grid(axis='y', color=GRID, linewidth=0.6, alpha=0.8)
    fig.tight_layout()
    out = io.BytesIO()
    fig.savefig(out, format='png', facecolor=INK, bbox_inches='tight')
    plt.close(fig)
    out.seek(0)
    return out


def _riskreturn(board: dict) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(8.4, 5.0), dpi=150)
    fig.patch.set_facecolor(INK)
    for row in board['rows']:
        m = row.get('metrics') or {}
        if m.get('volatility') is None or m.get('annualized_return') is None:
            continue
        size = 40 + (row.get('score') or 0) * 3.2
        ax.scatter(m['volatility'] * 100, m['annualized_return'] * 100, s=size,
                   color=BRASS, alpha=0.75, edgecolors=BRASS_LIGHT, linewidths=0.7)
        ax.annotate(row['name'].split(' ')[0], (m['volatility'] * 100, m['annualized_return'] * 100),
                    textcoords='offset points', xytext=(7, 4), fontsize=7, color=MUTED)
    ax.set_xlabel('Volatility (%)')
    ax.set_ylabel('Annualised return (%)')
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:.0f}%'))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f'{v:.0f}%'))
    _decorate(ax, board, CHARTS['riskreturn']['title'], CHARTS['riskreturn']['sub'])
    ax.grid(axis='x', visible=True)
    ax.grid(axis='y', color=GRID, linewidth=0.6, alpha=0.8)
    fig.tight_layout()
    out = io.BytesIO()
    fig.savefig(out, format='png', facecolor=INK, bbox_inches='tight')
    plt.close(fig)
    out.seek(0)
    return out


def render(board: dict, key: str) -> io.BytesIO:
    if key == 'crisis':
        return _crisis(board)
    if key == 'growth':
        return _growth(board)
    if key == 'riskreturn':
        return _riskreturn(board)
    if key == 'score':
        return _hbar(board, 'score')
    if key == 'alpha':
        return _hbar(board, 'alpha', pct=True)
    if key == 'drawdown':
        return _hbar(board, 'drawdown', metric='max_drawdown', pct=True, signed=False)
    if key == 'volatility':
        return _hbar(board, key, pct=True, signed=False)
    if key in ('sharpe', 'sortino', 'beta'):
        return _hbar(board, key, signed=(key != 'beta'))
    raise KeyError(key)


def warm(board: dict, cache_dir, as_of: str | None = None) -> int:
    """Pre-render every chart once, so the page never fans out eight cold renders."""
    from pathlib import Path
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    stamp = as_of or board.get('as_of', 'latest')
    written = 0
    for key in CHARTS:
        path = cache_dir / f'chart_{key}_{stamp}.png'
        if path.exists():
            continue
        try:
            path.write_bytes(render(board, key).getvalue())
            written += 1
        except Exception:
            continue
    return written
