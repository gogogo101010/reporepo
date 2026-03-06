"""Email confirmation token generation and (simulated) sending."""
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from itsdangerous import URLSafeTimedSerializer

# Token serializer - uses app's SECRET_KEY
_serializer = None


def _get_serializer():
    global _serializer
    if _serializer is None:
        secret = os.environ.get('SECRET_KEY', 'dev-secret-key-change-me')
        _serializer = URLSafeTimedSerializer(secret)
    return _serializer


def generate_confirmation_token(email):
    """Generate a time-limited email confirmation token."""
    s = _get_serializer()
    return s.dumps(email, salt='email-confirm-salt')


def confirm_token(token, max_age=86400):
    """Verify token and return email. Returns None if invalid/expired.
    Default max_age is 24 hours.
    """
    s = _get_serializer()
    try:
        email = s.loads(token, salt='email-confirm-salt', max_age=max_age)
        return email
    except Exception:
        return None


def send_confirmation_email(to_email, username, confirm_url):
    """Send confirmation email. Uses SMTP if configured, otherwise logs to console."""
    smtp_server = os.environ.get('SMTP_SERVER')
    smtp_port = int(os.environ.get('SMTP_PORT', '587'))
    smtp_user = os.environ.get('SMTP_USER')
    smtp_pass = os.environ.get('SMTP_PASS')
    from_email = os.environ.get('FROM_EMAIL', 'noreply@chess.com')

    subject = 'Confirm Your Chess Account'
    html_body = f"""
    <html>
    <body style="background-color: #0a0e27; color: #e2e8f0; font-family: 'Inter', sans-serif; padding: 40px;">
        <div style="max-width: 500px; margin: 0 auto; background: #16213e; border-radius: 16px; padding: 40px; border: 1px solid rgba(255,255,255,0.05);">
            <h1 style="text-align: center; background: linear-gradient(135deg, #667eea, #764ba2); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">Chess</h1>
            <h2 style="text-align: center; color: #e2e8f0;">Welcome, {username}!</h2>
            <p style="color: #94a3b8; text-align: center;">Please confirm your email address to activate your account and start playing.</p>
            <div style="text-align: center; margin: 30px 0;">
                <a href="{confirm_url}" style="background: linear-gradient(135deg, #667eea, #764ba2); color: white; padding: 14px 32px; border-radius: 12px; text-decoration: none; font-weight: 600; font-size: 16px;">Confirm Email</a>
            </div>
            <p style="color: #64748b; text-align: center; font-size: 12px;">This link expires in 24 hours.</p>
            <p style="color: #64748b; text-align: center; font-size: 12px;">If you didn't create this account, you can safely ignore this email.</p>
        </div>
    </body>
    </html>
    """

    if smtp_server and smtp_user and smtp_pass:
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = from_email
            msg['To'] = to_email
            msg.attach(MIMEText(html_body, 'html'))

            with smtplib.SMTP(smtp_server, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_pass)
                server.send_message(msg)
            return True
        except Exception as e:
            print(f'[EMAIL ERROR] Failed to send to {to_email}: {e}')
            return False
    else:
        # Dev mode: print to console
        print(f'\n{"="*60}')
        print(f'EMAIL CONFIRMATION (dev mode - SMTP not configured)')
        print(f'To: {to_email}')
        print(f'Subject: {subject}')
        print(f'Confirm URL: {confirm_url}')
        print(f'{"="*60}\n')
        return True
