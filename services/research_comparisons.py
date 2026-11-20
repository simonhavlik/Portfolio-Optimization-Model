"""Comparison panels for the downloadable research sheets."""

import numpy as np
import pandas as pd
from matplotlib.ticker import MaxNLocator, PercentFormatter, StrMethodFormatter

from services.issuer_classification import FUND_SECTOR, TECHNOLOGY, UNKNOWN
from services.leaderboard_research import ACCENTS, PAGE, PAPER, SOFT, _axis, _rule, _short


BOXES = ((.081, .607, .402, .245), (.587, .607, .386, .245),
         (.081, .123, .402, .258), (.587, .123, .386, .258))
PEERS = {
    'sectors': ('gates-trust', 'icahn', 'berkshire', 'bridgewater', 'citadel', 'pershing'),
    'industries': ('berkshire', 'pershing', 'tiger-global', 'appaloosa', 'wellington', 'fisher'),
    'sector-history': ('tiger-global', 'berkshire', 'pershing', 'appaloosa', 'point72'),
    'concentration': ('smith', 'berkshire', 'fisher', 'citadel'),
    'turnover': ('lone-pine', 'citadel', 'millennium', 'wellington'),
    'entry-exit': ('de-shaw', 'aqr', 'citadel', 'pershing'),
    'sector-gaps': ('renaissance', 'two-sigma', 'pershing', 'fairfax'),
}
METRICS = {
    'concentration': ('Top-ten weight', lambda row: row.get('top10', np.nan)),
    'turnover': ('Quarterly weight shift', lambda row: row.get('weight_shift', np.nan)),
    'entry-exit': ('Scaled entries and exits',
                   lambda row: min(1, (row['new'] + row['exited']) / (2 * row['positions']))
                   if 'new' in row else np.nan),
    'sector-gaps': ('Unclassified weight', lambda row: row.get('sectors', {}).get(UNKNOWN, np.nan)),
}


def _panel(fig, index, title, *, xpct=False, ypct=False):
    x, y, w, h = BOXES[index]
    ax = _axis(fig, (x, y, w, h))
    ax.set_title(title, loc='left', color=PAPER, fontsize=9.5, fontweight='bold', pad=8)
    for axis, percentage in ((ax.xaxis, xpct), (ax.yaxis, ypct)):
        axis.set_major_locator(MaxNLocator(nbins=5))
        if percentage:
            axis.set_major_formatter(PercentFormatter(1, decimals=0))
    return ax


def _legend(ax, *, columns=2):
    ax.legend(loc='best', ncols=columns, facecolor=PAGE, edgecolor='none', labelcolor=PAPER,
              framealpha=.92, fontsize=6.6, columnspacing=.65, handlelength=1.2)


def _history(fig, index, title, data, slugs, measure):
    ax = _panel(fig, index, title, ypct=True)
    periods, history = data['periods'], data['history']
    bench = np.asarray([[measure(history[slug].get(q, {})) for q in periods]
                        for slug in data['universe']])
    observed = np.isfinite(bench).any(axis=0)
    if observed.any():
        low, med, high = np.nanquantile(bench[:, observed], (.25, .5, .75), axis=0)
        index = np.flatnonzero(observed)
        ax.fill_between(index, low, high, color=ACCENTS[1], alpha=.16)
        ax.plot(index, med, color=SOFT, ls='--', lw=.75, label='Cohort median')
    for color, slug in zip(ACCENTS, slugs):
        ax.plot([measure(history[slug].get(q, {})) for q in periods],
                color=color, label=_short(slug), lw=1)
    ticks = list(range(0, len(periods), 16))
    ax.set_xticks(ticks, [periods[i][:4] for i in ticks])
    _legend(ax)


def _rank(fig, index, title, rows, *, percentage=True):
    ax = _panel(fig, index, title, xpct=percentage)
    labels, values = zip(*rows) if rows else ((), ())
    ax.barh(range(len(rows)), values, color=ACCENTS[1], height=.64)
    ax.set_yticks(range(len(rows)), labels, fontsize=7)
    ax.invert_yaxis()
    # Keep labels inside the panel, including names of manager pairs.
    ax.tick_params(axis='y', pad=3, direction='in', labelleft=False)
    for i, (name, value) in enumerate(rows):
        background = {'facecolor': PAGE, 'edgecolor': 'none', 'alpha': .84, 'pad': 1}
        ax.text(.018, i, name, transform=ax.get_yaxis_transform(), color=PAPER,
                fontsize=7, va='center', bbox=background)
        ax.text(.978, i, f'{value:.1%}' if percentage else f'{value:,.0f}',
                transform=ax.get_yaxis_transform(), ha='right', va='center',
                color=PAPER, fontsize=7, bbox=background)
    ax.grid(axis='x', color='#34434b', linewidth=.45)
    ax.grid(axis='y', visible=False)


def _scatter(fig, index, title, points, *, xpct=False, ypct=True, xlabel='', ylabel='', focus=(), log=False):
    ax = _panel(fig, index, title, xpct=xpct, ypct=ypct)
    ax.scatter([x for _,x,y in points], [y for _,x,y in points], s=9, alpha=.50, color=ACCENTS[1])
    selected = {name: (x, y) for name, x, y in points if name in focus}
    for color, name in zip(ACCENTS, focus):
        if name in selected:
            ax.scatter(*selected[name], s=22, color=color, label=_short(name), zorder=3)
    if selected:
        _legend(ax)
    if log:
        ax.set_xscale('log')
        ax.xaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
    ax.set_xlabel(xlabel, color=SOFT, fontsize=7, labelpad=3)
    ax.set_ylabel(ylabel, color=SOFT, fontsize=7, labelpad=2)


def _largest(rows, limit=7):
    return sorted((r for r in rows if np.isfinite(r[1])), key=lambda row: -row[1])[:limit]


def _date_ends(ax, index):
    first, last = index[0], index[-1]
    ax.set_xticks([first, last], [first.strftime('%b %y'), last.strftime('%b %y')])
    left, right = ax.get_xticklabels()
    left.set_ha('left')
    right.set_ha('right')


def _allocation(fig, key, data, latest, slugs):
    name = TECHNOLOGY if key == 'sectors' else 'Semiconductors'
    field = key

    def measure(row):
        return row[field].get(name, 0) if field in row else np.nan

    picks = {group for slug in slugs for group, weight in
             sorted(latest[slug][field].items(), key=lambda pair: -pair[1])[:2]
             if group not in {FUND_SECTOR, UNKNOWN} and weight > 0}
    groups = sorted(picks, key=lambda group: -sum(latest[s][field].get(group, 0) for s in slugs))[:6]
    ax = _panel(fig, 0, 'Largest allocations among selected managers')
    matrix = np.array([[latest[slug][field].get(group, 0) for group in groups] for slug in slugs])
    if groups:
        ax.imshow(matrix, cmap='viridis', interpolation='nearest', aspect='auto', vmin=0, vmax=max(.35, matrix.max()))
        ax.set_xticks(range(len(groups)), [g if len(g) < 18 else g[:15].rstrip()+'…' for g in groups],
                      rotation=25, ha='right', rotation_mode='anchor', fontsize=7)
        ax.set_yticks(range(len(slugs)), [_short(s) for s in slugs], fontsize=7)
        ax.grid(visible=False)
        for row, col in np.argwhere(matrix > .0005):
            v = matrix[row, col]
            ax.text(col, row, f'{v:.0%}', color=PAGE if v > .53*max(.35, matrix.max()) else PAPER,
                    ha='center', va='center', fontsize=7)
    _history(fig, 1, f'{name} exposure over time', data, slugs, measure)
    _rank(fig, 2, f'Highest {name.lower()} weights', _largest([(_short(slug), measure(row))
                                                               for slug, row in latest.items()]))
    _scatter(fig, 3, 'Breadth and exposure across the cohort',
             [(slug, r['positions'], measure(r)) for slug,r in latest.items()], log=True,
             xlabel='Reported positions (log scale)', ylabel=f'{name} weight', focus=slugs)


def _pairs(fig, key, data):
    pairs = data['pairs']
    label = lambda pair: f"{_short(pair['left'])} / {_short(pair['right'])}"
    _rank(fig, 0, 'Largest overlaps by portfolio weight',
          _largest([(label(p), p['overlap']) for p in pairs], 6))
    _rank(fig, 1, 'Most shared names' if key == 'shared-stocks' else 'Largest sector match above stock match',
          _largest([(label(p), p['shared'] if key == 'shared-stocks' else p['sectors'] - p['overlap'])
                    for p in pairs], 6), percentage=key != 'shared-stocks')
    _scatter(fig, 2, 'Number of names and weighted overlap',
             [(label(p), p['shared'], p['overlap']) for p in pairs],
             xlabel='Shared positions', ylabel='Shared weight')
    _scatter(fig, 3, 'Weighted overlap and return correlation',
             [(label(p), p['overlap'], p['correlation']) for p in pairs if p['correlation'] is not None],
             xpct=True, ypct=False, xlabel='Shared weight', ylabel='Return correlation (r)')


def _crowded(fig, data, latest):
    stocks = data['crowded'][:22]
    names = [s['issuer'].title()[:26] for s in stocks]
    maxima = [max(s['weights'].values(), default=0) for s in stocks]
    _rank(fig, 0, 'Most managers holding each stock', list(zip(names, [s['count'] for s in stocks]))[:7], percentage=False)
    _rank(fig, 1, 'Largest portfolio weight in one book', _largest(list(zip(names, maxima))))
    _scatter(fig, 2, 'How broadly held versus how heavily held',
             [(name, s['count'], peak) for name,s,peak in zip(names,stocks,maxima)],
             xlabel='Number of managers holding it', ylabel='Largest weight in one book')
    _rank(fig, 3, 'Heaviest allocations to the 22 most common names',
          _largest([(_short(slug), sum(s['weights'].get(slug, 0) for s in stocks)) for slug in latest]))


def cohort_comparisons(fig, study, data):
    key = study.key
    latest = {slug: data['history'][slug][data['as_of']] for slug in data['universe']}
    slugs = [slug for slug in PEERS.get(key, ()) if slug in latest]
    if key in {'sectors', 'industries'}:
        _allocation(fig, key, data, latest, slugs)
    elif key in {'shared-stocks', 'style-test'}:
        _pairs(fig, key, data)
    elif key == 'crowding':
        _crowded(fig, data, latest)
    else:
        metric, measure = METRICS.get(
            key, ('Technology weight', lambda row: row.get('sectors', {}).get(TECHNOLOGY, np.nan)))
        _history(fig, 0, f'{metric} by filing quarter', data, slugs, measure)
        _rank(fig, 1, f'Highest latest {metric.lower()}',
              _largest([(_short(slug), measure(r)) for slug, r in latest.items()]))
        _scatter(fig, 2, 'Latest reading against number of positions',
                 [(s,r['positions'],measure(r)) for s,r in latest.items() if np.isfinite(measure(r))],
                 log=True, xlabel='Reported positions (log scale)', ylabel=metric, focus=slugs)
        if len(data['periods']) > 4:
            prior = data['periods'][-5]
            comparable = [(s, measure(data['history'][s].get(prior, {})), measure(r)) for s,r in latest.items()]
            _scatter(fig, 3, 'Latest quarter versus a year earlier',
                     [r for r in comparable if np.isfinite(r[1:]).all()], xpct=True,
                     xlabel=prior, ylabel=data['as_of'], focus=slugs)
    _rule(fig, .98)
    fig.text(.014, .95, 'Manager comparisons', color=PAPER, fontsize=11, va='top', fontweight='bold')
    footer = ('Shaded ranges show the middle half of managers observed in each quarter.'
              if key in PEERS else 'Positions and weights come from the latest SEC filing quarter.')
    if key == 'entry-exit':
        footer = 'Entries plus exits divided by twice the latest position count, capped at 100%. Shading shows the middle half.'
    fig.text(.014, .025, footer, color=SOFT, fontsize=7.2)


def fund_comparisons(fig, data):
    slug = data['study'].funds[0]
    rows = data['board']['rows']
    cols = {_short(slug): data['series'][slug], **data['benchmarks']}
    daily = pd.DataFrame(cols).dropna()
    ann = (1 + daily).groupby(daily.index.year).prod() - 1
    sessions = daily.groupby(daily.index.year).size()
    ax = _panel(fig, 0, 'Calendar-year returns on common priced days', ypct=True)
    offsets = np.linspace(-.22, .22, len(daily.columns))
    for (name, values), shift, color in zip(ann.items(), offsets, ACCENTS):
        ax.bar(np.arange(len(ann)) + shift, values, width=.21, label=name, color=color)
    ax.set_xticks(range(len(ann)), [f'{year}{"*" if sessions[year] < 245 else ""}' for year in ann.index])
    _legend(ax)
    rolling = daily.rolling(63).std() * np.sqrt(252)
    ax = _panel(fig, 1, '63-session realized volatility', ypct=True)
    for (name, series), color in zip(rolling.items(), ACCENTS):
        ax.plot(series, color=color, label=name, lw=.85)
    _date_ends(ax, daily.index)
    _legend(ax)
    ax = _panel(fig, 2, 'Cumulative wealth relative to SPY', ypct=True)
    growth = (1 + daily).cumprod()
    for name, color in zip((n for n in growth if n != 'SPY'), (ACCENTS[0], ACCENTS[2])):
        ax.plot(growth[name].div(growth['SPY']) - 1, label=name, color=color, lw=1.1)
    ax.axhline(0, color=SOFT, linewidth=.55)
    _date_ends(ax, daily.index)
    _legend(ax)
    ax = _panel(fig, 3, 'Return and volatility across priced managers', xpct=True, ypct=True)
    ranked = [r for r in rows if r['metrics']]
    ax.scatter([r['metrics']['volatility'] for r in ranked], [r['metrics']['annualized_return'] for r in ranked],
               color=ACCENTS[1], s=14, alpha=.6, label=f'{len(ranked)} ranked managers')
    for peer,color in ((slug, ACCENTS[0]), ('duquesne', ACCENTS[2])):
        item = next((r for r in ranked if r['slug'] == peer), None)
        if item:
            ax.scatter(item['metrics']['volatility'], item['metrics']['annualized_return'],
                       s=29, color=color, label=_short(peer), zorder=3)
    ax.set_xlabel('Annualized volatility', color=SOFT, fontsize=7)
    ax.set_ylabel('Annualized return', color=SOFT, fontsize=7)
    _legend(ax, columns=1)
    _rule(fig, .98)
    fig.text(.014, .95, 'Performance and peer comparisons', color=PAPER, fontsize=11, va='top', fontweight='bold')
    fig.text(.014, .025, '* Fewer than 245 common sessions. Annual bars show cumulative returns for the sessions included.',
             color=SOFT, fontsize=7.2)
