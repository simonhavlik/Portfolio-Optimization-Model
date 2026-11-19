"""Bounded, explicit mapping and adjusted-price refresh for the holdings board."""

from __future__ import annotations

import json
import os
import pickle
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime

from services import fund_archive
from services.filing_history import atomic_write
from services.leaderboard import CACHE_DIR, PRICE_PERIOD, _book_positions, _mapping, _read_prices
from services.leaderboard_funds import FUNDS, BENCHMARK

MAP_FILE = CACHE_DIR / 'cusip_map.json'
CUTOFF = '2020-06-01'


def _priorities():
    priority = defaultdict(float)
    mapping = _mapping()
    for fund in FUNDS:
        for book in fund_archive.timeline(fund_archive.documents(fund['slug'])):
            if book['filed'] < CUTOFF:
                continue
            for row in _book_positions(fund, book, mapping):
                cusip = row['cusip']
                priority[cusip] = max(priority[cusip], row['weight'])
    return priority


def refresh_mapping(limit: int = 200) -> dict:
    """Resolve the largest historic positions first, observing anonymous API limits."""
    priority = _priorities()
    mapping = _mapping()
    key = os.environ.get('OPENFIGI_API_KEY')
    headers = {'Content-Type': 'application/json'}
    if key:
        headers['X-OPENFIGI-APIKEY'] = key
    todo = sorted((cusip for cusip in priority if cusip not in mapping),
                  key=lambda cusip: -priority[cusip])[:limit]
    for offset in range(0, len(todo), 10):
        batch = todo[offset:offset + 10]
        body = json.dumps([{'idType': 'ID_CUSIP', 'idValue': cusip, 'exchCode': 'US'}
                           for cusip in batch]).encode()
        request = urllib.request.Request('https://api.openfigi.com/v3/mapping', body, headers)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                matches = json.load(response)
        except (OSError, ValueError) as exc:
            print(f'OpenFIGI request paused at {offset}: {exc}', flush=True)
            break
        for cusip, match in zip(batch, matches):
            data = match.get('data') or []
            tickers = {row['ticker'].replace('/', '-').replace('.', '-') for row in data
                       if row.get('exchCode') == 'US' and row.get('ticker')}
            if len(tickers) == 1:
                mapping[cusip] = tickers.pop()
            elif 'error' not in match:
                mapping[cusip] = None
        atomic_write(MAP_FILE, json.dumps({'_cached_at': datetime.utcnow().isoformat(), 'data': mapping}).encode())
        time.sleep(.4 if key else 2.5)
    return mapping


def refresh_missing_prices(limit: int = 80, *, refresh_existing: bool = False) -> int:
    """Fetch the biggest missing constituent series, never from a visitor request."""
    from services.market_data import market_data_service

    priority = _priorities()
    mapping = _mapping()
    tickers = {}
    for cusip, weight in priority.items():
        ticker = mapping.get(cusip)
        if ticker:
            tickers[ticker] = max(tickers.get(ticker, 0), weight)
    prices = _read_prices()
    todo = sorted((ticker for ticker in tickers if ticker != BENCHMARK
                   and (refresh_existing or ticker not in prices)), key=lambda ticker: -tickers[ticker])[:limit]
    out = CACHE_DIR / f'prices_{PRICE_PERIOD}.pkl'
    added = 0
    for ticker in todo:
        data = market_data_service.fetch_historical_data(ticker, period=PRICE_PERIOD, use_cache=False)
        if data is not None and len(data.returns):
            returns = data.returns.copy()
            returns.index = returns.index.tz_localize(None)
            prices[ticker] = returns
            atomic_write(out, pickle.dumps(prices))
            added += 1
        time.sleep(.8)
    return added
