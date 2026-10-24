"""Curated registry of the funds tracked by the leaderboard.

Two kinds of fund:

* ``listed``  – the manager's vehicle has its own share price (Berkshire,
  Markel, Fairfax, Pershing Square).  The series is the traded equity, which
  is what an investor could actually own.
* ``13f``     – no listed vehicle, so the fund is proxied by its latest
  SEC 13F long US equity book, weighted by reported market value.

Every row carries the text used on the leaderboard page and the LinkedIn
cards, so the copy and the numbers cannot drift apart.
"""

from __future__ import annotations

FUNDS = [
    {
        'slug': 'berkshire',
        'name': 'Berkshire Hathaway',
        'manager': 'Warren Buffett',
        'kind': 'listed',
        'ticker': 'BRK-B',
        'style': 'Quality compounder',
        'seat': 'Omaha, US',
        'since': 1965,
        'blurb': 'The reference point: insurance float, a private industrial book and a '
                 'public equity portfolio run with decades of patience.',
    },
    {
        'slug': 'markel',
        'name': 'Markel Group',
        'manager': 'Tom Gayner',
        'kind': 'listed',
        'ticker': 'MKL',
        'style': 'Quality compounder',
        'seat': 'Richmond, US',
        'since': 1986,
        'blurb': 'Insurance float reinvested in equities and whole businesses; '
                 'the closest listed analogue to Berkshire.',
    },
    {
        'slug': 'fairfax',
        'name': 'Fairfax Financial',
        'manager': 'Prem Watsa',
        'kind': 'listed',
        'ticker': 'FFH.TO',
        'fx': 'CADUSD=X',
        'style': 'Value + macro hedge',
        'seat': 'Toronto, Canada',
        'since': 1985,
        'blurb': 'Canadian insurance group that will hedge the book when the price of '
                 'risk looks wrong — the discipline shows up in drawdowns.',
    },
    {
        'slug': 'pershing',
        'name': 'Pershing Square',
        'manager': 'Bill Ackman',
        'kind': 'listed',
        'ticker': 'PSH.L',
        'fx': 'GBPUSD=X',
        'style': 'Concentrated activist',
        'seat': 'New York, US',
        'since': 2004,
        'blurb': 'A handful of large-cap activist positions in a permanent-capital '
                 'wrapper; high conviction, high tracking error.',
    },
    {
        'slug': 'valley-forge',
        'name': 'Valley Forge Capital',
        'manager': 'Dev Kantesaria',
        'kind': '13f',
        'cik': '0001697868',
        'style': 'Ultra-concentrated quality',
        'seat': 'Wayne, US',
        'since': 2014,
        'blurb': 'Five to eight wide-moat compounders held for years. Concentration is '
                 'the strategy, not a side effect.',
        'aliases': {'ASML HLDG NV': 'ASML'},
    },
    {
        'slug': 'scion',
        'name': 'Scion Asset Management',
        'manager': 'Michael Burry',
        'kind': '13f',
        'cik': '0001649339',
        'style': 'Contrarian deep value',
        'seat': 'Saratoga, US',
        'since': 2013,
        'blurb': 'Deep-value positions sized for asymmetric payoff, with public calls on '
                 'bubbles and a willingness to sit in cash.',
    },
    {
        'slug': 'duquesne',
        'name': 'Duquesne Family Office',
        'manager': 'Stanley Druckenmiller',
        'kind': '13f',
        'cik': '0001536411',
        'style': 'Macro growth',
        'seat': 'New York, US',
        'since': 2010,
        'blurb': 'Top-down macro married to concentrated growth equity; the record is '
                 'built on position sizing as much as stock picking.',
    },
    {
        'slug': 'baupost',
        'name': 'Baupost Group',
        'manager': 'Seth Klarman',
        'kind': '13f',
        'cik': '0001061768',
        'style': 'Margin-of-safety value',
        'seat': 'Boston, US',
        'since': 1982,
        'blurb': 'Absolute-return value investing with a permanent cash reserve — the '
                 '13F shows the long book, not the dry powder.',
    },
    {
        'slug': 'himalaya',
        'name': 'Himalaya Capital',
        'manager': 'Li Lu',
        'kind': '13f',
        'cik': '0001709323',
        'style': 'Concentrated Asia value',
        'seat': 'Seattle, US',
        'since': 1997,
        'blurb': 'Very few names, held for a decade or more, bridging US and Asian '
                 'franchises.',
    },
    {
        'slug': 'third-point',
        'name': 'Third Point',
        'manager': 'Dan Loeb',
        'kind': '13f',
        'cik': '0001040273',
        'style': 'Event-driven activist',
        'seat': 'New York, US',
        'since': 1995,
        'blurb': 'Event-driven equity with an activist overlay; the long book turns over '
                 'faster than the buy-and-hold names on this list.',
    },
]

BENCHMARK = 'SPY'

# CUSIPs for non-US issuers (Irish/Channel Islands/Dutch registrations) often
# have no US listing code at OpenFIGI even when the stock trades in New York.
# These are resolved by issuer name and reviewed by hand.
ISSUER_ALIASES = {
    'ASML HLDG': 'ASML',
    'BRUKER': 'BRKR',
    'WARNER BROS DISCOVERY': 'WBD',
    'CRH PLC': 'CRH',
    'AON PLC': 'AON',
    'NORWEGIAN CRUISE': 'NCLH',
    'HERBALIFE': 'HLF',
    'LIBERTY GLOBAL': 'LBTYA',
    'AXALTA': 'AXTA',
    'SEAGATE TECHNOLOGY': 'STX',
    'FLEX LTD': 'FLEX',
    'LINDE PLC': 'LIN',
    'TEVA PHARMACEUTICAL': 'TEVA',
    'NEWAMSTERDAM PHARMA': 'NAMS',
    'BITDEER TECHNOLOGIES': 'BTDR',
    'BBB FOODS': 'TBBB',
    'JBS N.V.': 'JBS',
    'RIOT PLATFORMS': 'RIOT',
    'LITHIUM ARGENTINA': 'LAR',
    'ALPHABET INC': 'GOOG',
}

# Windows that matter to an allocator, with a label and a short explanation.
CRISIS_WINDOWS = [
    ('covid', 'COVID crash', '2020-02-19', '2020-03-23'),
    ('y2022', '2022 bear market', '2022-01-03', '2022-10-12'),
]

# Metrics that get ranked, with direction (+1 = higher is better).
RANK_METRICS = [
    ('sharpe_ratio', 'Sharpe', 1, 'Annualised excess return per unit of volatility.'),
    ('sortino_ratio', 'Sortino', 1, 'Excess return per unit of downside deviation.'),
    ('alpha', 'Alpha', 1, 'Annualised return not explained by the benchmark.'),
    ('annualized_return', 'Annual return', 1, 'Geometric annual return over the window.'),
    ('max_drawdown', 'Max drawdown', 1, 'Worst peak-to-trough loss (less negative is better).'),
    ('calmar_ratio', 'Calmar', 1, 'Annual return divided by the maximum drawdown.'),
    ('cvar_95', 'CVaR 95', -1, 'Average loss on the worst 5% of days.'),
    ('beta', 'Beta', -1, 'Sensitivity to the benchmark; lower is steadier.'),
    ('volatility', 'Volatility', -1, 'Annualised standard deviation of returns.'),
    ('concentration', 'Concentration', -1, 'Largest position weight; lower is more diversified.'),
]


def by_slug(slug: str):
    for fund in FUNDS:
        if fund['slug'] == slug:
            return fund
    return None
