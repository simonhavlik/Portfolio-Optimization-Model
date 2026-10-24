"""
Portfolio Optimizer - Example Portfolio Service
================================================

Pre-computed example portfolio for visitors.
Runs advanced analysis ONCE at startup and caches results.
Admin can manually refresh via admin panel.
"""

import json
import pickle
import os
import threading
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import logging

from flask import current_app
from dataclasses import asdict, is_dataclass

import pandas as pd
import numpy as np

from services.market_data import market_data_service
from services.risk_analytics import risk_analytics
from services.advanced_statistics import advanced_statistics
from services.advanced_risk import AdvancedRiskCalculator
from services.factor_models import FamaFrenchModel
from services.dashboard_charts import dashboard_charts
from config import config

logger = logging.getLogger(__name__)


def _finite_json(obj: Any) -> Any:
    """Convert non-finite floats to None so PostgreSQL JSONB accepts the payload."""
    if isinstance(obj, float):
        return obj if np.isfinite(obj) else None
    if isinstance(obj, dict):
        return {key: _finite_json(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_finite_json(item) for item in obj]
    return obj


def serialize_for_template(obj: Any) -> Any:
    """
    Recursively convert dataclasses, numpy types, and pandas objects 
    to JSON-serializable Python types for templates.
    """
    if is_dataclass(obj):
        return {k: serialize_for_template(v) for k, v in asdict(obj).items()}
    elif isinstance(obj, dict):
        return {k: serialize_for_template(v) for k, v in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [serialize_for_template(item) for item in obj]
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, pd.Series):
        return obj.to_dict()
    elif isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient='records')
    elif isinstance(obj, datetime):
        return obj.isoformat()
    else:
        return obj

# Example portfolio - Professional diversified portfolio
EXAMPLE_PORTFOLIO = {
    'name': 'Example Diversified Portfolio',
    'description': 'A balanced portfolio inspired by value investing principles with tech exposure',
    'holdings': [
        {'ticker': 'AAPL', 'weight': 0.12, 'sector': 'Technology'},
        {'ticker': 'MSFT', 'weight': 0.12, 'sector': 'Technology'},
        {'ticker': 'GOOGL', 'weight': 0.10, 'sector': 'Technology'},
        {'ticker': 'AMZN', 'weight': 0.08, 'sector': 'Consumer Discretionary'},
        {'ticker': 'NVDA', 'weight': 0.07, 'sector': 'Technology'},
        {'ticker': 'JPM', 'weight': 0.07, 'sector': 'Financials'},
        {'ticker': 'JNJ', 'weight': 0.06, 'sector': 'Healthcare'},
        {'ticker': 'V', 'weight': 0.06, 'sector': 'Financials'},
        {'ticker': 'PG', 'weight': 0.05, 'sector': 'Consumer Staples'},
        {'ticker': 'UNH', 'weight': 0.05, 'sector': 'Healthcare'},
        {'ticker': 'HD', 'weight': 0.05, 'sector': 'Consumer Discretionary'},
        {'ticker': 'MA', 'weight': 0.05, 'sector': 'Financials'},
        {'ticker': 'LLY', 'weight': 0.05, 'sector': 'Healthcare'},
        {'ticker': 'KO', 'weight': 0.05, 'sector': 'Consumer Staples'},
        {'ticker': 'PEP', 'weight': 0.02, 'sector': 'Consumer Staples'}
    ],
    'period': '2y'
}

# Cache file path
CACHE_DIR = 'data'
CACHE_FILE = os.path.join(CACHE_DIR, 'example_portfolio_cache.pkl')
CACHE_KEY = 'example_portfolio_analysis'
CACHE_TTL = timedelta(days=7)


def generate_ai_insights(metrics, distributions, stat_tests, pca_result, vol_analysis):
    """
    Generate AI-style quantitative research insights based on portfolio analysis.
    """
    insights = {
        'risk_assessment': '',
        'factor_analysis': '',
        'recommendation': ''
    }
    
    sharpe = getattr(metrics, 'sharpe_ratio', 0)
    sortino = getattr(metrics, 'sortino_ratio', 0)
    max_dd = getattr(metrics, 'max_drawdown', 0)
    volatility = getattr(metrics, 'volatility', 0)
    
    if sharpe > 1.2:
        risk_level = "excellent"
        risk_desc = "superior risk-adjusted returns with exceptional compensation for volatility"
    elif sharpe > 0.8:
        risk_level = "strong"
        risk_desc = "solid risk-adjusted performance above market norms"
    elif sharpe > 0.5:
        risk_level = "moderate"
        risk_desc = "adequate risk-adjusted returns"
    else:
        risk_level = "concerning"
        risk_desc = "below-average risk-adjusted performance suggesting need for optimization"
    
    insights['risk_assessment'] = (
        f"This portfolio demonstrates {risk_level} risk-adjusted returns with a Sharpe ratio of {sharpe:.2f}, "
        f"indicating {risk_desc}. The Sortino ratio of {sortino:.2f} suggests "
        f"{'effective' if sortino > sharpe * 1.1 else 'adequate'} downside risk management. "
        f"Maximum drawdown of {max_dd*100:.1f}% occurred over {getattr(metrics, 'max_drawdown_duration', 'N/A')} days, "
        f"testing investor conviction during stressed periods."
    )
    
    beta = getattr(metrics, 'beta', 1.0)
    alpha = getattr(metrics, 'alpha', 0)
    r_squared = getattr(metrics, 'r_squared', 0)
    
    if beta > 1.1:
        beta_desc = "aggressive market exposure amplifying systematic movements"
    elif beta < 0.9:
        beta_desc = "defensive positioning reducing market sensitivity"
    else:
        beta_desc = "neutral market sensitivity aligned with benchmark"
    
    best_dist = 'Normal'
    if distributions:
        try:
            best_dist = min(distributions.items(), key=lambda x: x[1].aic)[0]
        except:
            pass
    
    insights['factor_analysis'] = (
        f"Portfolio exhibits a market beta of {beta:.2f}, indicating {beta_desc}. "
        f"Alpha generation of {alpha*100:.1f}% suggests {'positive' if alpha > 0 else 'negative'} "
        f"security selection skill (R² = {r_squared:.2f}). "
        f"Return distribution is best modeled by {best_dist} distribution. "
    )
    
    if stat_tests:
        insights['factor_analysis'] += (
            f"Jarque-Bera test indicates returns are {'normally distributed' if stat_tests.is_normal else 'non-normal with fat tails'}. "
        )
    
    if pca_result and hasattr(pca_result, 'explained_variance_ratio'):
        pc1_var = pca_result.explained_variance_ratio[0] * 100
        insights['factor_analysis'] += (
            f"PCA reveals first principal component explains {pc1_var:.1f}% of variance."
        )
    
    recommendations = []
    
    if volatility > 0.25:
        recommendations.append("High volatility suggests consideration of hedging strategies or defensive allocations")
    
    if stat_tests and not stat_tests.is_normal:
        recommendations.append("Non-normal distribution detected - tail risk hedging recommended")
    
    if vol_analysis and getattr(vol_analysis, 'has_arch_effects', False):
        recommendations.append("Volatility clustering detected - GARCH models may improve risk forecasting")
    
    if sharpe < 0.5:
        recommendations.append("Consider rebalancing toward higher Sharpe ratio assets")
    
    if max_dd < -0.30:
        recommendations.append("Severe drawdown potential - implement stop-loss or protective put strategies")
    
    if not recommendations:
        recommendations.append("Portfolio structure appears sound - maintain current allocation with regular rebalancing")
    
    insights['recommendation'] = " ".join(recommendations)
    
    return insights


class ExamplePortfolioService:
    """
    Service for managing and serving the example portfolio.
    
    CRITICAL: Computes analysis ONCE at startup and caches to disk.
    Subsequent page loads serve from memory - NO recomputation.
    Admin can force refresh via admin panel.
    """
    
    def __init__(self):
        self._cached_data: Optional[Dict[str, Any]] = None
        self._cache_loaded = False
        self._last_computed: Optional[datetime] = None
        self._app = None
        self._refreshing = False

    def initialize(self):
        """
        Initialize the service - call this once at app startup.
        Loads the persisted cache first so restarts are instant, then
        refreshes in the background when it is older than a week.
        """
        logger.info("Initializing example portfolio service...")
        self._app = current_app._get_current_object()
        os.makedirs(CACHE_DIR, exist_ok=True)

        if self._load_from_db():
            logger.info("Example portfolio loaded from database cache")
        elif self._load_from_disk():
            logger.info("Example portfolio loaded from disk cache")
            self._save_to_db()
        else:
            logger.info("No cache found, computing fresh analysis...")
            self._cached_data = self._compute_analysis()
            self._last_computed = datetime.utcnow()
            self._save_to_disk()
            self._save_to_db()
            logger.info("Example portfolio analysis complete and cached")

        self.refresh_if_stale()

    def _load_from_db(self) -> bool:
        """Load the persisted analysis from Postgres."""
        try:
            from models.database import db, AppCache
            row = db.session.get(AppCache, CACHE_KEY)
            if row and row.payload:
                self._cached_data = row.payload
                self._last_computed = row.computed_at
                self._cache_loaded = True
                return True
        except Exception as e:
            logger.warning(f"Failed to load database cache: {e}")
        return False

    def _save_to_db(self) -> bool:
        """Persist the analysis so container restarts do not recompute it."""
        try:
            payload = _finite_json(serialize_for_template(self._cached_data))
            from models.database import db, AppCache
            row = db.session.get(AppCache, CACHE_KEY)
            if row is None:
                row = AppCache(key=CACHE_KEY)
                db.session.add(row)
            row.payload = payload
            row.computed_at = self._last_computed or datetime.utcnow()
            db.session.commit()
            return True
        except Exception as e:
            db.session.rollback()
            logger.warning(f"Failed to save database cache: {e}")
            return False

    def refresh_if_stale(self):
        """Recompute at most once a week, in the background."""
        if self._app is None or self._refreshing:
            return
        if self._last_computed and datetime.utcnow() - self._last_computed < CACHE_TTL:
            return

        self._refreshing = True
        app = self._app

        def worker():
            try:
                with app.app_context():
                    logger.info("Weekly example portfolio refresh started")
                    self._cached_data = self._compute_analysis()
                    self._last_computed = datetime.utcnow()
                    self._save_to_disk()
                    self._save_to_db()
                    logger.info("Weekly example portfolio refresh complete")
            except Exception as e:
                logger.error(f"Weekly example refresh failed: {e}")
            finally:
                self._refreshing = False

        threading.Thread(target=worker, daemon=True).start()
    
    def _load_from_disk(self) -> bool:
        """Load cached analysis from disk."""
        try:
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, 'rb') as f:
                    cache_data = pickle.load(f)
                    
                # Weekly cache window; staleness triggers a background refresh
                cache_time = cache_data.get('computed_at')
                if cache_time:
                    cache_age = datetime.utcnow() - cache_time
                    if cache_age < CACHE_TTL:
                        self._cached_data = cache_data.get('data')
                        self._last_computed = cache_time
                        self._cache_loaded = True
                        return True
                    else:
                        logger.info(f"Disk cache is {cache_age.total_seconds()/3600:.1f} hours old, will recompute")
                else:
                    self._cached_data = cache_data.get('data')
                    self._cache_loaded = True
                    return True
        except Exception as e:
            logger.warning(f"Failed to load disk cache: {e}")
        
        return False
    
    def _save_to_disk(self) -> bool:
        """Save cached analysis to disk."""
        try:
            cache_data = {
                'data': self._cached_data,
                'computed_at': datetime.utcnow(),
                'version': '1.0'
            }
            with open(CACHE_FILE, 'wb') as f:
                pickle.dump(cache_data, f)
            return True
        except Exception as e:
            logger.error(f"Failed to save disk cache: {e}")
            return False
    
    def get_analysis(self) -> Dict[str, Any]:
        """
        Get the pre-computed example portfolio analysis.
        
        This is FAST - returns cached data from memory.
        No database queries, no API calls, no computation.
        """
        if self._cached_data is None:
            # Fallback - should not happen if initialize() was called
            logger.warning("Cache not initialized, computing on demand")
            self._cached_data = self._compute_analysis()
            self._last_computed = datetime.utcnow()

        self.refresh_if_stale()

        # Convert dataclasses and numpy types to Python dicts for templates
        result = serialize_for_template(self._cached_data)
        
        # Add has_arch_effects to metrics for template compatibility
        if 'volatility_analysis' in result and 'metrics' in result:
            result['metrics']['has_arch_effects'] = result['volatility_analysis'].get('has_arch_effects', False)
        
        return result
    
    def compute_custom(self, holdings: list, period: str = '2y',
                       name: str = 'Your portfolio',
                       description: str = '',
                       risk_free_rate: float = 0.045,
                       benchmark: str = 'SPY',
                       n_simulations: int = 5000,
                       horizon_days: int = 252) -> Dict[str, Any]:
        """Run the exact example pipeline for arbitrary holdings."""
        normalised = []
        total = sum(float(h.get('weight') or 0) for h in holdings) or 1.0
        for holding in holdings:
            ticker = str(holding.get('ticker') or '').upper().strip()
            weight = float(holding.get('weight') or 0) / total
            if ticker and weight > 0:
                normalised.append({'ticker': ticker, 'weight': weight,
                                   'sector': holding.get('sector')})
        return self._compute_analysis({
            'name': name or 'Your portfolio',
            'description': description,
            'holdings': normalised,
            'period': period or '2y',
            'risk_free_rate': risk_free_rate,
            'benchmark': benchmark,
            'n_simulations': n_simulations,
            'horizon_days': horizon_days,
        })

    def refresh_cache(self) -> Dict[str, Any]:
        """
        Force recompute and refresh the cache.
        Call this from admin panel to update with fresh data.
        
        Returns the new cached data.
        """
        logger.info("Admin requested cache refresh, recomputing...")
        self._cached_data = self._compute_analysis()
        self._last_computed = datetime.utcnow()
        self._save_to_disk()
        self._save_to_db()
        logger.info("Cache refresh complete")
        return self._cached_data
    
    def get_cache_info(self) -> Dict[str, Any]:
        """Get information about the current cache."""
        return {
            'has_data': self._cached_data is not None,
            'last_computed': self._last_computed.isoformat() if self._last_computed else None,
            'cache_age_hours': (datetime.utcnow() - self._last_computed).total_seconds() / 3600 if self._last_computed else None,
            'disk_cache_exists': os.path.exists(CACHE_FILE),
            'cache_file_path': os.path.abspath(CACHE_FILE),
            'cache_ttl_days': CACHE_TTL.days
        }
    
    def clear_cache(self) -> bool:
        """Clear the disk cache."""
        try:
            if os.path.exists(CACHE_FILE):
                os.remove(CACHE_FILE)
                logger.info("Disk cache cleared")
            self._cached_data = None
            self._cache_loaded = False
            self._last_computed = None
            return True
        except Exception as e:
            logger.error(f"Failed to clear cache: {e}")
            return False
    
    def _compute_analysis(self, portfolio: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Compute comprehensive portfolio analysis.
        This is the HEAVY operation - takes ~10 seconds.
        Only called at startup or manual refresh.
        """
        portfolio = portfolio or EXAMPLE_PORTFOLIO
        logger.info("Computing portfolio analysis...")
        start_time = datetime.utcnow()

        holdings = portfolio['holdings']
        tickers = [h['ticker'] for h in holdings]
        weights_dict = {h['ticker']: h['weight'] for h in holdings}
        period = portfolio.get('period', '2y')
        risk_free = float(portfolio.get('risk_free_rate') if portfolio.get('risk_free_rate') is not None else 0.045)
        benchmark = (portfolio.get('benchmark') or 'SPY').upper()
        n_sims = int(portfolio.get('n_simulations') or 5000)
        horizon_days = int(portfolio.get('horizon_days') or 252)

        # Fetch market data
        batch_data = market_data_service.fetch_batch_data(tickers, period=period)
        valid_data = {t: d for t, d in batch_data.items() if d is not None}
        
        if len(valid_data) < 2:
            logger.error(f"Insufficient data for example portfolio: {len(valid_data)} tickers")
            return self._get_fallback_data(portfolio)
        
        # Build returns DataFrame
        returns_df = pd.DataFrame({t: d.returns for t, d in valid_data.items()}).dropna()
        
        # Portfolio returns
        weights_array = np.array([weights_dict.get(t, 0) for t in returns_df.columns])
        weights_array = weights_array / weights_array.sum()  # Normalize
        portfolio_returns = returns_df @ weights_array
        
        # Market data for beta calculation
        spy_data = market_data_service.fetch_historical_data(benchmark, period=period)
        market_returns = spy_data.returns if spy_data else None

        # 1. Risk Metrics
        logger.info("Computing risk metrics...")
        risk_metrics = risk_analytics.calculate_comprehensive_metrics(
            portfolio_returns, market_returns, risk_free_rate=risk_free)
        
        # 2. Advanced Risk
        logger.info("Computing advanced risk...")
        adv_risk = AdvancedRiskCalculator(returns_df, weights_array)
        advanced_risk_metrics = adv_risk.calculate_all()
        
        # 3. Distribution Fitting
        logger.info("Fitting distributions...")
        dist_results = advanced_statistics.fit_distributions(portfolio_returns)
        
        # 4. Monte Carlo Simulation (reduced for memory efficiency on low-RAM deployments)
        logger.info("Running Monte Carlo...")
        mc_results = advanced_statistics.monte_carlo_simulation(
            portfolio_returns,
            weights_array,
            initial_value=100000,
            n_simulations=n_sims,
            n_days=horizon_days
        )
        
        # 5. Statistical Tests
        logger.info("Running statistical tests...")
        stats_tests = advanced_statistics.run_statistical_tests(portfolio_returns)
        
        # 6. Rolling metrics
        logger.info("Computing rolling metrics...")
        rolling = risk_analytics.calculate_rolling_metrics(
            portfolio_returns,
            benchmark_returns=market_returns
        )
        
        # 7. Stress test
        logger.info("Running stress tests...")
        stress_results = risk_analytics.stress_test(portfolio_returns, weights_dict)
        
        # 8. Correlation matrix
        logger.info("Computing correlation matrix...")
        corr_matrix = risk_analytics.calculate_correlation_matrix(returns_df)
        
        # 9. Drawdown series
        logger.info("Computing drawdown series...")
        cum_returns = (1 + portfolio_returns).cumprod()
        rolling_max = cum_returns.expanding().max()
        drawdown_series = (cum_returns - rolling_max) / rolling_max
        
        # 10. Confidence intervals
        logger.info("Computing confidence intervals...")
        confidence_intervals = advanced_statistics.calculate_confidence_intervals(
            portfolio_returns, confidence=0.95
        )
        
        # 11. Volatility clustering analysis
        logger.info("Analyzing volatility clustering...")
        vol_analysis = advanced_statistics.analyze_volatility_clustering(portfolio_returns)
        
        # 12. Tail risk (Extreme Value Theory)
        logger.info("Computing tail risk metrics...")
        tail_risk = advanced_statistics.extreme_value_analysis(portfolio_returns)
        
        # 13. PCA
        logger.info("Running PCA...")
        pca_result = advanced_statistics.principal_component_analysis(returns_df)
        
        # 14. Rolling statistics
        logger.info("Computing rolling statistics...")
        rolling_stats = advanced_statistics.calculate_rolling_statistics(portfolio_returns, window=63)
        
        # 15. Benchmarks
        logger.info("Fetching benchmark data...")
        bench_list = [benchmark, 'VT'] if benchmark != 'VT' else [benchmark, 'SPY']
        benchmarks = risk_analytics.get_benchmark_data(bench_list, period=period)
        
        # 16. Monthly returns
        logger.info("Computing monthly returns heatmap...")
        monthly_returns_data = dashboard_charts.get_monthly_returns_heatmap(portfolio_returns)
        
        # 17. Rolling correlation
        logger.info("Computing rolling correlation...")
        rolling_correlation = dashboard_charts.get_rolling_correlation_chart(returns_df, window=90)
        
        # 18. Factor Exposure
        logger.info("Computing factor exposure...")
        ff_model = FamaFrenchModel()
        factor_exposure = ff_model.estimate_exposure(portfolio_returns)
        
        # 19. AI Insights
        logger.info("Generating AI insights...")
        ai_insights = generate_ai_insights(
            risk_metrics,
            dist_results,
            stats_tests,
            pca_result,
            vol_analysis
        )
        
        # 20. Charts
        logger.info("Generating charts...")
        charts = dashboard_charts.get_all_charts(
            returns_df=returns_df,
            portfolio_returns=portfolio_returns,
            weights=weights_dict,
            factor_exposure={'exposure': factor_exposure.__dict__ if factor_exposure else {}},
            period=period
        )
        
        # Fix charts to include missing data that index.html expects
        # Add beta to rolling_metrics (dashboard_charts doesn't include it)
        if 'rolling_metrics' in charts and '60d' in charts['rolling_metrics']:
            charts['rolling_metrics']['60d']['beta'] = rolling['beta'].fillna(1.0).tolist()
        
        # Add volatility chart data (dates, returns for returns chart)
        charts['volatility'] = {
            'dates': portfolio_returns.index.strftime('%Y-%m-%d').tolist(),
            'returns': portfolio_returns.tolist()
        }
        
        # 8. Holdings with info
        holdings_with_info = []
        for h in holdings:
            ticker = h['ticker']
            data = valid_data.get(ticker)
            if data:
                holdings_with_info.append({
                    'ticker': ticker,
                    'weight': h['weight'],
                    'name': ticker,
                    'sector': h.get('sector', 'Unknown'),
                    'price': float(data.prices.iloc[-1]) if data.prices is not None else None
                })
        
        # Combine all results
        result = {
            'meta': {
                'name': portfolio.get('name') or EXAMPLE_PORTFOLIO['name'],
                'description': portfolio.get('description') or EXAMPLE_PORTFOLIO['description'],
                'computed_at': datetime.utcnow().isoformat(),
                'period': period,
                'n_assets': len(holdings),
                'n_simulations': n_sims,
                'horizon_days': horizon_days,
                'risk_free_rate': risk_free,
                'benchmark': benchmark
            },
            'metrics': risk_metrics,
            'advanced_risk': advanced_risk_metrics,
            'distributions': dist_results,
            'distributions_dict': {
                'best_fit': min(dist_results.items(), key=lambda x: x[1].aic)[0] if dist_results else 'Normal',
                **{name: d for name, d in dist_results.items()}
            },
            'monte_carlo': mc_results,
            'statistical_tests': stats_tests,
            'factor_exposure': factor_exposure,
            'charts': charts,
            'holdings': holdings_with_info,
            'rolling_metrics': {
                'dates': rolling.index.strftime('%Y-%m-%d').tolist(),
                'volatility': rolling['volatility'].tolist(),
                'drawdown': rolling['drawdown'].tolist(),
                'beta': rolling['beta'].fillna(1.0).tolist(),
                'sharpe': rolling['sharpe'].tolist()
            },
            'stress_test': stress_results,
            'correlation_matrix': corr_matrix.to_dict(),
            'drawdown_series': {
                'dates': drawdown_series.index.strftime('%Y-%m-%d').tolist(),
                'drawdown': drawdown_series.tolist()
            },
            'confidence_intervals': {k: list(v) for k, v in confidence_intervals.items()},
            'volatility_analysis': vol_analysis,
            'tail_risk': tail_risk,
            'pca': pca_result,
            'rolling_stats_63d': {
                'dates': rolling_stats.index.strftime('%Y-%m-%d').tolist(),
                'mean': rolling_stats['mean'].tolist(),
                'std': rolling_stats['std'].tolist(),
                'skewness': rolling_stats['skewness'].tolist()
            } if rolling_stats is not None else None,
            'benchmarks': {
                name: (1 + returns).cumprod().tolist() 
                for name, returns in benchmarks.items()
            } if benchmarks else {},
            'monthly_returns': monthly_returns_data,
            'rolling_correlation': rolling_correlation,
            'ai_insights': ai_insights
        }
        
        elapsed = (datetime.utcnow() - start_time).total_seconds()
        logger.info(f"Example portfolio analysis complete! ({elapsed:.1f}s)")
        self._last_computed = datetime.utcnow()
        
        return result
    
    def _get_fallback_data(self, portfolio: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Return minimal fallback data if computation fails."""
        portfolio = portfolio or EXAMPLE_PORTFOLIO
        return {
            'meta': {
                'name': portfolio.get('name') or EXAMPLE_PORTFOLIO['name'],
                'description': portfolio.get('description') or EXAMPLE_PORTFOLIO['description'],
                'computed_at': datetime.utcnow().isoformat(),
                'error': 'Failed to compute full analysis',
                'period': portfolio.get('period', '2y'),
                'n_assets': len(portfolio.get('holdings') or []),
            },
            'metrics': {},
            'holdings': portfolio.get('holdings') or EXAMPLE_PORTFOLIO['holdings'],
            'charts': {}
        }


# Global singleton instance
example_portfolio_service = ExamplePortfolioService()


def initialize_example_portfolio():
    """Call this once at application startup."""
    example_portfolio_service.initialize()


if __name__ == "__main__":
    # Test the service
    logging.basicConfig(level=logging.INFO)
    service = ExamplePortfolioService()
    service.initialize()
    data = service.get_analysis()
    print(f"Analysis computed at: {data['meta']['computed_at']}")
    print(f"Cache info: {service.get_cache_info()}")
