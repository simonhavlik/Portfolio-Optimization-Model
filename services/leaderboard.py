"""Cross-sectional fund leaderboard.

Builds one comparable return series per fund, scores it on the metrics an
allocator would actually look at, and ranks the field.

Funds come in two shapes (see :mod:`services.leaderboard_funds`):

* listed vehicles — the traded share price, i.e. what an investor can own;
* 13F filers — their disclosed long US equity book, held at the reported
  weights (a buy-and-hold reconstruction, not the manager's actual trades).

The board is expensive to compute (a decade of daily prices for roughly a
hundred tickers), so it is written to disk and reused for a week.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from services import edgar_13f
from services import leaderboard_valuation as valuation
from services.leaderboard_funds import (
    BENCHMARK,
    CRISIS_WINDOWS,
    FUNDS,
    ISSUER_ALIASES,
    RANK_METRICS,
)
from services.risk_analytics import RiskAnalytics

CACHE_DIR = Path('data/leaderboard_cache')
BOARD_FILE = 'board.json'
CACHE_TTL = timedelta(days=7)

WINDOW_YEARS = 5
RISK_FREE = 0.045
PRICE_PERIOD = '10y'

risk = RiskAnalytics()


# --------------------------------------------------------------------------- #
# data assembly
# --------------------------------------------------------------------------- #

def _fund_book(fund: dict) -> dict | None:
    if fund['kind'] != '13f':
        return None
    book = edgar_13f.fund_13f(fund['cik'])
    return edgar_13f.apply_aliases(book, fund.get('aliases'), ISSUER_ALIASES)


def _weights(fund: dict, book: dict | None) -> dict[str, float]:
    if fund['kind'] == 'listed':
        return {fund['ticker']: 1.0}
    return edgar_13f.weights_from_book(book)


def _basket_returns(weights: dict[str, float], series: dict[str, pd.Series]) -> pd.Series:
    frame = {}
    for ticker, weight in weights.items():
        rets = series.get(ticker)
        if rets is None or rets.dropna().empty:
            continue
        frame[ticker] = rets * weight
    if not frame:
        return pd.Series(dtype=float)
    df = pd.DataFrame(frame)
    # only days where every constituent traded
    df = df.dropna(how='any')
    return df.sum(axis=1)


def _naive(series: pd.Series) -> pd.Series:
    if isinstance(series.index, pd.DatetimeIndex) and series.index.tz is not None:
        series = series.copy()
        series.index = series.index.tz_localize(None)
    return series


def _13f_series(books: list[tuple[dict, dict]], prices: dict) -> pd.Series:
    """Chain quarterly books: each filing's weights apply from its filing date
    until the next filing, which removes the look-ahead of a single snapshot."""
    parts = []
    for index, (filing, book) in enumerate(books):
        weights = edgar_13f.weights_from_book(book)
        series = {t: prices[t].returns for t in weights if t in prices}
        segment = _basket_returns(weights, series)
        if segment.empty:
            continue
        start = pd.Timestamp(filing['filed'])
        segment = segment[segment.index >= start]
        if index + 1 < len(books):
            end = pd.Timestamp(books[index + 1][0]['filed'])
            segment = segment[segment.index < end]
        if not segment.empty:
            parts.append(segment)
    if not parts:
        return pd.Series(dtype=float)
    return pd.concat(parts).sort_index()


def _fund_series(fund: dict, prices: dict, books: list[tuple[dict, dict]]) -> pd.Series:
    if fund['kind'] != 'listed':
        return _13f_series(books, prices)

    data = prices.get(fund['ticker'])
    if data is None:
        return pd.Series(dtype=float)
    local = data.returns.dropna()

    # non-USD listings must be compared with a USD benchmark on a common currency
    fx_ticker = fund.get('fx')
    if fx_ticker and fx_ticker in prices:
        fx = prices[fx_ticker].returns.dropna()
        joined = pd.concat([local, fx], axis=1, join='inner').dropna()
        if not joined.empty:
            local = (1 + joined.iloc[:, 0]) * (1 + joined.iloc[:, 1]) - 1
    return local


# --------------------------------------------------------------------------- #
# metrics
# --------------------------------------------------------------------------- #

def _window(series: pd.Series, years: int) -> pd.Series:
    if series.empty:
        return series
    end = series.index.max()
    start = end - pd.DateOffset(years=years)
    return series[series.index >= start]


def _crisis_return(series: pd.Series, start: str, end: str) -> float | None:
    if series is None or series.empty or not isinstance(series.index, pd.DatetimeIndex):
        return None
    window = series[(series.index >= start) & (series.index <= end)]
    if len(window) < 5:
        return None
    return float((1 + window).prod() - 1)


def _metrics(returns: pd.Series, benchmark: pd.Series) -> dict:
    aligned = pd.concat([returns, benchmark], axis=1, join='inner').dropna()
    if len(aligned) < 60:
        return {}
    port = aligned.iloc[:, 0]
    bench = aligned.iloc[:, 1]
    m = risk.calculate_comprehensive_metrics(port, bench, risk_free_rate=RISK_FREE)
    return {
        'annualized_return': float(m.annualized_return),
        'total_return': float(m.total_return),
        'volatility': float(m.volatility),
        'sharpe_ratio': float(m.sharpe_ratio),
        'sortino_ratio': float(m.sortino_ratio),
        'calmar_ratio': float(m.calmar_ratio),
        'max_drawdown': float(m.max_drawdown),
        'cvar_95': float(m.cvar_95),
        'beta': float(m.beta),
        'alpha': float(m.alpha),
        'information_ratio': float(m.information_ratio),
        'downside_volatility': float(m.downside_volatility),
    }


def _percentile_scores(rows: list[dict]) -> None:
    """Attach a 0-100 composite: mean percentile across the rankable metrics."""
    for key, _label, direction, _desc in RANK_METRICS:
        values = [(row, row['metrics'].get(key)) for row in rows]
        usable = [(row, v) for row, v in values if v is not None and not (isinstance(v, float) and math.isnan(v))]
        if len(usable) < 2:
            for row, _ in values:
                row.setdefault('ranks', {})[key] = None
            continue
        ordered = sorted(usable, key=lambda pair: pair[1], reverse=(direction > 0))
        count = len(ordered)
        for position, (row, _value) in enumerate(ordered, start=1):
            row.setdefault('ranks', {})[key] = position
            row.setdefault('percentiles', {})[key] = round(100 * (count - position) / max(1, count - 1), 1)

    for row in rows:
        parts = [v for v in row.get('percentiles', {}).values() if v is not None]
        row['score'] = round(sum(parts) / len(parts), 1) if parts else None
    rows.sort(key=lambda r: (r['score'] is None, -(r['score'] or 0)))


# --------------------------------------------------------------------------- #
# board
# --------------------------------------------------------------------------- #

def collect() -> tuple[set[str], dict, dict]:
    """Every ticker the board needs, plus the books behind the 13F funds."""
    histories: dict[str, list] = {}
    latest: dict[str, dict | None] = {}
    tickers: set[str] = {BENCHMARK}
    for fund in FUNDS:
        if fund['kind'] == 'listed':
            tickers.add(fund['ticker'])
            if fund.get('fx'):
                tickers.add(fund['fx'])
            continue
        filings = edgar_13f.fund_filings(fund['cik'], limit=20)
        books = []
        for filing in filings:
            book = edgar_13f.book_for_filing(fund['cik'], filing)
            book = edgar_13f.apply_aliases(book, fund.get('aliases'), ISSUER_ALIASES)
            books.append((filing, book))
            tickers.update(edgar_13f.weights_from_book(book).keys())
        histories[fund['slug']] = books
        latest[fund['slug']] = books[-1][1] if books else None
        print(f"[leaderboard] {fund['slug']}: {len(books)} filings", flush=True)
    return tickers, histories, latest


def collect_universe() -> list[str]:
    tickers, _histories, _latest = collect()
    return sorted(tickers)


def _top_weights(book: dict | None, limit: int = 15) -> dict[str, float]:
    if not book:
        return {}
    weights = edgar_13f.weights_from_book(book)
    return dict(sorted(weights.items(), key=lambda kv: kv[1], reverse=True)[:limit])


def _commentary(rows: list[dict], pe_median: float | None) -> dict:
    """Plain-language read of the board, built only from computed numbers."""
    ranked = [r for r in rows if r.get('score') is not None]
    ranked.sort(key=lambda r: r['score'], reverse=True)
    if not ranked:
        return {'lead': '', 'body': '', 'notable': ''}

    lead = ranked[0]
    runner = ranked[1] if len(ranked) > 1 else None
    last = ranked[-1]
    m = lead.get('metrics') or {}
    lead_text = (
        f"{lead['name']} leads on {lead['score']:.0f} of 100, with a Sharpe of "
        f"{m.get('sharpe_ratio', 0):.2f} and alpha of {m.get('alpha', 0) * 100:+.1f}% a year. "
        f"Its worst drawdown was {m.get('max_drawdown', 0) * 100:.1f}%."
    )
    spread = ranked[0]['score'] - last['score']
    body = (
        f"The spread between first and last is {spread:.0f} points. "
        f"{last['name']} sits at the bottom on {last['score']:.0f}, with a Sharpe of "
        f"{(last.get('metrics') or {}).get('sharpe_ratio', 0):.2f}."
    )
    if pe_median:
        body += f" The median trailing P/E across the priced books is {pe_median:.1f}x."
    notable = ''
    if runner:
        rm = runner.get('metrics') or {}
        notable = (
            f"{runner['name']} is second at {runner['score']:.0f}, on "
            f"{rm.get('annualized_return', 0) * 100:+.1f}% a year with "
            f"{rm.get('volatility', 0) * 100:.1f}% volatility."
        )
    return {'lead': lead_text, 'body': body, 'notable': notable}


def build_board(force: bool = False, window_years: int = WINDOW_YEARS, allow_fetch: bool = False) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / BOARD_FILE
    seed = Path('services/leaderboard_seed.json')
    if not force and not path.exists() and seed.exists():
        try:
            return json.loads(seed.read_text())
        except (OSError, ValueError):
            pass
    if not force and path.exists():
        try:
            payload = json.loads(path.read_text())
            stamped = datetime.fromisoformat(payload['computed_at'])
            if datetime.utcnow() - stamped < CACHE_TTL:
                return payload
        except (OSError, ValueError, KeyError):
            pass

    tickers, histories, latest = collect()

    from services.market_data import market_data_service
    import pickle

    price_cache = CACHE_DIR / f'prices_{PRICE_PERIOD}.pkl'
    cached_prices = {}
    if price_cache.exists():
        try:
            if datetime.utcnow() - datetime.fromtimestamp(price_cache.stat().st_mtime) < timedelta(days=3):
                cached_prices = pickle.loads(price_cache.read_bytes())
        except Exception:
            cached_prices = {}
    (CACHE_DIR / 'universe.json').write_text(json.dumps(sorted(tickers)))

    missing_tickers = [t for t in sorted(tickers) if t not in cached_prices]
    if missing_tickers and not allow_fetch:
        # never hit the network implicitly: a deploy, a page view or a rebuild
        # must not turn into an unplanned Yahoo fetch
        print(f'[leaderboard] {len(missing_tickers)} tickers missing from cache; '
              f'run the refresh API to fetch them', flush=True)
        batch = {}
    else:
        batch = market_data_service.fetch_batch_data(missing_tickers, period=PRICE_PERIOD) if missing_tickers else {}
    missing = [t for t in tickers if batch.get(t) is None]
    for ticker in missing:  # transient batch failures: retry one by one
        try:
            data = market_data_service.fetch_historical_data(ticker, period=PRICE_PERIOD)
            if data is not None:
                batch[ticker] = data
        except Exception:
            pass
    fetched = {t: _naive(d.returns) for t, d in batch.items() if d is not None and getattr(d, 'returns', None) is not None}
    cached_prices.update(fetched)
    try:
        price_cache.write_bytes(pickle.dumps(cached_prices))
    except Exception:
        pass
    prices = {t: type('P', (), {'returns': r})() for t, r in cached_prices.items() if t in tickers}
    benchmark_prices = prices.get(BENCHMARK)
    print(f'[leaderboard] prices {len(prices)}/{len(tickers)} tickers', flush=True)
    if not prices:
        raise RuntimeError('no price data returned; refusing to publish an empty board')

    bench_data = prices.get(BENCHMARK)
    benchmark = _window(bench_data.returns.dropna(), window_years) if bench_data is not None else pd.Series(dtype=float)

    rows = []
    for fund in FUNDS:
        try:
            series = _fund_series(fund, prices, histories.get(fund['slug'], []))
            full = series
            series = _window(series, window_years)
            metrics = _metrics(series, benchmark) if not series.empty else {}
        except Exception as exc:  # one bad fund must not sink the board
            series = pd.Series(dtype=float)
            metrics = {}
            print(f"[leaderboard] {fund['slug']} failed: {exc}", flush=True)
        book = latest.get(fund['slug'])
        row = {
            'slug': fund['slug'],
            'name': fund['name'],
            'manager': fund['manager'],
            'kind': fund['kind'],
            'style': fund['style'],
            'seat': fund['seat'],
            'since': fund['since'],
            'blurb': fund['blurb'],
            'ticker': fund.get('ticker'),
            'metrics': metrics,
            'holdings': 1,
            'coverage': round(book['coverage'], 4) if book else None,
            'filing': (book or {}).get('filed'),
            'crisis': {},
        }
        for key, _label, start, end in CRISIS_WINDOWS:
            # crises fall outside the five-year ranking window, so they are
            # measured on the full series rather than the windowed one
            row['crisis'][key] = _crisis_return(full, start, end)
        if fund['kind'] == '13f' and book:
            current = edgar_13f.weights_from_book(book)
            top = max(current.values()) if current else None
            row['holdings'] = len(current)
            row['metrics']['concentration'] = float(top) if top is not None else None
        else:
            row['metrics']['concentration'] = None
        row['observations'] = int(len(series))
        row['start'] = series.index.min().date().isoformat() if not series.empty else None
        if not series.empty:
            curve = (1 + series).cumprod() * 100
            weekly = curve.resample('W').last().dropna()
            row['curve'] = [round(float(v), 2) for v in weekly.tolist()]
            row['curve_dates'] = [d.date().isoformat() for d in weekly.index]
        rows.append(row)

    _percentile_scores(rows)

    # valuation: multiples come from a small, cached, rate-limited lookup; a
    # missing multiple is left missing rather than invented
    try:
        pe_universe = {t for r in rows if r['kind'] == '13f'
                       for t in list(_top_weights(latest.get(r['slug'])))[:15]}
        pe_universe.update(f['ticker'] for f in FUNDS if f['kind'] == 'listed')
        pe_cache = valuation.ensure(sorted(pe_universe), pause=0.9, limit=160) \
            if allow_fetch else valuation._load()
    except Exception:
        pe_cache = {}

    for row in rows:
        if row['kind'] == '13f':
            weights_book = _top_weights(latest.get(row['slug']))
            if weights_book:
                row['pe'] = valuation.weights_pe(weights_book, pe_cache)
        else:
            ticker = row.get('ticker')
            if ticker:
                row['pe'] = valuation.weights_pe({ticker: 1.0}, pe_cache)

    priced = [r['pe']['weighted'] for r in rows if (r.get('pe') or {}).get('weighted')]
    pe_median = sorted(priced)[len(priced) // 2] if priced else None

    payload = {
        'pe_median': pe_median,
        'commentary': _commentary(rows, pe_median),
        'computed_at': datetime.utcnow().isoformat(),
        'window_years': window_years,
        'risk_free': RISK_FREE,
        'benchmark': BENCHMARK,
        'as_of': benchmark.index.max().date().isoformat() if not benchmark.empty else None,
        'rows': rows,
        'metrics': [
            {'key': key, 'label': label, 'direction': direction, 'description': desc}
            for key, label, direction, desc in RANK_METRICS
        ],
        'crisis': [{'key': k, 'label': l, 'start': s, 'end': e} for k, l, s, e in CRISIS_WINDOWS],
    }
    path.write_text(json.dumps(payload, default=str))

    try:  # pre-render the chart board alongside the ranking
        from services.leaderboard_charts import warm
        warm(payload, CACHE_DIR, payload.get('as_of'))
    except Exception as exc:
        print(f'[leaderboard] chart warm failed: {exc}', flush=True)

    return payload


if __name__ == '__main__':
    board = build_board(force=True)
    print(f"as of {board['as_of']} · window {board['window_years']}y · rf {board['risk_free']:.1%}")
    print(f"{'#':>2} {'fund':26s} {'score':>5} {'sharpe':>6} {'sort':>5} {'alpha':>6} {'maxdd':>6} {'vol':>5} {'beta':>5}")
    for i, row in enumerate(board['rows'], 1):
        m = row['metrics']
        print(f"{i:>2} {row['name'][:26]:26s} {row['score'] or 0:5.1f} "
              f"{m.get('sharpe_ratio', 0):6.2f} {m.get('sortino_ratio', 0):5.2f} "
              f"{m.get('alpha', 0)*100:6.1f} {m.get('max_drawdown', 0)*100:6.1f} "
              f"{m.get('volatility', 0)*100:5.1f} {m.get('beta', 0):5.2f}")
