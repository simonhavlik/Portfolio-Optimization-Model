"""Cohort-wide chart and report panels for institutional holdings research."""

import numpy as np

from services.holdings_style import INDUSTRIES
from services.leaderboard_research import PAGE, PAPER, RULE, SOFT, _axis, _rule, _short, _table

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
    bottom = .157 if categorical else .084
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
    angle = {'crowding': 48, 'sectors': 48, 'shared-stocks': 61}.get(mode, 0)
    ax.set_xticks(range(len(labels)), labels, rotation=angle,
                  ha='right' if angle else 'center', rotation_mode='anchor')


def report(fig, study, data):
    title, metric = TOPICS[study.key]
    fig.text(.018, .985, title, va='top', color=PAPER, fontsize=10, fontweight='bold')
    fig.text(.018, .948, f"{data['start']} to {data['as_of']}  /  {len(data['universe'])} managers",
             va='top', color=SOFT, fontsize=8)
    latest = data['as_of']
    key = study.key
    headers = {
        'sectors': ('Top industry', 'Classified'), 'sector-history': ('Tech weight', 'Change'),
        'shared-stocks': ('Max overlap', 'Shared names'), 'style-test': ('Max overlap', 'Industry match'),
        'crowding': ('Of top 22', 'Total weight'), 'concentration': ('Top 10', 'Effective'),
        'turnover': ('Weight shift', 'Top 10'), 'entry-exit': ('Added', 'Exited'),
        'sector-gaps': ('Classified', 'Unclassified'),
    }
    best = {}
    for pair in data['pairs']:
        for slug in (pair['left'], pair['right']):
            if slug not in best or pair['overlap'] > best[slug]['overlap']:
                best[slug] = pair
    rows, observations = [], []
    for slug in data['universe']:
        history = data['history'][slug]
        item = history[latest]
        reading = None
        if key == 'sectors':
            primary = max((k for k in INDUSTRIES if k != 'Unknown'), key=item['sectors'].get)
            reading = item['sector_coverage']
            cells = (INDUSTRIES[primary].replace('Business equipment', 'Equipment')
                     .replace('Other industries', 'Other'), f'{reading:.0%}')
        elif key == 'sector-history':
            reading = item['sectors']['BusEq']
            start = history[min(history)]['sectors']['BusEq']
            cells = (f'{reading:.1%}', f'{(reading - start) * 100:+.1f} pp')
        elif key in {'shared-stocks', 'style-test'}:
            pair = best.get(slug)
            if pair:
                reading = pair['overlap']
                other = f"{pair['shared']:,}" if key == 'shared-stocks' else f"{pair['sectors']:.1%}"
                cells = (f'{reading:.1%}', other)
            else:
                cells = ('—', '—')
        elif key == 'crowding':
            weights = [stock['weights'].get(slug, 0) for stock in data['crowded'][:22]]
            reading = sum(weights)
            cells = (sum(weight > 0 for weight in weights), f'{reading:.1%}')
        elif key == 'concentration':
            reading = item['top10']
            cells = (f'{reading:.1%}', f"{item['effective']:.1f}")
        elif key == 'turnover':
            reading = item.get('weight_shift')
            cells = ('—' if reading is None else f'{reading:.1%}', f"{item['top10']:.1%}")
        elif key == 'entry-exit':
            if 'new' in item and item['positions']:
                reading = min(1.0, (item['new'] + item['exited']) / (2 * item['positions']))
            cells = (item.get('new', '—'), item.get('exited', '—'))
        else:
            reading = item['sector_coverage']
            cells = (f'{reading:.1%}', f"{item['sectors']['Unknown']:.1%}")
        if reading is not None:
            observations.append(reading)
        value = item.get('value', 0)
        amount = '$' + (f'{value / 1e9:,.1f}B' if value >= 1e9 else f'{value / 1e6:,.1f}M')
        rows.append([_short(slug), amount, f"{item['positions']:,}", *cells])

    split = (len(rows) + 1) // 2
    for table, left, right in [(rows[:split], .012, .493), (rows[split:], .507, .988)]:
        _table(fig, .907, .390, ['Manager', '13F value', 'Names', *headers[key]],
               table, [0,.32,.49,.62,.835,1], left=left, right=right)
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
    fig.text(.019, .355, heading, color=PAPER, fontsize=9.4, fontweight='bold')
    _table(fig, .329, .067, cols, detail, sizes, left=.012, right=.561)
    heading = {
        'sectors': 'Classified equity weight', 'sector-history': 'Business equipment weight',
        'shared-stocks': 'Largest stock overlap per manager', 'style-test': 'Industry match minus stock overlap',
        'crowding': 'Weight in the 22 most widely held names', 'concentration': 'Weight in the top ten holdings',
        'turnover': 'Weight shifted in the latest quarter', 'entry-exit': 'Scaled entries and exits',
        'sector-gaps': 'Classified equity weight',
    }[key]
    if key == 'style-test':
        observations = [pair['sectors'] - pair['overlap'] for pair in data['pairs']]
    if observations:
        from matplotlib.ticker import MaxNLocator, PercentFormatter
        median = float(np.median(observations))
        ax = _axis(fig, (.603, .112, .378, .19))
        ax.hist(observations, bins=10, color='#7fc8cc', alpha=.85, edgecolor=PAGE)
        ax.axvline(median, ls='--', color='#e6bd76', linewidth=1.1)
        ax.xaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
        ax.yaxis.set_major_locator(MaxNLocator(nbins=4, integer=True))
        fig.text(.586, .355, heading, color=PAPER, fontsize=9.4, fontweight='bold')
        fig.text(.586, .330, f"{len(observations)} observations · median {median:.1%}", color=SOFT, fontsize=8)
    _rule(fig, .052)
    fig.text(.012, .028, 'SEC Form 13F · Portfolio weights at the latest disclosure. The bottom panels show selected comparisons and the distribution.',
             fontsize=7, color=SOFT, va='center')
