"""Refresh the official list of 13F accessions for the filing coverage audit."""
import json

from services.edgar_13f import _sec_get, _submission_arrays
from services.filing_history import atomic_write
from services.leaderboard import CACHE_DIR
from services.leaderboard_funds import FUNDS


def main():
    sources = {}
    for manager in FUNDS:
        cik = manager['cik']
        source = json.loads(_sec_get(f'https://data.sec.gov/submissions/CIK{cik}.json'))
        sources[manager['slug']] = _submission_arrays(source, strict=True)
    atomic_write(CACHE_DIR / 'edgar_coverage.json', json.dumps(sources, separators=(',', ':')).encode())
    print(f'Audited {len(sources)} EDGAR identifiers')


if __name__ == '__main__':
    main()
