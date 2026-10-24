"""
Portfolio Optimizer - Flask Application
=======================================

Main application entry point implementing RESTful API and web interface
for portfolio optimization. Uses Flask with SQLAlchemy for ORM and
Jinja2 templating with Tailwind CSS for the frontend.

Architecture:
-------------
- Layered Architecture: Routes → Services → Models
- Repository Pattern: Data access abstraction
- Dependency Injection: Configurable services via config module

Routes:
-------
- /: Main dashboard
- /upload: Document upload interface
- /api/optimize: Portfolio optimization endpoint
- /api/upload: Document upload and parsing
- /api/portfolios/<id>/history: Risk history tracking
- /api/portfolios/<id>/risk-timeline: Risk evolution charts

References:
-----------
- Flask Documentation: https://flask.palletsprojects.com/
- RESTful API Design: https://restfulapi.net/
"""

import os
import json
import math
import logging
from datetime import datetime, date, timedelta
from typing import Dict, Any, List
from functools import wraps

from flask import Flask, render_template, request, jsonify, abort, session, redirect, send_file
from flask_cors import CORS
from flask_login import LoginManager, current_user, login_required, login_user
from werkzeug.utils import secure_filename
import pandas as pd
import numpy as np

from config import config
from models.database import (
    db, init_db, Portfolio, Asset, PortfolioHolding, OptimizationResult,
    PortfolioHistory, PortfolioDocument, AppCache, SavedReport
)
from services.market_data import market_data_service
from services.optimization import (
    PortfolioOptimizer, OptimizationMethod, OptimizationConstraints, OptimizationResult as OptResult
)
from services.ai_insights import ai_service, PortfolioContext
from services.document_parser import document_parser, ParsedPortfolio
from services.portfolio_extractor import extract as extract_portfolio
from services.rate_limit import rate_limit
from services.valuation import portfolio_valuation
from services.report_appendix import build_appendix
from services.example_portfolio import serialize_for_template, _finite_json
from uuid import uuid4
from services import cca as cca_service
from services.risk_analytics import risk_analytics, RiskMetrics
from services.advanced_statistics import advanced_statistics
from services.advanced_risk import AdvancedRiskCalculator, calculate_hierarchical_risk_parity
from services.factor_models import FamaFrenchModel, StatisticalFactorModel
from services.black_litterman import BlackLittermanModel, create_tactical_views
from services.covariance_estimators import LedoitWolfShrinkage, compare_estimators
from services.regime_detection import HiddenMarkovModel, detect_market_regimes, TrendFollowingFilter
from services.dashboard_charts import dashboard_charts, BENCHMARK_TICKERS
from services.dashboard_tables import dashboard_tables
from services.example_portfolio import example_portfolio_service
from services.email_service import email_service

# Import auth routes
from routes.auth import auth_bp, login_required, admin_required

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def sanitize_for_json(obj: Any) -> Any:
    """
    Recursively sanitize data for JSON serialization.
    
    Converts NaN, Infinity, -Infinity to None (null in JSON).
    JavaScript's JSON.parse cannot handle NaN values.
    """
    if isinstance(obj, dict):
        return {k: sanitize_for_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [sanitize_for_json(item) for item in obj]
    elif isinstance(obj, float):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return obj
    elif isinstance(obj, np.floating):
        if np.isnan(obj) or np.isinf(obj):
            return None
        return float(obj)
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.ndarray):
        return sanitize_for_json(obj.tolist())
    elif isinstance(obj, pd.Series):
        return sanitize_for_json(obj.tolist())
    elif isinstance(obj, pd.DataFrame):
        return sanitize_for_json(obj.to_dict(orient='records'))
    elif isinstance(obj, (datetime, date)):
        return obj.isoformat()
    else:
        return obj

# Upload configuration
UPLOAD_FOLDER = 'data/uploads'
ALLOWED_EXTENSIONS = {'pdf', 'csv', 'xlsx', 'xls', 'docx', 'png', 'jpg', 'jpeg', 'webp'}

# Initialize Flask app
def create_app() -> Flask:
    """Application factory pattern for Flask initialization."""
    app = Flask(__name__)
    
    # Load configuration
    app.config['SECRET_KEY'] = config.SECRET_KEY
    app.config['SQLALCHEMY_DATABASE_URI'] = config.SQLALCHEMY_DATABASE_URI
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = config.SQLALCHEMY_TRACK_MODIFICATIONS
    app.config['DEBUG'] = config.DEBUG
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
    
    # Ensure upload directory exists
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    
    # Initialize extensions
    CORS(app, supports_credentials=True)
    init_db(app)
    
    # Initialize Flask-Login
    login_manager = LoginManager()
    login_manager.init_app(app)
    login_manager.login_view = 'auth.login'
    login_manager.login_message = 'Please log in to access this page.'
    
    @login_manager.user_loader
    def load_user(user_id):
        from models.auth import User
        try:
            user = User.query.get(int(user_id))
            if user:
                logger.debug(f"User loaded: {user.email}")
            return user
        except Exception as e:
            logger.error(f"Error loading user {user_id}: {e}")
            return None
    
    # Register blueprints
    app.register_blueprint(auth_bp)
    
    # Context processor to make current_user available in all templates
    @app.context_processor
    def inject_user():
        from flask_login import current_user
        return dict(current_user=current_user)
    
    # Debug: Log authentication status for each request (only in debug mode)
    @app.before_request
    def sync_login_identity():
        if request.endpoint == 'static' or current_user.is_authenticated:
            return None
        user_id = session.get('user_id')
        if user_id:
            from models.auth import User
            user = User.query.get(user_id)
            if user and user.is_active:
                login_user(user)
        return None

    @app.before_request
    def canonical_host():
        host = request.host.split(':')[0].lower()
        if host == 'optimizer.havliksimon.eu':
            suffix = request.full_path.rstrip('?')
            return redirect(f'https://optimizer.simonhavlik.com{suffix}', code=301)

    @app.before_request
    def log_auth_status():
        if not config.DEBUG:
            return
        from flask_login import current_user
        from flask import request, session
        if request.endpoint and not request.endpoint.startswith('static'):
            if current_user.is_authenticated:
                logger.debug(f"Auth OK: {current_user.email} accessing {request.endpoint}")
            else:
                logger.debug(f"No auth for {request.endpoint}")
    
    # Initialize example portfolio on startup (computes once, caches to disk)
    with app.app_context():
        try:
            from services.api_keys import seed_from_env
            seed_from_env()
        except Exception as e:
            logger.warning(f"Could not seed API credential: {e}")

        try:
            from services.example_portfolio import initialize_example_portfolio
            initialize_example_portfolio()
            logger.info("Example portfolio service initialized")

            from services.report_charts import generate as generate_report_charts
            analysis = example_portfolio_service.get_analysis()
            generate_report_charts(analysis)
            logger.info("Report chart images ready")

            import threading
            def _warm_cca():
                with app.app_context():
                    try:
                        cca_service.build_and_cache(analysis.get('holdings', []))
                        logger.info("CCA cache ready")
                    except Exception as exc:
                        logger.warning(f"CCA warm-up failed: {exc}")
            threading.Thread(target=_warm_cca, daemon=True).start()
        except Exception as e:
            logger.warning(f"Could not initialize example portfolio service: {e}")
    
    return app

app = create_app()


def page_login_required(view):
    """Redirect browsers to the sign-in page instead of returning JSON 401."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(f"/auth/login?next={request.path}")
        return view(*args, **kwargs)
    return wrapped


def page_admin_required(view):
    """Admin-only HTML pages: sign-in redirect, then a real 403."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(f"/auth/login?next={request.path}")
        from models.auth import User
        user = User.query.get(session['user_id'])
        if not user or not user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def allowed_file(filename):
    """Check if file extension is allowed."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


# =============================================================================
# Web Routes (HTML Views)
# =============================================================================

@app.route('/')
def index():
    """Main dashboard view with example portfolio analysis."""
    try:
        # Get pre-computed example portfolio analysis
        # Data is already serialized to Python dicts by the service
        example_data = example_portfolio_service.get_analysis()
        
        valuation = None
        appendix = None
        try:
            valuation = portfolio_valuation(example_data.get('holdings', []),
                                            example_data.get('metrics', {}).get('beta', 1.0))
        except Exception as exc:
            logger.warning(f"Valuation unavailable: {exc}")
        try:
            appendix = build_appendix(example_data)
        except Exception as exc:
            logger.warning(f"Appendix unavailable: {exc}")
        try:
            cca = cca_service.cached_cca()
        except Exception:
            cca = None

        return render_template('index.html',
                             example_data=example_data,
                             valuation=valuation,
                             appendix=appendix,
                             cca=cca,
                             show_analysis=True)
    except Exception as e:
        import traceback
        logger.error(f"Error loading example portfolio: {e}")
        traceback.print_exc()
        return render_template('index.html',
                             example_data=None,
                             show_analysis=False)


def _json_safe(value):
    """Replace non-finite floats so the payload is valid JSON in browsers."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


@app.route('/api/example-charts')
def example_charts():
    """Chart payload for the example portfolio, fetched after first paint."""
    report = request.args.get('report')
    try:
        if report:
            row = db.session.get(AppCache, f'custom_analysis:{report}')
            if not row:
                return jsonify({'error': 'unknown report'}), 404
            example_data = row.payload
        else:
            example_data = example_portfolio_service.get_analysis()
    except Exception as e:
        logger.error(f"Error loading portfolio charts: {e}")
        return jsonify({'error': 'unavailable'}), 503

    if not example_data or not example_data.get('charts'):
        return jsonify({'error': 'not ready'}), 503

    payload = {
        key: example_data.get(key)
        for key in (
            'meta', 'metrics', 'holdings', 'monte_carlo', 'correlation_matrix',
            'advanced_risk', 'stress_test', 'distributions_dict',
            'factor_exposure', 'ai_insights', 'statistical_tests',
        )
        if example_data.get(key) is not None
    }
    payload['charts'] = example_data['charts']

    response = jsonify(_json_safe(payload))
    response.headers['Cache-Control'] = 'public, max-age=300'
    return response



@app.route('/api/analyse', methods=['POST'])
@login_required
@rate_limit('analyse', 6, 3600)
def analyse_custom():
    """Run the full report engine for user-supplied holdings."""
    data = request.get_json() or {}
    holdings = data.get('holdings') or []
    clean = []
    for holding in holdings:
        ticker = str(holding.get('ticker') or '').upper().strip()
        weight = holding.get('weight')
        if ticker and isinstance(weight, (int, float)) and weight > 0:
            clean.append({'ticker': ticker, 'weight': float(weight), 'sector': holding.get('sector')})
    if len(clean) < 2:
        return jsonify({'error': 'At least two holdings are required'}), 400
    if len(clean) > 50:
        return jsonify({'error': 'Maximum 50 holdings'}), 400

    period = str(data.get('period') or '2y').lower()
    if period not in ('6mo', '1y', '2y', '3y', '5y', '10y', 'max'):
        period = '2y'
    try:
        risk_free = float(data.get('risk_free_rate', 0.045))
    except (TypeError, ValueError):
        risk_free = 0.045
    risk_free = min(max(risk_free, 0.0), 0.25)
    benchmark = str(data.get('benchmark') or 'SPY').upper().strip()[:8] or 'SPY'
    if not benchmark.isalnum():
        benchmark = 'SPY'
    try:
        n_sims = int(data.get('n_simulations') or 5000)
    except (TypeError, ValueError):
        n_sims = 5000
    n_sims = min(max(n_sims, 200), 20000)
    try:
        horizon_days = int(data.get('horizon_days') or 252)
    except (TypeError, ValueError):
        horizon_days = 252
    horizon_days = min(max(horizon_days, 21), 504)

    try:
        analysis = example_portfolio_service.compute_custom(
            clean,
            period=period,
            name=data.get('name') or 'Your portfolio',
            description=data.get('description') or '',
            risk_free_rate=risk_free,
            benchmark=benchmark,
            n_simulations=n_sims,
            horizon_days=horizon_days,
        )
    except Exception as exc:
        logger.error(f"Custom analysis failed: {exc}")
        return jsonify({'error': str(exc)}), 500

    token = uuid4().hex
    payload = _finite_json(serialize_for_template(analysis))
    db.session.add(AppCache(key=f'custom_analysis:{token}', payload=payload,
                            computed_at=datetime.utcnow()))

    metrics = payload.get('metrics') or {}
    db.session.add(SavedReport(
        user_id=session['user_id'], token=token,
        name=(data.get('name') or 'Your portfolio')[:120],
        period=period,
        holdings=clean,
        summary={
            'annualized_return': metrics.get('annualized_return'),
            'volatility': metrics.get('volatility'),
            'sharpe_ratio': metrics.get('sharpe_ratio'),
            'max_drawdown': metrics.get('max_drawdown'),
            'n_assets': len(clean),
        },
    ))
    db.session.commit()
    return jsonify({'success': True, 'report_url': f'/report/{token}', 'token': token})


@app.route('/report/<token>')
@page_login_required
def custom_report(token):
    row = db.session.get(AppCache, f'custom_analysis:{token}')
    if not row or not row.payload:
        abort(404)
    example_data = row.payload
    valuation = None
    appendix = None
    cca = None
    try:
        valuation = portfolio_valuation(example_data.get('holdings', []),
                                        example_data.get('metrics', {}).get('beta', 1.0))
    except Exception as exc:
        logger.warning(f"Custom valuation unavailable: {exc}")
    try:
        appendix = build_appendix(example_data)
    except Exception as exc:
        logger.warning(f"Custom appendix unavailable: {exc}")
    try:
        cca = cca_service.build_cca(example_data.get('holdings', []), None)
    except Exception as exc:
        logger.warning(f"Custom CCA unavailable: {exc}")
    return render_template('index.html', example_data=example_data,
                           valuation=valuation, appendix=appendix, cca=cca,
                           report_id=token, show_analysis=True)



LEADERBOARD_ADMIN_EMAIL = 'havlik.simon@post.cz'
_lb_refresh_state = {'running': False, 'started': None, 'finished': None, 'error': None}


def _leaderboard_admin_ok() -> bool:
    try:
        return bool(current_user.is_authenticated and
                    (current_user.email or '').strip().lower() == LEADERBOARD_ADMIN_EMAIL)
    except Exception:
        return False


@app.route('/leaderboard')
def leaderboard_view():
    """Cross-sectional fund ranking, cached for a week."""
    from services.leaderboard import build_board
    board = build_board()
    return render_template('leaderboard.html', board=board)


@app.route('/leaderboard.csv')
def leaderboard_csv():
    import csv as _csv
    import io as _io
    from services.leaderboard import build_board

    board = build_board()
    metrics = [m['key'] for m in board.get('metrics', [])]
    buf = _io.StringIO()
    writer = _csv.writer(buf)
    writer.writerow(['rank', 'fund', 'manager', 'kind', 'score'] + metrics +
                    ['covid_2020', 'bear_2022', 'holdings', 'coverage', 'filing', 'observations'])
    for index, row in enumerate(board.get('rows', []), start=1):
        m = row.get('metrics') or {}
        writer.writerow([index, row['name'], row['manager'], row['kind'], row.get('score')] +
                        [m.get(k) for k in metrics] +
                        [row.get('crisis', {}).get('covid'), row.get('crisis', {}).get('y2022'),
                         row.get('holdings'), row.get('coverage'), row.get('filing'), row.get('observations')])
    stream = _io.BytesIO(buf.getvalue().encode('utf-8'))
    return send_file(stream, mimetype='text/csv', as_attachment=True,
                     download_name=f"fund-leaderboard-{board.get('as_of', 'latest')}.csv")


@app.route('/leaderboard/card.png')
def leaderboard_card_image():
    from services.leaderboard import build_board
    from services.leaderboard_card import render_card

    board = build_board()
    fmt = 'landscape' if request.args.get('format') == 'landscape' else 'square'
    stream = render_card(board, fmt)
    return send_file(stream, mimetype='image/png', as_attachment=False,
                     download_name=f"fund-leaderboard-{fmt}-{board.get('as_of', 'latest')}.png")


@app.route('/leaderboard/admin')
@login_required
def leaderboard_admin():
    if not _leaderboard_admin_ok():
        abort(403)
    from services.leaderboard import build_board
    from services import edgar_13f
    from services.leaderboard_funds import FUNDS

    board = build_board()
    rows = {row['slug']: row for row in board.get('rows', [])}
    funds = []
    for fund in FUNDS:
        row = rows.get(fund['slug'], {})
        latest_filing = None
        if fund['kind'] == '13f':
            try:
                filings = edgar_13f.fund_filings(fund['cik'], limit=1)
                latest_filing = filings[0]['period'] if filings else None
            except Exception:
                latest_filing = None
        funds.append({
            'slug': fund['slug'],
            'name': fund['name'],
            'kind': fund['kind'],
            'used': row.get('filing'),
            'latest': latest_filing,
            'stale': bool(latest_filing and row.get('filing') and latest_filing > row['filing']),
            'holdings': row.get('holdings'),
            'coverage': row.get('coverage'),
            'score': row.get('score'),
        })
    return render_template('leaderboard_admin.html', board=board, funds=funds,
                           state=_lb_refresh_state, admin_email=LEADERBOARD_ADMIN_EMAIL)


@app.route('/leaderboard/admin/refresh', methods=['POST'])
@login_required
def leaderboard_admin_refresh():
    if not _leaderboard_admin_ok():
        abort(403)
    if _lb_refresh_state['running']:
        return jsonify({'ok': False, 'message': 'A refresh is already running'}), 409
    import threading
    from services.leaderboard import build_board

    def run():
        _lb_refresh_state.update(running=True, started=datetime.utcnow().isoformat(),
                                 finished=None, error=None)
        try:
            build_board(force=True)
            _lb_refresh_state['finished'] = datetime.utcnow().isoformat()
        except Exception as exc:
            _lb_refresh_state['error'] = str(exc)
        finally:
            _lb_refresh_state['running'] = False

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'ok': True, 'message': 'Refresh started'})


@app.route('/leaderboard/admin/status')
@login_required
def leaderboard_admin_status():
    if not _leaderboard_admin_ok():
        abort(403)
    return jsonify(_lb_refresh_state)


@app.route('/reports')
@page_login_required
def reports_view():
    return render_template('reports.html')


@app.route('/api/reports', methods=['GET'])
@login_required
def list_reports():
    rows = (SavedReport.query.filter_by(user_id=session['user_id'])
            .order_by(SavedReport.created_at.desc()).limit(50).all())
    return jsonify({'reports': [{
        'token': row.token, 'name': row.name, 'period': row.period,
        'created_at': row.created_at.isoformat(), 'summary': row.summary,
        'holdings': row.holdings,
    } for row in rows]})


@app.route('/api/reports/<token>', methods=['DELETE'])
@login_required
def delete_report(token):
    row = SavedReport.query.filter_by(token=token, user_id=session['user_id']).first_or_404()
    cache = db.session.get(AppCache, f'custom_analysis:{token}')
    if cache:
        db.session.delete(cache)
    db.session.delete(row)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/report/xlsx')
def report_xlsx():
    """Excel workbook with the analysis in sell-side layout."""
    from services.xlsx_report import build as build_xlsx

    report = request.args.get('report')
    if report:
        row = db.session.get(AppCache, f'custom_analysis:{report}')
        example_data = row.payload if row else None
    else:
        example_data = example_portfolio_service.get_analysis()
    if not example_data:
        return jsonify({'error': 'Analysis unavailable'}), 503
    valuation = portfolio_valuation(example_data.get('holdings', []),
                                    example_data.get('metrics', {}).get('beta', 1.0))
    from services.cca import cached_cca
    stream = build_xlsx(example_data, valuation, cached_cca())
    filename = f"portfolio-analysis-{date.today().isoformat()}.xlsx"
    return send_file(stream, as_attachment=True, download_name=filename,
                     mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')



@app.route('/api/report/csv')
def report_csv():
    """Daily time series as CSV, for spreadsheet work outside the workbook."""
    import csv as _csv
    import io as _io

    report = request.args.get('report')
    if report:
        row = db.session.get(AppCache, f'custom_analysis:{report}')
        example_data = row.payload if row else None
    else:
        example_data = example_portfolio_service.get_analysis()
    if not example_data:
        return jsonify({'error': 'Analysis unavailable'}), 503

    charts = example_data.get('charts') or {}
    vol = charts.get('volatility') or {}
    dates = vol.get('dates') or []
    rets = vol.get('returns') or []
    if not dates or not rets:
        return jsonify({'error': 'Time series unavailable'}), 404

    bench_map = example_data.get('benchmarks') or {}
    bench = bench_map.get('SPY') or bench_map.get('VT') or []
    bench_name = 'SPY' if bench_map.get('SPY') else ('VT' if bench_map.get('VT') else '')

    buf = _io.StringIO()
    writer = _csv.writer(buf)
    head = ['date', 'daily_return', 'growth_of_100', 'drawdown']
    if bench:
        head += [f'{bench_name}_index', 'relative']
    writer.writerow(head)
    level = 1.0
    peak = 1.0
    for i, (day, ret) in enumerate(zip(dates, rets)):
        level *= (1 + (ret or 0))
        peak = max(peak, level)
        row_out = [day, f'{ret:.8f}', f'{level * 100:.6f}', f'{level / peak - 1:.8f}']
        if bench:
            pos = (i / max(1, len(rets) - 1)) * (len(bench) - 1)
            lo = int(pos)
            hi = min(len(bench) - 1, lo + 1)
            bval = bench[lo] + (bench[hi] - bench[lo]) * (pos - lo)
            row_out += [f'{bval * 100:.6f}', f'{level / bval - 1:.8f}']
        writer.writerow(row_out)

    stream = _io.BytesIO(buf.getvalue().encode('utf-8'))
    name = f"portfolio-{(report or 'example')[:8]}-timeseries.csv"
    return send_file(stream, as_attachment=True, download_name=name, mimetype='text/csv')


@app.route('/api/upload-text', methods=['POST'])
@rate_limit('upload_hour', 2, 3600)
@login_required
def upload_text():
    """Analyse holdings pasted as text."""
    from services.rate_limit import allow
    from services.portfolio_extractor import extract_text

    if not allow(f"weekly:user:{session['user_id']}", 3, 604800):
        return jsonify({
            'error': 'Weekly limit reached (3 analyses). Email me@simonhavlik.com for more.',
            'contact': 'me@simonhavlik.com',
        }), 429

    data = request.get_json() or {}
    text = (data.get('text') or '').strip()
    if len(text) < 8:
        return jsonify({'error': 'Paste a few holdings first'}), 400

    try:
        parsed = extract_text(text)
    except Exception as exc:
        logger.warning(f"Paste parsing failed: {exc}")
        return jsonify({'error': 'Could not read holdings from that text'}), 400

    if not parsed.holdings:
        return jsonify({'error': 'No holdings recognised'}), 400

    risk_metrics = None
    tickers = [h.ticker for h in parsed.holdings if h.ticker]
    if len(tickers) >= 2:
        risk_metrics = calculate_portfolio_risk_metrics(tickers, date.today())

    portfolio = Portfolio(user_id=session.get('user_id'), name='Pasted portfolio',
                          description='Holdings pasted as text')
    db.session.add(portfolio)
    db.session.flush()

    total = parsed.account_value or sum((h.market_value or 0) for h in parsed.holdings) or 100000
    for holding in parsed.holdings:
        asset = Asset.query.filter_by(ticker=holding.ticker).first()
        if not asset:
            asset = Asset(ticker=holding.ticker, name=holding.name or holding.ticker,
                          asset_class=holding.asset_class or 'Equity',
                          sector=holding.asset_class)
            db.session.add(asset)
            db.session.flush()
        weight = (holding.market_value or 0) / total if total else 0
        db.session.add(PortfolioHolding(portfolio_id=portfolio.id, asset_id=asset.id,
                                        current_weight=weight, shares=holding.shares))

    if risk_metrics:
        db.session.add(PortfolioHistory(
            portfolio_id=portfolio.id, snapshot_date=date.today(),
            portfolio_value=total, **risk_metrics,
            holdings_snapshot={h.ticker: {'value': h.market_value} for h in parsed.holdings},
            source='paste'))
    db.session.commit()

    positions = []
    for holding in parsed.holdings:
        weight = (holding.market_value or 0) / total if total else 0
        positions.append({'ticker': holding.ticker, 'weight': round(weight, 4),
                          'sector': holding.asset_class})
    review_metrics = {}
    if risk_metrics:
        review_metrics = {
            'annualized_return': round(risk_metrics.get('expected_return', 0), 4),
            'volatility': round(risk_metrics.get('volatility', 0), 4),
            'sharpe': round(risk_metrics.get('sharpe_ratio', 0), 3),
            'max_drawdown': round(risk_metrics.get('max_drawdown', 0), 4),
            'var_95': round(risk_metrics.get('var_95', 0), 4),
        }
    review = ''
    try:
        review = ai_service.generate_portfolio_review(positions, review_metrics)
    except Exception as exc:
        logger.warning(f"Paste review unavailable: {exc}")

    return jsonify({
        'success': True,
        'portfolio_id': portfolio.id,
        'extracted_data': parsed.to_dict(),
        'ai_review': review,
        'message': f"Extracted {len(parsed.holdings)} holdings",
    })


@app.route('/portfolio/<int:portfolio_id>')
def portfolio_detail(portfolio_id):
    """Portfolio detail view."""
    return render_template('portfolio.html', portfolio_id=portfolio_id)


@app.route('/optimize')
@page_login_required
def optimize_view():
    """Portfolio optimization interface."""
    return render_template('optimize.html')


@app.route('/analysis')
@page_login_required
def analysis_view():
    """AI analysis view."""
    return render_template('analysis.html')


@app.route('/analysis-comprehensive')
@page_login_required
def analysis_comprehensive_view():
    """Legacy analytics input: superseded by the saved-reports page."""
    return redirect('/reports')


@app.route('/upload')
def upload_view():
    """Document upload interface; visitors see an account invite."""
    return render_template('upload.html')


@app.route('/privacy')
def privacy_view():
    """Privacy policy."""
    return render_template('privacy.html')


@app.route('/auth/login')
def login_view():
    """Login page."""
    return render_template('auth/login.html')


@app.route('/auth/register')
def register_view():
    """Registration page."""
    return render_template('auth/register.html')


@app.route('/auth/forgot-password')
def forgot_password_view():
    """Forgot password page."""
    return render_template('auth/forgot_password.html')


@app.route('/auth/reset-password/<token>')
def reset_password_view(token):
    """Reset password page."""
    return render_template('auth/reset_password.html', token=token)


@app.route('/auth/logout')
def logout_view():
    """Log out user and redirect to home."""
    from flask import session, redirect, url_for
    session.clear()
    return redirect('/')


@app.route('/profile')
def profile_view():
    """User profile page - requires login."""
    from flask_login import login_required, current_user
    from models.auth import User
    
    # Check if user is logged in via session
    if 'user_id' not in session:
        return redirect('/auth/login')
    
    user = User.query.get(session['user_id'])
    if not user:
        session.clear()
        return redirect('/auth/login')
    
    return render_template('auth/profile.html', user=user)


@app.route('/admin')
@page_admin_required
def admin_view():
    """Admin dashboard."""
    from models.auth import User
    from models.database import db
    
    all_users = User.query.order_by(User.created_at.desc()).all()
    pending_users = [u for u in all_users if u.status == 'pending']
    
    stats = {
        'total_users': len(all_users),
        'pending_users': len(pending_users),
        'active_users': len([u for u in all_users if u.status == 'active']),
        'suspended_users': len([u for u in all_users if u.status == 'suspended'])
    }
    
    return render_template('auth/admin.html', 
                         all_users=all_users,
                         pending_users=pending_users,
                         stats=stats)


@app.route('/admin/cache')
@page_admin_required
def admin_cache_view():
    """Cache management admin page."""
    cache_info = example_portfolio_service.get_cache_info()
    return render_template('auth/admin_cache.html', cache_info=cache_info)


@app.route('/api/admin/cache/refresh', methods=['POST'])
def admin_cache_refresh():
    """
    Admin endpoint to refresh the example portfolio cache.
    This recomputes all analysis with fresh market data.
    """
    from flask_login import current_user
    
    # Check admin access
    if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        logger.info(f"Admin {current_user.email} requested cache refresh")
        
        # This will recompute everything and update disk cache
        refreshed_data = example_portfolio_service.refresh_cache()
        
        return jsonify({
            'success': True,
            'message': 'Example portfolio cache refreshed successfully',
            'computed_at': refreshed_data['meta']['computed_at'],
            'cache_info': example_portfolio_service.get_cache_info()
        })
    except Exception as e:
        logger.error(f"Cache refresh failed: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': f'Cache refresh failed: {str(e)}'}), 500


@app.route('/api/admin/api-keys', methods=['GET'])
@admin_required
def api_keys_list():
    """List provider credentials without ever returning the full key."""
    from models.database import ApiCredential

    return jsonify({
        'keys': [
            {
                'provider': row.provider,
                'model': row.model,
                'base_url': row.base_url,
                'active': row.is_active,
                'hint': (row.api_key[:6] + '...') if row.api_key else '',
                'updated_at': row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in ApiCredential.query.order_by(ApiCredential.provider).all()
        ]
    })


@app.route('/api/admin/api-keys', methods=['POST'])
@admin_required
def api_keys_set():
    """Replace the active key for a provider."""
    data = request.get_json() or {}
    provider = (data.get('provider') or 'deepseek').strip()
    api_key = (data.get('api_key') or '').strip()
    if not api_key:
        return jsonify({'error': 'api_key is required'}), 400

    from services.api_keys import set_credential
    row = set_credential(provider, api_key, data.get('base_url'), data.get('model'))
    return jsonify({'success': True, 'provider': row.provider, 'model': row.model})


@app.route('/api/admin/cache/info')
def admin_cache_info():
    """Get current cache status."""
    from flask_login import current_user
    
    if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
        return jsonify({'error': 'Admin access required'}), 403
    
    return jsonify(example_portfolio_service.get_cache_info())


@app.route('/api/admin/cache/clear', methods=['POST'])
def admin_cache_clear():
    """Clear the example portfolio cache."""
    from flask_login import current_user
    
    if not current_user.is_authenticated or not getattr(current_user, 'is_admin', False):
        return jsonify({'error': 'Admin access required'}), 403
    
    try:
        example_portfolio_service.clear_cache()
        return jsonify({
            'success': True,
            'message': 'Cache cleared successfully'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =============================================================================
# API Routes - Document Upload & Processing
# =============================================================================

@app.route('/api/upload', methods=['POST'])
@rate_limit('upload_hour', 2, 3600)
@login_required
def upload_document():
    """
    Upload and parse portfolio document (PDF, CSV, Excel).
    
    Form Data:
        file: The document file to upload
        broker: Optional broker hint (fidelity, schwab, etc.)
        portfolio_id: Optional existing portfolio ID to update
    
    Returns:
        Extracted portfolio data with holdings and metrics
    """
    from services.rate_limit import allow
    weekly_limit = 3
    if not allow(f"weekly:user:{session['user_id']}", weekly_limit, 604800):
        return jsonify({
            'error': f'Weekly limit reached ({weekly_limit} analyses). '
                     f'Email optimizer@simonhavlik.com for more.',
            'contact': 'optimizer@simonhavlik.com'
        }), 429

    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No file selected'}), 400
    
    if not allowed_file(file.filename):
        return jsonify({'error': 'Invalid file type. Allowed: PDF, CSV, XLSX'}), 400
    
    broker_hint = request.form.get('broker', 'generic')
    portfolio_id = request.form.get('portfolio_id', type=int)
    
    try:
        # Read file bytes
        file_bytes = file.read()
        filename = secure_filename(file.filename)
        
        # Parse document or screenshot (vision model for images)
        parsed = extract_portfolio(file_bytes, filename, broker_hint)
        
        # Create or update portfolio
        if portfolio_id:
            portfolio = Portfolio.query.get(portfolio_id)
            if not portfolio:
                return jsonify({'error': 'Portfolio not found'}), 404
        else:
            # Create new portfolio from document
            portfolio = Portfolio(
                user_id=session.get('user_id'),
                name=f"Portfolio from {filename}",
                description=f"Extracted from {parsed.broker or 'unknown broker'} statement"
            )
            db.session.add(portfolio)
            db.session.flush()
        
        # Save uploaded file reference
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{portfolio.id}_{filename}")
        with open(file_path, 'wb') as f:
            f.write(file_bytes)
        
        doc_record = PortfolioDocument(
            portfolio_id=portfolio.id,
            filename=filename,
            file_type=filename.rsplit('.', 1)[1].lower(),
            file_size=len(file_bytes),
            broker=parsed.broker,
            extracted_data=parsed.to_dict(),
            extraction_confidence=parsed.extraction_confidence,
            file_path=file_path,
            statement_date=parsed.statement_date
        )
        db.session.add(doc_record)
        
        # Create/update holdings
        for holding in parsed.holdings:
            if not holding.ticker or holding.ticker == 'UNKNOWN':
                continue
            
            # Get or create asset
            asset = Asset.query.filter_by(ticker=holding.ticker).first()
            if not asset:
                asset = Asset(
                    ticker=holding.ticker,
                    name=holding.name or holding.ticker,
                    asset_class=holding.asset_class or 'Equity'
                )
                db.session.add(asset)
                db.session.flush()
            
            # Check for existing holding
            existing = PortfolioHolding.query.filter_by(
                portfolio_id=portfolio.id,
                asset_id=asset.id
            ).first()
            
            if existing:
                existing.shares = holding.shares
                existing.cost_basis = holding.cost_basis
                existing.purchase_date = holding.purchase_date
                # Calculate weight based on market value
                if parsed.account_value and holding.market_value:
                    existing.current_weight = holding.market_value / parsed.account_value
            else:
                weight = 0.0
                if parsed.account_value and holding.market_value:
                    weight = holding.market_value / parsed.account_value
                
                new_holding = PortfolioHolding(
                    portfolio_id=portfolio.id,
                    asset_id=asset.id,
                    current_weight=weight,
                    shares=holding.shares,
                    cost_basis=holding.cost_basis,
                    purchase_date=holding.purchase_date
                )
                db.session.add(new_holding)
        
        # Calculate and store risk metrics for this snapshot
        risk_metrics = None
        if parsed.holdings and len(parsed.holdings) >= 2:
            risk_metrics = calculate_portfolio_risk_metrics(
                [h.ticker for h in parsed.holdings if h.ticker and h.ticker != 'UNKNOWN'],
                parsed.statement_date or date.today()
            )
            
            if risk_metrics:
                history = PortfolioHistory(
                    portfolio_id=portfolio.id,
                    snapshot_date=parsed.statement_date or date.today(),
                    portfolio_value=parsed.account_value,
                    **risk_metrics,
                    holdings_snapshot={h.ticker: {'shares': h.shares, 'value': h.market_value} 
                                      for h in parsed.holdings},
                    source='document_upload',
                    document_id=doc_record.id
                )
                db.session.add(history)
        
        db.session.commit()

        positions = []
        for holding in parsed.holdings:
            weight = 0.0
            if parsed.account_value and holding.market_value:
                weight = holding.market_value / parsed.account_value
            positions.append({
                'ticker': holding.ticker,
                'weight': round(weight, 4),
                'sector': holding.asset_class,
            })

        total_weight = sum(p['weight'] for p in positions)
        if positions and total_weight == 0:
            even = round(1 / len(positions), 4)
            for position in positions:
                position['weight'] = even

        review_metrics = {}
        if risk_metrics:
            review_metrics = {
                'annualized_return': round(risk_metrics.get('expected_return', 0), 4),
                'volatility': round(risk_metrics.get('volatility', 0), 4),
                'sharpe': round(risk_metrics.get('sharpe_ratio', 0), 3),
                'max_drawdown': round(risk_metrics.get('max_drawdown', 0), 4),
                'var_95': round(risk_metrics.get('var_95', 0), 4),
            }

        ai_review = ''
        if positions:
            try:
                ai_review = ai_service.generate_portfolio_review(positions, review_metrics)
            except Exception as exc:
                logger.warning(f"Portfolio review unavailable: {exc}")

        return jsonify({
            'success': True,
            'portfolio_id': portfolio.id,
            'document_id': doc_record.id,
            'extracted_data': parsed.to_dict(),
            'ai_review': ai_review,
            'message': f"Successfully extracted {len(parsed.holdings)} holdings"
        })
        
    except Exception as e:
        logger.error(f"Upload error: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


def calculate_portfolio_risk_metrics(tickers: List[str], as_of_date: date) -> Dict[str, float]:
    """
    Calculate risk metrics for a set of tickers as of a specific date.
    
    Returns:
        Dictionary with volatility, VaR, expected_return, sharpe_ratio, etc.
    """
    try:
        # Fetch historical data ending at as_of_date
        # For simplicity, we'll use current data and calculate
        batch_data = market_data_service.fetch_batch_data(tickers, period="1y")
        valid_data = {t: d for t, d in batch_data.items() if d is not None}
        
        if len(valid_data) < 2:
            return None
        
        returns_df = pd.DataFrame({t: d.returns for t, d in valid_data.items()})
        returns_df = returns_df.dropna()
        
        # Equal weights for simplicity
        weights = np.array([1.0 / len(valid_data)] * len(valid_data))
        
        # Calculate metrics
        mu = returns_df.mean() * 252  # Annualized
        Sigma = returns_df.cov() * 252
        
        portfolio_return = np.dot(weights, mu)
        portfolio_vol = np.sqrt(np.dot(weights.T, np.dot(Sigma, weights)))
        sharpe = (portfolio_return - config.DEFAULT_RISK_FREE_RATE) / portfolio_vol if portfolio_vol > 0 else 0
        
        # VaR calculation (parametric)
        z_95 = 1.645
        z_99 = 2.326
        var_95 = -(portfolio_return - z_95 * portfolio_vol)
        var_99 = -(portfolio_return - z_99 * portfolio_vol)
        
        # Max drawdown approximation
        portfolio_returns = returns_df.dot(weights)
        cum_returns = (1 + portfolio_returns).cumprod()
        rolling_max = cum_returns.expanding().max()
        drawdowns = (cum_returns - rolling_max) / rolling_max
        max_drawdown = drawdowns.min()
        
        return {
            'volatility': float(portfolio_vol),
            'var_95': float(var_95),
            'var_99': float(var_99),
            'expected_return': float(portfolio_return),
            'sharpe_ratio': float(sharpe),
            'max_drawdown': float(max_drawdown),
            'beta': 1.0  # Simplified - would need market data
        }
        
    except Exception as e:
        logger.error(f"Risk calculation error: {e}")
        return None


# =============================================================================
# API Routes - Portfolio History & Risk Timeline
# =============================================================================

@app.route('/api/portfolios/<int:portfolio_id>/history', methods=['GET'])
@login_required
def get_portfolio_history(portfolio_id):
    """
    Get portfolio risk metrics history over time.
    
    Query Parameters:
        from_date: Start date (YYYY-MM-DD)
        to_date: End date (YYYY-MM-DD)
        metric: Specific metric to return (volatility, var_95, sharpe_ratio, etc.)
    
    Returns:
        Time-series data of portfolio risk metrics
    """
    portfolio = Portfolio.query.get_or_404(portfolio_id)
    
    from_date = request.args.get('from_date')
    to_date = request.args.get('to_date')
    metric = request.args.get('metric')
    
    query = PortfolioHistory.query.filter_by(portfolio_id=portfolio_id)
    
    if from_date:
        query = query.filter(PortfolioHistory.snapshot_date >= from_date)
    if to_date:
        query = query.filter(PortfolioHistory.snapshot_date <= to_date)
    
    history = query.order_by(PortfolioHistory.snapshot_date.asc()).all()
    
    if metric:
        # Return specific metric time series
        data = [{
            'date': h.snapshot_date.isoformat(),
            'value': getattr(h, metric)
        } for h in history if getattr(h, metric) is not None]
        return jsonify({'metric': metric, 'data': data})
    
    return jsonify({
        'portfolio_id': portfolio_id,
        'portfolio_name': portfolio.name,
        'history': [h.to_dict() for h in history]
    })


@app.route('/api/portfolios/<int:portfolio_id>/risk-timeline', methods=['GET'])
def get_risk_timeline(portfolio_id):
    """
    Get comprehensive risk timeline data for charting.
    
    Returns data formatted for time-series charts showing:
    - Portfolio value over time
    - VaR evolution
    - Volatility changes
    - Drawdown periods
    """
    portfolio = Portfolio.query.get_or_404(portfolio_id)
    
    history = PortfolioHistory.query.filter_by(portfolio_id=portfolio_id)\
        .order_by(PortfolioHistory.snapshot_date.asc()).all()
    
    if not history:
        # Generate sample timeline from current holdings
        history = generate_historical_timeline(portfolio_id)
    
    timeline = {
        'dates': [h.snapshot_date.isoformat() for h in history],
        'portfolio_value': [h.portfolio_value for h in history],
        'volatility': [h.volatility for h in history],
        'var_95': [h.var_95 for h in history],
        'sharpe_ratio': [h.sharpe_ratio for h in history],
        'max_drawdown': [h.max_drawdown for h in history],
    }
    
    return jsonify({
        'portfolio_id': portfolio_id,
        'portfolio_name': portfolio.name,
        'timeline': timeline
    })


def generate_historical_timeline(portfolio_id: int) -> List[PortfolioHistory]:
    """
    Generate historical timeline from current holdings by backtesting.
    This is used when no historical snapshots exist.
    """
    portfolio = Portfolio.query.get(portfolio_id)
    if not portfolio:
        return []
    
    holdings = list(portfolio.holdings)
    if not holdings:
        return []
    
    tickers = [h.asset.ticker for h in holdings if h.asset]
    
    try:
        # Fetch historical data
        batch_data = market_data_service.fetch_batch_data(tickers, period="2y")
        valid_data = {t: d for t, d in batch_data.items() if d is not None}
        
        if len(valid_data) < 2:
            return []
        
        returns_df = pd.DataFrame({t: d.returns for t, d in valid_data.items()})
        
        # Get weights from holdings
        total_value = sum(h.shares * (h.cost_basis or 100) for h in holdings if h.shares)
        weights = {}
        for h in holdings:
            if h.asset and h.shares:
                value = h.shares * (h.cost_basis or 100)
                weights[h.asset.ticker] = value / total_value if total_value > 0 else 0
        
        # Calculate rolling metrics for each month-end
        history = []
        dates = returns_df.index
        
        for i in range(60, len(dates), 21):  # Monthly approx
            window = returns_df.iloc[i-60:i]
            snapshot_date = dates[i].date()
            
            # Calculate metrics for this window
            mu = window.mean() * 252
            Sigma = window.cov() * 252
            
            w = np.array([weights.get(t, 0) for t in returns_df.columns])
            w = w / w.sum() if w.sum() > 0 else np.ones(len(w)) / len(w)
            
            port_return = np.dot(w, mu)
            port_vol = np.sqrt(np.dot(w.T, np.dot(Sigma, w)))
            sharpe = (port_return - config.DEFAULT_RISK_FREE_RATE) / port_vol if port_vol > 0 else 0
            
            # VaR
            z_95 = 1.645
            var_95 = -(port_return - z_95 * port_vol)
            
            # Drawdown
            port_rets = window.dot(w)
            cum_rets = (1 + port_rets).cumprod()
            rolling_max = cum_rets.expanding().max()
            drawdowns = (cum_rets - rolling_max) / rolling_max
            max_dd = drawdowns.min()
            
            history.append(PortfolioHistory(
                portfolio_id=portfolio_id,
                snapshot_date=snapshot_date,
                portfolio_value=total_value * (1 + port_rets.sum()),
                volatility=float(port_vol),
                var_95=float(var_95),
                expected_return=float(port_return),
                sharpe_ratio=float(sharpe),
                max_drawdown=float(max_dd),
                source='calculated'
            ))
        
        return history
        
    except Exception as e:
        logger.error(f"Timeline generation error: {e}")
        return []


# =============================================================================
# API Routes - Portfolio Analysis
# =============================================================================

@app.route('/api/portfolio/analyze', methods=['POST'])
@login_required
def analyze_current_portfolio():
    """
    Analyze current portfolio composition and suggest optimizations.
    
    Request Body:
    {
        "holdings": [
            {"ticker": "AAPL", "weight": 0.15, "sector": "Technology"},
            ...
        ],
        "portfolio_value": 100000,
        "risk_profile": "moderate",
        "period": "2y"
    }
    
    Returns:
        Portfolio analysis with sector breakdown and rebalancing suggestions
    """
    data = request.get_json()
    
    holdings = data.get('holdings', [])
    if len(holdings) < 2:
        return jsonify({'error': 'At least 2 holdings required'}), 400
    
    # Get list of tickers
    tickers = [h['ticker'] for h in holdings]
    
    # Fetch market data
    period = data.get('period', '2y')
    batch_data = market_data_service.fetch_batch_data(tickers, period=period)
    valid_data = {t: d for t, d in batch_data.items() if d is not None}
    
    if len(valid_data) < 2:
        return jsonify({'error': 'Insufficient market data'}), 400
    
    # Build returns DataFrame
    returns_df = pd.DataFrame({t: d.returns for t, d in valid_data.items()})
    returns_df = returns_df.dropna()
    
    # Get current weights
    current_weights = {h['ticker']: h['weight'] for h in holdings if h['ticker'] in valid_data}
    total_weight = sum(current_weights.values())
    if total_weight > 0:
        current_weights = {t: w/total_weight for t, w in current_weights.items()}
    
    # Calculate portfolio metrics
    w = np.array([current_weights.get(t, 0) for t in returns_df.columns])
    
    mu = returns_df.mean() * 252
    Sigma = returns_df.cov() * 252
    
    port_return = np.dot(w, mu)
    port_vol = np.sqrt(np.dot(w.T, np.dot(Sigma, w)))
    sharpe = (port_return - config.DEFAULT_RISK_FREE_RATE) / port_vol if port_vol > 0 else 0
    
    # Calculate sector breakdown
    sector_allocation = {}
    for h in holdings:
        sector = h.get('sector', 'Unknown')
        sector_allocation[sector] = sector_allocation.get(sector, 0) + h['weight']
    
    # Find max position for concentration risk
    max_position = max(current_weights.values()) if current_weights else 0
    
    # Simple rebalancing suggestion (equal weight with sector limits)
    risk_profile = data.get('risk_profile', 'moderate')
    suggested = suggest_rebalancing(holdings, returns_df, risk_profile)
    
    return jsonify(sanitize_for_json({
        'success': True,
        'current_allocation': holdings,
        'suggested_allocation': suggested,
        'sector_allocation': sector_allocation,
        'metrics': {
            'expected_return': float(port_return),
            'volatility': float(port_vol),
            'sharpe_ratio': float(sharpe),
            'beta': 1.0,  # Would need market data
            'max_position': float(max_position),
            'concentration_risk': 'HIGH' if max_position > 0.30 else 'MODERATE' if max_position > 0.20 else 'LOW'
        },
        'rebalancing_needed': any(
            abs(s.get('suggested_weight', 0) - h['weight']) > 0.05 
            for h in holdings for s in suggested if s['ticker'] == h['ticker']
        )
    }))


def suggest_rebalancing(holdings, returns_df, risk_profile):
    """
    Generate simple rebalancing suggestions based on risk profile and sectors.
    """
    # Define sector target weights based on risk profile
    sector_targets = {
        'conservative': {
            'Technology': 0.20,
            'Healthcare': 0.15,
            'Financials': 0.20,
            'Consumer': 0.10,
            'Staples': 0.20,
            'Energy': 0.10,
            'Industrials': 0.05
        },
        'moderate': {
            'Technology': 0.35,
            'Healthcare': 0.15,
            'Financials': 0.20,
            'Consumer': 0.15,
            'Staples': 0.10,
            'Energy': 0.03,
            'Industrials': 0.02
        },
        'aggressive': {
            'Technology': 0.50,
            'Healthcare': 0.10,
            'Financials': 0.15,
            'Consumer': 0.15,
            'Staples': 0.05,
            'Energy': 0.03,
            'Industrials': 0.02
        }
    }
    
    targets = sector_targets.get(risk_profile, sector_targets['moderate'])
    
    # Group holdings by sector
    sector_holdings = {}
    for h in holdings:
        sector = h.get('sector', 'Unknown')
        if sector not in sector_holdings:
            sector_holdings[sector] = []
        sector_holdings[sector].append(h)
    
    suggested = []
    
    for sector, target_weight in targets.items():
        sector_stocks = sector_holdings.get(sector, [])
        if sector_stocks:
            # Distribute target weight equally among stocks in sector
            per_stock = target_weight / len(sector_stocks)
            for stock in sector_stocks:
                suggested.append({
                    'ticker': stock['ticker'],
                    'name': stock.get('name', stock['ticker']),
                    'sector': sector,
                    'current_weight': stock['weight'],
                    'suggested_weight': per_stock,
                    'action': 'REDUCE' if stock['weight'] > per_stock + 0.05 else 'INCREASE' if stock['weight'] < per_stock - 0.05 else 'HOLD',
                    'sectorColor': stock.get('sectorColor', '#6b7280')
                })
    
    # Add any holdings not in target sectors (suggest reduce)
    for sector, stocks in sector_holdings.items():
        if sector not in targets:
            for stock in stocks:
                suggested.append({
                    'ticker': stock['ticker'],
                    'name': stock.get('name', stock['ticker']),
                    'sector': sector,
                    'current_weight': stock['weight'],
                    'suggested_weight': 0.01,  # Minimize
                    'action': 'REDUCE',
                    'sectorColor': stock.get('sectorColor', '#6b7280')
                })
    
    return suggested


# =============================================================================
# API Routes - Comprehensive Analysis
# =============================================================================

@app.route('/api/portfolio/comprehensive-analysis', methods=['POST'])
@login_required
def comprehensive_portfolio_analysis():
    """
    Comprehensive portfolio analysis with full risk metrics and benchmarks.
    
    Returns:
        Complete analysis including rolling metrics, drawdowns, 
        benchmark comparisons, and factor analysis.
    """
    data = request.get_json()
    
    holdings = data.get('holdings', [])
    if len(holdings) < 2:
        return jsonify({'error': 'At least 2 holdings required'}), 400
    
    tickers = [h['ticker'] for h in holdings]
    period = data.get('period', '2y')
    
    # Fetch portfolio data
    batch_data = market_data_service.fetch_batch_data(tickers, period=period)
    valid_data = {t: d for t, d in batch_data.items() if d is not None}
    
    if len(valid_data) < 2:
        return jsonify({'error': 'Insufficient market data'}), 400
    
    # Build portfolio returns (ensure timezone-naive indices)
    returns_dict = {}
    for t, d in valid_data.items():
        returns = d.returns
        if returns.index.tz is not None:
            returns = returns.copy()
            returns.index = returns.index.tz_localize(None)
        returns_dict[t] = returns
    returns_df = pd.DataFrame(returns_dict).dropna()
    
    # Get weights
    weights = {h['ticker']: h['weight'] for h in holdings if h['ticker'] in valid_data}
    total_weight = sum(weights.values())
    if total_weight > 0:
        weights = {t: w/total_weight for t, w in weights.items()}
    
    w = np.array([weights.get(t, 0) for t in returns_df.columns])
    portfolio_returns = returns_df.dot(w)
    
    # Fetch benchmark data
    benchmarks = risk_analytics.get_benchmark_data(['SPY', 'VT'], period=period)
    
    # Calculate comprehensive metrics
    try:
        spy_returns = benchmarks.get('SPY')
        metrics = risk_analytics.calculate_comprehensive_metrics(
            portfolio_returns, 
            benchmark_returns=spy_returns,
            risk_free_rate=config.DEFAULT_RISK_FREE_RATE
        )
        
        # Calculate rolling metrics
        rolling = risk_analytics.calculate_rolling_metrics(
            portfolio_returns,
            benchmark_returns=spy_returns
        )
        
        # Stress test
        stress_results = risk_analytics.stress_test(portfolio_returns, weights)
        
        # Correlation matrix
        corr_matrix = risk_analytics.calculate_correlation_matrix(returns_df)
        
        # Calculate drawdown series
        cum_returns = (1 + portfolio_returns).cumprod()
        rolling_max = cum_returns.expanding().max()
        drawdown_series = (cum_returns - rolling_max) / rolling_max
        
        # ============================================
        # ADVANCED STATISTICAL ANALYSIS
        # ============================================
        
        # 1. Distribution Fitting (Normal, Student-t, Laplace)
        distributions = advanced_statistics.fit_distributions(portfolio_returns)
        
        # 2. Monte Carlo Simulation for forward projections
        mc_result = advanced_statistics.monte_carlo_simulation(
            portfolio_returns, w, initial_value=100000,
            n_simulations=5000, n_days=252, method='bootstrap'
        )
        
        # 3. Confidence Intervals for key statistics
        confidence_intervals = advanced_statistics.calculate_confidence_intervals(
            portfolio_returns, confidence=0.95
        )
        
        # 4. Statistical Tests (Jarque-Bera, Ljung-Box, etc.)
        stat_tests = advanced_statistics.run_statistical_tests(portfolio_returns)
        
        # 5. Volatility Clustering Analysis (ARCH effects)
        vol_analysis = advanced_statistics.analyze_volatility_clustering(portfolio_returns)
        
        # 6. Extreme Value Theory (Tail risk)
        tail_risk = advanced_statistics.extreme_value_analysis(portfolio_returns)
        
        # 7. Principal Component Analysis
        pca_result = advanced_statistics.principal_component_analysis(returns_df)
        
        # 8. Rolling Statistics with Confidence Bands
        rolling_stats = advanced_statistics.calculate_rolling_statistics(portfolio_returns, window=63)
        
        # 9. Fetch detailed sector/fundamental info
        stock_info = market_data_service.get_batch_stock_info(list(valid_data.keys()))
        
        response_data = {
            'success': True,
            'metrics': {
                'total_return': metrics.total_return,
                'annualized_return': metrics.annualized_return,
                'volatility': metrics.volatility,
                'downside_volatility': metrics.downside_volatility,
                'max_drawdown': metrics.max_drawdown,
                'max_drawdown_days': metrics.max_drawdown_duration,
                'var_95': metrics.var_95,
                'var_99': metrics.var_99,
                'cvar_95': metrics.cvar_95,
                'sharpe_ratio': metrics.sharpe_ratio,
                'sortino_ratio': metrics.sortino_ratio,
                'calmar_ratio': metrics.calmar_ratio,
                'omega_ratio': metrics.omega_ratio,
                'information_ratio': metrics.information_ratio,
                'treynor_ratio': metrics.treynor_ratio,
                'beta': metrics.beta,
                'alpha': metrics.alpha,
                'r_squared': metrics.r_squared,
                'effective_n': metrics.effective_n,
                'skewness': metrics.skewness,
                'kurtosis': metrics.kurtosis,
                'tail_ratio': metrics.tail_ratio
            },
            'rolling_metrics': {
                'dates': rolling.index.strftime('%Y-%m-%d').tolist(),
                'volatility': rolling['volatility'].tolist(),
                'drawdown': rolling['drawdown'].tolist(),
                'beta': rolling['beta'].fillna(1.0).tolist(),
                'sharpe': rolling['sharpe'].tolist()
            },
            'drawdown_series': {
                'dates': drawdown_series.index.strftime('%Y-%m-%d').tolist(),
                'drawdown': drawdown_series.tolist()
            },
            'stress_test': stress_results,
            'correlation_matrix': corr_matrix.to_dict(),
            'benchmarks': {
                name: (1 + returns).cumprod().tolist() 
                for name, returns in benchmarks.items()
            } if benchmarks else {},
            'holdings': [
                {
                    'ticker': t,
                    'weight': float(w),
                    'name': market_data_service.get_stock_info(t).get('name', t),
                    'sector': market_data_service.get_stock_info(t).get('sector', 'Unknown'),
                    'beta': market_data_service.get_stock_info(t).get('beta', 1.0)
                }
                for t, w in zip(tickers, w)
            ],
            # Advanced Statistics - Distribution Analysis
            'distributions': {
                name: {
                    'mean': dist.mean,
                    'std': dist.std,
                    'var_95': dist.var_95,
                    'var_99': dist.var_99,
                    'aic': dist.aic,
                    'bic': dist.bic,
                    'ks_statistic': dist.ks_statistic,
                    'p_value': dist.p_value
                }
                for name, dist in distributions.items()
            } if 'distributions' in locals() else {},
            'returns_distribution': {
                'returns': portfolio_returns.dropna().tolist(),
                'best_fit': min(distributions.items(), key=lambda x: x[1].aic)[0] if distributions else 'Normal',
                'var_95': metrics.var_95 if 'metrics' in locals() else None,
                'x_values': np.linspace(portfolio_returns.min(), portfolio_returns.max(), 100).tolist(),
                'pdf_fitted': [],  # Will be populated if we have fitted distribution
                'pdf_normal': []
            } if 'distributions' in locals() else {},
            # Monte Carlo Simulation Results
            'monte_carlo': {
                'mean_final': mc_result.mean_final,
                'median_final': mc_result.median_final,
                'std_final': mc_result.std_final,
                'var_95_final': mc_result.var_95,
                'var_99_final': mc_result.var_99,
                'probability_profit': mc_result.probability_profit,
                'probability_loss': mc_result.probability_loss,
                'probability_target': mc_result.probability_target,
                'confidence_interval_95': list(mc_result.confidence_interval_95),
                'worst_case': mc_result.worst_case,
                'best_case': mc_result.best_case,
                'expected_max_drawdown': float(np.mean(mc_result.max_drawdowns)),
                # Send sample of paths for visualization (50 paths to keep response size reasonable)
                'paths': mc_result.paths[::max(1, len(mc_result.paths)//50)].tolist() if len(mc_result.paths) > 0 else [],
                'initial_value': 100000
            } if 'mc_result' in locals() else {},
            # Confidence Intervals (95%)
            'confidence_intervals': {
                k: list(v) for k, v in confidence_intervals.items()
            } if 'confidence_intervals' in locals() else {},
            # Statistical Tests
            'statistical_tests': {
                'jarque_bera_stat': stat_tests.jarque_bera_stat,
                'jarque_bera_pvalue': stat_tests.jarque_bera_pvalue,
                'is_normal': stat_tests.is_normal,
                'ljung_box_stat': stat_tests.ljung_box_stat,
                'ljung_box_pvalue': stat_tests.ljung_box_pvalue,
                'has_autocorrelation': stat_tests.has_autocorrelation,
                'shapiro_wilk_stat': stat_tests.shapiro_wilk_stat,
                'shapiro_wilk_pvalue': stat_tests.shapiro_wilk_pvalue,
                'anderson_darling_stat': stat_tests.anderson_darling_stat
            } if 'stat_tests' in locals() else {},
            # Volatility Clustering (ARCH/GARCH effects)
            'volatility_analysis': {
                'has_arch_effects': vol_analysis.has_arch_effects,
                'arch_lm_stat': vol_analysis.arch_lm_stat,
                'arch_lm_pvalue': vol_analysis.arch_lm_pvalue,
                'vol_of_vol': vol_analysis.vol_of_vol,
                'volatility_persistence': vol_analysis.volatility_persistence,
                'half_life': vol_analysis.half_life,
                'volatility_series': {
                    'dates': portfolio_returns.index.strftime('%Y-%m-%d').tolist(),
                    'values': (portfolio_returns.rolling(21).std() * np.sqrt(252)).tolist()
                }
            } if 'vol_analysis' in locals() else {},
            # Tail Risk Analysis (Extreme Value Theory)
            'tail_risk': {
                'hill_estimator': tail_risk.hill_estimator,
                'tail_index': tail_risk.tail_index,
                'xi_parameter': tail_risk.xi_parameter,
                'sigma_parameter': tail_risk.sigma_parameter,
                'threshold': tail_risk.threshold,
                'black_swan_prob': tail_risk.black_swan_prob,
                'extreme_drawdown_prob': tail_risk.extreme_drawdown_prob
            } if 'tail_risk' in locals() else {},
            # Principal Component Analysis
            'pca': {
                'explained_variance_ratio': pca_result.explained_variance_ratio.tolist()[:5],
                'cumulative_variance': pca_result.cumulative_variance.tolist()[:5],
                'condition_number': pca_result.condition_number,
                'effective_rank': pca_result.effective_rank,
                'loadings': pca_result.loadings.to_dict()
            } if 'pca_result' in locals() else {},
            # Rolling Statistics with Confidence Bands
            'rolling_statistics': {
                'dates': rolling_stats.index.strftime('%Y-%m-%d').tolist(),
                'mean': rolling_stats['mean'].tolist(),
                'mean_upper': rolling_stats['mean_upper'].tolist(),
                'mean_lower': rolling_stats['mean_lower'].tolist(),
                'std': rolling_stats['std'].tolist(),
                'skewness': rolling_stats['skewness'].tolist(),
                'kurtosis': rolling_stats['kurtosis'].tolist(),
                'sharpe': rolling_stats['sharpe'].tolist()
            } if 'rolling_stats' in locals() else {},
            # Charts data for visualization
            'charts': {
                'monte_carlo': dashboard_charts.get_monte_carlo_chart(
                    portfolio_returns, 
                    w, 
                    n_paths=100, 
                    n_days=252
                ),
                'rolling_stats_63d': dashboard_charts.get_rolling_statistics_chart(
                    portfolio_returns, 
                    window=63
                ),
                'pca': dashboard_charts.get_pca_chart(returns_df),
                'drawdown': dashboard_charts.get_drawdown_chart(portfolio_returns),
                'performance': dashboard_charts.get_performance_chart_data(
                    portfolio_returns,
                    returns_df.columns.tolist(),
                    period=period
                ),
                'efficient_frontier': dashboard_charts.get_efficient_frontier_chart(
                    returns_df, w
                ),
                'risk_contribution': dashboard_charts.get_risk_contribution_chart(
                    returns_df, w
                ),
                'monthly_returns': dashboard_charts.get_monthly_returns_heatmap(
                    portfolio_returns
                ),
                'rolling_correlation': dashboard_charts.get_rolling_correlation_chart(
                    returns_df, window=90
                )
            },
            # AI Research Insights
            'ai_insights': generate_ai_insights(
                metrics,
                distributions if 'distributions' in locals() else {},
                stat_tests if 'stat_tests' in locals() else None,
                pca_result if 'pca_result' in locals() else None,
                vol_analysis if 'vol_analysis' in locals() else None
            )
        }
        
        # Sanitize data to ensure valid JSON (convert NaN/Infinity to null)
        return jsonify(sanitize_for_json(response_data))
        
    except Exception as e:
        logger.error(f"Comprehensive analysis error: {e}")
        return jsonify({'error': str(e)}), 500


def generate_ai_insights(metrics, distributions, stat_tests, pca_result, vol_analysis):
    """
    Generate AI-style quantitative research insights based on portfolio analysis.
    
    Returns structured insights for risk assessment, factor analysis, and recommendations.
    """
    insights = {
        'risk_assessment': '',
        'factor_analysis': '',
        'recommendation': ''
    }
    
    # Risk Assessment
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
    
    # Factor Analysis
    beta = getattr(metrics, 'beta', 1.0)
    alpha = getattr(metrics, 'alpha', 0)
    r_squared = getattr(metrics, 'r_squared', 0)
    
    if beta > 1.1:
        beta_desc = "aggressive market exposure amplifying systematic movements"
    elif beta < 0.9:
        beta_desc = "defensive positioning reducing market sensitivity"
    else:
        beta_desc = "neutral market sensitivity aligned with benchmark"
    
    # Check distribution fit
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
    
    # Recommendation
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


@app.route('/api/example-portfolio', methods=['GET'])
def get_example_portfolio():
    """
    Get pre-computed example portfolio analysis.
    
    Returns full advanced analysis for the example portfolio.
    Cached and updated every 6 hours.
    """
    try:
        force_refresh = request.args.get('refresh', 'false').lower() == 'true'
        analysis = example_portfolio_service.get_analysis(force_refresh)
        
        return jsonify({
            'success': True,
            'data': analysis
        })
    except Exception as e:
        logger.error(f"Example portfolio error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/example-portfolio/summary', methods=['GET'])
def get_example_portfolio_summary():
    """Get quick summary of example portfolio for homepage."""
    try:
        summary = example_portfolio_service.get_summary()
        return jsonify({
            'success': True,
            'data': summary
        })
    except Exception as e:
        logger.error(f"Example portfolio summary error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/benchmarks', methods=['GET'])
def get_available_benchmarks():
    """Get list of available benchmarks."""
    return jsonify({
        'success': True,
        'benchmarks': [
            {'name': name, 'ticker': ticker, 'description': get_benchmark_description(name)}
            for name, ticker in BENCHMARK_TICKERS.items()
        ]
    })


def get_benchmark_description(name: str) -> str:
    """Get description for benchmark."""
    descriptions = {
        'S&P 500': 'US Large Cap Equities',
        'FTSE 100': 'UK Large Cap Equities',
        'NASDAQ': 'US Technology Heavy Index',
        'MSCI World': 'Global Developed Markets',
        'Russell 2000': 'US Small Cap Equities',
        'DAX': 'German Large Cap Equities',
        'Nikkei 225': 'Japanese Large Cap Equities',
        'Emerging Markets': 'Emerging Market Equities',
        'Bonds (AGG)': 'US Aggregate Bonds',
        'Gold': 'Gold Spot Price'
    }
    return descriptions.get(name, 'Market Index')


# =============================================================================
# API Routes - Assets
# =============================================================================

@app.route('/api/assets/search')
def search_assets():
    """
    Search for assets by ticker or name.
    
    Query Parameters:
        q: Search query string
        limit: Maximum results (default: 10)
    
    Returns:
        JSON array of matching assets
    """
    query = request.args.get('q', '')
    limit = min(int(request.args.get('limit', 10)), 50)
    
    if len(query) < 2:
        return jsonify([])
    
    # Search Yahoo Finance
    results = market_data_service.search_tickers(query, limit)
    return jsonify(results)


@app.route('/api/assets/<ticker>/info')
def get_asset_info(ticker):
    """Get detailed information for a specific asset."""
    info = market_data_service.get_stock_info(ticker.upper())
    return jsonify(info)


@app.route('/api/assets/<ticker>/history')
def get_asset_history(ticker):
    """Get historical price data for an asset."""
    period = request.args.get('period', '2y')
    data = market_data_service.fetch_historical_data(ticker.upper(), period=period)
    
    if data is None:
        abort(404, description=f"No data available for {ticker}")
    
    # Convert Timestamp keys to ISO format strings
    prices_dict = {d.isoformat(): v for d, v in data.prices.to_dict().items()}
    returns_dict = {d.isoformat(): v for d, v in data.returns.to_dict().items()}
    
    return jsonify(sanitize_for_json({
        'ticker': data.ticker,
        'prices': prices_dict,
        'returns': returns_dict,
        'volatility': data.volatility,
        'total_return': data.total_return,
    }))


# =============================================================================
# API Routes - Portfolios
# =============================================================================

@app.route('/api/portfolios', methods=['GET'])
def list_portfolios():
    """List all portfolios."""
    portfolios = Portfolio.query.all()
    return jsonify([p.to_dict() for p in portfolios])


@app.route('/api/portfolios', methods=['POST'])
def create_portfolio():
    """Create a new portfolio."""
    data = request.get_json()
    
    portfolio = Portfolio(
        name=data.get('name', 'New Portfolio'),
        description=data.get('description', ''),
        risk_tolerance=data.get('risk_tolerance', 'moderate'),
        target_return=data.get('target_return'),
        optimization_params=data.get('optimization_params', {})
    )
    
    db.session.add(portfolio)
    db.session.commit()
    
    return jsonify(portfolio.to_dict()), 201


@app.route('/api/portfolios/<int:portfolio_id>', methods=['GET'])
def get_portfolio(portfolio_id):
    """Get portfolio details."""
    portfolio = Portfolio.query.get_or_404(portfolio_id)
    return jsonify(portfolio.to_dict())


@app.route('/api/portfolios/<int:portfolio_id>', methods=['PUT'])
def update_portfolio(portfolio_id):
    """Update portfolio."""
    portfolio = Portfolio.query.get_or_404(portfolio_id)
    data = request.get_json()
    
    portfolio.name = data.get('name', portfolio.name)
    portfolio.description = data.get('description', portfolio.description)
    portfolio.risk_tolerance = data.get('risk_tolerance', portfolio.risk_tolerance)
    portfolio.target_return = data.get('target_return', portfolio.target_return)
    
    db.session.commit()
    return jsonify(portfolio.to_dict())


@app.route('/api/portfolios/<int:portfolio_id>', methods=['DELETE'])
def delete_portfolio(portfolio_id):
    """Delete portfolio."""
    portfolio = Portfolio.query.get_or_404(portfolio_id)
    db.session.delete(portfolio)
    db.session.commit()
    return '', 204


@app.route('/api/portfolios/<int:portfolio_id>/holdings', methods=['POST'])
def add_holding(portfolio_id):
    """Add or update a holding in a portfolio."""
    portfolio = Portfolio.query.get_or_404(portfolio_id)
    data = request.get_json()
    
    ticker = data.get('ticker', '').upper()
    
    # Get or create asset
    asset = Asset.query.filter_by(ticker=ticker).first()
    if not asset:
        # Fetch info from Yahoo Finance
        info = market_data_service.get_stock_info(ticker)
        asset = Asset(
            ticker=ticker,
            name=info.get('name', ticker),
            sector=info.get('sector'),
            asset_class='Equity'
        )
        db.session.add(asset)
        db.session.flush()
    
    # Check if holding exists
    holding = PortfolioHolding.query.filter_by(
        portfolio_id=portfolio_id,
        asset_id=asset.id
    ).first()
    
    if holding:
        # Update existing
        holding.current_weight = data.get('weight', holding.current_weight)
        holding.min_weight = data.get('min_weight', holding.min_weight)
        holding.max_weight = data.get('max_weight', holding.max_weight)
        holding.shares = data.get('shares', holding.shares)
        holding.cost_basis = data.get('cost_basis', holding.cost_basis)
    else:
        # Create new
        holding = PortfolioHolding(
            portfolio_id=portfolio_id,
            asset_id=asset.id,
            current_weight=data.get('weight', 0),
            min_weight=data.get('min_weight', 0),
            max_weight=data.get('max_weight', 1.0),
            shares=data.get('shares'),
            cost_basis=data.get('cost_basis')
        )
        db.session.add(holding)
    
    db.session.commit()
    return jsonify(holding.to_dict()), 201


@app.route('/api/portfolios/<int:portfolio_id>/holdings/<int:holding_id>', methods=['DELETE'])
def remove_holding(portfolio_id, holding_id):
    """Remove a holding from portfolio."""
    holding = PortfolioHolding.query.filter_by(
        id=holding_id,
        portfolio_id=portfolio_id
    ).first_or_404()
    
    db.session.delete(holding)
    db.session.commit()
    return '', 204


@app.route('/api/portfolios/<int:portfolio_id>/documents', methods=['GET'])
def get_portfolio_documents(portfolio_id):
    """Get all uploaded documents for a portfolio."""
    portfolio = Portfolio.query.get_or_404(portfolio_id)
    documents = PortfolioDocument.query.filter_by(portfolio_id=portfolio_id).all()
    return jsonify([d.to_dict() for d in documents])


# =============================================================================
# API Routes - Optimization
# =============================================================================

@app.route('/api/optimize', methods=['POST'])
@login_required
def optimize_portfolio():
    """
    Execute portfolio optimization.
    
    Request Body:
    {
        "tickers": ["AAPL", "MSFT", "GOOGL", "AMZN", ...],
        "method": "max_sharpe" | "min_variance" | "mean_variance" | "risk_parity",
        "constraints": {
            "allow_short": false,
            "max_position_size": 0.3,
            "target_return": 0.15
        },
        "period": "2y",
        "risk_free_rate": 0.05
    }
    
    Returns:
        Optimization results with weights and metrics
    """
    data = request.get_json()
    
    # Validate input
    tickers = data.get('tickers', [])
    if len(tickers) < 2:
        return jsonify({'error': 'At least 2 assets required'}), 400
    
    if len(tickers) > config.MAX_PORTFOLIO_ASSETS:
        return jsonify({'error': f'Maximum {config.MAX_PORTFOLIO_ASSETS} assets allowed'}), 400
    
    method_str = data.get('method', 'max_sharpe')
    try:
        method = OptimizationMethod(method_str)
    except ValueError:
        return jsonify({'error': f'Invalid method: {method_str}'}), 400
    
    # Fetch market data
    period = data.get('period', '2y')
    batch_data = market_data_service.fetch_batch_data(tickers, period=period)
    
    # Filter successful fetches
    valid_data = {t: d for t, d in batch_data.items() if d is not None}
    failed_tickers = [t for t, d in batch_data.items() if d is None]
    
    if len(valid_data) < 2:
        return jsonify({
            'error': 'Insufficient data for optimization',
            'failed_tickers': failed_tickers
        }), 400
    
    # Build returns DataFrame
    returns_df = pd.DataFrame({t: d.returns for t, d in valid_data.items()})
    returns_df = returns_df.dropna()
    
    # Parse constraints
    constraints_data = data.get('constraints', {})
    constraints = OptimizationConstraints(
        allow_short=constraints_data.get('allow_short', False),
        max_position_size=constraints_data.get('max_position_size', 1.0),
        min_position_size=constraints_data.get('min_position_size', 0.0),
        target_return=constraints_data.get('target_return'),
        target_volatility=constraints_data.get('target_volatility')
    )
    
    # Execute optimization
    risk_free_rate = data.get('risk_free_rate', config.DEFAULT_RISK_FREE_RATE)
    
    try:
        optimizer = PortfolioOptimizer(returns_df, risk_free_rate=risk_free_rate)
        result = optimizer.optimize(method=method, constraints=constraints)
        
        # Generate efficient frontier data
        ef_data = optimizer.efficient_frontier(n_points=50, constraints=constraints)
        
        result_dict = result.to_dict()
        review = ''
        try:
            positions = [
                {'ticker': ticker, 'weight': float(weight)}
                for ticker, weight in (result_dict.get('weights') or {}).items()
                if float(weight) > 0
            ]
            review_metrics = {
                'expected_return': result_dict.get('expected_return'),
                'volatility': result_dict.get('volatility'),
                'sharpe_ratio': result_dict.get('sharpe_ratio'),
                'max_drawdown': result_dict.get('max_drawdown'),
            }
            review = ai_service.generate_portfolio_review(positions, review_metrics)
        except Exception as exc:
            logger.warning(f"Optimization review unavailable: {exc}")

        response = {
            'success': True,
            'optimization': result_dict,
            'efficient_frontier': ef_data.to_dict(orient='records'),
            'failed_tickers': failed_tickers,
            'method': method.value,
            'period': period,
            'risk_free_rate': risk_free_rate,
            'ai_review': review,
        }
        
        return jsonify(sanitize_for_json(response))
        
    except Exception as e:
        logger.error(f"Optimization error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/optimize/efficient-frontier', methods=['POST'])
def calculate_efficient_frontier():
    """Calculate efficient frontier for visualization."""
    data = request.get_json()
    
    tickers = data.get('tickers', [])
    period = data.get('period', '2y')
    
    batch_data = market_data_service.fetch_batch_data(tickers, period=period)
    valid_data = {t: d for t, d in batch_data.items() if d is not None}
    
    if len(valid_data) < 2:
        return jsonify({'error': 'Insufficient data'}), 400
    
    returns_df = pd.DataFrame({t: d.returns for t, d in valid_data.items()})
    
    optimizer = PortfolioOptimizer(returns_df)
    ef = optimizer.efficient_frontier(n_points=50)
    
    return jsonify(sanitize_for_json({
        'frontier': ef.to_dict(orient='records'),
        'tickers': list(valid_data.keys())
    }))


# =============================================================================
# API Routes - AI Analysis
# =============================================================================

@app.route('/api/analysis/portfolio', methods=['POST'])
@login_required
def analyze_portfolio():
    """
    Generate AI-powered portfolio analysis.
    
    Request Body:
    {
        "tickers": ["AAPL", "MSFT", ...],
        "weights": {"AAPL": 0.4, "MSFT": 0.6},
        "expected_return": 0.12,
        "volatility": 0.15,
        "sharpe_ratio": 0.8,
        "method": "max_sharpe"
    }
    
    Returns:
        AI-generated analysis text
    """
    if not config.ENABLE_AI_INSIGHTS:
        return jsonify({'error': 'AI insights disabled'}), 503
    
    data = request.get_json()
    
    # Build context
    context = PortfolioContext(
        tickers=data.get('tickers', []),
        weights=data.get('weights', {}),
        expected_return=data.get('expected_return', 0),
        volatility=data.get('volatility', 0),
        sharpe_ratio=data.get('sharpe_ratio', 0),
        method=data.get('method', 'unknown'),
        risk_free_rate=data.get('risk_free_rate', config.DEFAULT_RISK_FREE_RATE),
        sector_allocation=data.get('sector_allocation')
    )
    
    try:
        analysis = ai_service.generate_portfolio_summary(context)
        risk_analysis = ai_service.analyze_risk_factors(context)
        method_explanation = ai_service.explain_optimization_method(context.method, context)
        
        return jsonify({
            'summary': analysis,
            'risk_analysis': risk_analysis,
            'method_explanation': method_explanation,
            'generated_at': datetime.utcnow().isoformat()
        })
        
    except Exception as e:
        logger.error(f"AI analysis error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/analysis/risk', methods=['POST'])
def analyze_risk():
    """Generate AI-powered risk analysis."""
    if not config.ENABLE_AI_INSIGHTS:
        return jsonify({'error': 'AI insights disabled'}), 503
    
    data = request.get_json()
    
    context = PortfolioContext(
        tickers=data.get('tickers', []),
        weights=data.get('weights', {}),
        expected_return=data.get('expected_return', 0),
        volatility=data.get('volatility', 0),
        sharpe_ratio=data.get('sharpe_ratio', 0),
        method=data.get('method', 'unknown'),
        risk_free_rate=data.get('risk_free_rate', config.DEFAULT_RISK_FREE_RATE)
    )
    
    try:
        risk_analysis = ai_service.analyze_risk_factors(context)
        return jsonify(risk_analysis)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# =============================================================================
# Advanced Analytics API Routes
# =============================================================================

@app.route('/api/advanced/risk', methods=['POST'])
@login_required
def advanced_risk_analysis():
    """
    Calculate advanced risk metrics.
    
    Returns:
        Modified VaR, Kelly Criterion, Ulcer Index, and more
    """
    data = request.get_json()
    tickers = data.get('tickers', [])
    weights = data.get('weights', None)
    period = data.get('period', '2y')
    
    if len(tickers) < 2:
        return jsonify({'error': 'At least 2 tickers required'}), 400
    
    # Fetch data
    batch_data = market_data_service.fetch_batch_data(tickers, period=period)
    valid_data = {t: d for t, d in batch_data.items() if d is not None}
    
    if len(valid_data) < 2:
        return jsonify({'error': 'Insufficient data'}), 400
    
    # Build returns DataFrame
    returns_df = pd.DataFrame({t: d.returns for t, d in valid_data.items()}).dropna()
    
    # Calculate weights
    if weights is None:
        weights = np.ones(len(valid_data)) / len(valid_data)
    else:
        # Normalize provided weights
        total = sum(weights.values())
        weights = np.array([weights.get(t, 0) / total for t in valid_data.keys()])
    
    # Calculate advanced risk metrics
    calc = AdvancedRiskCalculator(returns_df, weights)
    
    try:
        mvar_95 = calc.modified_var(0.95)
        mvar_99 = calc.modified_var(0.99)
        kelly = calc.kelly_criterion()
        ulcer = calc.ulcer_index()
        dar = calc.drawdown_at_risk()
        tail = calc.tail_risk_metrics()
        
        return jsonify(sanitize_for_json({
            'success': True,
            'modified_var': {'var_95': mvar_95, 'var_99': mvar_99},
            'kelly_criterion': kelly,
            'ulcer_metrics': ulcer,
            'drawdown_at_risk': dar,
            'tail_risk': tail
        }))
    except Exception as e:
        logger.error(f"Advanced risk calculation error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/hierarchical-risk-parity', methods=['POST'])
@login_required
def hierarchical_risk_parity():
    """
    Calculate Hierarchical Risk Parity portfolio.
    
    Based on Lopez de Prado (2016) - uses machine learning
    clustering for allocation without inverting covariance.
    """
    data = request.get_json()
    tickers = data.get('tickers', [])
    period = data.get('period', '2y')
    
    if len(tickers) < 2:
        return jsonify({'error': 'At least 2 tickers required'}), 400
    
    batch_data = market_data_service.fetch_batch_data(tickers, period=period)
    valid_data = {t: d for t, d in batch_data.items() if d is not None}
    
    if len(valid_data) < 2:
        return jsonify({'error': 'Insufficient data'}), 400
    
    returns_df = pd.DataFrame({t: d.returns for t, d in valid_data.items()}).dropna()
    
    try:
        weights = calculate_hierarchical_risk_parity(returns_df)
        
        # Calculate portfolio metrics
        port_returns = returns_df @ weights.values
        mu = port_returns.mean() * 252
        sigma = port_returns.std() * np.sqrt(252)
        sharpe = (mu - config.DEFAULT_RISK_FREE_RATE) / sigma if sigma > 0 else 0
        
        return jsonify(sanitize_for_json({
            'success': True,
            'method': 'Hierarchical Risk Parity',
            'weights': weights.to_dict(),
            'expected_return': mu,
            'volatility': sigma,
            'sharpe_ratio': sharpe,
            'diversification_ratio': 1 / np.sum(weights ** 2)
        }))
    except Exception as e:
        logger.error(f"HRP error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/black-litterman', methods=['POST'])
def black_litterman_optimization():
    """
    Black-Litterman portfolio optimization.
    
    Combines market equilibrium with investor views.
    """
    data = request.get_json()
    tickers = data.get('tickers', [])
    views = data.get('views', [])
    period = data.get('period', '2y')
    
    if len(tickers) < 2:
        return jsonify({'error': 'At least 2 tickers required'}), 400
    
    batch_data = market_data_service.fetch_batch_data(tickers, period=period)
    valid_data = {t: d for t, d in batch_data.items() if d is not None}
    
    if len(valid_data) < 2:
        return jsonify({'error': 'Insufficient data'}), 400
    
    returns_df = pd.DataFrame({t: d.returns for t, d in valid_data.items()}).dropna()
    
    try:
        # Initialize model
        bl = BlackLittermanModel(returns_df)
        
        # Add views if provided
        for view in views:
            bl.add_absolute_view(
                view['asset'],
                view['expected_return'],
                view['confidence']
            )
        
        # Optimize
        result = bl.optimize()
        
        return jsonify(sanitize_for_json(result))
    except Exception as e:
        logger.error(f"Black-Litterman error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/regime-detection', methods=['POST'])
@login_required
def regime_detection():
    """
    Detect market regimes using Hidden Markov Model.
    
    Identifies bull/bear markets or high/low volatility regimes.
    """
    data = request.get_json()
    tickers = data.get('tickers', [])
    n_regimes = data.get('n_regimes', 2)
    period = data.get('period', '2y')
    
    if len(tickers) < 1:
        return jsonify({'error': 'At least 1 ticker required'}), 400
    
    batch_data = market_data_service.fetch_batch_data(tickers, period=period)
    valid_data = {t: d for t, d in batch_data.items() if d is not None}
    
    if len(valid_data) < 1:
        return jsonify({'error': 'Insufficient data'}), 400
    
    returns_df = pd.DataFrame({t: d.returns for t, d in valid_data.items()}).dropna()
    port_returns = returns_df.mean(axis=1)
    
    try:
        # Fit HMM
        hmm = HiddenMarkovModel(n_regimes).fit(port_returns)
        regime_probs = hmm.predict_proba(port_returns)
        current_regime = hmm.predict(port_returns).iloc[-1]
        
        # Get regime characteristics
        characteristics = hmm.get_regime_characteristics()
        
        return jsonify(sanitize_for_json({
            'success': True,
            'n_regimes': n_regimes,
            'current_regime': current_regime,
            'regime_probabilities': regime_probs.iloc[-1].to_dict(),
            'regime_characteristics': [
                {
                    'name': r.name,
                    'mean': r.mean,
                    'volatility': r.volatility,
                    'sharpe': r.sharpe,
                    'var_95': r.var_95,
                    'probability': r.probability
                }
                for r in characteristics
            ]
        }))
    except Exception as e:
        logger.error(f"Regime detection error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/covariance-estimators', methods=['POST'])
def compare_covariance_estimators():
    """
    Compare different covariance matrix estimators.
    
    Tests Ledoit-Wolf, OAS, Factor models, etc.
    """
    data = request.get_json()
    tickers = data.get('tickers', [])
    period = data.get('period', '2y')
    
    if len(tickers) < 2:
        return jsonify({'error': 'At least 2 tickers required'}), 400
    
    batch_data = market_data_service.fetch_batch_data(tickers, period=period)
    valid_data = {t: d for t, d in batch_data.items() if d is not None}
    
    if len(valid_data) < 2:
        return jsonify({'error': 'Insufficient data'}), 400
    
    returns_df = pd.DataFrame({t: d.returns for t, d in valid_data.items()}).dropna()
    
    try:
        results = compare_estimators(returns_df)
        
        return jsonify(sanitize_for_json({
            'success': True,
            'comparison': results.to_dict(orient='records')
        }))
    except Exception as e:
        logger.error(f"Covariance comparison error: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/factor-exposure', methods=['POST'])
def factor_exposure_analysis():
    """
    Analyze portfolio exposure to Fama-French factors.
    
    Estimates factor betas and risk decomposition.
    """
    data = request.get_json()
    tickers = data.get('tickers', [])
    weights = data.get('weights', None)
    model = data.get('model', '5factor')  # 3factor, 5factor, carhart
    period = data.get('period', '2y')
    
    if len(tickers) < 1:
        return jsonify({'error': 'At least 1 ticker required'}), 400
    
    batch_data = market_data_service.fetch_batch_data(tickers, period=period)
    valid_data = {t: d for t, d in batch_data.items() if d is not None}
    
    if len(valid_data) < 1:
        return jsonify({'error': 'Insufficient data'}), 400
    
    returns_df = pd.DataFrame({t: d.returns for t, d in valid_data.items()}).dropna()
    
    # Calculate portfolio returns
    if weights is None:
        weights = np.ones(len(valid_data)) / len(valid_data)
    else:
        total = sum(weights.values())
        weights = np.array([weights.get(t, 0) / total for t in valid_data.keys()])
    
    port_returns = returns_df @ weights
    
    try:
        # Factor model
        ff_model = FamaFrenchModel()
        exposure = ff_model.estimate_exposure(port_returns, model=model)
        
        return jsonify(sanitize_for_json({
            'success': True,
            'model': model,
            'exposure': {
                'market_beta': exposure.market_beta,
                'smb_beta': exposure.smb_beta,
                'hml_beta': exposure.hml_beta,
                'rmw_beta': exposure.rmw_beta,
                'cma_beta': exposure.cma_beta,
                'mom_beta': exposure.mom_beta,
                'alpha': exposure.alpha,
                'alpha_tstat': exposure.alpha_tstat,
                'alpha_significant': bool(exposure.alpha_pvalue < 0.05),
                'r_squared': exposure.r_squared,
                'factor_contributions': exposure.factor_contributions
            }
        }))
    except Exception as e:
        logger.error(f"Factor exposure error: {e}")
        return jsonify({'error': str(e)}), 500


# =============================================================================
# Error Handlers
# =============================================================================

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return jsonify({'error': 'Internal server error'}), 500


# =============================================================================
# Health Check
# =============================================================================

@app.route('/api/health')
def health_check():
    """Service health check endpoint."""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'version': '1.0.0',
        'database': config.DB_TYPE,
        'ai_enabled': config.ENABLE_AI_INSIGHTS
    })


@app.route('/api/debug/session')
@admin_required
def debug_session():
    """Debug endpoint to check session and auth status."""
    from flask_login import current_user
    
    return jsonify({
        'session_data': dict(session),
        'is_authenticated': current_user.is_authenticated if current_user else False,
        'user_id': current_user.id if current_user and current_user.is_authenticated else None,
        'user_email': current_user.email if current_user and current_user.is_authenticated else None,
        'user_obj': str(current_user) if current_user else None
    })


# =============================================================================
# Leaderboard refresh scheduler
# =============================================================================

def _start_leaderboard_scheduler():
    """Weekly, overnight, off-peak rebuild. Manual refresh stays available."""
    if os.getenv('ENABLE_LEADERBOARD_SCHEDULER', '1') == '0':
        return
    import threading
    import time as _time

    def loop():
        _time.sleep(120)
        while True:
            try:
                from services.leaderboard import CACHE_DIR, build_board
                path = CACHE_DIR / 'board.json'
                stale = True
                if path.exists():
                    age = datetime.utcnow() - datetime.fromtimestamp(path.stat().st_mtime)
                    stale = age > timedelta(days=7)
                if stale and 1 <= datetime.utcnow().hour <= 4:
                    app.logger.info('leaderboard: scheduled refresh starting')
                    build_board(force=True)
                    app.logger.info('leaderboard: scheduled refresh complete')
            except Exception as exc:  # never let the scheduler kill the app
                app.logger.warning('leaderboard scheduler: %s', exc)
            _time.sleep(3600)

    threading.Thread(target=loop, daemon=True, name='leaderboard-scheduler').start()


_start_leaderboard_scheduler()


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
