"""Active API credential lookup, switchable from the database."""

import logging
import time
from typing import Optional

from config import config
from models.database import db, ApiCredential

logger = logging.getLogger(__name__)

_CACHE_TTL = 60
_cache: dict = {"provider": None, "value": None, "read_at": 0.0}


def get_credential(provider: str = "deepseek") -> dict:
    """Return the active credential for a provider, falling back to env config."""
    now = time.monotonic()
    if _cache["provider"] == provider and now - _cache["read_at"] < _CACHE_TTL:
        return _cache["value"]

    value = None
    try:
        row = ApiCredential.query.filter_by(provider=provider, is_active=True).first()
        if row:
            value = {
                "api_key": row.api_key,
                "base_url": row.base_url or config.LLM_BASE_URL,
                "model": row.model or config.LLM_MODEL,
            }
    except Exception as exc:
        logger.warning("Could not read API credential: %s", exc)

    if not value:
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
    if not config.LLM_API_KEY:
        return None
    existing = ApiCredential.query.filter_by(provider="deepseek").first()
    if existing:
        return existing
    return set_credential("deepseek", config.LLM_API_KEY,
                          config.LLM_BASE_URL, config.LLM_MODEL)
