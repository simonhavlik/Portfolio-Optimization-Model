"""
Portfolio Optimizer - Dashboard Chart Data Service
===================================================

Generate data for all dashboard visualizations:
- Performance charts
- Risk charts
- Allocation charts
- Factor analysis charts
- Benchmark comparisons
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timedelta
import logging

from services.market_data import market_data_service
from services.risk_analytics import risk_analytics

logger = logging.getLogger(__name__)


BENCHMARK_TICKERS = {
    'S&P 500': 'SPY',
    'FTSE 100': 'VUKE.L',
    'NASDAQ': 'QQQ',
    'MSCI World': 'URTH',
    'Russell 2000': 'IWM',
    'DAX': 'EXS1.DE',
    'Nikkei 225': 'EWJ',
    'Emerging Markets': 'EEM',
    'Bonds (AGG)': 'AGG',
    'Gold': 'GLD'
}


class DashboardChartService:
    """Generate all chart data for the dashboard."""
    
    def __init__(self):
        self.benchmark_data = {}
        
    def get_performance_chart_data(self, 
                                   returns: pd.Series,
                                   tickers: List[str],
                                   period: str = '2y',
                                   benchmark_snapshot: Optional[Dict[str, pd.Series]] = None) -> Dict[str, Any]:
        """
        Generate portfolio performance vs benchmarks chart data.
        
        Returns cumulative returns comparison with multiple benchmarks.
        """
        portfolio_cum = (1 + returns).cumprod()
        
        # Get benchmark data
        benchmark_series = {}
        colors = ['#3b82f6', '#ef4444', '#22c55e', '#f59e0b', '#8b5cf6']
        
        for i, (name, ticker) in enumerate(list(BENCHMARK_TICKERS.items())[:5]):
            try:
                if benchmark_snapshot is None:
                    data = market_data_service.fetch_historical_data(ticker, period=period)
                    series = data.returns if data else None
                else:
                    series = benchmark_snapshot.get(ticker)
                    if series is not None:
                        series = np.expm1(series.reindex(returns.index))
                if series is not None and len(series) > 0:
                    bench_cum = (1 + series).cumprod()
                    # Normalize timezones and align dates
                    if portfolio_cum.index.tz is not None:
                        portfolio_cum.index = portfolio_cum.index.tz_localize(None)
                    if bench_cum.index.tz is not None:
                        bench_cum.index = bench_cum.index.tz_localize(None)
                    aligned = pd.concat([portfolio_cum, bench_cum], axis=1).dropna()
                    if len(aligned) > 0:
                        benchmark_series[name] = {
                            'dates': aligned.index.strftime('%Y-%m-%d').tolist(),
                            'values': aligned.iloc[:, 1].tolist(),
                            'color': colors[i]
                        }
            except Exception as e:
                logger.warning(f"Could not fetch {name}: {e}")
        
        return {
            'portfolio': {
                'dates': portfolio_cum.index.strftime('%Y-%m-%d').tolist(),
                'values': portfolio_cum.tolist(),
                'color': '#4f46e5'
            },
            'benchmarks': benchmark_series
        }
    
    def get_rolling_metrics_charts(self, returns: pd.Series) -> Dict[str, Any]:
        """
        Generate rolling metrics charts data.
        
        Returns rolling volatility, Sharpe, drawdown for different windows.
        """
        windows = {'30d': 30, '60d': 60, '90d': 90}
        result = {}
        
        for name, window in windows.items():
            if len(returns) < window:
                continue
                
            rolling_vol = returns.rolling(window).std() * np.sqrt(252)
            rolling_return = returns.rolling(window).mean() * 252
            rolling_sharpe = rolling_return / rolling_vol
            
            # Rolling max drawdown
            cum_ret = (1 + returns).cumprod()
            rolling_max = cum_ret.rolling(window, min_periods=1).max()
            rolling_dd = (cum_ret - rolling_max) / rolling_max
            
            result[name] = {
                'dates': returns.index.strftime('%Y-%m-%d').tolist(),
                'volatility': rolling_vol.tolist(),
                'sharpe': rolling_sharpe.tolist(),
                'drawdown': rolling_dd.tolist()
            }
        
        return result
    
    def get_allocation_charts(self, 
                             weights: Dict[str, float],
                             tickers: List[str],
                             known_sectors: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Generate asset allocation visualization data.
        
        Returns data for pie chart, treemap, and sector breakdown.
        """
        # Get stock info for sectors
        sectors = {}
        market_caps = {}
        
        for ticker in tickers:
            if known_sectors is not None:
                sectors[ticker] = known_sectors.get(ticker) or 'Unclassified'
                continue
            try:
                info = market_data_service.get_stock_info(ticker)
                sector = info.get('sector', 'Unknown')
                market_cap = info.get('market_cap', 0)
                
                sectors[ticker] = sector
                market_caps[ticker] = market_cap
            except:
                sectors[ticker] = 'Unknown'
                market_caps[ticker] = 0
        
        # Pie chart data - format for template compatibility
        sorted_weights = sorted(weights.items(), key=lambda x: x[1], reverse=True)
        pie_data = {
            'labels': [t for t, w in sorted_weights],
            'values': [w for t, w in sorted_weights]
        }
        
        # Sector aggregation
        sector_weights = {}
        for t, w in weights.items():
            s = sectors.get(t, 'Unknown')
            sector_weights[s] = sector_weights.get(s, 0) + w
        
        # Treemap data (hierarchical)
        treemap_data = []
        for sector, s_weight in sector_weights.items():
            sector_stocks = [
                {'ticker': t, 'weight': w}
                for t, w in weights.items()
                if sectors.get(t) == sector
            ]
            treemap_data.append({
                'sector': sector,
                'weight': s_weight,
                'stocks': sector_stocks
            })
        
        return {
            'pie': pie_data,
            'sectors': sector_weights,
            'treemap': treemap_data,
            'market_caps': market_caps
        }
    
    def get_risk_contribution_chart(self,
                                    returns_df: pd.DataFrame,
                                    weights: np.ndarray) -> Dict[str, Any]:
        """
        Generate risk contribution analysis chart data.
        
        Shows marginal VaR and component VaR by asset.
        """
        cov_matrix = returns_df.cov() * 252
        port_vol = np.sqrt(weights @ cov_matrix.values @ weights)
        
        # Marginal contribution
        marginal_contrib = (cov_matrix.values @ weights) / port_vol
        component_contrib = weights * marginal_contrib
        
        return {
            'tickers': returns_df.columns.tolist(),
            'weights': weights.tolist(),
            'marginal_risk': marginal_contrib.tolist(),
            'component_risk': component_contrib.tolist(),
            'risk_contrib_pct': (component_contrib / port_vol * 100).tolist()
        }
    
    def get_return_distribution_charts(self, returns: pd.Series) -> Dict[str, Any]:
        """
        Generate return distribution visualization data.
        
        Histogram, KDE, Q-Q plot data.
        """
        returns_clean = returns.dropna()
        
        # Histogram
        hist, bins = np.histogram(returns_clean, bins=50)
        
        # KDE (simplified)
        from scipy import stats
        kde = stats.gaussian_kde(returns_clean)
        x_range = np.linspace(returns_clean.min(), returns_clean.max(), 100)
        kde_values = kde(x_range)
        
        # Q-Q plot data vs normal
        sorted_returns = np.sort(returns_clean)
        theoretical = stats.norm.ppf(np.linspace(0.01, 0.99, len(sorted_returns)))
        
        return {
            'histogram': {
                'counts': hist.tolist(),
                'bins': bins.tolist()
            },
            'kde': {
                'x': x_range.tolist(),
                'y': kde_values.tolist()
            },
            'qq': {
                'theoretical': theoretical.tolist(),
                'actual': sorted_returns.tolist()
            },
            'stats': {
                'mean': returns_clean.mean(),
                'std': returns_clean.std(),
                'skewness': returns_clean.skew(),
                'kurtosis': returns_clean.kurtosis(),
                'jarque_bera': stats.jarque_bera(returns_clean)[0]
            }
        }
    
    def get_efficient_frontier_chart(self,
                                     returns_df: pd.DataFrame,
                                     current_weights: np.ndarray,
                                     n_points: int = 50) -> Dict[str, Any]:
        """
        Generate efficient frontier chart data compatible with frontend.
        """
        mu = returns_df.mean() * 252
        Sigma = returns_df.cov() * 252
        
        n = len(mu)
        tickers = returns_df.columns.tolist()
        
        # Generate random portfolios
        np.random.seed(42)
        random_weights = np.random.dirichlet(np.ones(n), 5000)
        
        port_returns = random_weights @ mu
        port_vols = np.sqrt(np.einsum('ij,jk,ik->i', random_weights, Sigma, random_weights))
        port_sharpes = port_returns / port_vols
        
        # Current portfolio
        current_return = current_weights @ mu
        current_vol = np.sqrt(current_weights @ Sigma @ current_weights)
        
        # Max Sharpe
        max_sharpe_idx = np.argmax(port_sharpes)
        
        # Min variance
        min_var_idx = np.argmin(port_vols)
        
        # Calculate efficient frontier curve
        # Use optimization to find efficient frontier
        target_returns = np.linspace(port_returns.min(), port_returns.max() * 1.2, n_points)
        efficient_vols = []
        efficient_rets = []
        
        for target in target_returns:
            # Find minimum variance for target return
            mask = port_returns >= target - 0.01
            if mask.sum() > 0:
                idx = np.argmin(port_vols[mask])
                efficient_vols.append(float(port_vols[mask][idx]))
                efficient_rets.append(float(port_returns[mask][idx]))
        
        # Format for template compatibility
        frontier_points = [
            {'return_annualized': r, 'volatility': v}
            for r, v in zip(efficient_rets, efficient_vols)
        ]
        
        individual_assets = [
            {'ticker': t, 'return_annualized': float(mu[t]), 'volatility': float(np.sqrt(Sigma.loc[t, t]))}
            for t in tickers
        ]
        
        return {
            'random_portfolios': {
                'returns': port_returns.tolist(),
                'volatility': port_vols.tolist(),
                'sharpe': port_sharpes.tolist()
            },
            'current_portfolio': {
                'return': float(current_return),
                'volatility': float(current_vol),
                'sharpe': float(current_return / current_vol) if current_vol > 0 else 0
            },
            'max_sharpe': {
                'return': float(port_returns[max_sharpe_idx]),
                'volatility': float(port_vols[max_sharpe_idx]),
                'sharpe': float(port_sharpes[max_sharpe_idx])
            },
            'min_vol': {
                'return': float(port_returns[min_var_idx]),
                'volatility': float(port_vols[min_var_idx]),
                'sharpe': float(port_sharpes[min_var_idx])
            },
            'frontier': frontier_points,
            'individual_assets': individual_assets
        }
    
    def get_factor_exposure_chart(self, factor_exposure: Dict) -> Dict[str, Any]:
        """
        Generate factor exposure visualization data.
        """
        return {
            'betas': {
                'market': factor_exposure.get('market_beta', 0),
                'smb': factor_exposure.get('smb_beta', 0),
                'hml': factor_exposure.get('hml_beta', 0),
                'rmw': factor_exposure.get('rmw_beta', 0),
                'cma': factor_exposure.get('cma_beta', 0),
                'mom': factor_exposure.get('mom_beta', 0)
            },
            'contributions': factor_exposure.get('factor_contributions', {}),
            'r_squared': factor_exposure.get('r_squared', 0),
            'alpha': factor_exposure.get('alpha', 0)
        }
    
    def get_rolling_correlation_chart(self,
                                     returns_df: pd.DataFrame,
                                     window: int = 30) -> Dict[str, Any]:
        """
        Generate rolling correlation heatmap data.
        """
        tickers = returns_df.columns.tolist()
        
        # Calculate rolling correlations
        rolling_corr = {}
        dates = returns_df.index[window:].strftime('%Y-%m-%d').tolist()
        
        for i, ticker1 in enumerate(tickers):
            for j, ticker2 in enumerate(tickers):
                if i < j:  # Only calculate upper triangle
                    pair_corr = returns_df[ticker1].rolling(window).corr(
                        returns_df[ticker2]
                    ).dropna()
                    
                    rolling_corr[f"{ticker1}-{ticker2}"] = pair_corr.tolist()
        
        return {
            'dates': dates,
            'tickers': tickers,
            'correlations': rolling_corr
        }
    
    def get_pca_biplot(self, returns_df: pd.DataFrame) -> Dict[str, Any]:
        """
        Generate PCA biplot data.
        """
        from sklearn.decomposition import PCA
        from sklearn.preprocessing import StandardScaler
        
        # Standardize
        scaler = StandardScaler()
        standardized = scaler.fit_transform(returns_df.dropna())
        
        # PCA
        pca = PCA(n_components=2)
        pcs = pca.fit_transform(standardized)
        
        # Loadings
        loadings = pca.components_.T * np.sqrt(pca.explained_variance_)
        
        return {
            'scores': {
                'pc1': pcs[:, 0].tolist(),
                'pc2': pcs[:, 1].tolist()
            },
            'loadings': {
                'pc1': loadings[:, 0].tolist(),
                'pc2': loadings[:, 1].tolist(),
                'tickers': returns_df.columns.tolist()
            },
            'explained_variance': pca.explained_variance_ratio_.tolist()
        }
    
    def get_stress_test_chart(self, stress_results: Dict[str, float]) -> Dict[str, Any]:
        """
        Generate stress test visualization.
        """
        scenarios = []
        for name, impact in stress_results.items():
            scenarios.append({
                'scenario': name,
                'impact': impact,
                'severity': 'high' if impact < -0.3 else 'medium' if impact < -0.15 else 'low'
            })
        
        if stress_results:
            return {
                'scenarios': scenarios,
                'worst_case': min(stress_results.values()),
                'avg_impact': np.mean(list(stress_results.values()))
            }
        else:
            return {
                'scenarios': [],
                'worst_case': 0.0,
                'avg_impact': 0.0
            }
    
    def get_monthly_returns_heatmap(self, returns: pd.Series) -> Dict[str, Any]:
        """
        Generate monthly returns heatmap data.
        """
        # Group by year and month
        monthly = returns.groupby([returns.index.year, returns.index.month]).sum()
        
        # Create matrix
        years = sorted(set(returns.index.year))
        months = list(range(1, 13))
        
        matrix = []
        for year in years:
            row = []
            for month in months:
                try:
                    val = monthly[(year, month)]
                    row.append(float(val))
                except KeyError:
                    row.append(None)
            matrix.append(row)
        
        return {
            'years': years,
            'months': ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
            'data': matrix
        }
    
    def get_monte_carlo_chart(self, 
                              returns: pd.Series, 
                              weights: np.ndarray,
                              n_paths: int = 100,
                              n_days: int = 252,
                              initial_value: float = 100000) -> Dict[str, Any]:
        """
        Generate Monte Carlo simulation chart data.
        """
        np.random.seed(42)
        returns_clean = returns.dropna()
        
        # Bootstrap simulation
        simulated_returns = np.random.choice(
            returns_clean, 
            size=(n_paths, n_days),
            replace=True
        )
        
        # Calculate paths
        price_paths = np.zeros((n_paths, n_days + 1))
        price_paths[:, 0] = initial_value
        
        for t in range(1, n_days + 1):
            price_paths[:, t] = price_paths[:, t-1] * (1 + simulated_returns[:, t-1])
        
        # Calculate percentiles for band
        percentiles = [5, 25, 50, 75, 95]
        bands = {}
        for p in percentiles:
            bands[f'p{p}'] = np.percentile(price_paths, p, axis=0).tolist()
        
        # Sample paths for display (reduce to 50 for performance)
        step = max(1, n_paths // 50)
        sample_paths = price_paths[::step].tolist()
        
        return {
            'paths': sample_paths,
            'percentiles': bands,
            'days': list(range(n_days + 1)),
            'initial_value': initial_value
        }
    
    def get_rolling_statistics_chart(self, returns: pd.Series, window: int = 63) -> Dict[str, Any]:
        """
        Generate rolling statistics with confidence bands.
        """
        returns_clean = returns.dropna()
        
        rolling_mean = returns_clean.rolling(window).mean() * 252
        rolling_std = returns_clean.rolling(window).std() * np.sqrt(252)
        rolling_sharpe = rolling_mean / rolling_std
        
        # Standard error for confidence bands
        sem = rolling_std / np.sqrt(window)
        mean_upper = rolling_mean + 1.96 * sem
        mean_lower = rolling_mean - 1.96 * sem
        
        return {
            'dates': returns_clean.index.strftime('%Y-%m-%d').tolist(),
            'mean': rolling_mean.tolist(),
            'std': rolling_std.tolist(),
            'sharpe': rolling_sharpe.tolist(),
            'mean_upper': mean_upper.tolist(),
            'mean_lower': mean_lower.tolist()
        }
    
    def get_pca_chart(self, returns_df: pd.DataFrame) -> Dict[str, Any]:
        """
        Generate PCA visualization data with explained variance and loadings.
        """
        from sklearn.decomposition import PCA
        from sklearn.preprocessing import StandardScaler
        
        returns_clean = returns_df.dropna()
        tickers = returns_clean.columns.tolist()
        
        # Standardize
        scaler = StandardScaler()
        standardized = scaler.fit_transform(returns_clean)
        
        # PCA with all components
        n_components = min(len(tickers), 10)
        pca = PCA(n_components=n_components)
        pcs = pca.fit_transform(standardized)
        
        # Explained variance
        explained_var = pca.explained_variance_ratio_
        cumulative_var = np.cumsum(explained_var)
        
        # Loadings (component vectors)
        loadings = pca.components_.T * np.sqrt(pca.explained_variance_)
        
        return {
            'scores': {
                'pc1': pcs[:, 0].tolist(),
                'pc2': pcs[:, 1].tolist()
            },
            'loadings': loadings.tolist(),
            'tickers': tickers,
            'explained_variance': explained_var.tolist(),
            'cumulative_variance': cumulative_var.tolist(),
            'components': list(range(1, n_components + 1)),
            'condition_number': explained_var[0] / explained_var[-1] if explained_var[-1] > 0 else np.inf
        }
    
    def get_drawdown_chart(self, returns: pd.Series) -> Dict[str, Any]:
        """
        Generate drawdown analysis chart data.
        """
        returns_clean = returns.dropna()
        cum_returns = (1 + returns_clean).cumprod()
        rolling_max = cum_returns.expanding().max()
        drawdown = (cum_returns - rolling_max) / rolling_max
        
        return {
            'dates': returns_clean.index.strftime('%Y-%m-%d').tolist(),
            'drawdown': drawdown.tolist(),
            'cumulative_returns': cum_returns.tolist(),
            'max_drawdown': drawdown.min(),
            'max_drawdown_date': drawdown.idxmin().strftime('%Y-%m-%d')
        }
    
    def get_all_charts(self,
                      returns_df: pd.DataFrame,
                      portfolio_returns: pd.Series,
                      weights: Dict[str, float],
                      factor_exposure: Optional[Dict] = None,
                      stress_results: Optional[Dict] = None,
                      period: str = '2y',
                      benchmark_snapshot: Optional[Dict[str, pd.Series]] = None,
                      known_sectors: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """
        Generate all dashboard charts at once.
        """
        weights_array = np.array([weights.get(t, 0) for t in returns_df.columns])
        
        return {
            'performance': self.get_performance_chart_data(portfolio_returns, 
                                                          returns_df.columns.tolist(), 
                                                          period, benchmark_snapshot),
            'rolling_metrics': self.get_rolling_metrics_charts(portfolio_returns),
            'rolling_stats_63d': self.get_rolling_statistics_chart(portfolio_returns, window=63),
            'allocation': self.get_allocation_charts(weights, returns_df.columns.tolist(), known_sectors),
            'risk_contribution': self.get_risk_contribution_chart(returns_df, weights_array),
            'return_distribution': self.get_return_distribution_charts(portfolio_returns),
            'efficient_frontier': self.get_efficient_frontier_chart(returns_df, weights_array),
            'rolling_correlation': self.get_rolling_correlation_chart(returns_df),
            'monthly_returns': self.get_monthly_returns_heatmap(portfolio_returns),
            'pca': self.get_pca_chart(returns_df),
            'pca_biplot': self.get_pca_biplot(returns_df),
            'monte_carlo': self.get_monte_carlo_chart(portfolio_returns, weights_array),
            'drawdown': self.get_drawdown_chart(portfolio_returns),
            'factor_exposure': self.get_factor_exposure_chart(factor_exposure or {}),
            'stress_test': self.get_stress_test_chart(stress_results or {})
        }


# Singleton instance
dashboard_charts = DashboardChartService()
