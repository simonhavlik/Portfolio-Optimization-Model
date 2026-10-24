"""
Portfolio Optimizer - Authentication Routes
============================================

User registration, login, password reset, and admin user management.
Implements approval-based registration workflow.
"""

from datetime import datetime, timedelta
from functools import wraps
import secrets
from flask import Blueprint, request, jsonify, session, current_app, redirect
from flask_login import login_user, logout_user
from sqlalchemy import or_
from models.database import db
from models.auth import User, PasswordResetToken, SavedScenario, UserActivity, GuestSession
from services.email_service import email_service
from services.rate_limit import rate_limit
from config import config
import logging

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__, url_prefix='/api/auth')


def login_required(f):
    """Decorator to require login for a route."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        
        # Check if user is still active (is_active is now a property, not a method)
        user = User.query.get(session['user_id'])
        if not user or not user.is_active:
            session.clear()
            return jsonify({'error': 'Account inactive or locked'}), 403
        
        return f(*args, **kwargs)
    return decorated_function


def admin_required(f):
    """Decorator to require admin role."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return jsonify({'error': 'Authentication required'}), 401
        
        user = User.query.get(session['user_id'])
        if not user or not user.is_admin:
            return jsonify({'error': 'Admin access required'}), 403
        
        return f(*args, **kwargs)
    return decorated_function


@auth_bp.route('/register', methods=['POST'])
@rate_limit('register', 5, 3600)
def register():
    """Register a new user account."""
    data = request.get_json()
    
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    first_name = data.get('first_name', '').strip()
    last_name = data.get('last_name', '').strip()
    
    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400
    
    if len(password) < 8:
        return jsonify({'error': 'Password must be at least 8 characters'}), 400
    
    existing_user = User.query.filter_by(email=email).first()
    if existing_user:
        return jsonify({'error': 'Email already registered'}), 409
    
    try:
        # Auto-approve admin email
        admin_email = config.ADMIN_EMAIL.lower() if hasattr(config, 'ADMIN_EMAIL') else 'simon2444444@gmail.com'
        is_admin = email.lower() == admin_email
        
        user = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            status='active',
            role='admin' if is_admin else 'user'
        )
        user.set_password(password)
        
        db.session.add(user)
        db.session.commit()
        
        # Accounts are active immediately; the admin only gets an information mail
        email_service.send_welcome_email(email, first_name)
        email_service.send_registration_request_to_admin(
            user_email=email,
            first_name=first_name,
            last_name=last_name,
            registration_date=user.created_at
        )

        login_user(user)
        session['user_id'] = user.id
        session['email'] = user.email
        session['role'] = user.role
        return jsonify({
            'success': True,
            'message': 'Registration successful. You are now logged in.',
            'user': {
                'id': user.id,
                'email': user.email,
                'status': user.status,
                'role': user.role
            },
            'auto_login': True
        }), 201
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Registration error: {e}")
        return jsonify({'error': 'Registration failed'}), 500


@auth_bp.route('/login', methods=['POST'])
def login():
    """Authenticate user and create session."""
    data = request.get_json()
    
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    
    if not email or not password:
        return jsonify({'error': 'Email and password are required'}), 400
    
    user = User.query.filter_by(email=email).first()
    
    if not user:
        return jsonify({'error': 'Invalid credentials'}), 401
    
    if user.locked_until and user.locked_until > datetime.utcnow():
        remaining = int((user.locked_until - datetime.utcnow()).total_seconds() / 60)
        return jsonify({'error': f'Account locked. Try again in {remaining} minutes.'}), 403
    
    if user.status == 'pending':
        return jsonify({'error': 'Account pending approval', 'status': 'pending'}), 403
    
    if user.status == 'rejected':
        return jsonify({'error': 'Registration declined', 'status': 'rejected'}), 403
    
    if user.status == 'suspended':
        return jsonify({'error': 'Account suspended', 'status': 'suspended'}), 403
    
    if not user.check_password(password):
        user.record_failed_login()
        db.session.commit()
        remaining = max(0, 5 - user.failed_login_attempts)
        return jsonify({'error': 'Invalid credentials', 'remaining_attempts': remaining}), 401
    
    # Successful login
    user.record_login(request.remote_addr)
    db.session.commit()
    
    # Log in the user with Flask-Login (this sets current_user)
    login_user(user, remember=True)
    
    session['user_id'] = user.id
    session['user_email'] = user.email
    session['user_role'] = user.role
    session.permanent = True
    
    logger.info(f"User logged in: {user.email}")
    
    return jsonify({
        'success': True,
        'user': {
            'id': user.id,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'role': user.role,
            'status': user.status
        }
    })


@auth_bp.route('/logout', methods=['GET', 'POST'])
@login_required
def logout():
    """Log out user; GET is the navigation path, POST is the API."""
    logout_user()  # Flask-Login logout
    session.clear()
    if request.method == 'GET':
        return redirect('/')
    return jsonify({'success': True, 'message': 'Logged out'})


@auth_bp.route('/me', methods=['GET'])
def get_current_user():
    """Get current user."""
    from flask_login import current_user
    
    if not current_user.is_authenticated:
        return jsonify({'error': 'Not authenticated'}), 401
    
    return jsonify({
        'success': True, 
        'user': {
            'id': current_user.id,
            'email': current_user.email,
            'first_name': current_user.first_name,
            'last_name': current_user.last_name,
            'role': current_user.role,
            'status': current_user.status
        }
    })


@auth_bp.route('/forgot-password', methods=['POST'])
@rate_limit('forgot', 5, 3600)
def forgot_password():
    """Request password reset."""
    data = request.get_json()
    email = data.get('email', '').strip().lower()
    
    user = User.query.filter_by(email=email).first()
    
    if user and user.status == 'active':
        try:
            token = user.generate_reset_token()
            email_service.send_password_reset(user.email, token, user.first_name)
        except Exception as e:
            logger.error(f"Password reset email error: {e}")
    
    # Always return success to prevent enumeration
    return jsonify({
        'success': True,
        'message': 'If email exists, reset instructions sent.'
    })


@auth_bp.route('/reset-password', methods=['POST'])
def reset_password():
    """Reset password with token."""
    data = request.get_json()
    token = data.get('token', '')
    # Accept both 'password' and 'new_password' for flexibility
    new_password = data.get('password') or data.get('new_password', '')
    
    if not new_password or len(new_password) < 8:
        return jsonify({'error': 'Password must be 8+ characters'}), 400
    
    reset_token = PasswordResetToken.query.filter_by(token=token).first()
    
    if not reset_token or not reset_token.is_valid():
        return jsonify({'error': 'Invalid or expired token'}), 400
    
    try:
        user = reset_token.user
        user.set_password(new_password)
        reset_token.mark_used()
        db.session.commit()
        
        return jsonify({'success': True, 'message': 'Password reset. Please log in.'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Password reset error: {e}")
        return jsonify({'error': 'Failed to reset password'}), 500


# Admin Routes
@auth_bp.route('/admin/users', methods=['GET'])
@admin_required
def list_users():
    """List all users."""
    status = request.args.get('status', 'all')
    query = User.query
    
    if status != 'all':
        query = query.filter_by(status=status)
    
    users = query.order_by(User.created_at.desc()).all()
    
    return jsonify({
        'success': True,
        'users': [u.to_dict(include_sensitive=True) for u in users]
    })


@auth_bp.route('/admin/users/<int:user_id>/approve', methods=['POST'])
@admin_required
def approve_user(user_id):
    """Approve pending user."""
    user = User.query.get_or_404(user_id)
    
    if user.status != 'pending':
        return jsonify({'error': 'User not pending'}), 400
    
    user.approve(session['user_id'])
    db.session.commit()
    
    email_service.send_registration_approved(user.email, user.first_name)
    
    return jsonify({'success': True, 'user': user.to_dict()})


@auth_bp.route('/admin/users/<int:user_id>/reject', methods=['POST'])
@admin_required
def reject_user(user_id):
    """Reject pending user."""
    data = request.get_json() or {}
    user = User.query.get_or_404(user_id)
    
    if user.status != 'pending':
        return jsonify({'error': 'User not pending'}), 400
    
    user.reject(session['user_id'], data.get('reason'))
    db.session.commit()
    
    email_service.send_registration_rejected(
        user.email, data.get('reason'), user.first_name
    )
    
    return jsonify({'success': True, 'user': user.to_dict()})


@auth_bp.route('/admin/stats', methods=['GET'])
@admin_required
def get_admin_stats():
    """Get admin stats."""
    return jsonify({
        'success': True,
        'stats': {
            'total_users': User.query.count(),
            'active_users': User.query.filter_by(status='active').count(),
            'pending_users': User.query.filter_by(status='pending').count(),
            'suspended_users': User.query.filter_by(status='suspended').count(),
        }
    })


# Saved Scenarios
@auth_bp.route('/scenarios', methods=['GET'])
@login_required
def list_scenarios():
    """List saved scenarios."""
    scenarios = SavedScenario.query.filter_by(
        user_id=session['user_id']
    ).order_by(SavedScenario.updated_at.desc()).all()
    
    return jsonify({
        'success': True,
        'scenarios': [s.to_dict() for s in scenarios]
    })


@auth_bp.route('/scenarios', methods=['POST'])
@login_required
def save_scenario():
    """Save scenario."""
    data = request.get_json()
    
    scenario = SavedScenario(
        user_id=session['user_id'],
        name=data.get('name', 'Untitled'),
        description=data.get('description', ''),
        holdings=data.get('holdings', {}),
        period=data.get('period', '2y'),
        analysis_results=data.get('analysis_results', {}),
        tags=data.get('tags', [])
    )
    
    db.session.add(scenario)
    db.session.commit()
    
    return jsonify({'success': True, 'scenario': scenario.to_dict()}), 201


@auth_bp.route('/scenarios/<int:scenario_id>', methods=['GET'])
@login_required
def get_scenario(scenario_id):
    """Get scenario."""
    scenario = SavedScenario.query.filter_by(
        id=scenario_id, user_id=session['user_id']
    ).first_or_404()
    
    return jsonify({'success': True, 'scenario': scenario.to_dict()})


@auth_bp.route('/scenarios/<int:scenario_id>', methods=['DELETE'])
@login_required
def delete_scenario(scenario_id):
    """Delete scenario."""
    scenario = SavedScenario.query.filter_by(
        id=scenario_id, user_id=session['user_id']
    ).first_or_404()
    
    db.session.delete(scenario)
    db.session.commit()
    
    return jsonify({'success': True, 'message': 'Deleted'})
