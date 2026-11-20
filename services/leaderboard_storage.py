"""Persist complete, versioned leaderboard publications in the application database."""

from __future__ import annotations

import io
import json
import re
from datetime import datetime
from pathlib import Path
from time import monotonic

import numpy as np
import pandas as pd
from flask import current_app, has_app_context, has_request_context
from sqlalchemy.exc import SQLAlchemyError

from models.database import AppCache, FundPriceHistory, FundResearchAsset, db
from services.filing_history import atomic_write

BOARD_KEY = 'leaderboard:board'
STUDIES_KEY = 'leaderboard:studies'
CUSIP_KEY = 'leaderboard:cusip_map'
VALUATION_KEY = 'leaderboard:valuation'
DRAFTS_KEY = 'leaderboard:editorial_backlog'


def _create_tables():
    for model in (AppCache, FundResearchAsset, FundPriceHistory):
        model.__table__.create(db.engine, checkfirst=True)


def _cache(key, payload):
    db.session.merge(AppCache(key=key, payload=payload, computed_at=datetime.utcnow()))


def serving_postgres():
    return has_request_context() and db.engine.dialect.name == 'postgresql'


def published_snapshot(key):
    """Check PostgreSQL for the active publication once per minute per worker."""
    if not serving_postgres():
        return None
    cache = current_app.extensions.setdefault('leaderboard_publications', {})
    expires, saved = cache.get(key, (0, None))
    if monotonic() >= expires:
        try:
            payload = db.session.scalar(db.select(AppCache.payload).where(AppCache.key == key))
        except SQLAlchemyError:
            db.session.rollback()
        else:
            saved = payload
        cache[key] = (monotonic() + 60, saved)
    return saved


def hydrate_snapshot(path: Path, key: str) -> dict | None:
    if not has_app_context():
        return None
    try:
        row = db.session.get(AppCache, key)
        if row and row.payload:
            atomic_write(path, json.dumps(row.payload, separators=(',', ':')).encode())
            return row.payload
    except SQLAlchemyError:
        db.session.rollback()
    return None


def hydrate_asset(path: Path) -> bool:
    if not has_app_context():
        return False
    try:
        data = db.session.scalar(db.select(FundResearchAsset.content).where(
            FundResearchAsset.path == f'{path.parent.name}/{path.name}'))
        if data:
            atomic_write(path, data)
            return True
    except SQLAlchemyError:
        db.session.rollback()
    return False


def _pack(series):
    index = pd.DatetimeIndex(series.index).tz_localize(None)
    ordinal = index.to_numpy().astype('datetime64[D]').astype(np.int32)
    out = io.BytesIO()
    np.savez_compressed(out, days=ordinal, returns=series.to_numpy(dtype=np.float64))
    return out.getvalue()


def _unpack(content):
    with np.load(io.BytesIO(content), allow_pickle=False) as source:
        return pd.Series(source['returns'], index=pd.to_datetime(source['days'], unit='D'))


def load_prices(tickers: set[str] | None = None):
    if not has_app_context():
        return {}
    stmt = db.select(FundPriceHistory.ticker, FundPriceHistory.returns)
    if tickers is not None:
        stmt = stmt.where(FundPriceHistory.ticker.in_(tickers))
    try:
        return {ticker: _unpack(content) for ticker, content in db.session.execute(stmt)}
    except SQLAlchemyError:
        db.session.rollback()
        return {}


def publish(*, board_path, studies_path, prices, drafts=None):
    """Store assets first and replace both metadata pointers in one final commit."""
    _create_tables()
    boards = json.loads(board_path.read_bytes())
    studies = json.loads(studies_path.read_bytes()) if studies_path.is_file() else {}
    chart_store = board_path.parent / 'charts'
    assets = []
    for board in boards.values():
        for key in board.get('charts', []):
            assets.extend(chart_store / f"{board.get('charts_revision', board['revision'])}-{board['cohort']}-{key}.{ext}" for ext in ('png', 'webp'))
    for key, study in studies.items():
        for panel in ('chart', 'report'):
            revision = study.get('report_revision', study['revision']) if panel == 'report' else study['revision']
            assets.extend(studies_path.parent / f"{revision}-{key}-{panel}.{ext}" for ext in ('png', 'webp'))
    for asset in assets:
        key = f'{asset.parent.name}/{asset.name}'
        if db.session.get(FundResearchAsset, key) is None:
            db.session.add(FundResearchAsset(path=key, content=asset.read_bytes()))
            db.session.commit()
    for ticker, series in prices.items():
        if not len(series) or not re.fullmatch(r'[-.^=A-Z0-9]{1,32}', ticker):
            continue
        existing = db.session.get(FundPriceHistory, ticker)
        end = series.index[-1].date()
        packed = _pack(series)
        if existing and existing.returns == packed:
            continue
        db.session.merge(FundPriceHistory(ticker=ticker, end_date=end, observations=len(series), returns=packed))
        db.session.commit()
    _cache(BOARD_KEY, boards)
    _cache(STUDIES_KEY, studies)
    for filename, key in [('cusip_map.json', CUSIP_KEY), ('pe_ratios.json', VALUATION_KEY),
                          ('holdings_style.json', 'leaderboard:holdings_style'),
                          ('issuer_classifications.json', 'leaderboard:issuer_classifications')]:
        path = board_path.parent / filename
        if path.is_file():
            _cache(key, json.loads(path.read_bytes()))
    if drafts is not None:
        _cache(DRAFTS_KEY, drafts)
    db.session.commit()
    return len(assets)
