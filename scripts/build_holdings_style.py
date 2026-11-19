"""Prepare the holdings research and versioned charts using saved filings and prices."""
from services.holdings_style import build
from services.leaderboard_research import _source_books, rebuild

if __name__ == '__main__':
    data = build(*_source_books(include_quarters=True))
    print(data['start'], data['as_of'], len(data['universe']), len(data['periods']), data['classified'], flush=True)
    rebuild()
