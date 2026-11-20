"""Publication graphics and prebuilt browser previews for the leaderboard."""

from __future__ import annotations

import io

import numpy as np
from PIL import Image

from services.filing_history import atomic_write
from services.leaderboard import CACHE_DIR
from services import leaderboard_charts as individual

plt = individual.plt
BG, PANEL, TEXT, MUTED = '#091018', '#101b26', '#eff6fc', '#9eafc1'
GOLD, CYAN, CORAL, GRID = '#efc775', '#74d8de', '#f494a7', '#283847'


def _name(row):
    return (row['name'].replace(' Asset Management', '').replace(' Family Office', '')
            .replace(' Hathaway', '').replace(' Technologies', '').replace(' Associates', ''))


def _ranked(board):
    return [row for row in board['rows'] if row['metrics']]


def _figure(board, title, sub):
    fig = plt.figure(figsize=(12.8, 7.2), dpi=300, facecolor=BG)
    fig.text(.017, .979, title, color=TEXT, fontsize=20, fontweight='bold', va='top')
    fig.text(.017, .918, sub, color=MUTED, fontsize=10, va='top')
    fig.text(.017, .029, f"{board['start']} – {board['as_of']}  ·  Adjusted equity prices  ·  SEC EDGAR",
             color=MUTED, fontsize=8)
    return fig


def _tone(slug):
    return GOLD if slug == 'berkshire' else CORAL if slug == 'scion' else CYAN


def _panel(ax, title, ylabel):
    ax.set_facecolor(PANEL)
    ax.set_title(title, loc='left', color=TEXT, fontsize=12, fontweight='bold', pad=10)
    ax.set_ylabel(ylabel, color=MUTED, fontsize=9)
    ax.grid(color=GRID, linewidth=.55)
    ax.tick_params(colors=MUTED, length=0, labelsize=8)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_color(GRID)


def _series_axis(ax, board, rows):
    import matplotlib.dates as dates
    from datetime import date
    _panel(ax, 'Growth of USD 100', 'USD')
    xs, ys = zip(*board['benchmark_curve'])
    ax.plot([date.fromisoformat(day) for day in xs], ys, '--', color=MUTED, linewidth=1.3, label='SPY')
    limit = 7
    featured = [row for row in rows if row['slug'] in ('berkshire', 'scion')]
    featured += [row for row in rows if row not in featured]
    for row in featured[:limit]:
        xs = [date.fromisoformat(day) for day in row['curve_dates']]
        picked = row['slug'] in ('berkshire', 'scion')
        ax.plot(xs, row['curve'], color=_tone(row['slug']), alpha=1 if picked else .42,
                linewidth=2 if picked else .95, label=_name(row) if picked else None)
    tick = dates.AutoDateLocator(minticks=4, maxticks=8)
    ax.xaxis.set_major_locator(tick)
    ax.xaxis.set_major_formatter(dates.ConciseDateFormatter(tick))
    ax.set_xlim(date.fromisoformat(board['start']), date.fromisoformat(board['as_of']))
    ax.legend(loc='upper left', fontsize=8, frameon=False, labelcolor=TEXT, ncol=3)


def _matrix(ax, data, labels, headers, scale, annotations):
    ax.imshow(data, aspect='auto', cmap='YlGnBu_r', vmin=scale[0], vmax=scale[1])
    ax.set_xticks(range(len(headers)), labels=headers, color=TEXT, fontsize=8, rotation=48, rotation_mode='anchor', ha='left')
    ax.set_yticks(range(len(labels)), labels=labels, color=TEXT, fontsize=9)
    ax.tick_params(axis='both', length=0, pad=8)
    ax.xaxis.tick_top()
    for spine in ax.spines.values():
        spine.set_visible(False)
    for i, row in enumerate(annotations):
        for j, value in enumerate(row):
            tone = BG if np.isfinite(data[i][j]) and data[i][j] >= sum(scale) / 2 else TEXT
            ax.text(j, i, value, color=tone, fontsize=9, ha='center', va='center', fontweight='bold')


def _atlas(board):
    rows = _ranked(board)
    fig = _figure(board, 'Anatomy of the long book', f"{len(rows)} comparable managers  /  Benchmark SPY  /  {board['window_years']}-year risk and return")
    g = fig.add_gridspec(3, 4, left=.063, right=.985, bottom=.105, top=.831, hspace=.73, wspace=.48)
    growth = fig.add_subplot(g[0, :])
    _series_axis(growth, board, rows)

    scatter = fig.add_subplot(g[1, :2])
    _panel(scatter, 'Risk versus return', 'Annualized return (%)')
    scatter.set_xlabel('Annualized volatility (%)', color=MUTED, fontsize=9)
    for row in rows:
        m = row['metrics']
        x, y = m['volatility'] * 100, m['annualized_return'] * 100
        scatter.scatter(x, y, s=28 + (row['score'] or 0) / 2, c=_tone(row['slug']), alpha=.87)
        if row['slug'] in ('berkshire', 'scion'):
            scatter.annotate(_name(row), (x, y), xytext=(5, 5), textcoords='offset points',
                             fontsize=8, color=_tone(row['slug']))
    scatter.margins(.18)

    def dot_ranking(ax, title, rows, values, formatter):
        _panel(ax, title, '')
        levels = range(len(rows))
        ax.hlines(levels, 0, values, color=GRID, linewidth=1.3)
        ax.scatter(values, levels, s=27, color=[_tone(row['slug']) for row in rows], zorder=3)
        ax.set_yticks(levels)
        ax.tick_params(axis='y', labelleft=False)
        for y, row in zip(levels, rows):
            ax.text(.018, y - .12, _name(row)[:23], transform=ax.get_yaxis_transform(),
                    color=TEXT, fontsize=7.2, va='bottom',
                    bbox={'facecolor': PANEL, 'edgecolor': 'none', 'pad': .25})
        ax.margins(y=.21)
        ax.invert_yaxis()
        ax.set_xlim(left=0, right=max(values, default=1) * 1.23)
        ax.xaxis.set_major_formatter(formatter)
        ax.grid(axis='y', visible=False)

    from matplotlib.ticker import FuncFormatter
    percent = FuncFormatter(lambda value, _: f'{value:.0f}%')
    risk = fig.add_subplot(g[1, 2:])
    bottom = sorted(rows, key=lambda row: row['metrics']['max_drawdown'])[:4]
    dot_ranking(risk, 'Worst drawdown', bottom, [abs(r['metrics']['max_drawdown']) * 100 for r in bottom], percent)

    value = fig.add_subplot(g[2, 0])
    multiples = [row for row in board['rows'] if row.get('pe', {}).get('weighted')
                 and row['pe']['coverage'] >= .50]
    if multiples:
        top = sorted(multiples, key=lambda r: r['pe']['weighted'], reverse=True)[:4]
        dot_ranking(value, 'Trailing valuation', top, [r['pe']['weighted'] for r in top],
                    FuncFormatter(lambda amount, _: f'{amount:.0f}×'))
    else:
        top = sorted((r for r in board['rows'] if r['size']), key=lambda r: r['size'], reverse=True)[:4]
        dot_ranking(value, 'Disclosed equity value', top, [r['size'] / 1e9 for r in top],
                    FuncFormatter(lambda amount, _: f'${amount:.0f}B'))

    conviction = fig.add_subplot(g[2, 1])
    biggest = sorted(rows, key=lambda r: r['metrics']['concentration'], reverse=True)[:4]
    dot_ranking(conviction, 'Largest position', biggest,
                [100 * r['metrics']['concentration'] for r in biggest], percent)
    import matplotlib.dates as dates
    from datetime import date
    history = fig.add_subplot(g[2, 2:])
    _panel(history, 'Disclosed equity value over time', 'USD billions · log scale')
    tracked = sorted((row for row in board['rows'] if row['size']), key=lambda row: -row['size'])[:4]
    palette = (GOLD, CYAN, '#b7a8fb', CORAL)
    for row, color in zip(tracked, palette):
        series = [(date.fromisoformat(day), value / 1e9) for day, value in row['disclosed_values'] if value > 0]
        if not series:
            continue
        xs, ys = zip(*series)
        history.plot(xs, ys, color=color, linewidth=1.5, label=_name(row))
    history.set_yscale('log')
    history.xaxis.set_major_locator(dates.YearLocator(2))
    history.xaxis.set_major_formatter(dates.DateFormatter('%Y'))
    history.legend(loc='lower left', fontsize=7, frameon=True, facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT, ncol=2)
    return fig


def _comparison(board):
    rows = _ranked(board)
    fig = _figure(board, 'Where the managers differ', 'Risk percentiles and overlap across disclosed equity books')
    g = fig.add_gridspec(1, 2, left=.13, right=.978, bottom=.108, top=.705, wspace=.40)
    score = fig.add_subplot(g[0])
    chosen = rows[:10]
    fields = [('annualized_return', 'RETURN'), ('volatility', 'LOW VOL'), ('sharpe_ratio', 'SHARPE'),
              ('max_drawdown', 'SMALL DD'), ('concentration', 'LOW TOP WT')]
    board_text = [[row.get('percentiles', {}).get(k, np.nan) for k, _ in fields] for row in chosen]
    if chosen:
        _matrix(score, np.array(board_text), [_name(row) for row in chosen], [label for _, label in fields],
                (0, 100), [['—' if np.isnan(v) else f'{v:.0f}' for v in rr] for rr in board_text])
    else:
        score.axis('off')
        score.text(.0, .5, 'Comparable histories are still being built', transform=score.transAxes, color=TEXT)
    fig.text(.017, .857, 'Rank percentile · higher is better', color=GOLD, fontsize=11, fontweight='bold')

    overlap = fig.add_subplot(g[1])
    candidates = sorted((r for r in board['rows'] if r['latest_tops']), key=lambda r: -r['size'])
    focus = [r for r in candidates if r['slug'] in ('berkshire', 'scion', 'pershing', 'duquesne')]
    focus = (focus + [r for r in candidates if r not in focus])[:6]
    if focus:
        def shared(a, b):
            return sum(min(a.get(k, 0), b.get(k, 0)) for k in a.keys() & b.keys())
        matrix = np.array([[shared(a['latest_tops'], b['latest_tops']) if a is not b else 1
                            for b in focus] for a in focus])
        _matrix(overlap, matrix, [_name(row) for row in focus],
                [_name(row).split()[0] for row in focus], (0, 1),
                [[f'{100 * v:.0f}%' for v in row] for row in matrix])
    else:
        overlap.axis('off')
    fig.text(.55, .857, 'Shared weight · positions of at least 0.5%', color=GOLD, fontsize=11, fontweight='bold')
    return fig


FEATURED = {'atlas': ('Anatomy of the long book', _atlas),
            'matrix': ('Where the managers differ', _comparison)}


def chart_file(board, key, ext):
    revision = board.get('charts_revision', board['revision'])
    return CACHE_DIR / 'charts' / f"{revision}-{board['cohort']}-{key}.{ext}"


def _png(board, key):
    if key in FEATURED:
        fig = FEATURED[key][1](board)
        try:
            out = io.BytesIO()
            fig.savefig(out, format='png', facecolor=BG)
            return out.getvalue()
        finally:
            plt.close(fig)
    return individual.render(board, key).getvalue()


def warm(board):
    if not board.get('as_of'):
        return
    # Avoid paying for gallery assets that have no underlying observations.
    available = {key for key in individual.CHARTS if key not in ('pe', 'crisis')}
    if any(row['pe'].get('weighted') for row in board['rows']):
        available.add('pe')
    if any(any(value is not None for value in row['crisis'].values()) for row in board['rows']):
        available.add('crisis')
    if _ranked(board):
        available.update(FEATURED)
    else:
        available.clear()
    for key in sorted(available):
        png = _png(board, key)
        atomic_write(chart_file(board, key, 'png'), png)
        with Image.open(io.BytesIO(png)) as image:
            image.thumbnail((850, 1050))
            output = io.BytesIO()
            image.convert('RGB').save(output, 'WEBP', quality=85, method=5)
        atomic_write(chart_file(board, key, 'webp'), output.getvalue())
    board['charts'] = sorted(available)
