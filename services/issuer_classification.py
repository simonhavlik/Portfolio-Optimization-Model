"""Saved Yahoo Finance classifications for the 13F archive."""

import json

from services.leaderboard import CACHE_DIR

KEY = 'leaderboard:issuer_classifications'
FILE = CACHE_DIR / 'issuer_classifications.json'
SECTORS = ('Basic Materials', 'Communication Services', 'Consumer Cyclical',
           'Consumer Defensive', 'Energy', 'Financial Services', 'Healthcare', 'Industrials',
           'Real Estate', 'Technology', 'Utilities', 'Funds and trusts', 'Unclassified')
FUND_SECTOR = 'Funds and trusts'
UNKNOWN = 'Unclassified'
TECHNOLOGY = 'Technology'


def load():
    from services.leaderboard_storage import hydrate_snapshot, published_snapshot

    data = published_snapshot(KEY)
    if data is not None:
        return data
    try:
        return json.loads(FILE.read_bytes())
    except (OSError, ValueError):
        return hydrate_snapshot(FILE, KEY) or {}


def classify(entry, classifications):
    classification = classifications.get(entry['cusip'], {})
    issuer = entry['issuer'].upper()
    if classification.get('fund') or any(name in issuer for name in (
        ' ETF', 'SPDR', 'ISHARES', 'VANGUARD INDEX', 'PROSHARES', 'ETFS'
    )):
        return FUND_SECTOR, FUND_SECTOR
    return classification.get('sector') or UNKNOWN, classification.get('industry') or UNKNOWN
