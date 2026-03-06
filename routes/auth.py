import os
import secrets
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from flask_login import login_user, logout_user, login_required, current_user
import bcrypt
from bson import ObjectId
from authlib.integrations.flask_client import OAuth

from extensions import db
from models import User
from wallet import generate_wallet
from email_utils import generate_confirmation_token, confirm_token, send_confirmation_email

auth_bp = Blueprint('auth', __name__)

# Google OAuth will be initialized in init_oauth()
oauth = OAuth()


def init_oauth(app):
    """Initialize OAuth with the Flask app. Called from app.py."""
    oauth.init_app(app)
    oauth.register(
        name='google',
        client_id=os.environ.get('GOOGLE_CLIENT_ID', ''),
        client_secret=os.environ.get('GOOGLE_CLIENT_SECRET', ''),
        server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
        client_kwargs={'scope': 'openid email profile'},
    )


@auth_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('game.dashboard'))
    return render_template('index.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('game.dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        referral_code = request.form.get('ref', '').strip() or request.args.get('ref', '').strip()

        if not username or not email or not password:
            flash('All fields are required.', 'error')
            return render_template('register.html')

        if len(username) < 3 or len(username) > 20:
            flash('Username must be 3-20 characters.', 'error')
            return render_template('register.html')

        if len(password) < 6:
            flash('Password must be at least 6 characters.', 'error')
            return render_template('register.html')

        if db.users.find_one({'$or': [{'username_lower': username.lower()}, {'email': email}]}):
            flash('Username or email already taken.', 'error')
            return render_template('register.html')

        wallet_address, wallet_secret = generate_wallet()
        user_referral_code = secrets.token_urlsafe(8)

        hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        user_doc = {
            'username': username,
            'username_lower': username.lower(),
            'email': email,
            'password': hashed,
            'rating': 1200,
            'games_played': 0,
            'wins': 0,
            'losses': 0,
            'draws': 0,
            'friends': [],
            'friend_requests': [],
            'created_at': datetime.utcnow(),
            'online': False,
            'wallet_address': wallet_address,
            'wallet_secret': wallet_secret,
            'sol_balance': 0.0,
            'total_wagered': 0.0,
            'total_won': 0.0,
            'total_lost': 0.0,
            'bet_wins': 0,
            'bet_losses': 0,
            'bet_draws': 0,
            'net_profit': 0.0,
            'email_confirmed': False,
            'auth_provider': 'local',
            'referral_code': user_referral_code,
            'referred_by': None,
            'referral_count': 0,
            'referral_earnings': 0.0,
        }

        # Process referral
        if referral_code:
            referrer = db.users.find_one({'referral_code': referral_code})
            if referrer:
                user_doc['referred_by'] = str(referrer['_id'])
                db.users.update_one({'_id': referrer['_id']}, {'$inc': {'referral_count': 1}})

        result = db.users.insert_one(user_doc)
        user_doc['_id'] = result.inserted_id

        # Send confirmation email
        token = generate_confirmation_token(email)
        confirm_url = url_for('auth.confirm_email', token=token, _external=True)
        send_confirmation_email(email, username, confirm_url)

        login_user(User(user_doc))
        flash('Welcome to Chess! Please check your email to confirm your account. Your Solana wallet has been created.', 'success')
        return redirect(url_for('game.dashboard'))
    ref = request.args.get('ref', '')
    return render_template('register.html', ref=ref)


@auth_bp.route('/confirm/<token>')
def confirm_email(token):
    email = confirm_token(token)
    if not email:
        flash('The confirmation link is invalid or has expired.', 'error')
        return redirect(url_for('auth.login'))

    user_data = db.users.find_one({'email': email})
    if not user_data:
        flash('Account not found.', 'error')
        return redirect(url_for('auth.register'))

    if user_data.get('email_confirmed'):
        flash('Email already confirmed. Please login.', 'info')
        return redirect(url_for('auth.login'))

    db.users.update_one({'_id': user_data['_id']}, {'$set': {'email_confirmed': True}})
    flash('Email confirmed! Your account is now fully activated.', 'success')

    if current_user.is_authenticated:
        return redirect(url_for('game.dashboard'))
    return redirect(url_for('auth.login'))


@auth_bp.route('/resend-confirmation')
@login_required
def resend_confirmation():
    user_data = db.users.find_one({'_id': ObjectId(current_user.id)})
    if user_data.get('email_confirmed'):
        flash('Email already confirmed.', 'info')
        return redirect(url_for('game.dashboard'))

    token = generate_confirmation_token(user_data['email'])
    confirm_url = url_for('auth.confirm_email', token=token, _external=True)
    send_confirmation_email(user_data['email'], user_data['username'], confirm_url)
    flash('Confirmation email resent! Check your inbox.', 'success')
    return redirect(url_for('game.dashboard'))


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('game.dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user_data = db.users.find_one({'username_lower': username.lower()})
        if user_data and user_data.get('password') and bcrypt.checkpw(password.encode('utf-8'), user_data['password']):
            login_user(User(user_data), remember=True)
            return redirect(url_for('game.dashboard'))
        flash('Invalid username or password.', 'error')
    return render_template('login.html')


@auth_bp.route('/login/google')
def google_login():
    """Initiate Google OAuth flow."""
    nonce = secrets.token_urlsafe(16)
    session['oauth_nonce'] = nonce
    redirect_uri = url_for('auth.google_callback', _external=True)
    return oauth.google.authorize_redirect(redirect_uri, nonce=nonce)


@auth_bp.route('/login/google/callback')
def google_callback():
    """Handle Google OAuth callback."""
    try:
        token = oauth.google.authorize_access_token()
        nonce = session.pop('oauth_nonce', None)
        user_info = oauth.google.parse_id_token(token, nonce=nonce)
    except Exception as e:
        flash(f'Google login failed. Please try again.', 'error')
        return redirect(url_for('auth.login'))

    if not user_info:
        flash('Could not get user info from Google.', 'error')
        return redirect(url_for('auth.login'))

    google_email = user_info.get('email', '').lower()
    google_name = user_info.get('name', '')
    google_sub = user_info.get('sub', '')

    if not google_email:
        flash('No email provided by Google.', 'error')
        return redirect(url_for('auth.login'))

    # Check if user exists
    user_data = db.users.find_one({'email': google_email})

    if user_data:
        if not user_data.get('google_id'):
            db.users.update_one({'_id': user_data['_id']}, {
                '$set': {'google_id': google_sub, 'email_confirmed': True}
            })
        login_user(User(user_data), remember=True)
        return redirect(url_for('game.dashboard'))

    # New user from Google
    base_username = google_name.replace(' ', '').lower()[:15] or 'player'
    username = base_username
    counter = 1
    while db.users.find_one({'username_lower': username.lower()}):
        username = f'{base_username}{counter}'
        counter += 1

    wallet_address, wallet_secret = generate_wallet()
    user_referral_code = secrets.token_urlsafe(8)

    user_doc = {
        'username': username,
        'username_lower': username.lower(),
        'email': google_email,
        'password': None,
        'google_id': google_sub,
        'rating': 1200,
        'games_played': 0,
        'wins': 0,
        'losses': 0,
        'draws': 0,
        'friends': [],
        'friend_requests': [],
        'created_at': datetime.utcnow(),
        'online': False,
        'wallet_address': wallet_address,
        'wallet_secret': wallet_secret,
        'sol_balance': 0.0,
        'total_wagered': 0.0,
        'total_won': 0.0,
        'total_lost': 0.0,
        'bet_wins': 0,
        'bet_losses': 0,
        'bet_draws': 0,
        'net_profit': 0.0,
        'email_confirmed': True,
        'auth_provider': 'google',
        'referral_code': user_referral_code,
        'referred_by': None,
        'referral_count': 0,
        'referral_earnings': 0.0,
    }

    # Check for referral in session
    ref_code = session.pop('referral_code', None)
    if ref_code:
        referrer = db.users.find_one({'referral_code': ref_code})
        if referrer:
            user_doc['referred_by'] = str(referrer['_id'])
            db.users.update_one({'_id': referrer['_id']}, {'$inc': {'referral_count': 1}})

    result = db.users.insert_one(user_doc)
    user_doc['_id'] = result.inserted_id
    login_user(User(user_doc), remember=True)
    flash('Welcome to Chess! Your account and Solana wallet have been created.', 'success')
    return redirect(url_for('game.dashboard'))


@auth_bp.route('/logout')
@login_required
def logout():
    db.users.update_one({'_id': ObjectId(current_user.id)}, {'$set': {'online': False}})
    logout_user()
    return redirect(url_for('auth.index'))
