"""Comparable company analysis: AI-proposed peers, algorithmically validated."""

import json
import logging
import statistics
from datetime import datetime, timedelta

from services.ai_insights import LLMClient

logger = logging.getLogger(__name__)

CACHE_DAYS = 7


def _cached_peers(ticker):
    try:
        from models.database import db, AppCache
        row = db.session.get(AppCache, f"cca_peers:{ticker}")
        if row and row.payload and (datetime.utcnow() - row.computed_at) < timedelta(days=CACHE_DAYS):
            return row.payload.get("peers") or []
    except Exception:
        db = None
    return None


def _store_peers(ticker, peers):
    try:
        from models.database import db, AppCache
        row = db.session.get(AppCache, f"cca_peers:{ticker}") or AppCache(key=f"cca_peers:{ticker}")
        row.payload = {"peers": peers}
        row.computed_at = datetime.utcnow()
        db.session.add(row)
        db.session.commit()
    except Exception:
        try:
            db.session.rollback()
        except Exception:
            pass


def _propose_peers(ticker, name, industry):
    prompt = (
        f"List four US-listed comparable companies for {ticker} ({name or ticker}"
        f"{', ' + industry if industry else ''}). "
        'Return only JSON: {"peers": ["TICK1", "TICK2", "TICK3", "TICK4"]}. '
        "Use exchange tickers, no commentary."
    )
    try:
        raw = LLMClient(temperature=0.1).complete(
            prompt,
            system_prompt="You are an equity research associate. Peers must be listed and tradable.",
            temperature=0.1,
        )
        raw = raw.strip()
        if "```" in raw:
            raw = max(raw.split("```"), key=len).replace("json", "", 1)
        start, end = raw.find("{"), raw.rfind("}")
        payload = json.loads(raw[start:end + 1])
        peers = [str(p).upper().strip() for p in payload.get("peers", [])]
        peers = [p for p in peers if p and p != ticker and p.isalnum()][:4]
        if peers:
            _store_peers(ticker, peers)
            return peers
    except Exception as exc:
        logger.warning("Peer proposal failed for %s: %s", ticker, exc)
    return []


def _peers_from_portfolio(ticker, sector, holdings, fundamentals):
    """Deterministic fallback: other holdings in the same sector."""
    peers = []
    for holding in holdings:
        other = (holding.get("ticker") or "").upper()
        if other and other != ticker and holding.get("sector") == sector:
            peers.append(other)
    return peers[:4]


def build_cca(holdings, fundamentals_fn, top_n=5):
    from services.valuation import _fundamentals  # reuse the cached fundamentals

    ranked = sorted(holdings, key=lambda h: float(h.get("weight") or 0), reverse=True)[:top_n]
    rows = []
    for holding in ranked:
        ticker = (holding.get("ticker") or "").upper()
        if not ticker:
            continue
        base = _fundamentals(ticker) or {}
        pe = base.get("pe")
        price = None
        try:
            import yfinance as yf
            price = yf.Ticker(ticker).fast_info.get("last_price")
        except Exception:
            price = None

        sector = base.get("sector") or holding.get("sector")
        peers = _cached_peers(ticker)
        if peers is None:
            peers = _propose_peers(ticker, base.get("name"), sector)
        if not peers:
            peers = _peers_from_portfolio(ticker, sector, holdings, base)

        peer_pes = []
        for peer in peers:
            data = _fundamentals(peer) or {}
            peer_pe = data.get("pe")
            if isinstance(peer_pe, (int, float)) and 2 < peer_pe < 120:
                peer_pes.append(peer_pe)

        median_pe = statistics.median(peer_pes) if peer_pes else None
        eps = (price / pe) if (price and pe and pe > 0) else None
        implied = (eps * median_pe) if (eps and median_pe) else None
        upside = ((implied / price) - 1) if (implied and price) else None

        rows.append({
            "ticker": ticker,
            "sector": sector,
            "weight": float(holding.get("weight") or 0),
            "pe": pe,
            "price": price,
            "peers": peers,
            "peer_pes": peer_pes,
            "peer_median_pe": median_pe,
            "implied_price": implied,
            "upside": upside,
        })

    medians = [row["peer_median_pe"] for row in rows if row["peer_median_pe"]]
    return {
        "rows": rows,
        "median_pe": statistics.median(medians) if medians else None,
        "method": (
            "Peers proposed by the model, validated against Yahoo Finance multiples; "
            "implied price uses the peer median P/E and the company's trailing EPS."
        ),
    }


def build_and_cache(holdings) -> dict:
    result = build_cca(holdings, None)
    try:
        from models.database import db, AppCache
        row = db.session.get(AppCache, "cca_example") or AppCache(key="cca_example")
        row.payload = result
        row.computed_at = datetime.utcnow()
        db.session.add(row)
        db.session.commit()
    except Exception as exc:
        try:
            db.session.rollback()
        except Exception:
            pass
        logger.warning("CCA cache not stored: %s", exc)
    return result


def cached_cca():
    try:
        from models.database import db, AppCache
        row = db.session.get(AppCache, "cca_example")
        if row and row.payload and (datetime.utcnow() - row.computed_at) < timedelta(days=CACHE_DAYS):
            return row.payload
    except Exception:
        pass
    return None
