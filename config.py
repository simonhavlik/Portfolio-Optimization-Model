"""
Portfolio Optimizer - Configuration Management
==============================================

Configuration module implementing the 12-Factor App methodology for 
environment-based configuration. Supports seamless switching between
SQLite (development) and PostgreSQL (production/Neon DB).

References:
-----------
- Wiggins, A. (2017). The Twelve-Factor App. https://12factor.net/
- NEON. (2024). Serverless PostgreSQL Documentation.
"""

import os
from dataclasses import dataclass, field
from typing import Optional

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass(frozen=True)
class Config:
    """
    Immutable configuration container implementing the Singleton pattern
    through module-level instantiation.
    """
    
    # Flask Core
    SECRET_KEY: str
    DEBUG: bool
    
    # Database
    DB_TYPE: str  # 'sqlite' or 'postgresql'
    SQLALCHEMY_DATABASE_URI: str
    SQLALCHEMY_TRACK_MODIFICATIONS: bool = False
    
    # LLM Configuration
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.deepseek.com/v1"
    LLM_MODEL: str = "deepseek-flash"
    LLM_TEMPERATURE: float = 0.7
    LLM_MAX_TOKENS: int = 0
    
    # Optimization Parameters
    DEFAULT_RISK_FREE_RATE: float = 0.05
    DEFAULT_CONFIDENCE_LEVEL: float = 0.95
    MAX_PORTFOLIO_ASSETS: int = 50
    
    # Feature Flags
    ENABLE_AI_INSIGHTS: bool = True
    
    # Email Configuration (Google SMTP)
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_USE_TLS: bool = True
    SMTP_USE_SSL: bool = False
    
    # Admin Configuration
    ADMIN_EMAIL: str = ""  # For registration approval notifications
    BASE_URL: str = "http://localhost:5000"  # For email links
    
    # Guest Session
    GUEST_SESSION_TIMEOUT: int = 3600  # 1 hour
    MAX_GUEST_SCENARIOS: int = 5  # Limit saved scenarios for guests
    
    # Neon DB Settings
    NEON_POOLER_MODE: bool = False  # Use PgBouncer/Pooler
    NEON_CONNECTION_TIMEOUT: int = 30
    NEON_STATEMENT_TIMEOUT: int = 30  # seconds
    ENABLE_CACHE: bool = True
    CACHE_TTL: int = 3600  # seconds


def _build_database_uri() -> tuple[str, str]:
    """
    Constructs database URI based on environment configuration.
    
    Returns:
        tuple: (db_type, connection_uri)
    """
    db_type = os.getenv('DB_TYPE', 'sqlite').lower()
    
    if db_type == 'postgresql':
        # Check for direct DATABASE_URL (Render, Heroku, Neon compatibility)
        database_url = os.getenv('DATABASE_URL')
        if database_url:
            # Handle Render/Heroku postgres:// vs postgresql://
            if database_url.startswith('postgres://'):
                database_url = database_url.replace('postgres://', 'postgresql://', 1)
            return db_type, database_url
        
        # Build from individual components
        host = os.getenv('POSTGRES_HOST', 'localhost')
        port = os.getenv('POSTGRES_PORT', '5432')
        db = os.getenv('POSTGRES_DB', 'portfolio_optimizer')
        user = os.getenv('POSTGRES_USER', 'postgres')
        password = os.getenv('POSTGRES_PASSWORD', '')
        ssl_mode = os.getenv('POSTGRES_SSL_MODE', 'prefer')
        
        uri = f"postgresql://{user}:{password}@{host}:{port}/{db}?sslmode={ssl_mode}"
        return db_type, uri
    
    else:  # Default to SQLite
        db_path = os.getenv('SQLITE_DB_PATH', 'data/portfolio_optimizer.db')
        # Convert to absolute path and ensure directory exists
        if not os.path.isabs(db_path):
            db_path = os.path.join(os.getcwd(), db_path)
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        return 'sqlite', f"sqlite:///{db_path}"


def load_config() -> Config:
    """
    Factory function for configuration instantiation.
    
    Loads configuration from environment variables following 12-Factor principles.
    """
    db_type, db_uri = _build_database_uri()
    
    # Validate required variables
    required = ['SECRET_KEY']
    missing = [var for var in required if not os.getenv(var)]
    if missing:
        # In development, use defaults with warning
        if os.getenv('FLASK_ENV') == 'development':
            print(f"WARNING: Missing required environment variables: {missing}")
        else:
            raise ValueError(f"Missing required environment variables: {missing}")
    
    return Config(
        SECRET_KEY=os.getenv('SECRET_KEY', 'dev-secret-key'),
        DEBUG=os.getenv('DEBUG', 'False').lower() == 'true',
        DB_TYPE=db_type,
        SQLALCHEMY_DATABASE_URI=db_uri,
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        LLM_API_KEY=os.getenv('LLM_API_KEY', ''),
        LLM_BASE_URL=os.getenv('LLM_BASE_URL', 'https://api.deepseek.com/v1'),
        LLM_MODEL=os.getenv('LLM_MODEL', 'deepseek-flash'),
        LLM_TEMPERATURE=float(os.getenv('LLM_TEMPERATURE', '0.7')),
        LLM_MAX_TOKENS=int(os.getenv('LLM_MAX_TOKENS', '0')),
        DEFAULT_RISK_FREE_RATE=float(os.getenv('DEFAULT_RISK_FREE_RATE', '0.05')),
        DEFAULT_CONFIDENCE_LEVEL=float(os.getenv('DEFAULT_CONFIDENCE_LEVEL', '0.95')),
        MAX_PORTFOLIO_ASSETS=int(os.getenv('MAX_PORTFOLIO_ASSETS', '50')),
        ENABLE_AI_INSIGHTS=os.getenv('ENABLE_AI_INSIGHTS', 'True').lower() == 'true',
        # Email settings
        SMTP_HOST=os.getenv('SMTP_HOST', 'smtp.gmail.com'),
        SMTP_PORT=int(os.getenv('SMTP_PORT', '587')),
        SMTP_USER=os.getenv('SMTP_USER', ''),
        SMTP_PASSWORD=os.getenv('SMTP_PASSWORD', ''),
        SMTP_USE_TLS=os.getenv('SMTP_USE_TLS', 'True').lower() == 'true',
        SMTP_USE_SSL=os.getenv('SMTP_USE_SSL', 'False').lower() == 'true',
        # Admin settings
        ADMIN_EMAIL=os.getenv('ADMIN_EMAIL', ''),
        BASE_URL=os.getenv('BASE_URL', 'http://localhost:5000'),
        # Guest settings
        GUEST_SESSION_TIMEOUT=int(os.getenv('GUEST_SESSION_TIMEOUT', '3600')),
        MAX_GUEST_SCENARIOS=int(os.getenv('MAX_GUEST_SCENARIOS', '5')),
        # Neon DB settings
        NEON_POOLER_MODE=os.getenv('NEON_POOLER_MODE', 'False').lower() == 'true',
        NEON_CONNECTION_TIMEOUT=int(os.getenv('NEON_CONNECTION_TIMEOUT', '30')),
        NEON_STATEMENT_TIMEOUT=int(os.getenv('NEON_STATEMENT_TIMEOUT', '30')),
        ENABLE_CACHE=os.getenv('ENABLE_CACHE', 'True').lower() == 'true',
        CACHE_TTL=int(os.getenv('CACHE_TTL', '3600')),
    )


# Module-level singleton instance
config = load_config()
