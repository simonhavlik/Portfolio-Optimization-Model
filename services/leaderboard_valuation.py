"""Trailing P/E for the leaderboard's holdings, fetched gently and cached.

Yahoo is the only free source here, so the fetch is deliberately small and
slow: the largest positions of each fund, one request at a time, with a pause,
cached for a week. A missing multiple is disclosed as missing rather than
guessed.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

CACHE_DIR = Path('data/leaderboard_cache')
PE_FILE = CACHE_DIR / 'pe_ratios.json'
TTL = timedelta(days=7)


def _load() -> dict:
    try:
        return json.loads(PE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save(payload: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    PE_FILE.write_text(json.dumps(payload))


def _fresh(entry: dict | None) -> bool:
    if not entry:
        return False
    try:
        return datetime.utcnow() - datetime.fromisoformat(entry['at']) < TTL
    except (KeyError, ValueError):
        return False


def _fetch(ticker: str) -> dict | None:
    import warnings

    warnings.filterwarnings('ignore')
    try:
        import yfinance as yf

        info = yf.Ticker(ticker).info or {}
        trailing = info.get('trailingPE')
        forward = info.get('forwardPE')
        market_cap = info.get('marketCap')
        if trailing is None and forward is None:
            return None
        return {
            'trailing': float(trailing) if trailing not in (None, 0) else None,
            'forward': float(forward) if forward not in (None, 0) else None,
            'market_cap': float(market_cap) if market_cap else None,
            'at': datetime.utcnow().isoformat(),
        }
    except Exception:
        return None


def ensure(tickers: list[str], pause: float = 0.9, limit: int = 160) -> dict:
    """Fetch missing multiples for at most ``limit`` tickers, pausing between."""
    cache = _load()
    todo = [t for t in tickers if not _fresh(cache.get(t))][:limit]
    for ticker in todo:
        entry = _fetch(ticker)
        if entry:
            cache[ticker] = entry
            _save(cache)
        time.sleep(pause)
    return cache


def weights_pe(weights: dict[str, float], cache: dict) -> dict:
    """Value-weighted P/E and the median multiple of the same book."""
    pairs = [(w, cache[t]['trailing']) for t, w in weights.items()
             if t in cache and cache[t].get('trailing')]
    pairs = [(w, pe) for w, pe in pairs if 0 < pe < 120]
    if not pairs:
        return {'weighted': None, 'median': None, 'coverage': 0.0}

    total_weight = sum(w for w, _ in pairs)
    weighted = sum(w * pe for w, pe in pairs) / total_weight if total_weight else None
    values = sorted(pe for _, pe in pairs)
    mid = len(values) // 2
    median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2
    covered = sum(weights.get(t, 0) for t, _ in [(t, cache[t]['trailing']) for t in weights
                                                  if t in cache and cache[t].get('trailing')])
    return {'weighted': round(weighted, 1) if weighted else None,
            'median': round(median, 1),
            'coverage': round(min(1.0, covered), 3)}
