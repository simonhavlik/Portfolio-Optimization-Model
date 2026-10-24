"""
Portfolio Optimizer - Comprehensive Risk Analytics
==================================================

Advanced portfolio risk metrics calculation including:
- Maximum drawdown analysis
- Rolling volatility and beta
- Value at Risk (VaR) and Expected Shortfall (CVaR)
- Risk-adjusted performance metrics (Sortino, Calmar, Omega)
- Benchmark comparisons
- Factor exposure analysis

References:
-----------
- Sortino, F. & Price, L. (1994). Performance Measurement in a Downside Risk Framework
- Choueifaty, Y. & Coignard, Y. (2008). Toward Maximum Diversification
- Ang, A. (2014). Asset Management: A Systematic Approach to Factor Investing
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from scipy import stats
import logging

from services.market_data import market_data_service

logger = logging.getLogger(__name__)


@dataclass
class RiskMetrics:
    """Comprehensive risk metrics for a portfolio."""
    # Return Metrics
    total_return: float
    annualized_return: float
    
    # Risk Metrics
    volatility: float
    downside_volatility: float
    max_drawdown: float
    max_drawdown_duration: int
    var_95: float
    var_99: float
    cvar_95: float  # Conditional VaR / Expected Shortfall
    
    # Risk-Adjusted Metrics
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    omega_ratio: float
    information_ratio: float
    treynor_ratio: float
    
    # Factor Metrics
    beta: float
    alpha: float
    r_squared: float
    
    # Diversification
    concentration: float  # Herfindahl index
    diversification_ratio: float
    effective_n: float  # Number of uncorrelated bets
    
    # Tail Risk
    skewness: float
    kurtosis: float
    tail_ratio: float


class RiskAnalytics:
    """
    Advanced risk analytics engine for portfolio analysis.
    
    Implements industry-standard risk metrics used by institutional
    portfolio managers and quantitative analysts.
    """
    
    BENCHMARK_TICKERS = {
        'SPY': 'S&P 500',
        'QQQ': 'Nasdaq 100',
        'IWM': 'Russell 2000',
        'DIA': 'Dow 30',
        'VT': 'FTSE All-World',
        'AGG': 'US Aggregate Bonds',
        'GLD': 'Gold'
    }
    
    def __init__(self):
        self.benchmark_data = {}
    
    def calculate_comprehensive_metrics(
        self,
        returns: pd.Series,
        benchmark_returns: Optional[pd.Series] = None,
        risk_free_rate: float = 0.05
    ) -> RiskMetrics:
        """
        Calculate all risk metrics for a portfolio.
        
        Args:
            returns: Daily portfolio returns
            benchmark_returns: Daily benchmark returns (optional)
            risk_free_rate: Annual risk-free rate
            
        Returns:
            RiskMetrics object with all calculated metrics
        """
        # Clean data
        returns = returns.dropna()
        if len(returns) < 30:
            raise ValueError("Insufficient data for risk calculation (need 30+ days)")
        
        # Basic return metrics
        total_return = (1 + returns).prod() - 1
        annualized_return = (1 + total_return) ** (252 / len(returns)) - 1
        
        # Volatility metrics
        volatility = returns.std() * np.sqrt(252)
        downside_returns = returns[returns < 0]
        downside_vol = downside_returns.std() * np.sqrt(252) if len(downside_returns) > 0 else 0
        
        # Drawdown analysis
        cum_returns = (1 + returns).cumprod()
        rolling_max = cum_returns.expanding().max()
        drawdowns = (cum_returns - rolling_max) / rolling_max
        max_drawdown = drawdowns.min()
        
        # Find max drawdown duration
        is_drawdown = drawdowns < 0
        drawdown_periods = []
        current_start = None
        for date, in_dd in is_drawdown.items():
            if in_dd and current_start is None:
                current_start = date
            elif not in_dd and current_start is not None:
                drawdown_periods.append((current_start, date))
                current_start = None
        max_dd_duration = max([(end - start).days for start, end in drawdown_periods], default=0)
        
        # VaR and CVaR calculations
        var_95 = np.percentile(returns, 5)
        var_99 = np.percentile(returns, 1)
        cvar_95 = returns[returns <= var_95].mean() if len(returns[returns <= var_95]) > 0 else var_95
        
        # Risk-adjusted metrics
        excess_return = annualized_return - risk_free_rate
        sharpe = excess_return / volatility if volatility > 0 else 0
        sortino = excess_return / downside_vol if downside_vol > 0 else 0
        calmar = annualized_return / abs(max_drawdown) if max_drawdown != 0 else 0
        
        # Omega ratio
        gain_returns = returns[returns > 0].sum()
        loss_returns = abs(returns[returns < 0].sum())
        omega = gain_returns / loss_returns if loss_returns > 0 else float('inf')
        
        # Factor metrics vs benchmark
        beta, alpha, r_squared, info_ratio, treynor = self._calculate_factor_metrics(
            returns, benchmark_returns, risk_free_rate
        )
        
        # Tail risk
        skew = returns.skew()
        kurt = returns.kurtosis()
        tail_ratio = abs(np.percentile(returns, 95)) / abs(np.percentile(returns, 5))
        
        # Diversification metrics
        concentration = (returns ** 2).mean() / (returns.var()) if returns.var() > 0 else 1
        effective_n = 1 / concentration if concentration > 0 else 1
        
        return RiskMetrics(
            total_return=total_return,
            annualized_return=annualized_return,
            volatility=volatility,
            downside_volatility=downside_vol,
            max_drawdown=max_drawdown,
            max_drawdown_duration=max_dd_duration,
            var_95=var_95,
            var_99=var_99,
            cvar_95=cvar_95,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            omega_ratio=omega,
            information_ratio=info_ratio,
            treynor_ratio=treynor,
            beta=beta,
            alpha=alpha,
            r_squared=r_squared,
            concentration=concentration,
            diversification_ratio=0,  # Calculate separately for multi-asset
            effective_n=effective_n,
            skewness=skew,
            kurtosis=kurt,
            tail_ratio=tail_ratio
        )
    
    def _calculate_factor_metrics(
        self,
        returns: pd.Series,
        benchmark_returns: Optional[pd.Series],
        risk_free_rate: float
    ) -> Tuple[float, float, float, float, float]:
        """Calculate beta, alpha, R², information ratio, and Treynor ratio."""
        if benchmark_returns is None or len(benchmark_returns) == 0:
            return 1.0, 0.0, 0.0, 0.0, 0.0
        
        # Ensure timezone-naive indices
        returns = returns.copy()
        if returns.index.tz is not None:
            returns.index = returns.index.tz_localize(None)
        # Normalize timezones to avoid comparison issues
        returns = returns.copy()
        benchmark_returns = benchmark_returns.copy()
        if returns.index.tz is not None:
            returns.index = returns.index.tz_localize(None)
        if benchmark_returns.index.tz is not None:
            benchmark_returns.index = benchmark_returns.index.tz_localize(None)
        
        # Align dates
        aligned = pd.concat([returns, benchmark_returns], axis=1).dropna()
        if len(aligned) < 30:
            return 1.0, 0.0, 0.0, 0.0, 0.0
        
        port = aligned.iloc[:, 0]
        bench = aligned.iloc[:, 1]
        
        # Beta and alpha via regression
        covariance = port.cov(bench)
        benchmark_var = bench.var()
        beta = covariance / benchmark_var if benchmark_var > 0 else 1.0
        
        port_ann = port.mean() * 252
        bench_ann = bench.mean() * 252
        alpha = port_ann - (risk_free_rate + beta * (bench_ann - risk_free_rate))
        
        # R-squared
        correlation = port.corr(bench)
        r_squared = correlation ** 2
        
        # Information ratio
        tracking_error = (port - bench).std() * np.sqrt(252)
        info_ratio = (port_ann - bench_ann) / tracking_error if tracking_error > 0 else 0
        
        # Treynor ratio
        treynor = (port_ann - risk_free_rate) / beta if beta > 0 else 0
        
        return beta, alpha, r_squared, info_ratio, treynor
    
    def calculate_rolling_metrics(
        self,
        returns: pd.Series,
        window: int = 63,  # ~3 months
        benchmark_returns: Optional[pd.Series] = None
    ) -> pd.DataFrame:
        """
        Calculate rolling risk metrics over time.
        
        Returns DataFrame with rolling volatility, drawdown, beta, etc.
        """
        rolling_vol = returns.rolling(window).std() * np.sqrt(252)
        
        # Rolling drawdown
        cum_returns = (1 + returns).cumprod()
        rolling_max = cum_returns.expanding().max()
        rolling_dd = (cum_returns - rolling_max) / rolling_max
        
        # Rolling beta if benchmark provided
        rolling_beta = pd.Series(index=returns.index, dtype=float)
        if benchmark_returns is not None:
            # Normalize timezone to avoid tz-naive vs tz-aware comparison issues
            ret_norm = returns.copy()
            bench_norm = benchmark_returns.copy()
            if ret_norm.index.tz is not None:
                ret_norm.index = ret_norm.index.tz_localize(None)
            if bench_norm.index.tz is not None:
                bench_norm.index = bench_norm.index.tz_localize(None)
            aligned = pd.concat([ret_norm, bench_norm], axis=1).dropna()
            for i in range(window, len(aligned)):
                period = aligned.iloc[i-window:i]
                if len(period) >= window:
                    cov = period.iloc[:, 0].cov(period.iloc[:, 1])
                    var = period.iloc[:, 1].var()
                    rolling_beta.iloc[i] = cov / var if var > 0 else 1.0
        
        # Rolling Sharpe (using expanding window for stability)
        rolling_return = returns.expanding().mean() * 252
        rolling_sharpe = rolling_return / rolling_vol
        
        return pd.DataFrame({
            'volatility': rolling_vol,
            'drawdown': rolling_dd,
            'beta': rolling_beta,
            'sharpe': rolling_sharpe
        })
    
    def calculate_correlation_matrix(
        self,
        returns_df: pd.DataFrame
    ) -> pd.DataFrame:
        """Calculate correlation matrix with significance indicators."""
        corr = returns_df.corr()
        return corr
    
    def stress_test(
        self,
        returns: pd.Series,
        weights: Dict[str, float],
        scenarios: Optional[Dict[str, float]] = None
    ) -> Dict[str, float]:
        """
        Perform stress testing under various market scenarios.
        
        Scenarios include: 2008 crisis, COVID crash, interest rate shock, etc.
        """
        if scenarios is None:
            scenarios = {
                '2008 Financial Crisis': -0.38,
                'COVID-19 Crash (Mar 2020)': -0.34,
                'Dot-com Crash': -0.49,
                'Rate Shock (+200bps)': -0.15,
                'Tech Selloff': -0.25,
                'Geopolitical Crisis': -0.20
            }
        
        current_vol = returns.std() * np.sqrt(252)
        results = {}
        
        for scenario_name, market_decline in scenarios.items():
            # Estimate portfolio impact based on beta and concentration
            concentration = sum(w**2 for w in weights.values())
            estimated_impact = market_decline * (1 + concentration)
            results[scenario_name] = estimated_impact
        
        return results
    
    def get_benchmark_data(
        self,
        tickers: List[str],
        period: str = "2y"
    ) -> Dict[str, pd.Series]:
        """Fetch benchmark data for comparison."""
        result = {}
        for ticker in tickers:
            if ticker in self.BENCHMARK_TICKERS:
                data = market_data_service.fetch_historical_data(ticker, period=period)
                if data:
                    returns = data.returns
                    # Ensure timezone-naive index
                    if returns.index.tz is not None:
                        returns = returns.copy()
                        returns.index = returns.index.tz_localize(None)
                    result[ticker] = returns
        return result


# Singleton instance
risk_analytics = RiskAnalytics()
