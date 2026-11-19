"""Accession-level Form 13F archive and point-in-time portfolio snapshots."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from decimal import Decimal
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
    r'^(?P<name>.+?)'
    r'(?P<cusip>(?![A-Z*@#]{6}\s?[A-Z*@#]{2}\s?[A-Z])[0-9A-Z*@#]{6}\s?[0-9A-Z*@#]{2}\s?[0-9A-Z]'
    r'|(?<![0-9A-Z])[0-9B-Z]{6}[0-9]|(?<![0-9A-Z])[0-9]{5,8})\s+'
    r'\$?\s*(?P<value>-?[\d,]+(?:\.\d+)?)\s+(?P<shares>[\d,]+(?:\.\d+)?(?:E[+-]?\d+)?)(?=\s|SH\b|PRN\b|$)(?P<rest>.*)$', re.I
)
TYPED_LEGACY = re.compile(
    COMMON_LEGACY.pattern.replace(r'(?=\s|SH\b|PRN\b|$)', r'(?=\s*(?:SH|PRN)\b)'), re.I
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
    blank_value = re.compile(
        r'^(?P<name>.+?)\s+(?P<cusip>[0-9A-Z]{9}) {10,}(?P<shares>[0-9,]+)\s*(?P<rest>(?:SH|PRN)\b.*)$',
        re.I)
    started = False
    for table in re.findall(r'<TABLE[^>]*>(.*?)</TABLE>', text, flags=re.S | re.I):
        if 'CUSIP' in table.upper():
            started = True
        if not started:
            continue
        table_start = len(rows)
        prior = None
        for line in table.splitlines():
            line = re.sub(r'</?[A-Z]+[^>]*>', '', line)
            typed = TYPED_LEGACY.fullmatch(line.strip())
            blank = blank_value.fullmatch(line.strip())
            match = typed or blank or COMMON_LEGACY.fullmatch(line.strip())
            if not match or not re.search(r'\d', match['cusip']):
                continuation = re.match(r'^ {20,}\$?([\d,]+)\s+([\d,]+)(?=\s|$)', line)
                if continuation and prior and len(re.sub(r'[^0-9]', '', continuation[1])) > 0:
                    rows.append({**prior, 'value': continuation[1].replace(',', ''),
                                 'shares': continuation[2].replace(',', ''), '_continuation': True})
                continue
            name, _, security_class = re.sub(r'\s{2,}', '|', match['name'].strip(), count=1).partition('|')
            if not security_class:
                name = LEGACY_CLASS.sub('', name).strip()
            tail = match['rest']
            option = re.search(r'\b(PUT|CALL)\b', tail, flags=re.I)
            quantity_type = 'PRN' if re.match(r'\s*PRN\b', tail, flags=re.I) else 'SH'
            identifier = ''.join(match['cusip'].split())
            if identifier.isdigit() and not re.search(r'foreign', security_class, re.I):
                identifier = identifier.zfill(9)
            prior = {
                'nameOfIssuer': name.rstrip('.'), 'titleOfClass': security_class,
                'cusip': identifier,
                'value': '0' if match is blank else match['value'].replace(',', ''),
                'value_missing': match is blank, '_typed': match is typed, 'shares': match['shares'].replace(',', ''),
                'quantityType': quantity_type, 'putCall': option[1].upper() if option else '',
            }
            rows.append(prior)
        if len(rows) - table_start > 2 and rows[-1].get('_continuation'):
            reported_total = Decimal(rows[-1]['value'])
            if reported_total == sum(Decimal(pos['value']) for pos in rows[table_start:-1]):
                rows.pop()
    return rows


def parse_filing(raw: bytes, filing: dict, manager_cik: str | None = None) -> dict | None:
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
    empty_filing = not lines and not xml and bool(
        re.search(r'Information Table Entry Total\s*:\s*0\b', text, re.I) and
        re.search(r'Information Table Value Total\s*:\s*\$?0\b', text, re.I))
    if not lines and not empty_filing:
        raise ValueError('No decodable information table')

    if not xml:
        entry_count = re.search(r'Information Table Entry Total\s*:\s*([\d,]+)', text, re.I)
        if entry_count and len(lines) == int(entry_count[1].replace(',', '')) + 1:
            summed = sum(Decimal(row['value']) for row in lines)
            lines = [row for row in lines
                     if not (row.get('_continuation') and Decimal(row['value']) * 2 == summed)]

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
        'cusip': line['cusip'].upper(), 'value': int(Decimal(str(line.get('value') or 0)) * factor),
        'shares': float(line.get('shares') or 0), 'quantity_type': line.get('quantityType') or 'SH',
        'option': (line.get('putCall') or '').upper(),
        **({'value_missing': True} if line.get('value_missing') else {}),
    } for line in lines]
    reconciliation = None
    summary = re.search(r'<(?:[\w.-]+:)?tableEntryTotal>\s*(\d+)\s*</(?:[\w.-]+:)?tableEntryTotal>', text)
    if xml and summary and int(summary[1]) != len(positions):
        value_total = re.search(r'<(?:[\w.-]+:)?tableValueTotal>\s*(\d+)\s*</', text)
        raw_rows = sum(len(re.findall(rb'<(?:[\w.-]+:)?infoTable\b', table)) for table in xml)
        declared = int(value_total[1]) * factor if value_total else None
        actual = sum(row['value'] for row in positions)
        if raw_rows != len(positions) or (declared != actual and (len(xml) != 1 or not int(summary[1]))):
            raise ValueError(f'Table entry count {len(positions)} differs from filing summary {summary[1]}')
        reconciliation = {'declared_value': declared, 'position_sum': actual, 'status': 'summary differs',
                          'declared_count': int(summary[1]), 'decoded_count': len(positions)}
    if not xml:
        total = re.search(
            r'Information Table Value (?:\(x \$1000\) )?Total\s*:\s*\$?\s*([\d,]+(?:\.\d+)?)(?:[ \t]+(billion|million))?',
            text, re.I)
        if total:
            amount = Decimal(total[1].replace(',', ''))
            reported_unit = {'billion': 1_000_000_000, 'million': 1_000_000}.get((total[2] or '').lower(), factor)
            reported_value = amount * reported_unit
            sum_of_rows = sum(row['value'] for row in positions)
            summary_count = re.search(r'Information Table Entry Total\s*:\s*([\d,]+)', text, re.I)
            count_matches = summary_count and int(summary_count[1].replace(',', '')) == len(positions)
            if not total[2] and count_matches and amount == sum_of_rows:
                # Some legacy summary pages state full dollars while the table uses thousands.
                reported_value = amount
            difference = abs(reported_value - sum_of_rows)
            tolerance = (Decimal(10) ** amount.as_tuple().exponent * reported_unit / 2
                         if total[2] else factor * len(positions) / 2)
            if difference:
                if not count_matches:
                    raise ValueError('Legacy table does not reconcile to reported count and value')
                fully_typed = all(row.get('_typed') for row in lines)
                rounded_variance = total[2] and difference <= reported_value * Decimal('.01')
                if difference > tolerance and not (fully_typed or rounded_variance):
                    raise ValueError('Legacy table does not reconcile to reported value')
                reconciliation = {'declared_value': int(reported_value), 'position_sum': sum_of_rows,
                                  'status': 'rounded' if difference <= tolerance else 'summary differs'}
    doc = {**filing, 'period': period, 'mode': mode, 'format': 'XML' if xml else 'legacy text',
           'value_scale': factor, 'positions': positions}
    if reconciliation:
        doc['reconciliation'] = reconciliation
    if empty_filing:
        doc['no_disclosed_positions'] = True
    if manager_cik:
        # The SEC cover page maps the manager number in each information-table row to its CIK.
        sequence = None
        for manager in re.findall(r'<(?:[\w.-]+:)?otherManager2\b[^>]*>(.*?)</(?:[\w.-]+:)?otherManager2>', text, re.S):
            cik = re.search(r'<(?:[\w.-]+:)?cik>\s*(\d+)\s*</', manager)
            number = re.search(r'<(?:[\w.-]+:)?sequenceNumber>\s*(\d+)\s*</', manager)
            if cik and number and int(cik[1]) == int(manager_cik):
                sequence = number[1]
                break
        attributed = [position for line, position in zip(lines, positions)
                      if sequence in (line.get('otherManager') or '').replace(' ', '').split(',')]
        if not attributed:
            return None
        doc.update(positions=attributed, attributed_manager=manager_cik, attributed_sequence=sequence,
                   source_position_count=len(positions), source_value=sum(p['value'] for p in positions))
    return normalize_filing(doc) if doc.get('accession') else doc


def download_history(funds: list[dict], retry_failures: bool = False, since=None, limit=None) -> None:
    """Cache each SEC filing independently so interrupted imports can resume."""
    for fund in funds:
        parent = fund.get('reporting_parent')
        sources = [(fund['cik'], None)]
        if parent:
            sources.append((parent['cik'], parent['first_period']))
        filings = []
        for cik, first_period in sources:
            url = f'https://data.sec.gov/submissions/CIK{cik}.json'
            payload = json.loads(edgar_13f._sec_get(url))
            filings.extend((filing, cik, fund['cik'] if first_period else None)
                           for filing in edgar_13f._submission_arrays(payload, strict=True)
                           if not first_period or filing['period'] >= first_period)
        counts = defaultdict(int)
        ordered = sorted((entry for entry in filings if not since or entry[0]['filed'] >= since),
                         key=lambda entry: entry[0]['filed'], reverse=True)
        for filing, cik, attributed_manager in ordered[:limit]:
            accession = filing['accession']
            target = ARCHIVE / fund['slug'] / f'{accession}.json'
            failed = target.with_suffix('.error')
            if target.exists() or failed.exists() and not retry_failures:
                counts['cached'] += 1
                continue
            document_url = (f'https://www.sec.gov/Archives/edgar/data/{int(cik)}/'
                            f"{accession.replace('-', '')}/{accession}.txt")
            try:
                book = parse_filing(edgar_13f._sec_get(document_url), {**filing, 'url': document_url},
                                    manager_cik=attributed_manager)
            except (ValueError, TypeError, SyntaxError) as exc:
                atomic_write(failed, str(exc).encode())
                counts['unparsed'] += 1
                continue
            except OSError as exc:
                print(f'{fund["slug"]} {accession}: {exc}', flush=True)
                counts['request failures'] += 1
                continue
            if book is None:
                counts['not attributed'] += 1
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
