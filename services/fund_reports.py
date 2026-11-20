"""Precompute the standard report for the dated, disclosed portfolio of each manager."""

from datetime import datetime, timezone
from collections import defaultdict
from io import BytesIO, StringIO
import json
import logging
import re
from zipfile import ZipFile

import pandas as pd
import requests

from models.database import AppCache, db
from services import fund_archive
from services.example_portfolio import _finite_json, example_portfolio_service, serialize_for_template
from services.holdings_style import INDUSTRIES
from services.leaderboard import CACHE_DIR, RISK_FREE, _book_positions, _mapping, _read_prices, _series, build_board, scheduled_returns
from services.leaderboard_funds import BENCHMARK, FUNDS, ISSUER_ALIASES
from services.leaderboard_storage import published_snapshot
from services.portfolio_timeline import FUND_REPORT_PREFIX

logger = logging.getLogger(__name__)
FULL_REPORT_PREFIX = 'fund_analysis:history-'
FACTOR_FILE = CACHE_DIR / 'french_daily_factors.csv'
FACTOR_KEY = 'leaderboard:daily_factors'
FACTOR_SOURCE = 'https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html'


def download_factors():
    """Store a daily factor snapshot for explicit offline report publication."""
    def read(name, columns):
        url = f'https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp/{name}_CSV.zip'
        response = requests.get(url, timeout=20)
        response.raise_for_status()
        with ZipFile(BytesIO(response.content)) as archive:
            raw = archive.read(archive.namelist()[0]).decode('latin1')
        lines = [line for line in raw.splitlines() if re.match(r'^\s*\d{8}\s*,', line)]
        data = pd.read_csv(StringIO('\n'.join(lines)), names=['day', *columns])
        data.index = pd.to_datetime(data.pop('day').astype(str), format='%Y%m%d')
        return data.astype(float).where(lambda frame: frame > -90).div(100)

    factors = read('F-F_Research_Data_5_Factors_2x3_daily', ['mkt-rf', 'smb', 'hml', 'rmw', 'cma', 'rf'])
    momentum = read('F-F_Momentum_Factor_daily', ['mom'])
    FACTOR_FILE.parent.mkdir(parents=True, exist_ok=True)
    factors.join(momentum, how='inner').dropna().loc['2008-01-01':].to_csv(FACTOR_FILE)


def _factor_snapshot(refresh):
    if refresh:
        try:
            download_factors()
        except (ValueError, OSError, requests.RequestException):
            logger.exception('Could not update published factor data')
    if FACTOR_FILE.is_file():
        return pd.read_csv(FACTOR_FILE, index_col=0, parse_dates=True)
    saved = db.session.get(AppCache, FACTOR_KEY)
    if saved and saved.payload:
        return pd.read_json(StringIO(saved.payload['table']), orient='split')


def _classification():
    saved = published_snapshot('leaderboard:issuer_industries')
    if saved:
        return saved
    path = CACHE_DIR / 'issuer_industries.json'
    if path.is_file():
        return json.loads(path.read_bytes())
    entry = db.session.get(AppCache, 'leaderboard:issuer_industries')
    return entry.payload if entry and entry.payload else {}


def _portfolio_positions(fund, book, mapping, industries):
    positions = _book_positions(fund, book, mapping)
    return [{
        'ticker': row['ticker'] or row['issuer'],
        'symbol': row['ticker'],
        'weight': row['weight'],
        'sector': INDUSTRIES.get(industries.get(row['cusip'], {}).get('sector'), 'Unclassified'),
        'name': row['issuer'],
        'price': None,
    } for row in positions]


def quarter_allocation(fund, period):
    """Read the saved quarter and its changes for the report's holdings control."""
    snapshot = fund_archive.saved_quarter(fund['slug'], period)
    if not snapshot:
        return None
    mapping, industries = _mapping(), _classification()
    aliases = {**ISSUER_ALIASES, **fund.get('aliases', {})}
    live, exits = [], []
    sectors = defaultdict(float)
    equities = [row for row in snapshot['rows'] if not row['option'] and row['quantity_type'] == 'SH']
    total = sum(row['value'] for row in equities if row['value'] > 0)
    for row in equities:
        issuer = row['issuer']
        ticker = mapping.get(row['cusip']) or next((ticker for prefix, ticker in aliases.items()
                                                     if issuer.upper().startswith(prefix)), None)
        sector = INDUSTRIES.get(industries.get(row['cusip'], {}).get('sector'), 'Unclassified')
        weight = row['value'] / total if total and row['value'] > 0 else 0
        if row.get('action') == 'Exited':
            exits.append((abs(row.get('weight_change', 0)), ticker or issuer))
        elif weight:
            sectors[sector] += weight
            live.append({'ticker': ticker, 'name': issuer, 'sector': sector, 'weight': weight,
                         'action': row.get('action'), 'shares_change': row.get('shares_change')})
    live.sort(key=lambda row: -row['weight'])
    exits.sort(reverse=True)
    return {'period': period, 'previous': snapshot['previous'], 'count': len(live),
            'filed': snapshot['book']['filed'] if snapshot['book'] else None,
            'rows': live[:25], 'exit_count': len(exits), 'exits': [label for _, label in exits[:5]],
            'sectors': dict(sorted(sectors.items(), key=lambda item: -item[1]))}


def compact_report(analysis):
    """Keep the report, chart-feed and workbook fields in the publication."""
    report = {key: analysis[key] for key in (
        'meta', 'holdings', 'disclosed_holdings', 'metrics', 'advanced_risk', 'charts',
        'monte_carlo', 'correlation_matrix', 'stress_test', 'statistical_tests',
        'factor_exposure', 'ai_insights', 'distributions_dict', 'benchmarks', 'revision',
    ) if key in analysis}
    if report['charts'].get('rolling_metrics'):
        report['charts']['rolling_metrics'] = {'60d': report['charts']['rolling_metrics']['60d']}
    return report


def _serialize_report(analysis):
    if simulation := analysis.get('monte_carlo'):
        analysis['monte_carlo'] = {name: getattr(simulation, name) for name in (
            'probability_profit', 'mean_final', 'median_final', 'var_95', 'confidence_interval_95',
        )}
    return _finite_json(serialize_for_template(analysis))


def dated_upload_report(timeline, schedule, prices, *, n_simulations, horizon_days):
    name, symbol = timeline['name'], timeline['benchmark']
    positions = [{'ticker': ticker, 'weight': weight, 'sector': 'Unclassified'}
                 for ticker, weight in sorted(schedule[-1][1].items(), key=lambda item: -item[1])]
    calendar = prices[symbol].dropna().index
    cutoff = calendar[-252:]
    meta = {'name': name, 'period': 'Dated allocations', 'computed_at': datetime.now(timezone.utc).isoformat(),
            'benchmark': symbol, 'n_assets': len(positions), 'risk_free_rate': timeline['risk_free'],
            'description': 'Performance reconstructed from your dated allocations'}
    result = {'meta': meta, 'holdings': positions, 'metrics': {}, 'charts': {}}
    matrix = [p for p in positions if p['ticker'] in prices
              and prices[p['ticker']].reindex(cutoff).notna().all()][:12]
    if not timeline['metrics'] or len(matrix) < 2:
        return result
    daily, _ = scheduled_returns(schedule, prices, calendar)
    analysis = _serialize_report(example_portfolio_service.compute_custom(
        matrix, name=name, benchmark=symbol, n_simulations=n_simulations, horizon_days=horizon_days,
        period=f"{timeline['metric_start']} to {timeline['metric_end']}",
        description=meta['description'], risk_free_rate=timeline['risk_free'], market_snapshot=prices,
        historical_returns=daily.loc[timeline['metric_start']:timeline['metric_end']],
        factor_returns=_factor_snapshot(False),
    ))
    if not analysis['metrics']:
        return result
    analysis['disclosed_holdings'] = positions
    analysis['meta'].update(disclosed_assets=len(positions), matrix_coverage=sum(p['weight'] for p in matrix),
                            matrix_start=cutoff[0].date().isoformat(), matrix_end=cutoff[-1].date().isoformat(),
                            disclosed_quarter=schedule[-1][0])
    return compact_report(analysis)


def standard_report(fund, timeline, *, prices, factors, mapping, industries, revision):
    books = fund_archive.timeline(fund_archive.documents(fund['slug']))
    if not books:
        return None
    latest = books[-1]
    positions = _portfolio_positions(fund, latest, mapping, industries)
    benchmark = prices[BENCHMARK]
    cap = 12
    meta = {
        'name': fund['name'], 'period': 'Filing history', 'computed_at': datetime.now(timezone.utc).isoformat(),
        'n_assets': len(positions), 'benchmark': BENCHMARK, 'risk_free_rate': RISK_FREE,
        'description': f'Long-equity allocations from SEC filings through {latest["period"]}',
        'disclosed_quarter': latest['period'], 'filing_period': latest['filed'],
    }
    result = {'meta': meta, 'holdings': positions[:25], 'metrics': {}, 'charts': {}, 'revision': revision}
    if not timeline.get('metric_start') or not timeline['metrics']:
        return result

    calendar = benchmark.dropna().index
    historical, _ = _series(fund, books, prices, calendar, mapping)
    daily = historical.loc[timeline['metric_start']:timeline['metric_end']].dropna()
    cutoff = calendar[-252:]
    matrix_positions = []
    used = set()
    for position in positions:
        ticker = position['symbol']
        if ticker in used or ticker not in prices:
            continue
        quote = prices[ticker].reindex(cutoff)
        if quote.notna().all():
            matrix_positions.append(position)
            used.add(ticker)
            if len(matrix_positions) == cap:
                break
    if len(matrix_positions) < 2:
        return result

    snapshot = {ticker: prices[ticker] for ticker in used}
    snapshot.update({ticker: prices[ticker] for ticker in ('SPY', 'VT', 'QQQ', 'EFA', 'GLD', 'IWM') if ticker in prices and ticker not in used})
    # If the chosen book itself holds a benchmark ETF, retain the benchmark horizon.
    snapshot[BENCHMARK] = benchmark
    analysis = example_portfolio_service.compute_custom(
        matrix_positions, name=fund['name'], period=f"{timeline['metric_start']} to {timeline['metric_end']}",
        description=meta['description'], n_simulations=5000, market_snapshot=snapshot,
        historical_returns=daily, factor_returns=factors,
    )
    analysis = _serialize_report(analysis)
    if not analysis.get('metrics'):
        return result
    analysis['meta'].update(n_assets=len(matrix_positions), disclosed_assets=len(positions),
                            disclosed_quarter=latest['period'], filing_period=latest['filed'],
                            matrix_coverage=sum(position['weight'] for position in matrix_positions),
                            matrix_start=cutoff[0].date().isoformat(), matrix_end=cutoff[-1].date().isoformat(),
                            factor_source=FACTOR_SOURCE if analysis.get('factor_exposure') else None)
    analysis['disclosed_holdings'] = positions[:25]
    sectors = defaultdict(float)
    for position in positions:
        sectors[position['sector']] += position['weight']
    analysis['charts']['allocation'] = {
        'sectors': dict(sectors), 'pie': {'labels': list(sectors), 'values': list(sectors.values())},
    }
    analysis['revision'] = revision
    return compact_report(analysis)


def publish_standard_reports(*, prices=None, revision=None, refresh_factors=False):
    """Publish reports alongside the compact filing-history snapshots."""
    prices = _read_prices() if prices is None else prices
    revision = revision or build_board().get('revision')
    factors = _factor_snapshot(refresh_factors)
    if factors is not None and FACTOR_FILE.is_file():
        db.session.merge(AppCache(key=FACTOR_KEY, payload={'table': factors.to_json(orient='split', date_format='iso')}))
        db.session.commit()
    mapping = _mapping()
    industries = _classification()
    counts = [0, 0]
    for fund in FUNDS:
        slug = fund['slug']
        timeline = db.session.get(AppCache, FUND_REPORT_PREFIX + slug)
        if not timeline or not timeline.payload:
            continue
        report = standard_report(fund, timeline.payload, prices=prices, factors=factors,
                                 mapping=mapping, industries=industries, revision=revision)
        if report:
            db.session.merge(AppCache(key=FULL_REPORT_PREFIX + slug, payload=report, computed_at=datetime.now(timezone.utc)))
            db.session.commit()
            counts[0] += bool(report['metrics'])
            counts[1] += 1
            logger.info('%s standard report cached (%s)', slug, 'priced' if report['metrics'] else 'holdings')
    return counts


if __name__ == '__main__':
    from app import app

    with app.app_context():
        print('Full reports, total reports:', publish_standard_reports(refresh_factors=True))
