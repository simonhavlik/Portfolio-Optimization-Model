"""Accuracy harness for portfolio extraction across document formats.

Builds the same known portfolio as DOCX, PDF, PNG and JPG, runs the real
extractor, and compares tickers, company names, sectors and weights.
Run: PYTHONPATH=. python scripts/test_extraction.py
"""

import os
import sys
import zipfile
from io import BytesIO

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from services.portfolio_extractor import extract

OUT = "/tmp/extraction-fixtures"
TRUTH = [
    ("AAPL", "Apple Inc", "Technology", 0.30),
    ("MSFT", "Microsoft", "Technology", 0.25),
    ("NVDA", "NVIDIA", "Technology", 0.25),
    ("JPM", "JPMorgan Chase", "Financials", 0.20),
]
VALUE = 100000
SECTOR_ALIASES = {
    "information technology": "technology",
    "financial services": "financials",
    "consumer cyclical": "consumer discretionary",
    "consumer defensive": "consumer staples",
    "health care": "healthcare",
    "communication services": "communication services",
}


def _rows():
    return [["Ticker", "Company", "Sector", "Weight", "Market Value"]] + [
        [t, n, s, f"{w * 100:.0f}%", f"${w * VALUE:,.0f}"] for t, n, s, w in TRUTH
    ]


def _table_figure(rows, size=(7.0, 2.6)):
    fig, ax = plt.subplots(figsize=size)
    ax.axis("off")
    table = ax.table(cellText=rows[1:], colLabels=rows[0], loc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.7)
    return fig


def build_png(path):
    fig = _table_figure(_rows())
    fig.savefig(path, dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)


def build_jpg(path):
    fig = _table_figure(_rows())
    fig.savefig(path, dpi=170, bbox_inches="tight", facecolor="white", format="jpg")
    plt.close(fig)


def build_pdf(path):
    from matplotlib.backends.backend_pdf import PdfPages
    with PdfPages(path) as pdf:
        fig = _table_figure(_rows(), size=(8.3, 3.2))
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)


def build_docx(path):
    def cell(text):
        return f'<w:tc><w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p></w:tc>'

    def row(values):
        return "<w:tr>" + "".join(cell(v) for v in values) + "</w:tr>"

    rows = "".join(row(r) for r in _rows())
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body><w:tbl>{rows}</w:tbl></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)


def normalise_sector(value):
    value = (value or "").strip().lower()
    return SECTOR_ALIASES.get(value, value)


def check(label, parsed):
    got = {h.ticker: h for h in parsed.holdings}
    expected = {t: (n, s, w) for t, n, s, w in TRUTH}
    missing = sorted(set(expected) - set(got))
    extra = sorted(set(got) - set(expected))
    problems = []
    if missing:
        problems.append(f"missing {missing}")
    if extra:
        problems.append(f"extra {extra}")

    total = sum((h.market_value or 0) for h in parsed.holdings) or 1
    for ticker, (name, sector, weight) in expected.items():
        holding = got.get(ticker)
        if not holding:
            continue
        if normalise_sector(holding.asset_class) != normalise_sector(sector):
            problems.append(f"{ticker} sector {holding.asset_class!r} != {sector!r}")
        implied = (holding.market_value or 0) / total
        if abs(implied - weight) > 0.03:
            problems.append(f"{ticker} weight {implied:.3f} != {weight:.3f}")
        company = (holding.name or "").lower()
        if name.split()[0].lower() not in company:
            problems.append(f"{ticker} name {holding.name!r} != {name!r}")

    status = "PASS" if not problems else "FAIL"
    print(f"{status:4} {label:5} holdings={len(got)} "
          f"tickers={sorted(got)}")
    for problem in problems:
        print("       -", problem)
    return status == "PASS"


def main():
    os.makedirs(OUT, exist_ok=True)
    builders = [
        ("docx", "portfolio.docx", build_docx),
        ("pdf", "portfolio.pdf", build_pdf),
        ("png", "portfolio.png", build_png),
        ("jpg", "portfolio.jpg", build_jpg),
    ]
    results = {}
    for label, filename, builder in builders:
        path = os.path.join(OUT, filename)
        builder(path)
        with open(path, "rb") as handle:
            parsed = extract(handle.read(), filename)
        results[label] = check(label, parsed)

    print("\nsummary:", ", ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
