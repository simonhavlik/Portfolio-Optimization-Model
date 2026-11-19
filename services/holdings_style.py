"""Sixteen-year Form 13F industry, concentration and shared-position analysis."""

from collections import Counter, defaultdict
from itertools import combinations
import json

import numpy as np
import pandas as pd

from services import fund_archive
from services.filing_history import atomic_write
from services.leaderboard import CACHE_DIR, _mapping, _read_prices, _series
from services.leaderboard_funds import by_slug

KEY = 'leaderboard:holdings_style'
FILE = CACHE_DIR / 'holdings_style.json'
INDUSTRIES = {
    'NoDur': 'Consumer staples', 'Durbl': 'Consumer durables', 'Manuf': 'Manufacturing',
    'Enrgy': 'Energy', 'Chems': 'Chemicals', 'BusEq': 'Business equipment', 'Telcm': 'Telecom',
    'Utils': 'Utilities', 'Shops': 'Retail', 'Hlth': 'Healthcare', 'Money': 'Finance',
    'Other': 'Other industries', 'Funds': 'Funds and trusts', 'Unknown': 'Unclassified'
}
HORIZON = 16


def load():
    from services.leaderboard_storage import hydrate_snapshot, published_snapshot

    data = published_snapshot(KEY)
    if data:
        return data
    try:
        return json.loads(FILE.read_bytes())
    except (OSError, ValueError):
        return hydrate_snapshot(FILE, KEY) or {}


def _book(book, industries):
    result = {}
    for entry in fund_archive.aggregate(book, equity_only=True):
        if entry['weight'] <= 0:
            continue
        issuer = entry['issuer'].upper()
        identity = industries.get(entry['cusip'], {})
        group = identity.get('sector', 'Unknown')
        fund = identity.get('sic') in {'6722', '6726'} or any(name in issuer for name in (
            ' ETF', 'SPDR', 'ISHARES', 'VANGUARD INDEX', 'PROSHARES', 'ETFS'))
        if fund:
            group = 'Funds'
        # Index funds and trusts can share a sponsor CIK but are distinct assets.
        key = f"cik:{identity['cik']}" if identity.get('cik') and not fund else entry['cusip']
        row = result.setdefault(key, {'issuer': entry['issuer'], 'sector': group, 'weight': 0.0})
        row['weight'] += entry['weight']
    return result


def _cross_section(book):
    sectors = defaultdict(float)
    for row in book.values():
        sectors[row['sector']] += row['weight']
    weights = sorted((r['weight'] for r in book.values()), reverse=True)
    return {
        'sectors': {s: round(sectors.get(s, 0), 5) for s in INDUSTRIES},
        'positions': len(weights),
        'top': round(weights[0], 4),
        'top10': round(sum(weights[:10]), 4),
        'effective': round(1 / sum(w*w for w in weights), 1),
        'sector_coverage': round(1-sectors['Unknown'], 4),
    }


def compare(a, b):
    shared = a.keys() & b.keys()
    matched = sorted(
        ({'issuer': a[key]['issuer'], 'sector': a[key]['sector'], 'a': a[key]['weight'],
          'b': b[key]['weight'], 'overlap': min(a[key]['weight'], b[key]['weight'])}
         for key in shared), key=lambda r: (-r['overlap'], r['issuer']))
    return {'overlap': round(sum(r['overlap'] for r in matched), 5),
            'shared': len(matched), 'positions': matched}


def build(books, reported=None):
    industry_path = CACHE_DIR / 'issuer_industries.json'
    industries = json.loads(industry_path.read_bytes()) if industry_path.exists() else {}
    reported = reported if reported is not None else {
        slug: {b['period']: b for b in timeline} for slug, timeline in books.items()}
    latest = max(period for quarters in reported.values() for period in quarters)
    start = (pd.Timestamp(latest) - pd.DateOffset(years=HORIZON)).date().isoformat()
    periods = [d.date().isoformat() for d in pd.date_range(start, latest, freq='QE')]
    manager_books = {slug: {period: filing for period, filing in quarters.items() if period in periods}
                     for slug, quarters in reported.items()}
    roots = {}
    for slug, by_period in manager_books.items():
        if latest in by_period:
            rows = fund_archive.aggregate(by_period[latest], equity_only=True)
            roots[slug] = sum(r['value'] for r in rows)
    universe = sorted(roots, key=lambda slug: (-roots[slug], slug))
    history_universe = universe + [slug for slug, docs in manager_books.items() if docs and slug not in roots]
    series = {}
    latest_books = {}
    for slug in history_universe:
        metrics = {}
        before = None
        for quarter, filing in sorted(manager_books[slug].items()):
            positions = _book(filing, industries)
            if not positions:
                continue
            reading = _cross_section(positions)
            reading['filing_url'] = filing.get('url')
            reading['filed'] = filing.get('filed')

            reading['value'] = sum(row['value'] for row in filing['positions']
                                   if not row['option'] and row['quantity_type'] == 'SH')
            if before:
                reading['new'] = len(positions.keys()-before.keys())
                reading['exited'] = len(before.keys()-positions.keys())
                reading['weight_shift'] = round(.5 * sum(
                    abs((positions.get(k) or {}).get('weight', 0) -
                        (before.get(k) or {}).get('weight', 0))
                    for k in positions.keys() | before.keys()), 5)
            metrics[quarter] = reading
            before = positions
            if quarter == latest:
                latest_books[slug] = positions
        series[slug] = metrics

    prices = _read_prices()
    calendar = prices.get('SPY', pd.Series(dtype=float)).index
    returns = {}
    if len(calendar):
        mapping = _mapping()
        for slug in history_universe:
            returns[slug] = _series(by_slug(slug), books[slug], prices, calendar, mapping)[0]
    annual_returns = {}
    annual_risk = {}
    if returns:
        channels = {**returns, 'SPY': np.expm1(prices['SPY'])}
        for slug, daily in channels.items():
            observed = daily.loc[start:latest]
            valid = [(str(year), window) for year, window in observed.groupby(observed.index.year)
                     if len(window) >= 245 and window.notna().all()]
            annual_returns[slug] = {year: round(float((1 + window).prod() - 1), 5) for year, window in valid}
            from services.leaderboard import _metrics
            annual_risk[slug] = {year: _metrics(window, channels['SPY']) for year, window in valid}
    pairs = []
    for left, right in combinations(universe, 2):
        a, b = latest_books[left], latest_books[right]
        overlap = compare(a, b)
        left_sectors, right_sectors = series[left][latest]['sectors'], series[right][latest]['sectors']
        match = sum(min(left_sectors[k], right_sectors[k]) for k in INDUSTRIES if k != 'Unknown')
        corr, days = None, 0
        first_priced, last_priced = None, None
        if returns:
            same_days = pd.concat([returns[left], returns[right]], axis=1).loc[start:].dropna()
            days = len(same_days)
            if days:
                first_priced = same_days.index[0].date().isoformat()
                last_priced = same_days.index[-1].date().isoformat()
            if days >= 252:
                value = same_days.iloc[:, 0].corr(same_days.iloc[:, 1])
                corr = round(float(value), 4) if np.isfinite(value) else None
        pairs.append({'left': left, 'right': right, 'shared': overlap['shared'],
                      'overlap': overlap['overlap'], 'sectors': round(match, 5),
                      'correlation': corr, 'priced_days': days, 'first_priced': first_priced,
                      'last_priced': last_priced, 'common_quarters': len(series[left].keys() & series[right].keys()),
                      'top_shared': overlap['positions'][:5]})
    counts = Counter(key for book in latest_books.values() for key in book)
    crowded = sorted(
        ({'issuer': next(book[key]['issuer'] for book in latest_books.values() if key in book),
          'key': key, 'count': count,
          'weights': {slug: book[key]['weight'] for slug, book in latest_books.items() if key in book},
          'average': sum(book.get(key, {}).get('weight', 0)
                                                  for book in latest_books.values())/len(universe)}
         for key, count in counts.items()), key=lambda r: (-r['count'], -r['average'], r['issuer']))
    outliers = {}
    for metric in ('top10', 'effective', 'weight_shift', 'sector_coverage'):
        candidates = [(slug, series[slug][latest][metric]) for slug in universe if metric in series[slug][latest]]
        if len(candidates) >= 10:
            candidates.sort(key=lambda item: (-item[1], item[0]))
            outliers[metric] = {'count': len(candidates), 'median': float(np.median([v for _,v in candidates])),
                                'high': candidates[:3], 'low': candidates[-3:][::-1]}
    audit_path = CACHE_DIR / 'edgar_coverage.json'
    sec_sources = json.loads(audit_path.read_bytes()) if audit_path.exists() else {}
    coverage = {}
    for slug in history_universe:
        expected = {r['period'] for r in sec_sources.get(slug, [])
                    if r['form'] == '13F-HR' and r['period'] in periods}
        observed = set(series[slug])
        empty = {period for period, filing in manager_books[slug].items() if filing.get('no_disclosed_positions')}
        coverage[slug] = {
            'observed': len(observed), 'expected': len(expected) if slug in sec_sources else None,
            'filed_missing': sorted((expected-observed-empty) & set(periods)),
            'reported_empty': sorted(empty),
            'no_original': sorted(set(periods)-expected-observed) if slug in sec_sources else [],
        }
    record = {'outliers': outliers, 'coverage': coverage, 'start': start, 'as_of': latest, 'universe': universe, 'periods': periods,
              'history': series, 'tracked': history_universe, 'pairs': pairs, 'size': roots, 'crowded': crowded[:50],
              'industries': INDUSTRIES, 'classified': len(industries), 'annual_returns': annual_returns, 'annual_risk': annual_risk}
    atomic_write(FILE, json.dumps(record, separators=(',', ':'), allow_nan=False).encode())
    return record


def pair_detail(quarter, first, second):
    industries = CACHE_DIR / 'issuer_industries.json'
    from services.leaderboard_storage import hydrate_snapshot, published_snapshot

    classifications = published_snapshot('leaderboard:issuer_industries')
    if classifications is None:
        try:
            classifications = json.loads(industries.read_bytes())
        except (OSError, ValueError):
            classifications = hydrate_snapshot(industries, 'leaderboard:issuer_industries') or {}
    left, right = [fund_archive.quarter_book(fund_archive.documents(slug, quarter), quarter)
                   for slug in (first, second)]
    if not left or not right:
        return None
    return compare(_book(left, classifications), _book(right, classifications))
