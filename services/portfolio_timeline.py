"""Analyze dated books using adjusted constituent returns and point-in-time weights."""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from models.database import AppCache, db
from services import fund_archive
from services.example_portfolio import _finite_json
from services.leaderboard import (
    MAX_FILING_AGE, MIN_COVERAGE, _book_positions, _mapping, _metrics, _read_prices,
    _weight_map, build_board, scheduled_returns,
)
from services.leaderboard_funds import BENCHMARK, FUNDS

FUND_REPORT_PREFIX = 'fund_timeline:'


def _dates(index):
    return index.strftime('%Y-%m-%d').tolist()


def _projection(segments, n_simulations, horizon_days):
    block = 21
    sets = [np.lib.stride_tricks.sliding_window_view(np.log1p(segment), block).sum(axis=1)
            for segment in segments if len(segment) >= block]
    if not sets:
        return {}
    available = np.concatenate(sets)
    rng = np.random.default_rng(801)
    count, remainder = divmod(horizon_days, block)
    sampled = rng.choice(available, size=(n_simulations, count)).sum(axis=1)
    if remainder:
        fragments = np.concatenate([
            np.lib.stride_tricks.sliding_window_view(np.log1p(segment), remainder).sum(axis=1)
            for segment in segments if len(segment) >= block
        ])
        sampled += rng.choice(fragments, size=n_simulations)
    values = np.exp(sampled) * 100
    low, median, high = np.percentile(values, [10, 50, 90])
    return {'low': float(low), 'median': float(median), 'high': float(high),
            'loss': float((values < 100).mean()), 'paths': n_simulations, 'horizon': horizon_days}


def report(name, schedule, changes, prices, *, benchmark=BENCHMARK, public=False,
           risk_free=0.045, n_simulations=3000, horizon_days=252):
    """Show all filings, and keep separate valued stretches across missing data."""
    benchmark_log = prices.get(benchmark, pd.Series(dtype=float)).dropna()
    benchmark_returns = pd.Series(np.expm1(benchmark_log), name=benchmark)
    calendar = benchmark_returns.loc[schedule[0][0]:].index
    daily, _ = scheduled_returns(schedule, prices, calendar, after_close=public,
                                 max_age=MAX_FILING_AGE if public else None)
    observed = daily.notna()
    groups = observed.ne(observed.shift()).cumsum()
    segments = [section for _, section in daily[observed].groupby(groups[observed])]
    longest = max(segments, key=len, default=pd.Series(dtype=float))
    metrics = _metrics(longest, benchmark_returns.reindex(longest.index), risk_free) if len(longest) >= 60 else {}
    dates, values, market, drawdown, intervals = [], [], [], [], []
    for segment in segments:
        wealth = (1 + segment).cumprod()
        comparator = (1 + benchmark_returns.reindex(segment.index)).cumprod()
        peaks = wealth.cummax().clip(lower=1)
        if dates:
            # A null point on the first missing trading day stops Plotly joining two books.
            missing = calendar[calendar.searchsorted(dates[-1], side='right')].date().isoformat()
            dates.append(missing)
            for line in (values, market, drawdown):
                line.append(None)
        baseline_index = calendar.searchsorted(segment.index[0]) - 1
        floor = segment.index[0].date().isoformat()
        if baseline_index >= 0:
            floor = calendar[baseline_index].date().isoformat()
            dates.append(floor)
            values.append(0)
            market.append(0)
            drawdown.append(0)
        dates.extend(_dates(segment.index))
        values.extend(wealth.sub(1).round(6).tolist())
        market.extend(comparator.sub(1).round(6).tolist())
        drawdown.extend(wealth.div(peaks).sub(1).round(6).tolist())
        intervals.append({'baseline': floor, 'start': segment.index[0].date().isoformat(), 'end': dates[-1],
                          'sessions': len(segment), 'portfolio': float(wealth.iloc[-1] - 1),
                          'benchmark': float(comparator.iloc[-1] - 1)})
    return _finite_json({
        'name': name, 'changes': changes, 'benchmark': benchmark, 'metrics': metrics,
        'benchmark_return': float((1 + benchmark_returns.reindex(longest.index)).prod() - 1) if len(longest) else None,
        'modeled_days': int(observed.sum()), 'market_days': max(len(calendar) - 1, 0),
        'segment_count': len(segments), 'metric_start': _dates(longest.index[:1])[0] if len(longest) else None,
        'metric_end': _dates(longest.index[-1:])[0] if len(longest) else None,
        'chart': {'dates': dates, 'values': values, 'benchmark': market, 'drawdown': drawdown,
                  'intervals': intervals},
        'simulation': _projection(segments, n_simulations, horizon_days),
        'risk_free': risk_free, 'min_coverage': MIN_COVERAGE,
    })


def allocation_changes(before, positions):
    current = {p['id']: p for p in positions}
    old = {p['id']: p for p in before or []}
    movements = []
    for key in current.keys() | old.keys():
        a, b = old.get(key, {}), current.get(key, {})
        delta = b.get('weight', 0) - a.get('weight', 0)
        if abs(delta) >= .0001:
            movements.append({'name': (b or a)['name'], 'delta': delta})
    movements.sort(key=lambda row: (-abs(row['delta']), row['name']))
    return {
        'added': len(current.keys() - old.keys()) if before is not None else None,
        'exited': len(old.keys() - current.keys()) if before is not None else None,
        'shift': sum(abs(row['delta']) for row in movements) / 2 if before is not None else None,
        'top': movements[:5] if before is not None else [],
    }


def fund_report(fund, *, prices=None, mapping=None):
    mapping = _mapping() if mapping is None else mapping
    books = fund_archive.timeline(fund_archive.documents(fund['slug']))
    if not books:
        return None

    schedule, changes, before = [], [], None
    for book in books:
        holdings = _book_positions(fund, book, mapping)
        positions = [{'id': p['cusip'], 'name': p['ticker'] or p['issuer'], 'weight': p['weight']}
                     for p in holdings]
        changes.append({
            'date': book['filed'], 'period': book['period'], 'count': len(holdings),
            'value': sum(p['value'] for p in holdings),
            **allocation_changes(before, positions),
        })
        schedule.append((book['filed'], _weight_map(holdings)))
        before = positions

    if prices is None:
        tickers = {ticker for _, weights in schedule for ticker in weights}
        prices = _read_prices(tickers | {BENCHMARK})
    result = report(fund['name'], schedule, changes, prices, public=True)
    return {**result, 'slug': fund['slug']}


def publish_fund_reports(*, prices=None, revision=None):
    """Compute all reports offline using the same database archive and stored prices."""
    prices = _read_prices() if prices is None else prices
    revision = revision or build_board().get('revision')
    mapping = _mapping()
    reports = {}
    for fund in FUNDS:
        payload = fund_report(fund, prices=prices, mapping=mapping)
        if payload:
            reports[FUND_REPORT_PREFIX + fund['slug']] = {**payload, 'revision': revision}
    for key, payload in reports.items():
        db.session.merge(AppCache(key=key, payload=payload, computed_at=datetime.utcnow()))
    db.session.commit()
    return reports
