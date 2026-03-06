from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_user, logout_user, login_required, current_user
import bcrypt
from bson import ObjectId

from extensions import db
from models import User
from wallet import generate_wallet

auth_bp = Blueprint('auth', __name__)


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
        }
        result = db.users.insert_one(user_doc)
        user_doc['_id'] = result.inserted_id
        login_user(User(user_doc))
        flash('Welcome to Chess! Your Solana wallet has been created.', 'success')
        return redirect(url_for('game.dashboard'))
    return render_template('register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('game.dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user_data = db.users.find_one({'username_lower': username.lower()})
        if user_data and bcrypt.checkpw(password.encode('utf-8'), user_data['password']):
            login_user(User(user_data), remember=True)
            return redirect(url_for('game.dashboard'))
        flash('Invalid username or password.', 'error')
    return render_template('login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    db.users.update_one({'_id': ObjectId(current_user.id)}, {'$set': {'online': False}})
    logout_user()
    return redirect(url_for('auth.index'))
