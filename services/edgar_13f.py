"""SEC 13F ingestion for the fund leaderboard.

Pulls the latest 13F-HR for a manager, parses the information table, maps
CUSIPs to tradable US tickers with OpenFIGI and returns the long equity book
as normalised weights.

Everything is cached on disk (``data/leaderboard_cache``) because EDGAR asks
for at most ten requests per second and the data only changes quarterly.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path

CACHE_DIR = Path('data/leaderboard_cache')
CACHE_TTL = timedelta(days=7)
UA = {'User-Agent': 'Portfolio Optimizer research me@simonhavlik.com'}
SEC_PAUSE = 0.14

_last_sec_call = 0.0


def _cache_path(name: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / name


def _read_cache(name: str, ttl: timedelta = CACHE_TTL):
    path = _cache_path(name)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    stamped = payload.get('_cached_at')
    if stamped:
        try:
            if datetime.utcnow() - datetime.fromisoformat(stamped) > ttl:
                return None
        except ValueError:
            return None
    return payload.get('data')


def _write_cache(name: str, data) -> None:
    _cache_path(name).write_text(json.dumps({'_cached_at': datetime.utcnow().isoformat(), 'data': data}))


def _sec_get(url: str) -> bytes:
    global _last_sec_call
    wait = SEC_PAUSE - (time.time() - _last_sec_call)
    if wait > 0:
        time.sleep(wait)
    _last_sec_call = time.time()
    request = urllib.request.Request(url, headers=UA)
    for attempt in range(3):
        try:
            return urllib.request.urlopen(request, timeout=40).read()
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt < 2:
                time.sleep(1.2 * (attempt + 1))
                continue
            raise
    raise RuntimeError('SEC request failed')


def latest_13f_index(cik: str) -> dict:
    """Return filing metadata for the most recent 13F-HR of ``cik``."""
    cik10 = cik.zfill(10)
    payload = json.loads(_sec_get(f'https://data.sec.gov/submissions/CIK{cik10}.json'))
    recent = payload['filings']['recent']
    for form, accession, filed in zip(recent['form'], recent['accessionNumber'], recent['filingDate']):
        if form.startswith('13F-HR'):
            return {'cik': cik10, 'form': form, 'accession': accession, 'filed': filed,
                    'company': payload.get('name', '')}
    raise LookupError(f'no 13F-HR found for CIK {cik}')


def _information_table_url(cik: str, accession: str) -> tuple[str, str]:
    """Locate the information-table XML inside a filing."""
    flat = accession.replace('-', '')
    index = json.loads(_sec_get(f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{flat}/index.json'))
    names = [item['name'] for item in index['directory']['item']]
    candidates = [n for n in names if n.lower().endswith('.xml') and 'infotable' in n.lower()]
    if not candidates:
        candidates = [n for n in names if n.lower().endswith('.xml') and 'primary' not in n.lower()]
    if not candidates:
        raise LookupError('no information table in filing')
    name = candidates[0]
    return f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/{flat}/{name}', name


def _local_name(tag: str) -> str:
    return tag.rsplit('}', 1)[-1] if '}' in tag else tag


def _parse_information_table(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    rows = []
    for node in root.iter():
        if _local_name(node.tag) != 'infoTable':
            continue
        record = {}
        for child in node:
            name = _local_name(child.tag)
            if name == 'shrsOrPrnAmt':
                for sub in child:
                    if _local_name(sub.tag) == 'sshPrnamt':
                        record['shares'] = float(sub.text or 0)
            elif name in ('putCall', 'investmentDiscretion'):
                record[name] = (child.text or '').strip()
            else:
                record[name] = (child.text or '').strip()
        if record.get('cusip'):
            rows.append(record)
    return rows


def fund_filings(cik: str, limit: int = 28) -> list[dict]:
    """Every 13F-HR on file, oldest first, de-duplicated by period."""
    cik10 = cik.zfill(10)
    payload = json.loads(_sec_get(f'https://data.sec.gov/submissions/CIK{cik10}.json'))
    recent = payload['filings']['recent']
    seen: dict[str, dict] = {}
    for form, accession, filed, period in zip(recent['form'], recent['accessionNumber'],
                                              recent['filingDate'], recent['reportDate']):
        if not form.startswith('13F-HR'):
            continue
        # newest first, so the first filing seen for a period wins (amendments supersede)
        if period not in seen:
            seen[period] = {'form': form, 'accession': accession, 'filed': filed, 'period': period}
    ordered = sorted(seen.values(), key=lambda f: f['period'])
    return ordered[-limit:]


def book_for_filing(cik: str, filing: dict) -> dict:
    """Parsed and mapped book for one filing, cached by period."""
    cache_name = f"13f_book_{cik}_{filing['period']}.json"
    cached = _read_cache(cache_name, ttl=timedelta(days=400))
    if cached:
        return cached

    cik10 = cik.zfill(10)
    url, _ = _information_table_url(cik10, filing['accession'])
    rows = _parse_information_table(_sec_get(url))
    rows = [r for r in rows if not r.get('putCall')]

    total = sum(float(r.get('value') or 0) for r in rows) or 1.0
    for row in rows:
        row['value'] = float(row.get('value') or 0)
        row['weight'] = row['value'] / total

    tickers = map_cusips([r['cusip'] for r in rows])
    positions = []
    covered = 0.0
    for row in rows:
        ticker = tickers.get(row['cusip'])
        if ticker:
            covered += row['value']
        positions.append({
            'issuer': row.get('nameOfIssuer', ''),
            'cusip': row['cusip'],
            'value': row['value'],
            'shares': row.get('shares', 0.0),
            'weight': row['weight'],
            'ticker': ticker,
        })

    book = {
        'cik': cik10,
        'company': filing.get('company', ''),
        'form': filing['form'],
        'filed': filing['filed'],
        'period': filing['period'],
        'url': url,
        'positions': positions,
        'coverage': covered / total if total else 0.0,
        'fetched_at': datetime.utcnow().isoformat(),
    }
    _write_cache(cache_name, book)
    return book


def fund_13f(cik: str, force: bool = False) -> dict:
    """Latest 13F long equity book for a manager, with weights resolved."""
    cache_name = f'13f_{cik}.json'
    if not force:
        cached = _read_cache(cache_name)
        if cached:
            return cached

    meta = latest_13f_index(cik)
    url, _ = _information_table_url(meta['cik'], meta['accession'])
    rows = _parse_information_table(_sec_get(url))

    # options are not the equity book
    rows = [r for r in rows if not r.get('putCall')]

    total = sum(float(r.get('value') or 0) for r in rows) or 1.0
    for row in rows:
        row['value'] = float(row.get('value') or 0)
        row['weight'] = row['value'] / total

    tickers = map_cusips([r['cusip'] for r in rows])
    positions = []
    covered = 0.0
    for row in rows:
        ticker = tickers.get(row['cusip'])
        if ticker:
            covered += row['value']
        positions.append({
            'issuer': row.get('nameOfIssuer', ''),
            'cusip': row['cusip'],
            'value': row['value'],
            'shares': row.get('shares', 0.0),
            'weight': row['weight'],
            'ticker': ticker,
        })

    result = {
        'cik': meta['cik'],
        'company': meta['company'],
        'form': meta['form'],
        'filed': meta['filed'],
        'url': url,
        'positions': positions,
        'coverage': covered / total if total else 0.0,
        'fetched_at': datetime.utcnow().isoformat(),
    }
    _write_cache(cache_name, result)
    return result


def map_cusips(cusips: list[str], aliases: dict | None = None) -> dict[str, str]:
    """CUSIP → US ticker, cached.  Unknown identifiers are simply absent."""
    aliases = aliases or {}
    cache = _read_cache('cusip_map.json', ttl=timedelta(days=60)) or {}
    todo = [c for c in dict.fromkeys(cusips) if c and c not in cache]
    for start in range(0, len(todo), 10):
        batch = todo[start:start + 10]
        body = json.dumps([{'idType': 'ID_CUSIP', 'idValue': c} for c in batch]).encode()
        request = urllib.request.Request('https://api.openfigi.com/v3/mapping', data=body,
                                         headers={'Content-Type': 'application/json'})
        try:
            response = json.loads(urllib.request.urlopen(request, timeout=45).read())
        except Exception:
            continue
        for cusip, item in zip(batch, response):
            listings = item.get('data') or []
            us = [d for d in listings if d.get('exchCode') == 'US' and d.get('ticker')]
            cache[cusip] = us[0]['ticker'].replace('/', '-') if us else None
        time.sleep(0.3)
    _write_cache('cusip_map.json', cache)
    return {c: cache.get(c) for c in cusips if cache.get(c)}


def apply_aliases(book: dict, aliases: dict, defaults: dict | None = None) -> dict:
    """Fill gaps where CUSIP mapping failed by matching issuer names."""
    merged = dict(defaults or {})
    merged.update(aliases or {})
    aliases = merged
    if not aliases:
        return book
    for position in book['positions']:
        if position['ticker']:
            continue
        issuer = (position['issuer'] or '').upper()
        for needle, ticker in aliases.items():
            if needle.upper() in issuer:
                position['ticker'] = ticker
                break
    covered = sum(p['value'] for p in book['positions'] if p['ticker'])
    total = sum(p['value'] for p in book['positions']) or 1
    book['coverage'] = covered / total
    return book


def weights_from_book(book: dict, min_weight: float = 0.002) -> dict[str, float]:
    """Normalise the mapped portion of a 13F book into portfolio weights."""
    mapped = [p for p in book['positions'] if p.get('ticker')]
    total = sum(p['value'] for p in mapped) or 1.0
    weights: dict[str, float] = {}
    for position in mapped:
        weight = position['value'] / total
        if weight < min_weight:
            continue
        weights[position['ticker']] = weights.get(position['ticker'], 0.0) + weight
    scale = sum(weights.values()) or 1.0
    return {ticker: weight / scale for ticker, weight in weights.items()}
