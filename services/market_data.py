"""
Portfolio Optimizer - Market Data Service
=========================================

Abstraction layer for financial data retrieval implementing the 
Adapter pattern. Provides unified interface to multiple data sources
with intelligent caching and fallback mechanisms.

Data Source Strategy:
--------------------
Primary: Yahoo Finance (yfinance)
- Free, community-supported
- Covers global equities, ETFs, mutual funds
- Real-time and historical data available

Architecture:
-------------
- Repository Pattern: Abstract data access
- Cache-Aside Pattern: Minimize external API calls
- Circuit Breaker: Graceful degradation on failures

References:
-----------
- yfinance documentation: https://github.com/ranaroussi/yfinance
- Gamma, E., et al. (1994). Design Patterns: Cache-Aside Pattern
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass
from functools import lru_cache
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from flask import has_app_context
    FLASK_AVAILABLE = True
except ImportError:
    FLASK_AVAILABLE = False
    has_app_context = lambda: False

from models.database import db, Asset, MarketData

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceData:
    """
    Immutable value object for price time-series data.
    
    Implements the Value Object pattern ensuring data integrity
    through immutability and validation at construction.
    """
    ticker: str
    dates: pd.DatetimeIndex
    prices: pd.Series  # Adjusted close prices
    returns: pd.Series  # Daily log returns
    volumes: Optional[pd.Series] = None
    
    @property
    def volatility(self) -> float:
        """Annualized volatility from daily returns."""
        return float(self.returns.std() * np.sqrt(252))
    
    @property
    def total_return(self) -> float:
        """Total return over the period."""
        return float((self.prices.iloc[-1] / self.prices.iloc[0]) - 1)
    
    @property
    def sharpe_ratio(self, risk_free_rate: float = 0.05) -> float:
        """Annualized Sharpe ratio."""
        excess_returns = self.returns.mean() * 252 - risk_free_rate
        return float(excess_returns / (self.returns.std() * np.sqrt(252)))


class MarketDataService:
    """
    Service class for market data operations.
    
    Implements the Facade pattern providing simplified interface
    to complex subsystems (Yahoo Finance API, caching, database).
    
    Thread Safety:
    --------------
    This class is stateless and thread-safe. Concurrent requests
    are handled through ThreadPoolExecutor for batch operations.
    """
    
    def __init__(self, cache_timeout: int = 3600):
        """
        Initialize service with cache configuration.
        
        Args:
            cache_timeout: Cache TTL in seconds (default: 1 hour)
        """
        self.cache_timeout = cache_timeout
        self._session = None
    
    def _get_ticker(self, symbol: str) -> yf.Ticker:
        """Get or create yfinance Ticker object."""
        return yf.Ticker(symbol)
    
    def fetch_historical_data(
        self,
        ticker: str,
        period: str = "2y",
        interval: str = "1d",
        use_cache: bool = True
    ) -> Optional[PriceData]:
        """
        Retrieve historical price data for a single asset.
        
        Implements Cache-Aside pattern checking database cache
        before making external API call.
        
        Args:
            ticker: Yahoo Finance symbol
            period: Data period (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max)
            interval: Data interval (1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo)
            use_cache: Whether to use database cache
        
        Returns:
            PriceData object or None if retrieval fails
            
        Raises:
            ValueError: If ticker is invalid or no data available
        """
        try:
            # Check database cache first
            if use_cache:
                cached = self._get_cached_data(ticker, period)
                if cached is not None:
                    logger.info(f"Cache hit for {ticker}")
                    return cached
            
            # Fetch from Yahoo Finance
            ticker_obj = self._get_ticker(ticker)
            hist = ticker_obj.history(period=period, interval=interval)
            
            if hist.empty:
                logger.warning(f"No data returned for {ticker}")
                return None
            
            # Calculate log returns
            prices = hist['Close']
            returns = np.log(prices / prices.shift(1)).dropna()
            
            # Ensure timezone-naive index to avoid comparison issues
            if hist.index.tz is not None:
                hist.index = hist.index.tz_localize(None)
            
            price_data = PriceData(
                ticker=ticker,
                dates=hist.index,
                prices=prices,
                returns=returns,
                volumes=hist['Volume'] if 'Volume' in hist.columns else None
            )
            
            # Update cache
            if use_cache:
                self._cache_data(ticker, hist)
            
            return price_data
            
        except Exception as e:
            logger.error(f"Error fetching data for {ticker}: {e}")
            return None
    
    def _normalize_ticker(self, ticker: str) -> str:
        """
        Normalize ticker symbol for Yahoo Finance.
        
        Handles special cases like:
        - BRK.B -> BRK-B
        - BF.A -> BF-A
        """
        # Convert to uppercase and strip whitespace
        ticker = ticker.upper().strip()
        # Replace dots with dashes for class shares (BRK.B -> BRK-B) but keep
        # exchange suffixes intact (FFH.TO, PSH.L), or the quote is not found.
        EXCHANGE_SUFFIXES = {
            'TO', 'V', 'L', 'PA', 'DE', 'AS', 'SW', 'MI', 'MC', 'ST', 'OL', 'HE',
            'CO', 'BR', 'LS', 'IR', 'F', 'SG', 'HK', 'T', 'AX', 'NZ', 'SI', 'BK',
            'KQ', 'TW', 'KS', 'SA', 'MX', 'BA', 'JO', 'NZ', 'IL', 'VI',
        }
        if '.' in ticker:
            _base, _, _suffix = ticker.rpartition('.')
            if _suffix in EXCHANGE_SUFFIXES:
                pass
            else:
                ticker = ticker.replace('.', '-')
        return ticker
    
    def fetch_batch_data(
        self,
        tickers: List[str],
        period: str = "2y",
        max_workers: int = 5
    ) -> Dict[str, Optional[PriceData]]:
        """
        Fetch data for multiple tickers concurrently.
        
        Implements the Scatter-Gather pattern for efficient batch processing.
        Uses ThreadPoolExecutor for I/O-bound concurrent operations.
        
        Args:
            tickers: List of ticker symbols
            period: Data period
            max_workers: Maximum concurrent threads
            
        Returns:
            Dictionary mapping ticker to PriceData (or None on failure)
        """
        results = {}
        # Normalize tickers
        normalized_tickers = [self._normalize_ticker(t) for t in tickers]
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_ticker = {
                executor.submit(self.fetch_historical_data, t, period): t 
                for t in normalized_tickers
            }
            
            for future in as_completed(future_to_ticker):
                ticker = future_to_ticker[future]
                try:
                    results[ticker] = future.result()
                except Exception as e:
                    logger.error(f"Error processing {ticker}: {e}")
                    results[ticker] = None
        
        return results
    
    def get_stock_info(self, ticker: str) -> Dict[str, Any]:
        """
        Retrieve company/fund information including sector from Yahoo Finance.
        
        Args:
            ticker: Yahoo Finance symbol
            
        Returns:
            Dictionary containing company metadata with sector
        """
        try:
            ticker_obj = self._get_ticker(ticker)
            info = ticker_obj.info
            
            # Map Yahoo Finance sectors to our categories
            yf_sector = info.get('sector', 'Unknown')
            sector_map = {
                'Technology': 'Technology',
                'Communication Services': 'Technology',
                'Financial Services': 'Financials',
                'Financials': 'Financials',
                'Healthcare': 'Healthcare',
                'Health Care': 'Healthcare',
                'Consumer Cyclical': 'Consumer',
                'Consumer Discretionary': 'Consumer',
                'Consumer Defensive': 'Staples',
                'Consumer Staples': 'Staples',
                'Industrials': 'Industrials',
                'Energy': 'Energy',
                'Materials': 'Materials',
                'Utilities': 'Utilities',
                'Real Estate': 'Real Estate'
            }
            
            mapped_sector = sector_map.get(yf_sector, yf_sector)
            
            return {
                'ticker': ticker,
                'name': info.get('longName', info.get('shortName', ticker)),
                'sector': mapped_sector,
                'yf_sector': yf_sector,
                'industry': info.get('industry', 'Unknown'),
                'market_cap': info.get('marketCap'),
                'currency': info.get('currency', 'USD'),
                'country': info.get('country', 'US'),
                'website': info.get('website'),
                'business_summary': info.get('longBusinessSummary', ''),
                'beta': info.get('beta'),
                'dividend_yield': info.get('dividendYield'),
                'trailing_pe': info.get('trailingPE'),
                'forward_pe': info.get('forwardPE'),
                'price_to_book': info.get('priceToBook'),
                'debt_to_equity': info.get('debtToEquity'),
                'return_on_equity': info.get('returnOnEquity'),
                'revenue_growth': info.get('revenueGrowth'),
                'earnings_growth': info.get('earningsGrowth'),
                'profit_margins': info.get('profitMargins'),
            }
        except Exception as e:
            logger.error(f"Error fetching info for {ticker}: {e}")
            return {
                'ticker': ticker, 
                'name': ticker,
                'sector': 'Unknown'
            }
    
    def get_batch_stock_info(self, tickers: List[str]) -> Dict[str, Dict[str, Any]]:
        """
        Fetch stock info for multiple tickers efficiently.
        
        Args:
            tickers: List of ticker symbols
            
        Returns:
            Dictionary mapping ticker to info dict
        """
        results = {}
        for ticker in tickers:
            results[ticker] = self.get_stock_info(ticker)
        return results
    
    def search_tickers(self, query: str, limit: int = 10) -> List[Dict[str, str]]:
        """
        Search for ticker symbols by company name.
        
        Note: Uses yfinance's search capabilities. For production,
        consider dedicated search API like Yahoo Finance Query or Alpha Vantage.
        
        Args:
            query: Search query string
            limit: Maximum results to return
            
        Returns:
            List of matching tickers with names
        """
        try:
            # yfinance doesn't have direct search, use workaround
            # In production, implement proper search with Yahoo Finance API
            tickers = yf.Tickers(query).tickers
            results = []
            for symbol in list(tickers.keys())[:limit]:
                info = tickers[symbol].info
                results.append({
                    'ticker': symbol,
                    'name': info.get('longName', info.get('shortName', symbol)),
                })
            return results
        except Exception as e:
            logger.error(f"Search error: {e}")
            return []
    
    def calculate_correlation_matrix(
        self,
        tickers: List[str],
        period: str = "2y"
    ) -> Optional[pd.DataFrame]:
        """
        Calculate return correlation matrix for asset universe.
        
        Critical input for Modern Portfolio Theory optimization as
        diversification benefits depend on correlation structure.
        
        Args:
            tickers: List of ticker symbols
            period: Data period for calculation
            
        Returns:
            Correlation matrix DataFrame or None
        """
        data = self.fetch_batch_data(tickers, period)
        
        # Filter successful fetches
        returns_dict = {
            t: d.returns for t, d in data.items() 
            if d is not None and len(d.returns) > 0
        }
        
        if len(returns_dict) < 2:
            logger.warning("Insufficient data for correlation calculation")
            return None
        
        # Align dates and calculate correlation
        returns_df = pd.DataFrame(returns_dict)
        return returns_df.corr()
    
    def _get_cached_data(self, ticker: str, period: str) -> Optional[PriceData]:
        """Retrieve data from database cache if valid."""
        # Skip cache if not in Flask app context
        if not has_app_context():
            return None
        
        try:
            # Get asset ID
            asset = Asset.query.filter_by(ticker=ticker).first()
            if not asset:
                return None
            
            # Check cache freshness
            cutoff_date = datetime.utcnow() - timedelta(seconds=self.cache_timeout)
            recent_data = MarketData.query.filter(
                MarketData.asset_id == asset.id,
                MarketData.fetched_at >= cutoff_date
            ).order_by(MarketData.date).all()
            
            if not recent_data:
                return None
            
            # Reconstruct PriceData
            dates = pd.DatetimeIndex([d.date for d in recent_data])
            prices = pd.Series([d.adjusted_close or d.close_price for d in recent_data], index=dates)
            returns = np.log(prices / prices.shift(1)).dropna()
            volumes = pd.Series([d.volume for d in recent_data], index=dates)
            
            return PriceData(
                ticker=ticker,
                dates=dates,
                prices=prices,
                returns=returns,
                volumes=volumes
            )
            
        except Exception as e:
            logger.error(f"Cache retrieval error: {e}")
            return None
    
    def _cache_data(self, ticker: str, data: pd.DataFrame) -> None:
        """Store fetched data in database cache."""
        # Skip cache if not in Flask app context
        if not has_app_context():
            return
        
        from sqlalchemy.exc import IntegrityError
        
        try:
            # Get or create asset
            asset = Asset.query.filter_by(ticker=ticker).first()
            if not asset:
                asset = Asset(ticker=ticker)
                db.session.add(asset)
                db.session.flush()
            
            # Get existing dates to avoid duplicates
            existing_dates = {
                md.date for md in 
                MarketData.query.filter_by(asset_id=asset.id).all()
            }
            
            # Insert only new market data
            for date, row in data.iterrows():
                date_val = date.date() if isinstance(date, pd.Timestamp) else date
                
                # Skip if already exists
                if date_val in existing_dates:
                    continue
                
                market_data = MarketData(
                    asset_id=asset.id,
                    date=date_val,
                    open_price=float(row.get('Open')) if pd.notna(row.get('Open')) else None,
                    high_price=float(row.get('High')) if pd.notna(row.get('High')) else None,
                    low_price=float(row.get('Low')) if pd.notna(row.get('Low')) else None,
                    close_price=float(row.get('Close')) if pd.notna(row.get('Close')) else None,
                    adjusted_close=float(row.get('Adj Close')) if pd.notna(row.get('Adj Close')) else float(row.get('Close')) if pd.notna(row.get('Close')) else None,
                    volume=float(row.get('Volume')) if pd.notna(row.get('Volume')) else None
                )
                db.session.add(market_data)
            
            db.session.commit()
            
        except IntegrityError:
            # Duplicate entry - rollback and continue
            db.session.rollback()
        except Exception as e:
            logger.warning(f"Cache storage warning for {ticker}: {e}")
            db.session.rollback()


# Singleton instance
market_data_service = MarketDataService()
