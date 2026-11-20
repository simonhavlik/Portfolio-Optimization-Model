"""Cohort-wide chart and report panels for institutional holdings research."""

import numpy as np

from services.holdings_style import INDUSTRIES
from services.leaderboard_research import PAGE, PAPER, RULE, SOFT, _rule, _short, _table

TOPICS = {
    'sectors': ('Where the largest 13F books are invested', 'Reported value by industry'),
    'sector-history': ('How institutional technology exposure changed', 'Business equipment weight by quarter'),
    'shared-stocks': ('Common holdings across the largest managers', 'Shared weight across all disclosed holdings'),
    'style-test': ('Does holding the same stocks imply the same strategy?', 'Shared stock weight vs. classified industry overlap'),
    'crowding': ('Which stocks appear in the most 13Fs?', 'Most widely held positions and portfolio weights'),
    'concentration': ('How concentrated are institutional portfolios?', 'Weight in the ten largest holdings each quarter'),
    'turnover': ('Where managers reshuffled the book', 'Half the sum of absolute weight changes vs. the preceding report'),
    'entry-exit': ('Which managers keep the same names?', 'Position entries and exits as a share of the reported book'),
    'sector-gaps': ('What can 13F data price and classify?', 'Classified weight vs. positions per manager'),
}


def main_chart(fig, study, data):
    mode = study.key
    universe, history = data['universe'], data['history']
    latest, quarters = data['as_of'], data['periods']
    names = [_short(slug) for slug in universe]
    title, metric = TOPICS[mode]
    fig.text(.012, .983, title, va='top', color=PAPER, fontsize=17, fontweight='bold')
    fig.text(.012, .92, f'{len(universe)} managers  /  {metric}', va='top', color=SOFT, fontsize=11)
    fig.text(.987, .92, latest, va='top', ha='right', color=SOFT, fontsize=10)
    categorical = mode in {'sectors', 'shared-stocks', 'crowding'}
    bottom = .168 if categorical else .084
    left, right = (.084, .937) if mode == 'style-test' else (.112, .969)
    ax = fig.add_axes((left, bottom, right - left, .825 - bottom), facecolor=PAGE)
    ax.tick_params(length=0, colors=SOFT, labelsize=8)
    for spine in ax.spines.values():
        spine.set_visible(False)
    if mode == 'sectors':
        matrix = [[history[s][latest]['sectors'][k] for k in INDUSTRIES] for s in universe]
        labels = list(INDUSTRIES.values())
    elif mode == 'shared-stocks':
        lookup = {(p['left'], p['right']): p['overlap'] for p in data['pairs']}
        matrix = [[lookup.get((a, b), lookup.get((b, a), np.nan)) for b in universe] for a in universe]
        labels = names
    elif mode == 'crowding':
        companies = data['crowded'][:22]
        matrix = [[stock['weights'].get(s, 0) for stock in companies] for s in universe]
        labels = [stock['issuer'][:17].title() for stock in companies]
    elif mode in {'sector-history', 'concentration', 'turnover', 'entry-exit'}:
        def value(row):
            if mode == 'sector-history':
                return row['sectors']['BusEq'] if row else np.nan
            if mode == 'entry-exit':
                return min(1, (row['new'] + row['exited']) / (2*row['positions'])) if 'new' in row else np.nan
            return row.get('top10' if mode == 'concentration' else 'weight_shift', np.nan)

        matrix = [[value(history[slug].get(q, {})) for q in quarters] for slug in universe]
        labels = [q[:4] if q.endswith('-12-31') and int(q[:4]) % 2 == 0 else '' for q in quarters]
    else:
        ax.grid(color=RULE, linewidth=.45)
        if mode == 'style-test':
            pairs = data['pairs']
            observed = [p for p in pairs if p['correlation'] is not None]
            ax.scatter([p['overlap']*100 for p in pairs], [p['sectors']*100 for p in pairs],
                       color='#83dbe0', s=12, alpha=.42, label=f'{len(pairs)} manager pairs')
            if observed:
                points = ax.scatter([p['overlap']*100 for p in observed],
                                    [p['sectors']*100 for p in observed],
                                    c=[p['correlation'] for p in observed], vmin=-1, vmax=1, s=24,
                                    cmap='coolwarm', label=f'{len(observed)} pairs with priced correlation')
                bar = fig.colorbar(points, ax=ax, aspect=30, fraction=.025, pad=.015)
                bar.set_label('Return correlation', color=PAPER)
                bar.ax.tick_params(colors=SOFT)
            ax.set(xlabel='Shared holdings weight, %', ylabel='Classified industry match, %')
            ax.legend(loc='upper right', facecolor=PAGE, labelcolor=PAPER)
        else:
            for i, s in enumerate(universe):
                reading = history[s][latest]
                ax.scatter(reading['sector_coverage']*100, i, color='#83dbe0', s=22)
                ax.text(reading['sector_coverage']*100+1,i,f"{reading['positions']:,}",va='center',fontsize=8,color=PAPER)
            ax.set_yticks(range(len(names)), names)
            ax.invert_yaxis()
            ax.set(xlabel='Classified share of reported long equity value, %', xlim=(0, 110))
        return
    image = ax.imshow(np.ma.masked_invalid(matrix), aspect='auto', vmin=0,
                      vmax=float(np.nanmax(matrix)) if np.any(np.isfinite(matrix)) else 1,
                      cmap='viridis', interpolation='nearest')
    bar = fig.colorbar(image, ax=ax, aspect=35, fraction=.025, pad=.015, format=lambda n,_:f'{n:.0%}')
    bar.ax.tick_params(colors=SOFT, labelsize=8)
    ax.set_yticks(range(len(names)), names)
    ax.tick_params(axis='y', labelsize=7.0)
    ax.set_xticks(range(len(labels)), labels, rotation=90 if categorical else 0, ha='center')


def report(fig, study, data):
    title, metric = TOPICS[study.key]
    fig.text(.018, .985, title, va='top', color=PAPER, fontsize=10, fontweight='bold')
    fig.text(.018, .948, f"{data['start']} to {data['as_of']}  /  {len(data['universe'])} managers",
             va='top', color=SOFT, fontsize=8)
    latest = data['as_of']
    rows = []
    for slug in data['universe']:
        item = data['history'][slug][latest]
        primary = max((s for s in INDUSTRIES if s != 'Unknown'), key=item['sectors'].get)
        industry_label = INDUSTRIES[primary].replace('Business equipment','Equipment').replace('Other industries','Other')
        last_return = data.get('annual_returns', {}).get(slug, {}).get('2025')
        reshuffle = item.get('weight_shift')
        rows.append([_short(slug), f"${item.get('value',0)/1e9:,.1f}B",
                     f"{item['positions']:,}", f"{item['effective']:,.1f}", f"{item['top']:.0%}",
                     f"{item['top10']:.0%}", f"{item['sector_coverage']:.0%}", industry_label,
                     '—' if reshuffle is None else f'{reshuffle:.1%}',
                     f"{item.get('new','—')} / {item.get('exited','—')}",
                     '—' if last_return is None else f'{last_return:+.1%}'])
    _table(fig, .91, .325,
           ['Manager','13F value','Names','Effective','Largest','Top 10','Classified','Top industry','Shift','In / out','2025 return'],
           rows, [0,.14,.23,.30,.38,.445,.504,.588,.718,.789,.886,1])
    key = study.key
    if key in {'shared-stocks', 'style-test'}:
        by_overlap = sorted(data['pairs'], key=lambda p: -p['overlap'])
        strongest = by_overlap[:6] if key == 'shared-stocks' else sorted(
            data['pairs'], key=lambda p: -(p['sectors']-p['overlap']))[:6]
        detail = [[f"{_short(p['left'])} / {_short(p['right'])}", f"{p['shared']:,}",
                   f"{p['overlap']:.1%}", f"{p['sectors']:.1%}",
                   '—' if p['correlation'] is None else f"{p['correlation']:.2f}"] for p in strongest]
        heading = ('Largest stock overlaps' if key == 'shared-stocks' else
                   'Similar industry weights and different holdings')
        cols, sizes = ['Pair','Shared','Overlap','Industry','Return r'], [0,.48,.60,.73,.85,1]
    else:
        if key == 'crowding':
            heading = 'Most widely held by the managers in this study'
            detail = [[x['issuer'].title()[:29], x['count'], f"{x['average']:.2%}"] for x in data['crowded'][:6]]
            cols, sizes = ['Security','Managers','Mean weight'], [0,.58,.77,1]
        else:
            desc = {
                'sectors': ('Highest classified industry weights', 'industry'),
                'sector-history': ('Largest changes in business equipment weight', 'BusEq'),
                'concentration': ('Highest average top-ten weight', 'top10'),
                'turnover': ('Largest average changes in portfolio weight', 'weight_shift'),
                'entry-exit': ('Highest average entries and exits per comparison', 'new'),
                'sector-gaps': ('Largest current unclassified exposures', 'Unknown'),
            }
            heading, metric = desc[key]
            stats = []
            for slug in data['universe']:
                series = list(data['history'][slug].values())
                if metric in {'industry','Unknown','BusEq'}:
                    recent = series[-1]['sectors']
                    code = max((k for k in INDUSTRIES if k != 'Unknown'), key=recent.get) if metric == 'industry' else metric
                    now, then = recent[code], series[0]['sectors'][code]
                    value = abs(now-then) if metric == 'BusEq' else now
                    stats.append((_short(slug), len(series), value, f'{now:.1%}'))
                else:
                    usable = [r for r in series if metric in r]
                    values = [r['new']+r['exited'] if metric == 'new' else r[metric] for r in usable]
                    if values:
                        value = float(np.mean(values))
                        stats.append((_short(slug), len(values), value,
                                      f'{value:.1f}' if metric == 'new' else f'{value:.1%}'))
            top = sorted(stats, key=lambda row: -row[2])[:6]
            detail = [[name, n, f'{score:.1%}' if metric == 'BusEq' else v] for name,n,score,v in top]
            cols, sizes = ['Manager','Quarters','Value'], [0,.50,.73,1]
    fig.text(.015, .295, heading, color=PAPER, fontsize=9)
    _table(fig,.278,.067,cols,detail,sizes)
    _rule(fig,.052)
    fig.text(.012,.028,'SEC Form 13F · Shift measures disclosed weights · 2025 modeled returns require continuous 75% price coverage.',
             fontsize=7, color=SOFT, va='center')
