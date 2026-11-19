"""Accession-level Form 13F archive and point-in-time portfolio snapshots."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

from services import edgar_13f
from services.leaderboard_funds import ISSUER_ALIASES, FUNDS

ARCHIVE = edgar_13f.CACHE_DIR / 'filings'
XML_DOLLARS_FROM = '2023-01-03'

# These original reports continued using thousands after the SEC switched to
# dollars. Checked accession by accession against quantity × quarter-end price
# of liquid equity lines. Keep the original figures with their normalized values.
THOUSANDS_EXCEPTIONS = {
    '0001061768-24-000005',
    '0001061768-25-000002',
    '0001061768-25-000003',
    '0001061768-25-000005',
    '0001061768-25-000007',
    '0001061768-26-000005',
    '0001061768-26-000007',
    '0001061768-26-000010',
    '0001061768-26-000015',
    '0001567619-23-002543',
    '0001567619-23-006061',
    '0001567619-23-006910',
    '0001567619-23-007709',
    '0001567619-24-000192',
    '0001567619-24-000317',
    '0001567619-24-000363',
    '0001536411-23-000005',
    '0001536411-23-000006',
    '0001536411-23-000008',
    '0001536411-24-000002',
    '0001536411-24-000005',
    '0001536411-24-000007',
    '0001536411-24-000009',
    '0001536411-25-000006',
    '0001536411-25-000008',
    '0001536411-25-000011',
    '0001536411-25-000017',
    '0001536411-26-000002',
    '0001536411-26-000004',
    '0001536411-26-000006',
    '0001536411-26-000011',
    '0001709323-23-000001',
    '0001709323-23-000004',
    '0001709323-23-000005',
    '0001709323-23-000006',
    '0001709323-24-000001',
    '0001709323-24-000004',
    '0001037389-23-000119',
    '0001037389-23-000121',
    '0001037389-23-000122',
    '0001037389-23-000124',
    '0001037389-24-000071',
    '0001037389-24-000072',
}

def normalize_filing(doc: dict) -> dict:
    if doc['accession'] not in THOUSANDS_EXCEPTIONS or doc.get('value_scale') == 1000:
        return doc
    return {**doc, 'value_scale': 1000, 'positions': [
        {**p, 'reported_value': p['value'], 'value': p['value'] * 1000} for p in doc['positions']
    ]}

COMMON_LEGACY = re.compile(
    r'^(?P<name>.+?)(?P<cusip>[0-9A-Z*@#]{6}\s?[0-9A-Z*@#]{2}\s?[0-9])\s+'
    r'\$?\s*(?P<value>[\d,]+)\s+(?P<shares>[\d,]+)(?=\s|$)(?P<rest>.*)$', re.I
)
LEGACY_CLASS = re.compile(r'\s{2,}\S.*$|(?:COMMON(?: STOCK)?|REIT)$', re.I)


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def _legacy_rows(text: str) -> list[dict]:
    rows = []
    for table in re.findall(r'<TABLE[^>]*>(.*?)</TABLE>', text, flags=re.S | re.I):
        if 'CUSIP' not in table.upper():
            continue
        prior = None
        for line in table.splitlines():
            line = re.sub(r'</?[A-Z]+[^>]*>', '', line)
            match = COMMON_LEGACY.fullmatch(line.strip())
            if not match or not re.search(r'\d', match['cusip']):
                continuation = re.match(r'^ {20,}\$?([\d,]+)\s+([\d,]+)(?=\s|$)', line)
                if continuation and prior and len(re.sub(r'[^0-9]', '', continuation[1])) > 0:
                    rows.append({**prior, 'value': continuation[1].replace(',', ''),
                                 'shares': continuation[2].replace(',', '')})
                continue
            name, _, security_class = re.sub(r'\s{2,}', '|', match['name'].strip(), count=1).partition('|')
            if not security_class:
                name = LEGACY_CLASS.sub('', name).strip()
            tail = match['rest']
            option = re.search(r'\b(PUT|CALL)\b', tail, flags=re.I)
            quantity_type = 'PRN' if re.match(r'\s*PRN\b', tail, flags=re.I) else 'SH'
            prior = {
                'nameOfIssuer': name.rstrip('.'), 'titleOfClass': security_class,
                'cusip': ''.join(match['cusip'].split()),
                'value': match['value'].replace(',', ''), 'shares': match['shares'].replace(',', ''),
                'quantityType': quantity_type, 'putCall': option[1].upper() if option else '',
            }
            rows.append(prior)
    return rows


def parse_filing(raw: bytes, filing: dict) -> dict:
    text = raw.decode('utf-8', errors='replace')
    header = re.search(r'CONFORMED PERIOD OF REPORT:\s*(\d{8})', text)
    period = filing.get('period')
    if header:
        report_date = header[1]
        period = f'{report_date[:4]}-{report_date[4:6]}-{report_date[6:]}'
    if not period:
        raise ValueError('Missing report period')
    date.fromisoformat(period)

    xml = re.finditer(rb'<(?P<prefix>[\w.-]+:|)informationTable\b.*?</(?P=prefix)informationTable>', raw, flags=re.S)
    xml = [match[0] for match in xml]
    lines = [line for table in xml for line in edgar_13f._parse_information_table(table)]
    if not xml:
        lines = _legacy_rows(text)
    if not lines:
        raise ValueError('No decodable information table')

    mode = 'original'
    if filing['form'].endswith('/A'):
        if re.search(r'<amendmentType>\s*NEW HOLDINGS|\[\s*x\s*\]\s*(?:adds?|is adding) new holdings', text, re.I):
            mode = 'addition'
        elif re.search(r'<amendmentType>\s*RESTATEMENT|\[\s*x\s*\]\s*is a restatement', text, re.I):
            mode = 'restatement'
        else:
            mode = 'unclassified amendment'

    factor = 1000 if filing['filed'] < XML_DOLLARS_FROM else 1
    positions = [{
        'issuer': line.get('nameOfIssuer', ''), 'class': line.get('titleOfClass', ''),
        'cusip': line['cusip'].upper(), 'value': int(line.get('value') or 0) * factor,
        'shares': float(line.get('shares') or 0), 'quantity_type': line.get('quantityType') or 'SH',
        'option': (line.get('putCall') or '').upper(),
    } for line in lines]
    summary = re.search(r'<(?:[\w.-]+:)?tableEntryTotal>\s*(\d+)\s*</(?:[\w.-]+:)?tableEntryTotal>', text)
    if xml and summary and int(summary[1]) != len(positions):
        value_total = re.search(r'<(?:[\w.-]+:)?tableValueTotal>\s*(\d+)\s*</', text)
        raw_rows = sum(len(re.findall(rb'<(?:[\w.-]+:)?infoTable\b', table)) for table in xml)
        if (not value_total or not int(value_total[1]) or raw_rows != len(positions)
                or int(value_total[1]) * factor != sum(row['value'] for row in positions)):
            raise ValueError(f'Table entry count {len(positions)} differs from filing summary {summary[1]}')
    if not xml:
        total = re.search(r'Information Table Value (?:\(x \$1000\) )?Total\s*:\s*\$?\s*([\d,]+)', text, re.I)
        if total and int(total[1].replace(',', '')) * factor != sum(row['value'] for row in positions):
            raise ValueError('Legacy table does not reconcile to reported value')
    doc = {**filing, 'period': period, 'mode': mode, 'format': 'XML' if xml else 'legacy text',
           'value_scale': factor, 'positions': positions}
    return normalize_filing(doc) if doc.get('accession') else doc


def download_history(funds: list[dict], retry_failures: bool = False, since=None, limit=None) -> None:
    """Cache each SEC filing independently so interrupted imports can resume."""
    for fund in funds:
        cik = fund['cik']
        url = f'https://data.sec.gov/submissions/CIK{cik}.json'
        payload = json.loads(edgar_13f._sec_get(url))
        filings = edgar_13f._submission_arrays(payload, strict=True)
        counts = defaultdict(int)
        ordered = sorted((f for f in filings if not since or f['filed'] >= since),
                         key=lambda f: f['filed'], reverse=True)
        for filing in ordered[:limit]:
            accession = filing['accession']
            target = ARCHIVE / fund['slug'] / f'{accession}.json'
            failed = target.with_suffix('.error')
            if target.exists() or failed.exists() and not retry_failures:
                counts['cached'] += 1
                continue
            document_url = (f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/'
                            f"{accession.replace('-', '')}/{accession}.txt")
            try:
                book = parse_filing(edgar_13f._sec_get(document_url), {**filing, 'url': document_url})
            except (ValueError, TypeError, SyntaxError) as exc:
                atomic_write(failed, str(exc).encode())
                counts['unparsed'] += 1
                continue
            except OSError as exc:
                print(f'{fund["slug"]} {accession}: {exc}', flush=True)
                counts['request failures'] += 1
                continue
            atomic_write(target, json.dumps(book, separators=(',', ':')).encode())
            failed.unlink(missing_ok=True)
            counts['downloaded'] += 1
        manifest = {**counts, 'filings': len(filings)}
        atomic_write(ARCHIVE / fund['slug'] / 'manifest.json', json.dumps(manifest).encode())
        print(f'{fund["slug"]}: {manifest}', flush=True)


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='Download individual SEC 13F filings')
    parser.add_argument('--fund', action='append', choices=[fund['slug'] for fund in FUNDS])
    parser.add_argument('--retry-failures', action='store_true')
    parser.add_argument('--since', help='Earliest filing date, YYYY-MM-DD')
    parser.add_argument('--limit', type=int, help='Maximum recent filings per manager')
    args = parser.parse_args()
    download_history([f for f in FUNDS if not args.fund or f['slug'] in args.fund], args.retry_failures, args.since, args.limit)
