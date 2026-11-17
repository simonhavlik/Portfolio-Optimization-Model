"""Active API credential lookup, switchable from the database."""

import logging
import os
import time
from typing import Optional

from config import config
from models.database import db, ApiCredential

logger = logging.getLogger(__name__)

_CACHE_TTL = 60
_cache: dict = {"provider": None, "value": None, "read_at": 0.0}


def _requires_database() -> bool:
    default = "1" if config.DB_TYPE == "postgresql" else "0"
    return os.environ.get("LLM_REQUIRE_DATABASE", default) == "1"


def get_credential(provider: str = "deepseek") -> dict:
    """Return the active database credential, with opt-in development fallback."""
    require_database = _requires_database()
    try:
        from flask import has_app_context
        if not has_app_context():
            if require_database:
                return {"api_key": None, "base_url": None, "model": None}
            return {
                "api_key": config.LLM_API_KEY,
                "base_url": config.LLM_BASE_URL,
                "model": config.LLM_MODEL,
            }
    except Exception:
        pass

    now = time.monotonic()
    if _cache["provider"] == provider and now - _cache["read_at"] < _CACHE_TTL:
        return _cache["value"]

    value = None
    try:
        row = ApiCredential.query.filter_by(provider=provider, is_active=True).first()
        if row and (not require_database or (row.api_key and row.base_url and row.model)):
            value = {
                "api_key": row.api_key,
                "base_url": row.base_url or config.LLM_BASE_URL,
                "model": row.model or config.LLM_MODEL,
            }
    except Exception:
        logger.warning("Could not read API credential")

    if value is None and require_database:
        return {"api_key": None, "base_url": None, "model": None}

    if value is None:
        value = {
            "api_key": config.LLM_API_KEY,
            "base_url": config.LLM_BASE_URL,
            "model": config.LLM_MODEL,
        }

    _cache.update(provider=provider, value=value, read_at=now)
    return value


def set_credential(provider: str, api_key: str, base_url: str = None,
                   model: str = None) -> ApiCredential:
    """Create or replace a provider credential and make it active."""
    row = ApiCredential.query.filter_by(provider=provider).first()
    if row is None:
        row = ApiCredential(provider=provider)
        db.session.add(row)
    row.api_key = api_key
    row.base_url = base_url or config.LLM_BASE_URL
    row.model = model or config.LLM_MODEL
    row.is_active = True
    db.session.commit()
    _cache.update(provider=None, value=None, read_at=0.0)
    return row


def seed_from_env() -> Optional[ApiCredential]:
    """Store the environment key once, so it can be switched later from the UI."""
    if _requires_database() or not config.LLM_API_KEY:
        return None
    existing = ApiCredential.query.filter_by(provider="deepseek").first()
    if existing:
        return existing
    return set_credential("deepseek", config.LLM_API_KEY,
                          config.LLM_BASE_URL, config.LLM_MODEL)
