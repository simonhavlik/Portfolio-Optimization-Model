"""Printable risk, exposure and implementation report for individual 13F books."""

import numpy as np
import pandas as pd

from services.leaderboard import _metrics, _simulation
from services.leaderboard_funds import CRISIS_WINDOWS
from services.leaderboard_research import (
    ACCENTS, PAGE, PAPER, SOFT, _axis, _drawdown, _metric, _paths, _rule, _short, _table,
)


def _heading(fig, x, y, text):
    fig.text(x, y, text, color=PAPER, fontsize=11, fontweight='bold', va='center')


def _section_table(fig, left, bottom, width, height, cols, rows, cuts):
    from matplotlib.lines import Line2D
    from matplotlib.patches import Rectangle

    step = height / (len(rows) + 1)
    for i, items in enumerate([cols, *rows]):
        top = bottom + height - step * i
        fill = '#202d36' if i == 0 else ('#181f28' if i % 2 else PAGE)
        fig.add_artist(Rectangle((left, top-step), width, step, transform=fig.transFigure,
                                 facecolor=fill, edgecolor='none', zorder=-1))
        fig.add_artist(Line2D([left,left+width],[top,top],color='#34434b',linewidth=.4,
                              transform=fig.transFigure))
        for j, item in enumerate(items):
            x = left + width * (cuts[j]+.015 if j == 0 else cuts[j+1]-.012)
            fig.text(x, top-step*.5, item, color=SOFT if i==0 else PAPER, fontsize=8.3,
                     va='center', ha='left' if j==0 else 'right')


def render_report(fig, data, style):
    slug = data['study'].funds[0]
    board = data['board']
    model = data['series'][slug].dropna()
    spy = data['benchmarks']['SPY']
    peers = [r for r in board['rows'] if r['slug'] != slug and r['metrics']]
    preference = ['duquesne', 'himalaya', 'valley-forge', 'pershing', 'smith', 'citadel']
    peers = sorted(peers, key=lambda r: preference.index(r['slug']) if r['slug'] in preference else len(preference))[:2]
    benchmarks = [(name, returns.dropna()) for name, returns in data['benchmarks'].items()]
    named = [(_short(slug), data['rows'][slug]['metrics'])]
    named.extend((name, _metrics(returns, spy)) for name, returns in benchmarks)
    named.extend((_short(r['slug']), r['metrics']) for r in peers)

    _heading(fig, .018, .902, 'Performance, downside and benchmark-relative efficiency')
    measure = [
        ('Cumulative return','total_return','+.1%'),
        ('Annualized return','annualized_return','+.1%'),
        ('Annualized volatility','volatility','.1%'),
        ('Maximum drawdown','max_drawdown','.1%'),
        ('Daily expected shortfall (95%)','cvar_95','.2%'),
        ('Downside volatility','downside_volatility','.1%'),
        ('Sharpe ratio','sharpe_ratio','.2f'),
        ('Sortino ratio','sortino_ratio','.2f'),
        ('Calmar ratio','calmar_ratio','.2f'),
        ('Beta vs SPY','beta','.2f'),
        ('Annualized alpha vs SPY','alpha','+.1%'),
        ('Information ratio','information_ratio','.2f'),
    ]
    metric_rows = [[title,*[_metric(metrics.get(key), style) for _,metrics in named]]
                   for title,key,style in measure]
    step = .65 / len(named)
    _table(fig,.88,.526,['Metric',*[name for name,_ in named]],metric_rows,
           [0,.35,*[.35+step*i for i in range(1,len(named)+1)]])

    _heading(fig,.018,.499,'Market shocks and recovery')
    stress = []
    row = data['rows'][slug]
    for key, label, first, last in CRISIS_WINDOWS:
        fund = row['crisis'].get(key)
        stress.append([label, f'{first[:7]} – {last[:7]}', _metric(fund,'+.1%')])
    drawdown = _drawdown(model)
    if len(drawdown):
        low = drawdown.idxmin()
        highs = (1+model).cumprod().loc[:low]
        peak = highs.idxmax()
        recovery = (1+model).cumprod().loc[low:]
        recovered = recovery[recovery.ge(highs.max())]
        recovery_date = 'Open' if recovered.empty else recovered.index[0].date().isoformat()
        stress += [
            ['Peak to trough', f'{peak.date()} – {low.date()}', f'{drawdown.min():.1f}%'],
            ['Recovery after trough', recovery_date,
             '—' if recovered.empty else f'{(recovered.index[0]-low).days:,}d'],
        ]
    sim = row.get('simulation')
    if sim:
        stress += [['One-year median / loss chance', f"${sim['median']:.2f}", f"{sim['loss']:.1%}"],
                   ['One-year 10th / 90th', f"${sim['low']:.2f}", f"${sim['high']:.2f}"]]
    _section_table(fig, .012, .276, .49, .201, ['Window','Dates / outcome','Return'], stress, [0,.46,.83,1])

    _heading(fig,.525,.499,'Portfolio exposure and valuation')
    historical = style.get('history', {}).get(slug, {})
    quarter = max((date for date in historical if date <= board['as_of']), default=None)
    profile = historical.get(quarter,{})
    industries = style.get('industries',{})
    leaders = sorted(profile.get('sectors',{}).items(),key=lambda item:-item[1])[:4]
    observed = [
        ['Largest reported position', _metric(row['metrics'].get('concentration'), '.1%')],
        ['Top ten positions', _metric(profile.get('top10'),'.1%')],
        ['Effective positions', _metric(profile.get('effective'),'.1f')],
        ['Minimum priced weight', _metric(row.get('coverage'),'.1%')],
        ['Trailing P/E · weighted / median',
         '—' if row['pe'].get('weighted') is None else
         f"{row['pe']['weighted']:.1f}× / {row['pe']['median']:.1f}×"],
    ]
    observed += [[industries.get(group,group),f'{weight:.1%}'] for group,weight in leaders if weight > 0]
    _section_table(fig,.518,.276,.469,.201,['Exposure',quarter or 'As filed'],observed,[0,.73,1])

    _heading(fig, .018, .247, 'Peak drawdown vs benchmarks')
    risk_ax = _axis(fig,(.064,.065,.406,.151))
    risk_ax.plot(drawdown.index, drawdown.values, color=ACCENTS[0], lw=1)
    for i,(_name,returns) in enumerate(benchmarks,1):
        dip=_drawdown(returns)
        risk_ax.plot(dip.index,dip.values,color=ACCENTS[i],ls='--',lw=.65)
    risk_ax.set_xticks([model.index[0], model.index[-1]],
                       [str(model.index[0].year),str(model.index[-1].year)])
    risk_ax.set_yticks([float(round(drawdown.min())),0])
    _heading(fig,.525,.247,'One year · 3,000 block-bootstrap paths')
    distribution = _axis(fig,(.563,.065,.407,.151))
    if len(model) >= 21:
        bands,_ = _paths(model)
        distribution.fill_between(range(253),bands[0],bands[2],color=ACCENTS[1],alpha=.25)
        distribution.plot(bands[1], color=ACCENTS[1], linewidth=1)
        distribution.set_xticks([0,126,252],['Day 0','126','252'])
    _rule(fig,.043)
    fig.text(.016,.018,'SEC Form 13F · Model assumes next-session rebalancing after release · Monte Carlo band shows 10th–90th percentiles per day.',
             fontsize=7.3,color=SOFT,va='center')
