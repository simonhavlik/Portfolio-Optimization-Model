"""Public reads must never turn cache misses/expiry into market-data fetches.

Use lightweight dependency stand-ins so this regression suite needs only stdlib.
"""
import ast
import importlib.util
import logging
import pickle
from pathlib import Path
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]

class PublicCacheTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.session.get.return_value = None
        modules = {
            'numpy': types.SimpleNamespace(),
            'config': types.SimpleNamespace(config=types.SimpleNamespace()),
            'models.database': types.SimpleNamespace(db=types.SimpleNamespace(session=self.session), AppCache=Mock()),
            'yfinance': types.SimpleNamespace(Ticker=Mock(side_effect=AssertionError('network fetch'))),
            'requests': types.SimpleNamespace(get=Mock(side_effect=AssertionError('network fetch'))),
        }
        self.patches = patch.dict(sys.modules, modules)
        self.patches.start()
        self.addCleanup(self.patches.stop)
        spec = importlib.util.spec_from_file_location('cache_test_valuation', ROOT / 'services/valuation.py')
        self.valuation = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.valuation)

    def row(self, payload, days):
        self.session.get.return_value = types.SimpleNamespace(payload=payload, computed_at=datetime.utcnow()-timedelta(days=days))

    def test_expired_fundamentals_are_served_without_fetch(self):
        self.row({'pe': 20}, 90)
        self.assertEqual(self.valuation._fundamentals('AAPL'), {'pe': 20})

    def test_fresh_fundamentals_refresh_reuses_database(self):
        self.row({'pe': 20}, 1)
        self.assertEqual(self.valuation._fundamentals('AAPL', refresh=True), {'pe': 20})

    def test_missing_fundamentals_do_not_fetch_or_write(self):
        self.assertEqual(self.valuation._fundamentals('AAPL'), {'ticker': 'AAPL'})
        self.session.commit.assert_not_called()

    def test_expired_erp_is_served_without_fetch(self):
        self.row({'value': .0423}, 90)
        self.assertEqual(self.valuation.damodaran_erp(), {'value': .0423})

    def test_missing_erp_is_labelled_fallback_without_writes(self):
        result = self.valuation.damodaran_erp()
        self.assertIn('Fallback', result['source'])
        self.session.commit.assert_not_called()

    def test_scheduler_refresh_ignores_stale_memory(self):
        self.row({'pe': 25}, 1)
        self.valuation._remember('fundamentals:AAPL', {'pe': 10})
        self.assertEqual(self.valuation._fundamentals('AAPL', refresh=True), {'pe': 25})

    def example_class(self):
        tree = ast.parse((ROOT / 'services/example_portfolio.py').read_text())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'ExamplePortfolioService')
        ns = {'Dict': dict, 'Any': object, 'Optional': __import__('typing').Optional,
              'datetime': datetime, 'timedelta': timedelta, 'logger': logging.getLogger(__name__),
              'os': __import__('os'), 'pickle': pickle, 'serialize_for_template': lambda x: x,
              'EXAMPLE_PORTFOLIO': {'holdings': [], 'name': 'Example', 'description': 'Example'}}
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(ROOT/'services/example_portfolio.py'), 'exec'), ns)
        return ns['ExamplePortfolioService'], ns

    def test_get_analysis_never_starts_refresh_even_without_cache(self):
        cls, _ = self.example_class()
        service = cls()
        service._compute_analysis = Mock(side_effect=AssertionError('compute'))
        service.refresh_if_stale = Mock(side_effect=AssertionError('refresh'))
        self.assertIn('meta', service.get_analysis())
        service._cached_data = {'metrics': {'sharpe_ratio': 1}}
        self.assertEqual(service.get_analysis()['metrics']['sharpe_ratio'], 1)

    def test_initialize_does_not_refresh_on_restart(self):
        cls, ns = self.example_class()
        ns['current_app'] = types.SimpleNamespace(_get_current_object=lambda: object())
        with tempfile.TemporaryDirectory() as directory:
            ns['CACHE_DIR'] = directory
            for saved in (True, False):
                service = cls()
                service._load_from_db = Mock(return_value=saved)
                service._load_from_disk = Mock(return_value=False)
                service._compute_analysis = Mock(side_effect=AssertionError('compute'))
                service.refresh_if_stale = Mock(side_effect=AssertionError('refresh'))
                service.initialize()
                service._compute_analysis.assert_not_called()
                service.refresh_if_stale.assert_not_called()

    def test_expired_disk_snapshot_survives_restart(self):
        cls, ns = self.example_class()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'cache.pkl'
            at = datetime.utcnow()-timedelta(days=90)
            path.write_bytes(pickle.dumps({'data': {'metrics': {}}, 'computed_at': at}))
            ns['CACHE_FILE'] = str(path)
            service = cls()
            self.assertTrue(service._load_from_disk())
            self.assertEqual(service._last_computed, at)

if __name__ == '__main__':
    unittest.main()
