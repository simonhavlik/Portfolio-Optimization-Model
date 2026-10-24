"""
Portfolio Optimizer - Database Models
=====================================

SQLAlchemy ORM models implementing the repository pattern for 
portfolio data persistence. Supports both SQLite and PostgreSQL
through SQLAlchemy's abstraction layer.
"""

from datetime import datetime, date
from typing import Dict, List, Optional, Any
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import Index, event
import json

db = SQLAlchemy()


class Asset(db.Model):
    """Financial asset entity representing securities available for optimization."""
    
    __tablename__ = 'assets'
    
    id = db.Column(db.Integer, primary_key=True)
    ticker = db.Column(db.String(20), unique=True, nullable=False, index=True)
    name = db.Column(db.String(200))
    asset_class = db.Column(db.String(50), default='Equity')
    sector = db.Column(db.String(100))
    currency = db.Column(db.String(3), default='USD')
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    portfolio_holdings = db.relationship('PortfolioHolding', back_populates='asset', lazy='dynamic')
    
    def __repr__(self) -> str:
        return f"<Asset {self.ticker}: {self.name}>"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'ticker': self.ticker,
            'name': self.name,
            'asset_class': self.asset_class,
            'sector': self.sector,
            'currency': self.currency,
        }


class Portfolio(db.Model):
    """Portfolio entity representing a collection of assets with target allocations."""
    
    __tablename__ = 'portfolios'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # NULL for guest portfolios
    
    name = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    
    optimization_params = db.Column(JSONB().with_variant(db.JSON, "sqlite"), default=dict)
    
    target_return = db.Column(db.Float)
    risk_tolerance = db.Column(db.String(20), default='moderate')
    
    is_optimized = db.Column(db.Boolean, default=False)
    optimized_at = db.Column(db.DateTime)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    holdings = db.relationship('PortfolioHolding', back_populates='portfolio', 
                               cascade='all, delete-orphan', lazy='dynamic')
    optimization_results = db.relationship('OptimizationResult', back_populates='portfolio',
                                          cascade='all, delete-orphan', lazy='dynamic')
    history = db.relationship('PortfolioHistory', back_populates='portfolio',
                             cascade='all, delete-orphan', lazy='dynamic',
                             order_by='PortfolioHistory.snapshot_date.desc()')
    documents = db.relationship('PortfolioDocument', back_populates='portfolio',
                               cascade='all, delete-orphan', lazy='dynamic')
    
    def __repr__(self) -> str:
        return f"<Portfolio {self.name}: {self.holdings.count()} assets>"
    
    @property
    def total_weight(self) -> float:
        return sum(h.current_weight for h in self.holdings) or 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'target_return': self.target_return,
            'risk_tolerance': self.risk_tolerance,
            'is_optimized': self.is_optimized,
            'optimized_at': self.optimized_at.isoformat() if self.optimized_at else None,
            'created_at': self.created_at.isoformat(),
            'holdings': [h.to_dict() for h in self.holdings],
        }


class PortfolioHolding(db.Model):
    """Association entity linking Portfolio to Asset with weight information."""
    
    __tablename__ = 'portfolio_holdings'
    
    id = db.Column(db.Integer, primary_key=True)
    portfolio_id = db.Column(db.Integer, db.ForeignKey('portfolios.id'), nullable=False)
    asset_id = db.Column(db.Integer, db.ForeignKey('assets.id'), nullable=False)
    
    current_weight = db.Column(db.Float, default=0.0)
    target_weight = db.Column(db.Float)
    
    min_weight = db.Column(db.Float, default=0.0)
    max_weight = db.Column(db.Float, default=1.0)
    
    # Position data (for extracted portfolios)
    shares = db.Column(db.Float)
    cost_basis = db.Column(db.Float)
    purchase_date = db.Column(db.Date)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    portfolio = db.relationship('Portfolio', back_populates='holdings')
    asset = db.relationship('Asset', back_populates='portfolio_holdings')
    
    __table_args__ = (
        db.UniqueConstraint('portfolio_id', 'asset_id', name='uix_portfolio_asset'),
        Index('idx_portfolio_holdings_portfolio', 'portfolio_id'),
    )
    
    def __repr__(self) -> str:
        return f"<Holding {self.asset.ticker}: {self.current_weight:.2%}>"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'asset': self.asset.to_dict() if self.asset else None,
            'current_weight': self.current_weight,
            'target_weight': self.target_weight,
            'min_weight': self.min_weight,
            'max_weight': self.max_weight,
            'shares': self.shares,
            'cost_basis': self.cost_basis,
            'purchase_date': self.purchase_date.isoformat() if self.purchase_date else None,
        }


class PortfolioHistory(db.Model):
    """
    Time-series storage of portfolio risk metrics and composition.
    
    Enables tracking of portfolio risk evolution over time, supporting
    historical VaR analysis, drawdown tracking, and risk trend visualization.
    """
    
    __tablename__ = 'portfolio_history'
    
    id = db.Column(db.Integer, primary_key=True)
    portfolio_id = db.Column(db.Integer, db.ForeignKey('portfolios.id'), nullable=False)
    
    snapshot_date = db.Column(db.Date, nullable=False, index=True)
    
    # Risk Metrics
    portfolio_value = db.Column(db.Float)  # Total portfolio value
    volatility = db.Column(db.Float)  # Annualized volatility
    var_95 = db.Column(db.Float)  # Value at Risk (95% confidence)
    var_99 = db.Column(db.Float)  # Value at Risk (99% confidence)
    expected_return = db.Column(db.Float)  # Expected annual return
    sharpe_ratio = db.Column(db.Float)
    max_drawdown = db.Column(db.Float)  # Maximum drawdown
    beta = db.Column(db.Float)  # Market beta
    
    # Composition
    holdings_snapshot = db.Column(JSONB().with_variant(db.JSON, "sqlite"), default=dict)
    
    # Source
    source = db.Column(db.String(50), default='manual')  # manual, document_upload, api
    document_id = db.Column(db.Integer, db.ForeignKey('portfolio_documents.id'), nullable=True)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    portfolio = db.relationship('Portfolio', back_populates='history')
    document = db.relationship('PortfolioDocument', back_populates='history_snapshots')
    
    __table_args__ = (
        db.UniqueConstraint('portfolio_id', 'snapshot_date', name='uix_portfolio_history_date'),
        Index('idx_portfolio_history_date', 'portfolio_id', 'snapshot_date'),
    )
    
    def __repr__(self) -> str:
        return f"<PortfolioHistory {self.portfolio_id} @ {self.snapshot_date}: VaR95={self.var_95}>"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'snapshot_date': self.snapshot_date.isoformat(),
            'portfolio_value': self.portfolio_value,
            'volatility': self.volatility,
            'var_95': self.var_95,
            'var_99': self.var_99,
            'expected_return': self.expected_return,
            'sharpe_ratio': self.sharpe_ratio,
            'max_drawdown': self.max_drawdown,
            'beta': self.beta,
            'holdings_snapshot': self.holdings_snapshot,
            'source': self.source,
        }


class PortfolioDocument(db.Model):
    """
    Uploaded portfolio documents (PDF statements, CSV exports).
    
    Stores the raw file and extracted data for audit and re-processing.
    """
    
    __tablename__ = 'portfolio_documents'
    
    id = db.Column(db.Integer, primary_key=True)
    portfolio_id = db.Column(db.Integer, db.ForeignKey('portfolios.id'), nullable=False)
    
    filename = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(50))  # pdf, csv, xlsx
    file_size = db.Column(db.Integer)
    broker = db.Column(db.String(100))  # Detected broker
    
    # Extracted data
    extracted_data = db.Column(JSONB().with_variant(db.JSON, "sqlite"), default=dict)
    extraction_confidence = db.Column(db.Float)
    
    # Raw file storage (or path to storage)
    file_path = db.Column(db.String(500))
    
    statement_date = db.Column(db.Date)  # Date from document
    processed_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    portfolio = db.relationship('Portfolio', back_populates='documents')
    history_snapshots = db.relationship('PortfolioHistory', back_populates='document', lazy='dynamic')
    
    def __repr__(self) -> str:
        return f"<PortfolioDocument {self.filename}: {self.broker}>"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'filename': self.filename,
            'file_type': self.file_type,
            'broker': self.broker,
            'statement_date': self.statement_date.isoformat() if self.statement_date else None,
            'extraction_confidence': self.extraction_confidence,
            'processed_at': self.processed_at.isoformat(),
        }


class OptimizationResult(db.Model):
    """Immutable record of optimization execution results."""
    
    __tablename__ = 'optimization_results'
    
    id = db.Column(db.Integer, primary_key=True)
    portfolio_id = db.Column(db.Integer, db.ForeignKey('portfolios.id'), nullable=False)
    
    method = db.Column(db.String(50), nullable=False)
    version = db.Column(db.String(20), default='1.0')
    
    weights = db.Column(JSONB().with_variant(db.JSON, "sqlite"), nullable=False)
    metrics = db.Column(JSONB().with_variant(db.JSON, "sqlite"), nullable=False)
    
    ai_analysis = db.Column(db.Text)
    
    risk_free_rate = db.Column(db.Float)
    market_volatility = db.Column(db.Float)
    
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    portfolio = db.relationship('Portfolio', back_populates='optimization_results')
    
    def __repr__(self) -> str:
        return f"<Optimization {self.method} @ {self.created_at}>"
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            'id': self.id,
            'method': self.method,
            'weights': self.weights,
            'metrics': self.metrics,
            'ai_analysis': self.ai_analysis,
            'risk_free_rate': self.risk_free_rate,
            'market_volatility': self.market_volatility,
            'created_at': self.created_at.isoformat(),
        }


class MarketData(db.Model):
    """Cached historical market data for efficient retrieval."""
    
    __tablename__ = 'market_data'
    
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey('assets.id'), nullable=False)
    
    date = db.Column(db.Date, nullable=False)
    open_price = db.Column(db.Float)
    high_price = db.Column(db.Float)
    low_price = db.Column(db.Float)
    close_price = db.Column(db.Float, nullable=False)
    adjusted_close = db.Column(db.Float)
    volume = db.Column(db.BigInteger)
    
    data_source = db.Column(db.String(50), default='yahoo')
    fetched_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.UniqueConstraint('asset_id', 'date', name='uix_market_data_asset_date'),
        Index('idx_market_data_asset_date', 'asset_id', 'date'),
    )
    
    def __repr__(self) -> str:
        return f"<MarketData {self.asset_id} @ {self.date}: ${self.close_price:.2f}>"


# Event listeners for cross-database compatibility
@event.listens_for(Portfolio.optimization_params, 'set', retval=True)
def receive_set_jsonb(target, value, oldvalue, initiator):
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return value


@event.listens_for(OptimizationResult.weights, 'set', retval=True)
@event.listens_for(OptimizationResult.metrics, 'set', retval=True)
@event.listens_for(PortfolioHistory.holdings_snapshot, 'set', retval=True)
@event.listens_for(PortfolioDocument.extracted_data, 'set', retval=True)
def receive_set_jsonb_results(target, value, oldvalue, initiator):
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return value


class AppCache(db.Model):
    """Key/value cache persisted in Postgres so restarts keep computed data."""

    __tablename__ = 'app_cache'

    key = db.Column(db.String(80), primary_key=True)
    payload = db.Column(JSONB().with_variant(db.JSON, "sqlite"))
    computed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class ApiCredential(db.Model):
    """Active provider credential, switchable without a redeploy."""

    __tablename__ = 'api_credentials'

    id = db.Column(db.Integer, primary_key=True)
    provider = db.Column(db.String(40), unique=True, nullable=False)
    api_key = db.Column(db.Text, nullable=False)
    base_url = db.Column(db.String(255))
    model = db.Column(db.String(80))
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


def init_db(app):
    """Initialize database with Flask app context."""
    db.init_app(app)
    with app.app_context():
        try:
            db.create_all()
        except Exception:
            # Tables may already exist (race condition with multiple workers)
            pass
