import pandas as pd
import pytest

from services.filing_history import parse_filing
from services.fund_archive import aggregate, changes, quarter_book, timeline
from services.leaderboard import _series, _simulation


def filing(filed, period, mode, cusip, value=100):
    return {'filed': filed, 'period': period, 'mode': mode, 'accession': filed,
            'positions': [{'cusip': cusip, 'issuer': cusip, 'class': 'COM',
                           'quantity_type': 'SH', 'option': '', 'value': value, 'shares': 1}]}


def test_legacy_grouped_cusips_and_manager_rows_are_kept():
    raw = b"""CONFORMED PERIOD OF REPORT: 20080630
<TABLE>
  CUSIP     Value
American Express    Com    025816 10 9     648,880 17,225,400
                                           73,196  1,943,100
</TABLE>
"""
    doc = parse_filing(raw, {'filed': '2008-08-15', 'form': '13F-HR'})
    rows = aggregate(doc)
    assert doc['period'] == '2008-06-30'
    assert rows[0]['cusip'] == '025816109'
    assert rows[0]['value'] == 722_076_000


def test_xml_units_follow_filing_date_and_the_quantity_type_is_kept():
    raw = b"""<ns:informationTable xmlns:ns="test"><ns:infoTable>
      <ns:nameOfIssuer>Test</ns:nameOfIssuer><ns:cusip>025816109</ns:cusip>
      <ns:value>620</ns:value><ns:shrsOrPrnAmt><ns:sshPrnamt>5</ns:sshPrnamt>
      <ns:sshPrnamtType>PRN</ns:sshPrnamtType></ns:shrsOrPrnAmt>
    </ns:infoTable></ns:informationTable>"""
    doc = parse_filing(raw, {'filed': '2023-02-14', 'period': '2022-12-31', 'form': '13F-HR'})
    assert doc['positions'][0]['value'] == 620
    assert aggregate(doc, equity_only=True) == []


def test_amendments_are_point_in_time_and_old_quarters_do_not_replace_new_books():
    q1, q2 = '2024-03-31', '2024-06-30'
    first = filing('2024-05-15', q1, 'original', 'AAA')
    add = filing('2024-05-17', q1, 'addition', 'BBB')
    next_quarter = filing('2024-08-14', q2, 'original', 'CCC')
    late = filing('2024-08-20', q1, 'restatement', 'BBB')
    snapshots = timeline([late, first, next_quarter, add])
    assert [book['filed'] for book in snapshots] == [first['filed'], add['filed'], next_quarter['filed']]
    assert {row['cusip'] for row in snapshots[1]['positions']} == {'AAA', 'BBB'}
    assert quarter_book([late, first, add], q1)['positions'][0]['cusip'] == 'BBB'
    changes_by_cusip = {row['cusip']: row for row in changes(aggregate(snapshots[-1]), aggregate(snapshots[1]))}
    assert changes_by_cusip['AAA']['action'] == 'Exited'
    assert changes_by_cusip['AAA']['value'] == 0


def test_return_begins_after_the_rebalance_close():
    days = pd.bdate_range('2024-05-15', periods=6)
    book = filing('2024-05-15', '2024-03-31', 'original', 'AAA')
    logs = pd.Series(.05, index=days)
    returns, coverage = _series({'slug': 'test'}, [book], {'A': logs}, days, {'AAA': 'A'})
    assert returns.iloc[:2].isna().all()
    assert returns.iloc[2] == pytest.approx(.0512710964)
    assert coverage.iloc[2] == 1


def test_simulation_is_repeatable_and_has_a_deterministic_flat_case():
    returns = pd.Series([0.0] * 252)
    result = _simulation(returns)
    assert result == {'low': 100.0, 'median': 100.0, 'high': 100.0, 'loss': 0.0}
    mixed = pd.Series([.01, -.012, .002] * 100)
    assert _simulation(mixed) == _simulation(mixed)


def test_postgres_price_payload_preserves_the_trading_calendar():
    from services.leaderboard_storage import _pack, _unpack

    source = pd.Series([.005, -.003, .02], index=pd.bdate_range('2024-05-24', periods=3))
    result = _unpack(_pack(source))
    pd.testing.assert_series_equal(source, result, check_freq=False, check_index_type=False)


def test_unit_transition_keeps_the_as_reported_number_for_audited_exceptions():
    from services.filing_history import normalize_filing

    source = {'accession': '0001037389-23-000119', 'positions': [{'value': 922454}]}
    normalized = normalize_filing(source)
    assert normalized['positions'][0] == {'value': 922454000, 'reported_value': 922454}
    assert normalize_filing(normalized) == normalized
    assert source['positions'][0]['value'] == 922454


def test_material_disclosures_and_largest_addition_and_reduction_are_annotated():
    from services.leaderboard_research import STUDIES, _disclosed_moves, _select_notes

    study = STUDIES[0]
    notes = []
    for day in pd.date_range('2020-01-01', periods=12, freq='QS'):
        stamp = day.date().isoformat()
        book = {'filed': stamp, 'period': stamp, 'accession': stamp, 'url': 'https://www.sec.gov/'}
        changes = [
            {'issuer': cusip, 'cusip': cusip, 'action': action, 'shares_change': shares,
             'weight_change': delta, 'weight': .1}
            for cusip, action, shares, delta in [
                ('A', 'Reduced', -25, -.11), ('B', 'New', 10, .07),
                ('C', 'Increased', 4, .05), ('D', 'Increased', 2, .01)]
        ]
        notes.extend(_disclosed_moves(study, 'berkshire', book, changes, {}))
    result = _select_notes(study, notes)
    assert len(result) == 36
    assert {row['cusip'] for row in result} == {'A', 'B', 'C'}


def test_incorrect_xml_row_count_requires_a_reconciled_value_and_all_source_rows():
    raw = b"""<tableEntryTotal>0</tableEntryTotal><tableValueTotal>620</tableValueTotal>
      <informationTable><infoTable><nameOfIssuer>Example</nameOfIssuer>
      <cusip>025816109</cusip><value>620</value><shrsOrPrnAmt><sshPrnamt>5</sshPrnamt>
      </shrsOrPrnAmt></infoTable></informationTable>"""
    meta = {'filed': '2024-02-14', 'period': '2023-12-31', 'form': '13F-HR'}
    assert len(parse_filing(raw, meta)['positions']) == 1
    with pytest.raises(ValueError, match='entry count'):
        parse_filing(raw.replace(b'>620</tableValueTotal>', b'>621</tableValueTotal>'), meta)


def test_late_amendment_belongs_to_the_quarter_without_replacing_the_current_book():
    from services.fund_archive import quarter_books

    earlier = filing('2024-05-15', '2024-03-31', 'original', 'AAA')
    later = filing('2024-08-15', '2024-06-30', 'original', 'BBB')
    amended = filing('2024-08-16', '2024-03-31', 'restatement', 'CCC')
    assert quarter_books([earlier, later, amended])['2024-03-31']['positions'][0]['cusip'] == 'CCC'
    assert timeline([earlier, later, amended])[-1]['positions'][0]['cusip'] == 'BBB'


def test_legacy_cusip_padding_principal_type_and_signed_values():
    document = b"""<TABLE>
    Issuer                   Title   CUSIP        Value      Qty
    ABB LTD                  ADR    375204          343    15295 SH      SOLE
    US ISSUER                NOTE   012345678       174    12000PRN      SOLE
    PROSHARES TRUST          ETF    74347R297      -3842    38420 SH      SOLE
    </TABLE>"""
    record = parse_filing(document, {'period': '2010-09-30', 'filed': '2010-11-15', 'form': '13F-HR'})
    positions = record['positions']
    assert positions[0]['cusip'] == '000375204'
    assert positions[1]['quantity_type'] == 'PRN'
    assert positions[2]['value'] == -3_842_000
    assert len(aggregate(record, equity_only=True)) == 1


def test_composite_keeps_exact_ranks_in_the_correct_direction():
    from services.leaderboard import _score

    rows = [
        {'slug': 'a', 'name': 'A', 'metrics': {'volatility': .12, 'sharpe_ratio': .4}},
        {'slug': 'b', 'name': 'B', 'metrics': {'volatility': .30, 'sharpe_ratio': .7}},
        {'slug': 'c', 'name': 'C', 'metrics': {}},
    ]
    _score(rows)
    saved = {row['slug']: row for row in rows}
    assert saved['a']['metric_ranks']['volatility'] == {'rank': 1, 'sample': 2}
    assert saved['a']['metric_ranks']['sharpe_ratio'] == {'rank': 2, 'sample': 2}
    assert 'metric_ranks' not in saved['c']


def test_dated_allocations_apply_only_after_each_effective_close():
    from math import log1p

    from services.leaderboard import scheduled_returns

    days = pd.bdate_range('2024-05-13', periods=6)
    prices = {'A': pd.Series(log1p(.01), index=days), 'B': pd.Series(log1p(.03), index=days)}
    schedule = [(days[0], {'A': 1.0}), (days[2], {'B': 1.0})]
    returns, _ = scheduled_returns(schedule, prices, days)
    assert pd.isna(returns.iloc[0])
    assert returns.iloc[1:].tolist() == pytest.approx([.01, .01, .03, .03, .03])


def test_dated_partial_updates_preserve_explicit_target_weights_and_exits():
    from services.portfolio_history_import import allocation_csv, validate_snapshots

    raw = """date,ticker,amount,basis,mode
2020-06-30,AAPL,55,weight,change
2020-06-30,GOOG,0,weight,change
2020-03-31,AAPL,40,weight,snapshot
2020-03-31,GOOG,30,weight,snapshot
2020-03-31,MSFT,30,weight,snapshot
"""
    schedule = validate_snapshots(allocation_csv(raw))
    assert schedule[0] == ('2020-03-31', {'AAPL': .4, 'GOOG': .3, 'MSFT': .3})
    assert schedule[1] == ('2020-06-30', {'AAPL': .55, 'MSFT': .45})


def test_parent_filing_includes_only_the_sec_attributed_manager_rows():
    cover = b"""<otherManager2>
      <sequenceNumber>1</sequenceNumber><otherManager><cik>0001336528</cik></otherManager>
    </otherManager2>"""
    table = b"""<informationTable>
      <infoTable><nameOfIssuer>Test</nameOfIssuer><cusip>025816109</cusip>
      <value>200</value><otherManager>1, 2</otherManager></infoTable>
      <infoTable><nameOfIssuer>Different manager</nameOfIssuer><cusip>594918104</cusip>
      <value>125</value><otherManager>11</otherManager></infoTable></informationTable>"""
    meta = {'filed': '2026-08-14', 'period': '2026-06-30', 'form': '13F-HR'}
    doc = parse_filing(cover + table, meta, manager_cik='0001336528')
    assert doc['source_value'] == 325
    assert doc['attributed_sequence'] == '1'
    assert [p['cusip'] for p in doc['positions']] == ['025816109']
    assert parse_filing(cover + table, meta, manager_cik='0002026053') is None
