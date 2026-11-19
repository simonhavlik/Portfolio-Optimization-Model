"""Read saved rankings and rebuild them from the SEC disclosure archive."""

from __future__ import annotations

import json
import math
import pickle
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from services import fund_archive, leaderboard_valuation as valuation
from services.filing_history import atomic_write
from services.leaderboard_funds import BENCHMARK, CRISIS_WINDOWS, FUNDS, ISSUER_ALIASES, RANK_METRICS
from services.risk_analytics import RiskAnalytics

CACHE_DIR = Path('data/leaderboard_cache')
BOARD_FILE = 'board_13f.json'
WINDOW_YEARS = 5
RISK_FREE = 0.045
PRICE_PERIOD = '10y'
MIN_COVERAGE = .75
MAX_FILING_AGE = pd.Timedelta(days=155)
ARCHIVED_END = '2025-11-03'
BOARDS = {'current': ('Latest five years', WINDOW_YEARS, None),
          'long': ('Sixteen years', 16, None),
          'scion': ('Scion comparison · 2023–25', 2, ARCHIVED_END)}

risk = RiskAnalytics()


def _mapping() -> dict:
    from services.leaderboard_storage import CUSIP_KEY, published_snapshot
    saved = published_snapshot(CUSIP_KEY)
    if saved is not None:
        return saved.get('data', {})
    path = CACHE_DIR / 'cusip_map.json'
    try:
        return json.loads(path.read_bytes())['data']
    except (OSError, KeyError, ValueError):
        from services.leaderboard_storage import hydrate_snapshot
        return (hydrate_snapshot(path, CUSIP_KEY) or {}).get('data', {})


def _book_positions(fund: dict, book: dict | None, mapping: dict) -> list[dict]:
    positions = fund_archive.aggregate(book, equity_only=True)
    aliases = {**ISSUER_ALIASES, **fund.get('aliases', {})}
    for row in positions:
        ticker = mapping.get(row['cusip'])
        issuer = row['issuer'].upper()
        if not ticker:
            ticker = next((ticker for name, ticker in aliases.items() if issuer.startswith(name)), None)
        row['ticker'] = ticker
    return positions


def _fund_book(fund: dict, period: str | None = None) -> dict | None:
    if not period:
        dates = fund_archive.periods(fund['slug'])
        if not dates:
            return None
        period = dates[0]
    documents = fund_archive.documents(fund['slug'], period)
    return fund_archive.quarter_book(documents, period)


def _weight_map(positions: list[dict]) -> dict[str, float]:
    weights = defaultdict(float)
    for row in positions:
        if row['ticker'] and row['weight'] > 0:
            weights[row['ticker']] += row['weight']
    return dict(weights)


def _weights(fund: dict, book: dict | None) -> dict[str, float]:
    return _weight_map(_book_positions(fund, book, _mapping())) if book else {}


def _read_prices(tickers: set[str] | None = None) -> dict[str, pd.Series]:
    from services.leaderboard_storage import load_prices, serving_postgres
    if serving_postgres():
        return load_prices(tickers)
    try:
        cached = pickle.loads((CACHE_DIR / f'prices_{PRICE_PERIOD}.pkl').read_bytes())
        return cached if tickers is None else {t: cached[t] for t in tickers if t in cached}
    except (OSError, ValueError, pickle.UnpicklingError):
        return load_prices(tickers)


def collect_universe() -> list[str]:
    """Prioritize large recent equity positions for the gradual price refresh."""
    mapping = _mapping()
    priorities = defaultdict(float)
    for fund in FUNDS:
        docs = fund_archive.documents(fund['slug'])
        for book in fund_archive.timeline(docs)[-24:]:
            for ticker, weight in _weights_for(fund, book, mapping).items():
                priorities[ticker] = max(priorities[ticker], weight)
    return [BENCHMARK, *sorted((t for t in priorities if t != BENCHMARK),
                               key=lambda t: (-priorities[t], t))]


def _weights_for(fund, book, mapping):
    return _weight_map(_book_positions(fund, book, mapping))


def _series(fund: dict, books: list[dict], prices: dict, calendar: pd.DatetimeIndex,
            mapping: dict) -> tuple[pd.Series, pd.Series]:
    """Buy at the next close after publication, then hold until the next rebalance."""
    returns = pd.Series(np.nan, index=calendar)
    coverage = pd.Series(np.nan, index=calendar)
    for index, book in enumerate(books):
        filed = pd.Timestamp(book['filed'])
        rebalance = calendar.searchsorted(filed, side='right')
        if rebalance >= len(calendar) - 1:
            break
        cutoff = filed + MAX_FILING_AGE
        stop = calendar.searchsorted(cutoff, side='right')
        if index + 1 < len(books):
            next_rebalance = calendar.searchsorted(pd.Timestamp(books[index + 1]['filed']), side='right')
            stop = min(stop, next_rebalance + 1)
        if stop <= rebalance + 1:
            continue

        days = calendar[rebalance:stop]
        weights = _weights_for(fund, book, mapping)
        usable = {t: w for t, w in weights.items()
                  if t in prices and pd.notna(prices[t].get(days[0]))}
        initial_coverage = sum(usable.values())
        coverage.loc[days[1:]] = initial_coverage
        if initial_coverage < MIN_COVERAGE:
            continue

        logs = pd.DataFrame({t: prices[t].reindex(days) for t in usable}, index=days)
        # A missing price ends that capital sleeve for the remainder of this book.
        # The segment ends if too much capital loses pricing.
        alive = logs.notna().cummin()
        invested = alive.mul(pd.Series(usable)).sum(axis=1)
        coverage.loc[days[1:]] = invested.iloc[1:]
        valid = invested.ge(MIN_COVERAGE).cummin()
        values = np.exp(logs.where(alive, 0).cumsum().sub(logs.iloc[0])).mul(pd.Series(usable))
        strategy = values.sum(axis=1).pct_change(fill_method=None)
        returns.loc[days[1:]] = strategy.where(valid).iloc[1:]
    return returns, coverage


def _metrics(series: pd.Series, benchmark: pd.Series) -> dict:
    m = risk.calculate_comprehensive_metrics(series, benchmark, risk_free_rate=RISK_FREE)
    keys = ('annualized_return', 'total_return', 'volatility', 'sharpe_ratio', 'sortino_ratio',
            'calmar_ratio', 'max_drawdown', 'cvar_95', 'beta', 'alpha', 'information_ratio',
            'downside_volatility')
    return {key: float(getattr(m, key)) for key in keys if math.isfinite(getattr(m, key))}


def _simulation(returns: pd.Series) -> dict:
    """Bootstrap contiguous blocks of daily model returns into 252-day paths."""
    daily = np.log1p(returns.to_numpy())
    size = 21
    months = 12
    paths = 3000
    monthly = np.lib.stride_tricks.sliding_window_view(daily, size).sum(axis=1)
    rng = np.random.default_rng(801)
    sampled = rng.choice(monthly, size=(paths, months))
    end_values = np.exp(sampled.sum(axis=1)) * 100
    low, median, high = np.percentile(end_values, [10, 50, 90])
    return {'low': round(float(low), 2), 'median': round(float(median), 2),
            'high': round(float(high), 2), 'loss': round(float((end_values < 100).mean()), 4)}


def _score(rows: list[dict]) -> None:
    eligible = [row for row in rows if row['metrics']]
    for key, _, direction, _ in RANK_METRICS:
        scores = pd.Series({row['slug']: row['metrics'][key] * direction for row in eligible
                            if key in row['metrics']}, dtype=float)
        if len(scores) < 2:
            continue
        ranked = (scores.rank(method='average') - 1) * 100 / (len(scores) - 1)
        positions = scores.rank(method='min', ascending=False)
        for row in eligible:
            slug = row['slug']
            if slug in ranked:
                row.setdefault('percentiles', {})[key] = round(float(ranked[slug]), 1)
                row.setdefault('metric_ranks', {})[key] = {'rank': int(positions[slug]), 'sample': len(scores)}
    for row in rows:
        values = list(row.get('percentiles', {}).values())
        row['score'] = round(sum(values) / len(values), 1) if values else None
    rows.sort(key=lambda r: (r['score'] is None, -(r['score'] or 0), r['name']))


def matched_risk_ranks(first, second, slug):
    """Compare ranks on identical manager membership in both time windows."""
    samples = [{row['slug']: row['metrics'] for row in board['rows'] if row['metrics']}
               for board in (first, second)]
    common = samples[0].keys() & samples[1].keys()
    if slug not in common:
        return None
    result = []
    for metrics in samples:
        focus = metrics[slug]
        result.append({
            'volatility': 1 + sum(metrics[peer]['volatility'] < focus['volatility'] for peer in common),
            'sharpe': 1 + sum(metrics[peer]['sharpe_ratio'] > focus['sharpe_ratio'] for peer in common),
        })
    return len(common), result


def _format_pe(positions: list[dict], pe_cache: dict, as_of: str | None) -> dict:
    if not as_of:
        return {}
    # Current trailing multiples are displayed as a dated snapshot only.
    cutoff = pd.Timestamp(as_of) - pd.Timedelta(days=30)
    dated = {key: row for key, row in pe_cache.items() if row.get('at')
             and cutoff <= pd.Timestamp(row['at']).normalize() <= pd.Timestamp(as_of) + pd.Timedelta(days=2)}
    weights = _weight_map(positions)
    result = valuation.weights_pe(weights, dated)
    result['at'] = max((dated[t]['at'][:10] for t in weights if t in dated), default=None)
    return result


def _compile(cohort, archive, prices, mapping, pe_cache, stats, pricing=None):
    title, years, end = BOARDS[cohort]
    bench_log = prices.get(BENCHMARK, pd.Series(dtype=float)).dropna()
    if end:
        bench_log = bench_log.loc[:end]
    benchmark = np.expm1(bench_log)
    calendar = benchmark.index
    window = benchmark.loc[calendar[-1] - pd.DateOffset(years=years):] if len(calendar) else benchmark
    as_of = calendar[-1].date().isoformat() if len(calendar) else None
    # Keep the actual last session label in holidays and partial weeks.
    weekly_dates = window.groupby(window.index.to_period('W-FRI')).tail(1).index if len(window) else window.index
    rows = []
    for fund in FUNDS:
        slug = fund['slug']
        books = archive[slug]
        latest = next((book for book in reversed(books) if not as_of or book['filed'] <= as_of), None)
        positions = _book_positions(fund, latest, mapping)
        if pricing is None:
            full, coverage = _series(fund, books, prices, calendar, mapping)
        else:
            full, coverage = (series.reindex(calendar) for series in pricing[slug])
        series = full.reindex(window.index)
        comparable = len(series) >= 252 and series.notna().all()
        metrics = _metrics(series, window) if comparable else {}
        if comparable:
            metrics['concentration'] = max((p['weight'] for p in positions), default=0)
        weekly = (1 + series).cumprod().mul(100).reindex(weekly_dates) if comparable else None
        crisis = {}
        for key, _, start, stop in CRISIS_WINDOWS:
            event = full.loc[start:stop] if not full.empty else full
            complete = len(event) >= 5 and event.notna().all() and calendar[0] <= pd.Timestamp(start) and calendar[-1] >= pd.Timestamp(stop)
            crisis[key] = float((1 + event).prod() - 1) if complete else None
        measured = coverage.reindex(window.index).dropna()
        archive_stats = stats.get(slug, {})
        recent_values = {}
        if as_of:
            first_year = str(int(as_of[:4]) - max(10, years))
            for book in books:
                if book['period'] >= first_year and book['filed'] <= as_of:
                    recent_values[book['period']] = sum(p['value'] for p in book['positions']
                                                       if not p['option'] and p['quantity_type'] == 'SH')
        rows.append({**fund, 'metrics': metrics, 'crisis': crisis,
                     'filing': latest['filed'] if latest else None, 'period': latest['period'] if latest else None,
                     'holdings': len(positions), 'coverage': round(float(measured.min()), 4) if len(measured) else None,
                     'observations': int(series.count()), 'archive': archive_stats,
                     'simulation': _simulation(series) if comparable else None,
                     'size': sum(p['value'] for p in positions),
                     'disclosed_values': sorted(recent_values.items()),
                     'pe': _format_pe(positions, pe_cache, as_of) if cohort == 'current' else {},
                     'curve': weekly.round(2).tolist() if weekly is not None else [],
                     'curve_dates': [d.date().isoformat() for d in weekly.index] if weekly is not None else [],
                     'latest_tops': {p['cusip']: p['weight'] for p in positions if p['weight'] >= .005},
                     'reason': None if comparable else ('No parsed filing' if not latest else 'Insufficient continuous coverage'),
                     })
    _score(rows)
    comparator = (1 + window).cumprod().mul(100)
    if len(comparator):
        comparator = comparator.reindex(weekly_dates)
    return {'rows': rows, 'cohort': cohort, 'title': title, 'as_of': as_of,
            'start': window.index[0].date().isoformat() if len(window) else None, 'window_years': years,
            'risk_free': RISK_FREE, 'benchmark': BENCHMARK, 'observations': len(window),
            'benchmark_curve': list(zip([d.date().isoformat() for d in comparator.index],
                                        comparator.round(2).tolist())) if len(comparator) else [],
            'pe_median': None, 'commentary': {},
            'metrics': [{'key': key, 'label': label, 'direction': direction, 'description': desc}
                        for key, label, direction, desc in RANK_METRICS],
            'crisis': [{'key': k, 'label': label, 'start': s, 'end': e} for k, label, s, e in CRISIS_WINDOWS],
            }


@lru_cache(maxsize=4)
def _saved_board(path: Path, revision: int) -> dict:
    return json.loads(path.read_bytes())


def build_board(force: bool = False, cohort: str = 'current') -> dict:
    """Normal visitor traffic only reads a snapshot. Refresh builds both cohorts."""
    path = CACHE_DIR / BOARD_FILE
    if not force:
        from services.leaderboard_storage import BOARD_KEY, hydrate_snapshot, published_snapshot
        saved = published_snapshot(BOARD_KEY)
        if saved and cohort in saved:
            return saved[cohort]
        try:
            return _saved_board(path, path.stat().st_mtime_ns)[cohort]
        except (OSError, ValueError, KeyError):
            saved = hydrate_snapshot(path, BOARD_KEY)
            if saved and cohort in saved:
                return saved[cohort]
            return _compile(cohort, {f['slug']: [] for f in FUNDS}, {}, {}, {}, {})
    prices = _read_prices()
    if BENCHMARK not in prices:
        raise RuntimeError('Benchmark prices are missing')
    for ticker, series in prices.items():
        series = series.copy()
        series.index = pd.DatetimeIndex(series.index).tz_localize(None).normalize()
        prices[ticker] = series[~series.index.duplicated(keep='last')].sort_index()
    archive = {f['slug']: fund_archive.timeline(fund_archive.documents(f['slug'])) for f in FUNDS}
    stats = fund_archive.stats()
    mapping = _mapping()
    pe_cache = valuation._load()
    stamp = datetime.now(timezone.utc).isoformat()
    revision = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')
    calendar = prices[BENCHMARK].dropna().index
    pricing = {f['slug']: _series(f, archive[f['slug']], prices, calendar, mapping) for f in FUNDS}
    payload = {}
    for key in BOARDS:
        board = _compile(key, archive, prices, mapping, pe_cache, stats, pricing=pricing)
        board.update(computed_at=stamp, revision=revision)
        priced = [row['pe']['weighted'] for row in board['rows'] if row['pe'].get('weighted')]
        board['pe_median'] = float(np.median(priced)) if priced else None
        payload[key] = board
    from services.leaderboard_graphics import warm
    for board in payload.values():
        warm(board)
    atomic_write(path, json.dumps(payload, allow_nan=False, separators=(',', ':')).encode())
    return payload[cohort]
