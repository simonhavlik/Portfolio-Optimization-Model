"""
Portfolio Optimizer - Email Service
====================================

Email notification system for user registration, password reset,
and admin notifications using Google SMTP.

Configuration in .env:
- SMTP_HOST=smtp.gmail.com
- SMTP_PORT=587
- SMTP_USER=your-email@gmail.com
- SMTP_PASSWORD=your-app-password
- ADMIN_EMAIL=admin@yourdomain.com
"""

import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
from datetime import datetime

from config import config

logger = logging.getLogger(__name__)


class EmailService:
    """Email service for sending notifications."""
    
    def __init__(self):
        self.smtp_host = getattr(config, 'SMTP_HOST', 'smtp.gmail.com')
        self.smtp_port = getattr(config, 'SMTP_PORT', 587)
        self.smtp_user = getattr(config, 'SMTP_USER', None)
        self.smtp_password = getattr(config, 'SMTP_PASSWORD', None)
        self.smtp_use_ssl = getattr(config, 'SMTP_USE_SSL', False) or self.smtp_port == 465
        self.admin_email = getattr(config, 'ADMIN_EMAIL', None)
        self.enabled = all([self.smtp_user, self.smtp_password, self.admin_email])
        
        if not self.enabled:
            logger.warning("Email service not configured. Set SMTP_USER, SMTP_PASSWORD, and ADMIN_EMAIL in .env")
    
    def _send_email(self, to_email: str, subject: str, html_body: str, 
                   text_body: str = None) -> bool:
        """
        Send email via SMTP.
        
        Args:
            to_email: Recipient email address
            subject: Email subject
            html_body: HTML content
            text_body: Plain text content (optional)
            
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.enabled:
            logger.info(f"[EMAIL MOCK] To: {to_email}, Subject: {subject}")
            return True
        
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = f"Portfolio Optimizer <{self.smtp_user}>"
            msg['To'] = to_email
            
            # Add text part
            if text_body:
                msg.attach(MIMEText(text_body, 'plain'))
            
            # Add HTML part
            msg.attach(MIMEText(html_body, 'html'))
            
            # Send via SMTP (implicit TLS on 465, STARTTLS otherwise)
            if self.smtp_use_ssl:
                server = smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=20)
            else:
                server = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=20)
                server.starttls()
            with server:
                server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)
            
            logger.info(f"Email sent to {to_email}: {subject}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {e}")
            return False
    
    def send_registration_request_to_admin(self, user_email: str, 
                                          first_name: str = None,
                                          last_name: str = None,
                                          registration_date: datetime = None) -> bool:
        """
        Send registration approval request to admin.
        
        Args:
            user_email: Email of user requesting registration
            first_name: User's first name
            last_name: User's last name
            registration_date: When user registered
        """
        if not self.admin_email:
            logger.warning("Admin email not configured")
            return False
        
        full_name = f"{first_name or ''} {last_name or ''}".strip() or user_email
        
        subject = f"[Portfolio Optimizer] New Registration Request: {full_name}"
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <h2 style="color: #4f46e5;">New Registration Request</h2>
            
            <p>A new user has requested access to the Portfolio Optimizer:</p>
            
            <table style="border-collapse: collapse; width: 100%; max-width: 500px;">
                <tr>
                    <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Email:</td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{user_email}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Name:</td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{full_name}</td>
                </tr>
                <tr>
                    <td style="padding: 8px; border: 1px solid #ddd; font-weight: bold;">Registered:</td>
                    <td style="padding: 8px; border: 1px solid #ddd;">{registration_date.strftime('%Y-%m-%d %H:%M UTC') if registration_date else 'Unknown'}</td>
                </tr>
            </table>
            
            <p style="margin-top: 20px;">
                <a href="{getattr(config, 'BASE_URL', 'http://localhost:5000')}/admin/users" 
                   style="background-color: #4f46e5; color: white; padding: 10px 20px; 
                          text-decoration: none; border-radius: 5px; display: inline-block;">
                    Review Request
                </a>
            </p>
            
            <hr style="margin-top: 30px; border: none; border-top: 1px solid #ddd;">
            <p style="font-size: 12px; color: #666;">
                This is an automated message from Portfolio Optimizer.
            </p>
        </body>
        </html>
        """
        
        text_body = f"""
New Registration Request

A new user has requested access to the Portfolio Optimizer:

Email: {user_email}
Name: {full_name}
Registered: {registration_date.strftime('%Y-%m-%d %H:%M UTC') if registration_date else 'Unknown'}

Please log in to review and approve/reject this request.
        """
        
        return self._send_email(self.admin_email, subject, html_body, text_body)
    
    def send_registration_approved(self, user_email: str, first_name: str = None) -> bool:
        """Send registration approval notification to user."""
        subject = "[Portfolio Optimizer] Your Account Has Been Approved"
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <h2 style="color: #22c55e;">Welcome to Portfolio Optimizer!</h2>
            
            <p>Hi {first_name or 'there'},</p>
            
            <p>Great news! Your registration has been approved by an administrator.</p>
            
            <p>You can now log in and start using all features:</p>
            
            <p style="margin-top: 20px;">
                <a href="{getattr(config, 'BASE_URL', 'http://localhost:5000')}/login" 
                   style="background-color: #22c55e; color: white; padding: 10px 20px; 
                          text-decoration: none; border-radius: 5px; display: inline-block;">
                    Log In Now
                </a>
            </p>
            
            <p>Features available to you:</p>
            <ul>
                <li>Advanced portfolio optimization (Mean-Variance, Risk Parity, Black-Litterman)</li>
                <li>Risk analytics (VaR, CVaR, Monte Carlo simulation)</li>
                <li>Factor exposure analysis</li>
                <li>Regime detection</li>
                <li>Save and compare portfolio scenarios</li>
                <li>Generate detailed PDF reports</li>
            </ul>
            
            <hr style="margin-top: 30px; border: none; border-top: 1px solid #ddd;">
            <p style="font-size: 12px; color: #666;">
                If you have any questions, please contact the administrator.
            </p>
        </body>
        </html>
        """
        
        text_body = f"""
Welcome to Portfolio Optimizer!

Hi {first_name or 'there'},

Great news! Your registration has been approved by an administrator.

You can now log in and start using all features at:
{getattr(config, 'BASE_URL', 'http://localhost:5000')}/login

Features available:
- Advanced portfolio optimization
- Risk analytics (VaR, CVaR, Monte Carlo)
- Factor exposure analysis
- Regime detection
- Save and compare portfolio scenarios

If you have any questions, please contact the administrator.
        """
        
        return self._send_email(user_email, subject, html_body, text_body)
    
    def send_registration_rejected(self, user_email: str, reason: str = None,
                                   first_name: str = None) -> bool:
        """Send registration rejection notification to user."""
        subject = "[Portfolio Optimizer] Registration Request Update"
        
        reason_html = f"<p><strong>Reason:</strong> {reason}</p>" if reason else ""
        reason_text = f"Reason: {reason}" if reason else ""
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <h2 style="color: #ef4444;">Registration Request</h2>
            
            <p>Hi {first_name or 'there'},</p>
            
            <p>We regret to inform you that your registration request for Portfolio Optimizer 
            has been declined.</p>
            
            {reason_html}
            
            <p>If you believe this is an error or have any questions, please contact the administrator.</p>
            
            <hr style="margin-top: 30px; border: none; border-top: 1px solid #ddd;">
            <p style="font-size: 12px; color: #666;">
                This is an automated message from Portfolio Optimizer.
            </p>
        </body>
        </html>
        """
        
        text_body = f"""
Registration Request

Hi {first_name or 'there'},

We regret to inform you that your registration request for Portfolio Optimizer has been declined.

{reason_text}

If you believe this is an error or have any questions, please contact the administrator.
        """
        
        return self._send_email(user_email, subject, html_body, text_body)
    
    def send_password_reset(self, user_email: str, reset_token: str,
                           first_name: str = None) -> bool:
        """Send password reset email with token."""
        subject = "[Portfolio Optimizer] Password Reset Request"
        
        reset_url = f"{getattr(config, 'BASE_URL', 'http://localhost:5000')}/reset-password?token={reset_token}"
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <h2 style="color: #4f46e5;">Password Reset</h2>
            
            <p>Hi {first_name or 'there'},</p>
            
            <p>You requested a password reset for your Portfolio Optimizer account.</p>
            
            <p>Click the button below to reset your password:</p>
            
            <p style="margin-top: 20px;">
                <a href="{reset_url}" 
                   style="background-color: #4f46e5; color: white; padding: 10px 20px; 
                          text-decoration: none; border-radius: 5px; display: inline-block;">
                    Reset Password
                </a>
            </p>
            
            <p style="font-size: 14px; color: #666;">
                Or copy and paste this link:<br>
                <code style="background-color: #f3f4f6; padding: 5px; word-break: break-all;">
                    {reset_url}
                </code>
            </p>
            
            <p style="color: #ef4444; font-weight: bold;">
                This link will expire in 24 hours.
            </p>
            
            <p style="font-size: 14px; color: #666;">
                If you didn't request this reset, please ignore this email. Your password will remain unchanged.
            </p>
            
            <hr style="margin-top: 30px; border: none; border-top: 1px solid #ddd;">
            <p style="font-size: 12px; color: #666;">
                This is an automated message from Portfolio Optimizer.
            </p>
        </body>
        </html>
        """
        
        text_body = f"""
Password Reset Request

Hi {first_name or 'there'},

You requested a password reset for your Portfolio Optimizer account.

Click this link to reset your password:
{reset_url}

This link will expire in 24 hours.

If you didn't request this reset, please ignore this email. Your password will remain unchanged.
        """
        
        return self._send_email(user_email, subject, html_body, text_body)
    
    def send_welcome_email(self, user_email: str, first_name: str = None) -> bool:
        """Send welcome email after registration (before approval)."""
        subject = "[Portfolio Optimizer] Registration Received"
        
        html_body = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <h2 style="color: #4f46e5;">Welcome to Portfolio Optimizer!</h2>
            
            <p>Hi {first_name or 'there'},</p>
            
            <p>Thank you for registering with Portfolio Optimizer. We've received your request 
            and it's now pending administrator approval.</p>
            
            <p>You will receive an email notification once your account has been reviewed.</p>
            
            <p style="background-color: #fef3c7; padding: 15px; border-radius: 5px; color: #92400e;">
                <strong>Note:</strong> Approval typically takes 1-2 business days.
            </p>
            
            <hr style="margin-top: 30px; border: none; border-top: 1px solid #ddd;">
            <p style="font-size: 12px; color: #666;">
                This is an automated message from Portfolio Optimizer.
            </p>
        </body>
        </html>
        """
        
        text_body = f"""
Welcome to Portfolio Optimizer!

Hi {first_name or 'there'},

Thank you for registering with Portfolio Optimizer. We've received your request and it's now pending administrator approval.

You will receive an email notification once your account has been reviewed.

Note: Approval typically takes 1-2 business days.
        """
        
        return self._send_email(user_email, subject, html_body, text_body)


# Singleton instance
email_service = EmailService()
