"""Portfolio valuation: Damodaran ERP, reverse-DCF implied return, CAPM cost of equity."""

import logging
import os
import tempfile
import time
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any, Dict, Optional

import numpy as np

from config import config

logger = logging.getLogger(__name__)

ERP_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/histimpl.xls"
ERP_CACHE_KEY = "damodaran_erp"
ERP_MAX_AGE = timedelta(days=30)
FUND_CACHE_DAYS = 7

_memory: Dict[str, Any] = {}


def _cached(key: str, max_age: timedelta):
    entry = _memory.get(key)
    if entry and datetime.utcnow() - entry["at"] < max_age:
        return entry["value"]
    return None


def _remember(key: str, value) -> None:
    _memory[key] = {"value": value, "at": datetime.utcnow()}


def damodaran_erp() -> Dict[str, Any]:
    """Latest implied equity risk premium from Damodaran's annual workbook."""
    memo = _cached("erp", timedelta(hours=12))
    if memo:
        return memo

    try:
        from models.database import db, AppCache
        row = db.session.get(AppCache, ERP_CACHE_KEY)
        if row and row.payload and (datetime.utcnow() - row.computed_at) < ERP_MAX_AGE:
            _remember("erp", row.payload)
            return row.payload
    except Exception:
        row = None

    value = None
    try:
        import requests
        import xlrd

        response = requests.get(ERP_URL, timeout=25)
        response.raise_for_status()
        book = xlrd.open_workbook(file_contents=response.content)
        sheet = book.sheet_by_index(0)

        header_row, erp_col = None, None
        for r in range(min(20, sheet.nrows)):
            for c in range(sheet.ncols):
                if "Implied ERP (FCFE)" in str(sheet.cell_value(r, c)):
                    header_row, erp_col = r, c
                    break
            if erp_col is not None:
                break

        if erp_col is not None:
            for r in range(sheet.nrows - 1, header_row, -1):
                year = sheet.cell_value(r, 0)
                erp = sheet.cell_value(r, erp_col)
                if isinstance(year, float) and isinstance(erp, float) and 0.005 < erp < 0.25:
                    value = {
                        "value": erp,
                        "year": int(year),
                        "source": "Damodaran implied ERP (FCFE), histimpl.xls",
                        "fetched_at": datetime.utcnow().isoformat(),
                    }
                    break
    except Exception as exc:
        logger.warning("Damodaran ERP unavailable: %s", exc)

    if value is None:
        value = {
            "value": 0.05,
            "year": None,
            "source": "Fallback constant (Damodaran data unavailable)",
            "fetched_at": datetime.utcnow().isoformat(),
        }

    try:
        from models.database import db, AppCache
        row = db.session.get(AppCache, ERP_CACHE_KEY) or AppCache(key=ERP_CACHE_KEY)
        row.payload = value
        row.computed_at = datetime.utcnow()
        db.session.add(row)
        db.session.commit()
    except Exception as exc:
        logger.debug("ERP cache not stored: %s", exc)
        try:
            db.session.rollback()
        except Exception:
            pass

    _remember("erp", value)
    return value


def _fundamentals(ticker: str) -> Optional[Dict[str, Any]]:
    """Per-ticker owner-earnings inputs, cached in Postgres for a week."""
    key = f"fundamentals:{ticker}"
    memo = _cached(key, timedelta(hours=12))
    if memo is not None:
        return memo

    payload = None
    try:
        from models.database import db, AppCache
        row = db.session.get(AppCache, key)
        if row and row.payload and (datetime.utcnow() - row.computed_at) < FUND_CACHE_DAYS:
            payload = row.payload
    except Exception:
        row = None

    if payload is None:
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info or {}
            payload = {
                "ticker": ticker,
                "name": info.get("longName") or info.get("shortName"),
                "pe": info.get("trailingPE"),
                "forward_pe": info.get("forwardPE"),
                "dividend_yield": info.get("dividendYield"),
                "fcf": info.get("freeCashflow"),
                "market_cap": info.get("marketCap"),
                "beta": info.get("beta"),
                "sector": info.get("sector"),
            }
            from models.database import db, AppCache
            row = db.session.get(AppCache, key) or AppCache(key=key)
            row.payload = payload
            row.computed_at = datetime.utcnow()
            db.session.add(row)
            db.session.commit()
        except Exception as exc:
            logger.warning("Fundamentals unavailable for %s: %s", ticker, exc)
            try:
                db.session.rollback()
            except Exception:
                pass
            payload = {"ticker": ticker}

    _remember(key, payload)
    return payload


def _clip(value: float, low: float, high: float) -> float:
    return float(min(max(value, low), high))


def portfolio_valuation(holdings: list, fallback_beta: float = 1.0) -> Dict[str, Any]:
    """Owner-earnings implied return versus a CAPM cost of equity."""
    erp = damodaran_erp()
    risk_free = float(getattr(config, "DEFAULT_RISK_FREE_RATE", 0.04) or 0.04)
    rows = []
    total_weight = 0.0
    covered_weight = 0.0
    weighted_beta = 0.0
    beta_weight = 0.0
    implied_sum = 0.0
    owner_yield_sum = 0.0

    for holding in holdings:
        ticker = (holding.get("ticker") or "").upper()
        weight = float(holding.get("weight") or 0)
        if not ticker or weight <= 0:
            continue
        total_weight += weight
        fundamentals = _fundamentals(ticker) or {}

        pe = fundamentals.get("pe")
        forward_pe = fundamentals.get("forward_pe")
        fcf = fundamentals.get("fcf")
        market_cap = fundamentals.get("market_cap")
        beta = fundamentals.get("beta")

        fcf_yield = (fcf / market_cap) if (fcf and market_cap and market_cap > 0) else None
        owner_yield = None
        if pe and pe > 0:
            owner_yield = 1.0 / pe
        elif fcf_yield:
            owner_yield = fcf_yield

        growth = 0.025
        if pe and forward_pe and forward_pe > 0 and pe > 0:
            growth = _clip(pe / forward_pe - 1.0, 0.0, 0.12)

        if owner_yield is None:
            rows.append({"ticker": ticker, "weight": weight, "covered": False})
            continue

        implied = _clip(owner_yield + growth, -0.10, 0.35)
        covered_weight += weight
        implied_sum += weight * implied
        owner_yield_sum += weight * owner_yield
        if beta:
            weighted_beta += weight * float(beta)
            beta_weight += weight
        elif fallback_beta:
            weighted_beta += weight * fallback_beta
            beta_weight += weight

        rows.append({
            "ticker": ticker,
            "weight": weight,
            "covered": True,
            "pe": pe,
            "forward_pe": forward_pe,
            "owner_yield": owner_yield,
            "fcf_yield": fcf_yield,
            "growth": growth,
            "implied_return": implied,
            "beta": beta,
        })

    coverage = covered_weight / total_weight if total_weight else 0.0
    implied_return = implied_sum / covered_weight if covered_weight else None
    portfolio_owner_yield = owner_yield_sum / covered_weight if covered_weight else None
    beta = weighted_beta / beta_weight if beta_weight else fallback_beta
    cost_of_equity = risk_free + beta * erp["value"]

    sensitivity = []
    if portfolio_owner_yield is not None:
        for growth in (0.00, 0.01, 0.02, 0.03, 0.04):
            row = {"growth": growth}
            for erp_shift in (-0.01, 0.0, 0.01):
                erp_value = max(0.01, erp["value"] + erp_shift)
                cost = risk_free + beta * erp_value
                row[f"erp_{int(round(erp_value * 100))}"] = (portfolio_owner_yield + growth) - cost
            sensitivity.append(row)

    return {
        "erp": erp,
        "risk_free": risk_free,
        "beta": beta,
        "cost_of_equity": cost_of_equity,
        "implied_return": implied_return,
        "owner_yield": portfolio_owner_yield,
        "spread": (implied_return - cost_of_equity) if implied_return is not None else None,
        "coverage": coverage,
        "positions": rows,
        "sensitivity": sensitivity,
        "method": (
            "Owner earnings (FCF/market cap, else 1/PE) plus expected growth, "
            "compared with CAPM using the Damodaran implied ERP."
        ),
    }
