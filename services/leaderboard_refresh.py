"""Explicit, rate-limited leaderboard update with a cross-worker lock."""

from __future__ import annotations

import fcntl
import json
import pickle
import time
from datetime import datetime, timedelta, timezone

from services.filing_history import atomic_write
from services.leaderboard import CACHE_DIR, PRICE_PERIOD, _read_prices
from services.leaderboard_funds import BENCHMARK, FUNDS

STATE_FILE = CACHE_DIR / 'refresh_state.json'
PRICES_FILE = CACHE_DIR / f'prices_{PRICE_PERIOD}.pkl'
META_FILE = CACHE_DIR / 'prices_meta.json'
LOCK_FILE = CACHE_DIR / 'refresh.lock'
PRICE_TTL = timedelta(days=7)
RETRY_TTL = timedelta(days=1)


def _read_json(path, default):
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        return default


def _write_json(path, payload):
    atomic_write(path, json.dumps(payload).encode())


def state() -> dict:
    return _read_json(STATE_FILE, {'running': False, 'fetched_total': 0, 'remaining': None,
                                   'last_run': None, 'last_error': None, 'last_build': None})


def _save_state(**changes) -> dict:
    payload = {**state(), **changes}
    _write_json(STATE_FILE, payload)
    return payload


def refresh_all(batch_size: int = 20, pause_seconds: float = 1.2, max_rounds: int = 4) -> dict:
    """Refresh SEC changes, then progress through a bounded queue of price updates."""
    from services.filing_history import download_history
    from services.fund_archive import import_archive, publish_quarters
    from services.leaderboard import BOARD_FILE, build_board, collect_universe
    from services.leaderboard_research import MANIFEST, rebuild
    from services.leaderboard_storage import publish
    from services.leaderboard_market import refresh_mapping
    from services.market_data import market_data_service
    from services.portfolio_timeline import publish_fund_reports
    from services.fund_reports import publish_standard_reports

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open('ab') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return state()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        _save_state(running=True, started_at=now.isoformat(), last_error=None)
        try:
            download_history(FUNDS)
            import_archive()
            publish_quarters()
            refresh_mapping()
            prices = _read_prices()
            meta = _read_json(META_FILE, {})
            attempted = meta.pop('_failed', {})
            tickers = collect_universe()
            cutoff = (now - PRICE_TTL).isoformat()
            retry = (now - RETRY_TTL).isoformat()
            work = [t for t in tickers if t != BENCHMARK
                    and (t not in prices or meta.get(t, '') < cutoff)
                    and attempted.get(t, '') < retry]
            fetched = 0
            errors = 0
            step = work[:batch_size * max_rounds]
            for ticker in step:
                data = market_data_service.fetch_historical_data(ticker, period=PRICE_PERIOD, use_cache=False)
                if data is not None and len(data.returns):
                    series = data.returns.copy()
                    series.index = series.index.tz_localize(None)
                    prices[ticker] = series
                    meta[ticker] = now.isoformat()
                    attempted.pop(ticker, None)
                    atomic_write(PRICES_FILE, pickle.dumps(prices))
                    fetched += 1
                else:
                    attempted[ticker] = now.isoformat()
                    errors += 1
                _write_json(META_FILE, {**meta, '_failed': attempted})
                time.sleep(pause_seconds)

            remaining = len(work) - len(step)
            # Advance the common end date after the constituent refresh queue drains.
            if BENCHMARK not in prices or not remaining:
                data = market_data_service.fetch_historical_data(BENCHMARK, period=PRICE_PERIOD, use_cache=False)
                if data is not None and len(data.returns):
                    prices[BENCHMARK] = data.returns
                    atomic_write(PRICES_FILE, pickle.dumps(prices))
            board = build_board(force=True)
            rebuild()
            publish(board_path=CACHE_DIR / BOARD_FILE, studies_path=MANIFEST, prices=prices)
            publish_fund_reports(prices=prices, revision=board['revision'])
            publish_standard_reports(prices=prices, revision=board['revision'], refresh_factors=True)
            return _save_state(running=False, last_run=now.isoformat(), last_build=now.isoformat(),
                               fetched_total=state().get('fetched_total', 0) + fetched, remaining=remaining,
                               last_error=f'{errors} price series unavailable' if errors else None)
        except Exception as exc:
            _save_state(running=False, last_error=str(exc))
            raise
