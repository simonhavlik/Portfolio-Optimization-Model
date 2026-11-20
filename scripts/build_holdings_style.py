"""Prepare the holdings research and versioned charts using saved filings and prices."""
import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

from services.filing_history import atomic_write
from services.holdings_style import build
from services.issuer_classification import FILE, SECTORS
from services.leaderboard import CACHE_DIR, _book_positions, _mapping
from services.leaderboard_funds import by_slug
from services.leaderboard_research import _source_books, rebuild

NAME_ABBREVIATIONS = {
    'HLDG': 'HOLDINGS', 'HLDGS': 'HOLDINGS', 'HLDNGS': 'HOLDINGS',
    'INTL': 'INTERNATIONAL', 'NATL': 'NATIONAL', 'INDS': 'INDUSTRIES',
    'SVCS': 'SERVICES', 'SYS': 'SYSTEMS', 'MFG': 'MANUFACTURING',
    'MGMT': 'MANAGEMENT', 'PPTY': 'PROPERTY',
}
SUCCESSORS = {
    # One-for-one conversion of Texas Pacific Land Trust units to TPL shares.
    # https://www.sec.gov/Archives/edgar/data/1811074/000181107422000027/R16.htm
    '882610108': 'TPL',
}
ARCHIVED_GROUPS = {
    # Historical operating businesses absent from current company profiles.
    'G81477104': ('Communication Services', 'Internet Content & Information',
                  'https://www.sec.gov/Archives/edgar/data/1094005/000110465920052758/sina-20191231x20f.htm'),
    'Y75638109': ('Industrials', 'Marine Shipping',
                  'https://www.sec.gov/Archives/edgar/data/1794846/000162828023008528/sc-6k2022x12x31.htm'),
    'Y0436Q109': ('Industrials', 'Marine Shipping',
                  'https://www.sec.gov/Archives/edgar/data/1794846/000156459021014405/atco-20f_20201231.htm'),
    '489398107': ('Real Estate', 'Real Estate Services',
                  'https://www.sec.gov/Archives/edgar/data/1408100/000140810025000084/kw-20241231.htm'),
    '76117W109': ('Basic Materials', 'Paper & Paper Products',
                  'https://www.resolutefp.com/uploadedFiles/Media/Publications/Resolute-at-a-Glance-2021.pdf'),
    '708160106': ('Consumer Cyclical', 'Department Stores',
                  'https://www.sec.gov/Archives/edgar/data/1166126/000116612619000012/jcp-0202201910k.htm'),
    '370023103': ('Real Estate', 'REIT - Retail',
                  'https://www.sec.gov/Archives/edgar/data/1496048/000104746913001941/a2213015z10-k.htm'),
    '349631101': ('Industrials', 'Conglomerates',
                  'https://www.sec.gov/Archives/edgar/data/789073/000119312511043658/d10k.htm'),
    '307000109': ('Consumer Defensive', 'Discount Stores',
                  'https://www.sec.gov/Archives/edgar/data/34408/000003440814000010/fdo-20140830x10k.htm'),
    '73755L107': ('Basic Materials', 'Agricultural Inputs',
                  'https://www.sec.gov/Archives/edgar/data/855931/000119312517055499/d280847d10k.htm'),
    '530307305': ('Communication Services', 'Telecom Services',
                  'https://www.sec.gov/Archives/edgar/data/1611983/000155837025001788/lbrda-20241231x10k.htm'),
    '524901105': ('Financial Services', 'Asset Management',
                  'https://www.sec.gov/Archives/edgar/data/704051/000070405120000060/lm10k3312020.htm'),
    '886547108': ('Consumer Cyclical', 'Luxury Goods',
                  'https://www.sec.gov/Archives/edgar/data/98246/000009824620000042/tif-2020131x10k.htm'),
    '950587105': ('Consumer Cyclical', 'Restaurants',
                  'https://www.sec.gov/Archives/edgar/data/30697/000003069711000019/form10-k_01022011.htm'),
}


def _name_tokens(name):
    words = re.findall(r'[A-Z0-9]+', name.upper())
    expanded = (NAME_ABBREVIATIONS.get(word, word) for word in words)
    return [word for word in expanded if word not in {
        'THE', 'INC', 'CORP', 'CORPORATION', 'COMPANY', 'CO', 'LIMITED', 'LTD', 'DEL',
        'PLC', 'LLC', 'LP', 'CL', 'ORD', 'COM', 'SHS', 'NEW', 'GROUP', 'HOLDINGS', 'AND',
    }]


def _matches(issuer, name):
    filed, listed = _name_tokens(issuer), _name_tokens(name)
    if not filed or not listed:
        return False
    if ''.join(filed).startswith(''.join(listed)) or ''.join(listed).startswith(''.join(filed)):
        return True
    return len(filed) >= 2 and filed[:2] == listed[:2]


def _profile_index(profiles, sec_listings):
    by_name = defaultdict(dict)
    by_cik = defaultdict(dict)
    for ticker, profile in profiles.items():
        for key in ('shortName', 'longName'):
            if profile.get(key):
                by_name[tuple(_name_tokens(profile[key]))][ticker] = profile
    for cik, name, symbol, _ in sec_listings:
        ticker = symbol.replace('.', '-')
        if profile := profiles.get(ticker):
            if any(_matches(name, profile.get(key, '')) for key in ('shortName', 'longName')):
                by_cik[cik][ticker] = profile
                by_name[tuple(_name_tokens(name))][ticker] = profile
    return by_name, by_cik


def _unique_classification(candidates):
    if not candidates:
        return None, None
    categories = {(p.get('sector'), p.get('industry'), p.get('quoteType')) for p in candidates.values()}
    if len(categories) == 1:
        return next(iter(candidates.items()))
    return None, None


def classify_archive(quarters, profiles, sec_listings=()):
    """Link verified current company profiles to historical security identifiers."""
    legacy = CACHE_DIR / 'issuer_industries.json'
    old = json.loads(legacy.read_bytes()) if legacy.is_file() else {}
    saved = json.loads(FILE.read_bytes()) if FILE.is_file() else {}
    profiles = {ticker: {**profile, 'industry': profile['industry'].replace('—', ' - ')}
                if profile.get('industry') else profile for ticker, profile in profiles.items()}
    identities = {cusip: {**{key: entry[key] for key in ('cik', 'fund') if key in entry}}
                  for cusip, entry in {**old, **saved}.items()}
    for cusip, entry in old.items():
        if entry.get('sic') in {'6722', '6726'}:
            identities[cusip]['fund'] = True
    mapping = _mapping()
    by_name, by_cik = _profile_index(profiles, sec_listings)
    issuers = defaultdict(set)
    for slug, filings in quarters.items():
        fund = by_slug(slug)
        for book in filings.values():
            for row in _book_positions(fund, book, mapping):
                issuers[row['cusip']].add((row['issuer'], row['ticker']))

    for cusip, names in issuers.items():
        linked = {
            ticker: profiles[ticker] for issuer, ticker in names if ticker in profiles
            and any(_matches(issuer, profiles[ticker].get(key, '')) for key in ('shortName', 'longName'))
        }
        ticker, profile = _unique_classification(linked)
        if not profile:
            ticker, profile = _unique_classification(by_cik.get(identities.get(cusip, {}).get('cik')))
        if not profile:
            exact = {}
            for issuer, _ in names:
                exact.update(by_name.get(tuple(_name_tokens(issuer)), {}))
            ticker, profile = _unique_classification(exact)
        if not profile and cusip in SUCCESSORS:
            ticker = SUCCESSORS[cusip]
            profile = profiles.get(ticker)
        if not profile:
            continue
        if profile.get('quoteType') in {'ETF', 'MUTUALFUND'}:
            identities.setdefault(cusip, {})['fund'] = True
        elif profile.get('sector') in SECTORS:
            identities[cusip] = {**identities.get(cusip, {}), 'ticker': ticker,
                                 'sector': profile['sector'], 'industry': profile.get('industry'),
                                 'as_of': profile.get('as_of', '')}
    for cusip, (sector, industry, source) in ARCHIVED_GROUPS.items():
        if cusip in issuers and not identities.get(cusip, {}).get('sector'):
            identities[cusip] = {**identities.get(cusip, {}), 'sector': sector,
                                 'industry': industry, 'source': source}

    for cusip, classification in saved.items():
        if classification.get('sector') and not identities[cusip].get('sector'):
            identities[cusip].update(classification)
            if industry := classification.get('industry'):
                identities[cusip]['industry'] = industry.replace('—', ' - ')

    companies = defaultdict(list)
    for entry in identities.values():
        if entry.get('cik') and entry.get('sector') and not entry.get('fund'):
            companies[entry['cik']].append(entry)
    for entry in identities.values():
        matches = companies.get(entry.get('cik'), [])
        labels = {(match['sector'], match.get('industry')) for match in matches}
        if not entry.get('sector') and not entry.get('fund') and len(labels) == 1:
            source = max(matches, key=lambda match: match.get('as_of', ''))
            entry.update((key, source[key]) for key in ('ticker', 'sector', 'industry', 'as_of', 'source')
                         if key in source)
    atomic_write(FILE, json.dumps(identities, separators=(',', ':')).encode())


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--yahoo-profiles', type=Path, help='Saved yfinance company profiles keyed by ticker')
    parser.add_argument('--sec-listings', type=Path, help='SEC company_tickers_exchange.json for issuer matching')
    parser.add_argument('--study', action='append', help='Rebuild only the named study, repeatable')
    args = parser.parse_args()
    books, quarters = _source_books(include_quarters=True)
    if args.yahoo_profiles:
        listings = json.loads(args.sec_listings.read_bytes())['data'] if args.sec_listings else ()
        classify_archive(quarters, json.loads(args.yahoo_profiles.read_bytes()), listings)
    data = build(books, quarters)
    print(data['start'], data['as_of'], len(data['universe']), len(data['periods']), data['classified'], flush=True)
    rebuild(only=set(args.study) if args.study else None)
