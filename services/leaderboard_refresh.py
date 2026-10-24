"""Gradual, resumable price refresh for the leaderboard.

Nothing here runs on its own. A deploy, a page view or a rebuild never calls
Yahoo: the board is served from the price cache or the committed seed, and
prices only move when this module is invoked explicitly (API, admin button or
the opt-in scheduler).

Each invocation fetches a small slice of stale tickers, one at a time with a
pause in between, persisting after every ticker so a crash or a timeout loses
nothing. When the slice is exhausted it rebuilds the board from the cache.
"""

from __future__ import annotations

import json
import pickle
import time
from datetime import datetime, timedelta
from pathlib import Path

CACHE_DIR = Path('data/leaderboard_cache')
STATE_FILE = CACHE_DIR / 'refresh_state.json'
PRICES_FILE = CACHE_DIR / 'prices_10y.pkl'
META_FILE = CACHE_DIR / 'prices_meta.json'
UNIVERSE_FILE = CACHE_DIR / 'universe.json'

PRICE_TTL = timedelta(days=7)


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def _read_prices() -> dict:
    try:
        return pickle.loads(PRICES_FILE.read_bytes())
    except (OSError, ValueError, pickle.UnpicklingError):
        return {}


def _write_prices(prices: dict) -> None:
    PRICES_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRICES_FILE.write_bytes(pickle.dumps(prices))


def state() -> dict:
    return _read_json(STATE_FILE, {
        'running': False,
        'fetched_total': 0,
        'remaining': None,
        'last_run': None,
        'last_error': None,
        'last_build': None,
    })


def _save_state(**changes) -> dict:
    current = state()
    current.update(changes)
    _write_json(STATE_FILE, current)
    return current


def universe() -> list[str]:
    tickers = _read_json(UNIVERSE_FILE, [])
    if tickers:
        return tickers
    try:
        from services.leaderboard import collect_universe
        tickers = collect_universe()
        _write_json(UNIVERSE_FILE, tickers)
    except Exception:
        tickers = []
    return tickers


def stale_tickers() -> list[str]:
    """Tickers in the universe with no price or a price older than the TTL."""
    prices = _read_prices()
    meta = _read_json(META_FILE, {})
    cutoff = (datetime.utcnow() - PRICE_TTL).isoformat()
    stale = []
    for ticker in universe():
        if ticker not in prices:
            stale.append(ticker)
        elif meta.get(ticker, '') < cutoff:
            stale.append(ticker)
    return stale


def run_batch(batch_size: int = 20, pause_seconds: float = 1.2, time_budget: float = 240.0) -> dict:
    """Fetch at most ``batch_size`` stale tickers, pausing between requests."""
    from services.market_data import market_data_service

    started = time.time()
    prices = _read_prices()
    meta = _read_json(META_FILE, {})
    pending = stale_tickers()
    if not pending:
        _save_state(running=False, remaining=0, last_run=datetime.utcnow().isoformat())
        return state()

    fetched = 0
    failures = 0
    for ticker in pending[:batch_size]:
        if time.time() - started > time_budget:
            break
        try:
            data = market_data_service.fetch_historical_data(ticker, period='10y')
            if data is not None and getattr(data, 'returns', None) is not None and len(data.returns):
                returns = data.returns
                if getattr(returns.index, 'tz', None) is not None:
                    returns = returns.copy()
                    returns.index = returns.index.tz_localize(None)
                prices[ticker] = returns
                meta[ticker] = datetime.utcnow().isoformat()
                fetched += 1
            else:
                failures += 1
        except Exception:
            failures += 1
        # persist after every ticker so a timeout loses nothing
        _write_prices(prices)
        _write_json(META_FILE, meta)
        time.sleep(pause_seconds)

    remaining = len(stale_tickers())
    _save_state(
        running=remaining > 0,
        fetched_total=state().get('fetched_total', 0) + fetched,
        remaining=remaining,
        last_run=datetime.utcnow().isoformat(),
        last_error=f'{failures} tickers failed' if failures else None,
    )

    if remaining == 0:
        try:
            from services.leaderboard import build_board
            build_board(force=True, allow_fetch=False)
            _save_state(last_build=datetime.utcnow().isoformat())
        except Exception as exc:
            _save_state(last_error=f'build failed: {exc}')

    return state()


def refresh_all(batch_size: int = 20, pause_seconds: float = 1.2, max_rounds: int = 40) -> dict:
    """Run batches until the universe is fresh (used by the background thread)."""
    _save_state(running=True, started_at=datetime.utcnow().isoformat())
    for _ in range(max_rounds):
        result = run_batch(batch_size=batch_size, pause_seconds=pause_seconds)
        if not result.get('remaining'):
            break
    _save_state(running=False, finished_at=datetime.utcnow().isoformat())
    return state()
