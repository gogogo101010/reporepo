import os
import secrets
from datetime import datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_wtf import CSRFProtect
import bcrypt
import chess
import redis
import json
from bson import ObjectId
from functools import wraps

from db import get_db, get_redis
from models import User
from game_engine import ChessGame, BotEngine
from wallet import generate_wallet, BET_TIERS, HOUSE_EDGE_PERCENT, WINNER_PAYOUT_PERCENT

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['WTF_CSRF_ENABLED'] = False  # We handle CSRF via SocketIO

socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')
login_manager = LoginManager(app)
login_manager.login_view = 'login'

# Store active games in Redis
rd = get_redis()
db = get_db()

# SOL/USD rate (in production, fetch from an oracle/API)
SOL_USD_RATE = float(os.environ.get('SOL_USD_RATE', '150.0'))

# ─── Helpers ────────────────────────────────────────────────────────

def json_serial(obj):
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def usd_to_sol(usd_amount):
    """Convert USD bet amount to SOL."""
    return round(usd_amount / SOL_USD_RATE, 6)


def sol_to_usd(sol_amount):
    """Convert SOL to USD."""
    return round(sol_amount * SOL_USD_RATE, 2)


@login_manager.user_loader
def load_user(user_id):
    user_data = db.users.find_one({'_id': ObjectId(user_id)})
    if user_data:
        return User(user_data)
    return None


# ─── Auth Routes ────────────────────────────────────────────────────

@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
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

        # Generate Solana wallet for the user
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
            # Wallet & betting
            'wallet_address': wallet_address,
            'wallet_secret': wallet_secret,  # encrypted in production
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
        return redirect(url_for('dashboard'))
    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        user_data = db.users.find_one({'username_lower': username.lower()})
        if user_data and bcrypt.checkpw(password.encode('utf-8'), user_data['password']):
            login_user(User(user_data), remember=True)
            return redirect(url_for('dashboard'))
        flash('Invalid username or password.', 'error')
    return render_template('login.html')


@app.route('/logout')
@login_required
def logout():
    db.users.update_one({'_id': ObjectId(current_user.id)}, {'$set': {'online': False}})
    logout_user()
    return redirect(url_for('index'))


# ─── Dashboard ──────────────────────────────────────────────────────

@app.route('/dashboard')
@login_required
def dashboard():
    user_data = db.users.find_one({'_id': ObjectId(current_user.id)})
    # Get recent games
    recent_games = list(db.games.find(
        {'$or': [{'white_id': current_user.id}, {'black_id': current_user.id}]},
    ).sort('created_at', -1).limit(10))
    # Get friend requests
    friend_requests = []
    for uid in user_data.get('friend_requests', []):
        fr = db.users.find_one({'_id': ObjectId(uid)}, {'username': 1, 'rating': 1})
        if fr:
            friend_requests.append(fr)
    # Online friends
    friends_online = []
    for fid in user_data.get('friends', []):
        fr = db.users.find_one({'_id': ObjectId(fid)}, {'username': 1, 'rating': 1, 'online': 1})
        if fr:
            friends_online.append(fr)
    # Recent bets
    recent_bets = list(db.bets.find(
        {'player_id': current_user.id},
    ).sort('created_at', -1).limit(5))
    return render_template('dashboard.html', user=user_data, recent_games=recent_games,
                           friend_requests=friend_requests, friends=friends_online,
                           recent_bets=recent_bets, sol_rate=SOL_USD_RATE)


# ─── Wallet & Deposit ──────────────────────────────────────────────

@app.route('/wallet')
@login_required
def wallet_page():
    user_data = db.users.find_one({'_id': ObjectId(current_user.id)})
    transactions = list(db.transactions.find(
        {'user_id': current_user.id}
    ).sort('created_at', -1).limit(50))
    bet_history = list(db.bets.find(
        {'player_id': current_user.id}
    ).sort('created_at', -1).limit(50))
    return render_template('wallet.html', user=user_data, transactions=transactions,
                           bet_history=bet_history, sol_rate=SOL_USD_RATE,
                           bet_tiers=BET_TIERS)


@app.route('/wallet/deposit', methods=['POST'])
@login_required
def deposit():
    """Simulate a SOL deposit (in production, verify on-chain tx)."""
    try:
        amount = float(request.form.get('amount', 0))
    except (ValueError, TypeError):
        flash('Invalid amount.', 'error')
        return redirect(url_for('wallet_page'))

    if amount <= 0 or amount > 1000:
        flash('Amount must be between 0 and 1000 SOL.', 'error')
        return redirect(url_for('wallet_page'))

    db.users.update_one(
        {'_id': ObjectId(current_user.id)},
        {'$inc': {'sol_balance': amount}}
    )
    db.transactions.insert_one({
        'user_id': current_user.id,
        'type': 'deposit',
        'amount': amount,
        'description': f'Deposited {amount} SOL',
        'created_at': datetime.utcnow(),
    })
    flash(f'Successfully deposited {amount} SOL!', 'success')
    return redirect(url_for('wallet_page'))


@app.route('/wallet/withdraw', methods=['POST'])
@login_required
def withdraw():
    """Simulate a SOL withdrawal."""
    try:
        amount = float(request.form.get('amount', 0))
    except (ValueError, TypeError):
        flash('Invalid amount.', 'error')
        return redirect(url_for('wallet_page'))

    user_data = db.users.find_one({'_id': ObjectId(current_user.id)})
    balance = user_data.get('sol_balance', 0)

    if amount <= 0:
        flash('Amount must be positive.', 'error')
        return redirect(url_for('wallet_page'))

    if amount > balance:
        flash('Insufficient balance.', 'error')
        return redirect(url_for('wallet_page'))

    withdraw_address = request.form.get('address', '').strip()
    if not withdraw_address:
        flash('Withdrawal address is required.', 'error')
        return redirect(url_for('wallet_page'))

    db.users.update_one(
        {'_id': ObjectId(current_user.id)},
        {'$inc': {'sol_balance': -amount}}
    )
    db.transactions.insert_one({
        'user_id': current_user.id,
        'type': 'withdrawal',
        'amount': amount,
        'address': withdraw_address,
        'description': f'Withdrew {amount} SOL to {withdraw_address[:8]}...',
        'created_at': datetime.utcnow(),
    })
    flash(f'Successfully withdrew {amount} SOL!', 'success')
    return redirect(url_for('wallet_page'))


# ─── Play vs Bot (Practice - Free) ─────────────────────────────────

@app.route('/play/bot', methods=['GET', 'POST'])
@login_required
def play_bot():
    if request.method == 'POST':
        difficulty = request.form.get('difficulty', 'medium')
        color = request.form.get('color', 'white')
        game = ChessGame()
        game_id = secrets.token_urlsafe(12)
        game_data = {
            'game_id': game_id,
            'type': 'bot',
            'white_id': current_user.id if color == 'white' else 'bot',
            'black_id': 'bot' if color == 'white' else current_user.id,
            'white_name': current_user.username if color == 'white' else f'Bot ({difficulty})',
            'black_name': f'Bot ({difficulty})' if color == 'white' else current_user.username,
            'fen': game.board.fen(),
            'pgn': '',
            'moves': [],
            'status': 'active',
            'difficulty': difficulty,
            'player_color': color,
            'bet_amount': 0,
            'bet_usd': 0,
            'created_at': datetime.utcnow().isoformat(),
        }
        # If player chose black, bot makes first move
        if color == 'black':
            board = game.board
            bot = BotEngine(difficulty)
            bot_move = bot.get_move(board)
            if bot_move:
                san = board.san(bot_move)
                board.push(bot_move)
                game_data['moves'].append({'uci': bot_move.uci(), 'san': san, 'fen': board.fen()})
                game_data['fen'] = board.fen()

        rd.setex(f'game:{game_id}', 3600, json.dumps(game_data))
        return redirect(url_for('game_page', game_id=game_id))
    return render_template('play_bot.html')


# ─── Play vs Player (Betting Matchmaking) ──────────────────────────

@app.route('/play/online')
@login_required
def play_online():
    user_data = db.users.find_one({'_id': ObjectId(current_user.id)})
    balance = user_data.get('sol_balance', 0)
    return render_template('play_online.html', bet_tiers=BET_TIERS,
                           sol_rate=SOL_USD_RATE, balance=balance,
                           house_edge=HOUSE_EDGE_PERCENT)


# ─── Game Page ──────────────────────────────────────────────────────

@app.route('/game/<game_id>')
@login_required
def game_page(game_id):
    game_json = rd.get(f'game:{game_id}')
    if not game_json:
        game_data = db.games.find_one({'game_id': game_id})
        if not game_data:
            flash('Game not found.', 'error')
            return redirect(url_for('dashboard'))
        game_data['_id'] = str(game_data['_id'])
    else:
        game_data = json.loads(game_json)
    return render_template('game.html', game=game_data, game_id=game_id,
                           house_edge=HOUSE_EDGE_PERCENT, sol_rate=SOL_USD_RATE)


# ─── Friends ────────────────────────────────────────────────────────

@app.route('/friends')
@login_required
def friends_page():
    user_data = db.users.find_one({'_id': ObjectId(current_user.id)})
    friends = []
    for fid in user_data.get('friends', []):
        fr = db.users.find_one({'_id': ObjectId(fid)}, {'username': 1, 'rating': 1, 'online': 1})
        if fr:
            friends.append(fr)
    friend_requests = []
    for uid in user_data.get('friend_requests', []):
        fr = db.users.find_one({'_id': ObjectId(uid)}, {'username': 1, 'rating': 1})
        if fr:
            friend_requests.append(fr)
    return render_template('friends.html', friends=friends, friend_requests=friend_requests)


@app.route('/friends/add', methods=['POST'])
@login_required
def add_friend():
    username = request.form.get('username', '').strip()
    target = db.users.find_one({'username_lower': username.lower()})
    if not target:
        flash('User not found.', 'error')
        return redirect(url_for('friends_page'))
    if str(target['_id']) == current_user.id:
        flash('You cannot add yourself.', 'error')
        return redirect(url_for('friends_page'))
    user_data = db.users.find_one({'_id': ObjectId(current_user.id)})
    if str(target['_id']) in user_data.get('friends', []):
        flash('Already friends!', 'info')
        return redirect(url_for('friends_page'))
    if current_user.id in target.get('friend_requests', []):
        flash('Friend request already sent.', 'info')
        return redirect(url_for('friends_page'))
    db.users.update_one({'_id': target['_id']}, {'$addToSet': {'friend_requests': current_user.id}})
    flash(f'Friend request sent to {target["username"]}!', 'success')
    return redirect(url_for('friends_page'))


@app.route('/friends/accept/<user_id>', methods=['POST'])
@login_required
def accept_friend(user_id):
    db.users.update_one({'_id': ObjectId(current_user.id)},
                        {'$pull': {'friend_requests': user_id}, '$addToSet': {'friends': user_id}})
    db.users.update_one({'_id': ObjectId(user_id)},
                        {'$addToSet': {'friends': current_user.id}})
    flash('Friend request accepted!', 'success')
    return redirect(url_for('friends_page'))


@app.route('/friends/decline/<user_id>', methods=['POST'])
@login_required
def decline_friend(user_id):
    db.users.update_one({'_id': ObjectId(current_user.id)},
                        {'$pull': {'friend_requests': user_id}})
    flash('Friend request declined.', 'info')
    return redirect(url_for('friends_page'))


@app.route('/friends/remove/<user_id>', methods=['POST'])
@login_required
def remove_friend(user_id):
    db.users.update_one({'_id': ObjectId(current_user.id)}, {'$pull': {'friends': user_id}})
    db.users.update_one({'_id': ObjectId(user_id)}, {'$pull': {'friends': current_user.id}})
    flash('Friend removed.', 'info')
    return redirect(url_for('friends_page'))


# ─── Chat ───────────────────────────────────────────────────────────

@app.route('/chat/<friend_id>')
@login_required
def chat_page(friend_id):
    friend = db.users.find_one({'_id': ObjectId(friend_id)}, {'username': 1, 'online': 1})
    if not friend:
        flash('User not found.', 'error')
        return redirect(url_for('friends_page'))
    room = '_'.join(sorted([current_user.id, friend_id]))
    messages = list(db.messages.find({'room': room}).sort('created_at', 1).limit(100))
    return render_template('chat.html', friend=friend, messages=messages, room=room)


# ─── Profile ────────────────────────────────────────────────────────

@app.route('/profile/<username>')
@login_required
def profile(username):
    user_data = db.users.find_one({'username_lower': username.lower()})
    if not user_data:
        flash('User not found.', 'error')
        return redirect(url_for('dashboard'))
    games = list(db.games.find(
        {'$or': [{'white_id': str(user_data['_id'])}, {'black_id': str(user_data['_id'])}]},
    ).sort('created_at', -1).limit(20))
    return render_template('profile.html', profile_user=user_data, games=games,
                           sol_rate=SOL_USD_RATE)


# ─── Leaderboard ────────────────────────────────────────────────────

@app.route('/leaderboard')
@login_required
def leaderboard():
    top_players = list(db.users.find(
        {}, {'username': 1, 'rating': 1, 'wins': 1, 'losses': 1, 'draws': 1,
             'games_played': 1, 'total_won': 1, 'net_profit': 1, 'sol_balance': 1}
    ).sort('rating', -1).limit(50))
    return render_template('leaderboard.html', players=top_players, sol_rate=SOL_USD_RATE)


# ─── Socket.IO Events ──────────────────────────────────────────────

online_users = {}  # sid -> user_id
matchmaking_queues = {}  # bet_usd -> list of {sid, user_id, username, rating}


@socketio.on('connect')
def handle_connect():
    if current_user.is_authenticated:
        online_users[request.sid] = current_user.id
        db.users.update_one({'_id': ObjectId(current_user.id)}, {'$set': {'online': True}})


@socketio.on('disconnect')
def handle_disconnect():
    uid = online_users.pop(request.sid, None)
    if uid:
        db.users.update_one({'_id': ObjectId(uid)}, {'$set': {'online': False}})
        # Remove from all matchmaking queues and refund escrowed bets
        for tier_key, queue in matchmaking_queues.items():
            for p in queue:
                if p['sid'] == request.sid and p.get('escrowed'):
                    db.users.update_one(
                        {'_id': ObjectId(p['user_id'])},
                        {'$inc': {'sol_balance': p['bet_sol']}}
                    )
            matchmaking_queues[tier_key] = [p for p in queue if p['sid'] != request.sid]


@socketio.on('join_game')
def handle_join_game(data):
    game_id = data.get('game_id')
    join_room(f'game_{game_id}')
    game_json = rd.get(f'game:{game_id}')
    if game_json:
        game_data = json.loads(game_json)
        emit('game_state', game_data)


@socketio.on('make_move')
def handle_make_move(data):
    game_id = data.get('game_id')
    move_uci = data.get('move')

    game_json = rd.get(f'game:{game_id}')
    if not game_json:
        emit('error', {'message': 'Game not found'})
        return

    game_data = json.loads(game_json)
    if game_data['status'] != 'active':
        emit('error', {'message': 'Game is over'})
        return

    board = chess.Board(game_data['fen'])

    try:
        move = chess.Move.from_uci(move_uci)
        if move not in board.legal_moves:
            # Check for promotion
            promo_move = chess.Move.from_uci(move_uci + 'q')
            if promo_move in board.legal_moves:
                move = promo_move
            else:
                emit('error', {'message': 'Illegal move'})
                return
    except ValueError:
        emit('error', {'message': 'Invalid move format'})
        return

    san = board.san(move)
    board.push(move)

    game_data['moves'].append({'uci': move.uci(), 'san': san, 'fen': board.fen()})
    game_data['fen'] = board.fen()

    # Check game end
    if board.is_checkmate():
        winner = 'black' if board.turn == chess.WHITE else 'white'
        game_data['status'] = 'checkmate'
        game_data['winner'] = winner
        _save_finished_game(game_data)
    elif board.is_stalemate():
        game_data['status'] = 'stalemate'
        game_data['winner'] = 'draw'
        _save_finished_game(game_data)
    elif board.is_insufficient_material():
        game_data['status'] = 'draw'
        game_data['winner'] = 'draw'
        _save_finished_game(game_data)
    elif board.can_claim_fifty_moves():
        game_data['status'] = 'draw'
        game_data['winner'] = 'draw'
        _save_finished_game(game_data)

    rd.setex(f'game:{game_id}', 3600, json.dumps(game_data))
    emit('game_state', game_data, room=f'game_{game_id}')

    # Bot response
    if game_data['type'] == 'bot' and game_data['status'] == 'active':
        _handle_bot_move(game_id, game_data)


def _handle_bot_move(game_id, game_data):
    board = chess.Board(game_data['fen'])
    bot = BotEngine(game_data.get('difficulty', 'medium'))
    bot_move = bot.get_move(board)
    if bot_move:
        san = board.san(bot_move)
        board.push(bot_move)
        game_data['moves'].append({'uci': bot_move.uci(), 'san': san, 'fen': board.fen()})
        game_data['fen'] = board.fen()

        if board.is_checkmate():
            winner = 'black' if board.turn == chess.WHITE else 'white'
            game_data['status'] = 'checkmate'
            game_data['winner'] = winner
            _save_finished_game(game_data)
        elif board.is_stalemate():
            game_data['status'] = 'stalemate'
            game_data['winner'] = 'draw'
            _save_finished_game(game_data)
        elif board.is_insufficient_material():
            game_data['status'] = 'draw'
            game_data['winner'] = 'draw'
            _save_finished_game(game_data)

        rd.setex(f'game:{game_id}', 3600, json.dumps(game_data))
        socketio.emit('game_state', game_data, room=f'game_{game_id}')


def _save_finished_game(game_data):
    """Save finished game to MongoDB, process bets, and update stats."""
    bet_sol = game_data.get('bet_amount', 0)
    bet_usd = game_data.get('bet_usd', 0)
    is_paid_game = bet_sol > 0

    game_doc = {
        'game_id': game_data['game_id'],
        'type': game_data['type'],
        'white_id': game_data['white_id'],
        'black_id': game_data['black_id'],
        'white_name': game_data['white_name'],
        'black_name': game_data['black_name'],
        'moves': game_data['moves'],
        'status': game_data['status'],
        'winner': game_data.get('winner'),
        'bet_amount': bet_sol,
        'bet_usd': bet_usd,
        'created_at': datetime.utcnow(),
    }
    db.games.insert_one(game_doc)

    # Update player stats and process bet payouts
    for pid, color in [(game_data['white_id'], 'white'), (game_data['black_id'], 'black')]:
        if pid == 'bot':
            continue

        update = {'$inc': {'games_played': 1}}

        if game_data.get('winner') == 'draw':
            update['$inc']['draws'] = 1
            if is_paid_game:
                refund = bet_sol  # full refund on draw
                update['$inc']['sol_balance'] = refund
                update['$inc']['bet_draws'] = 1
                _record_bet(pid, game_data['game_id'], bet_usd, bet_sol, 'draw', 0, refund)
                _record_transaction(pid, 'bet_refund', refund,
                                    f'Draw refund - ${bet_usd} game vs {_opponent_name(game_data, pid)}')
        elif game_data.get('winner') == color:
            update['$inc']['wins'] = 1
            update['$inc']['rating'] = 15
            if is_paid_game:
                # Winner gets 90% of total pot (both stakes combined)
                total_pot = bet_sol * 2
                payout = round(total_pot * WINNER_PAYOUT_PERCENT / 100, 6)
                profit = round(payout - bet_sol, 6)
                update['$inc']['sol_balance'] = payout
                update['$inc']['total_won'] = payout
                update['$inc']['bet_wins'] = 1
                update['$inc']['net_profit'] = profit
                _record_bet(pid, game_data['game_id'], bet_usd, bet_sol, 'won', profit, payout)
                _record_transaction(pid, 'bet_win', payout,
                                    f'Won ${bet_usd} game vs {_opponent_name(game_data, pid)} (+{profit:.4f} SOL)')
        else:
            update['$inc']['losses'] = 1
            update['$inc']['rating'] = -10
            if is_paid_game:
                update['$inc']['total_lost'] = bet_sol
                update['$inc']['bet_losses'] = 1
                update['$inc']['net_profit'] = -bet_sol
                _record_bet(pid, game_data['game_id'], bet_usd, bet_sol, 'lost', -bet_sol, 0)
                _record_transaction(pid, 'bet_loss', 0,
                                    f'Lost ${bet_usd} game vs {_opponent_name(game_data, pid)} (-{bet_sol:.4f} SOL)')

        db.users.update_one({'_id': ObjectId(pid)}, update)

    # Record house edge revenue
    if is_paid_game and game_data.get('winner') != 'draw':
        house_cut = round(bet_sol * 2 * HOUSE_EDGE_PERCENT / 100, 6)
        db.house_revenue.insert_one({
            'game_id': game_data['game_id'],
            'amount': house_cut,
            'bet_usd': bet_usd,
            'created_at': datetime.utcnow(),
        })


def _opponent_name(game_data, player_id):
    if player_id == game_data['white_id']:
        return game_data['black_name']
    return game_data['white_name']


def _record_bet(player_id, game_id, bet_usd, bet_sol, result, profit, payout):
    db.bets.insert_one({
        'player_id': player_id,
        'game_id': game_id,
        'bet_usd': bet_usd,
        'bet_sol': bet_sol,
        'result': result,
        'profit': profit,
        'payout': payout,
        'created_at': datetime.utcnow(),
    })


def _record_transaction(user_id, tx_type, amount, description):
    db.transactions.insert_one({
        'user_id': user_id,
        'type': tx_type,
        'amount': amount,
        'description': description,
        'created_at': datetime.utcnow(),
    })


@socketio.on('resign')
def handle_resign(data):
    game_id = data.get('game_id')
    game_json = rd.get(f'game:{game_id}')
    if not game_json:
        return
    game_data = json.loads(game_json)
    if game_data['status'] != 'active':
        return
    if current_user.id == game_data['white_id']:
        game_data['winner'] = 'black'
    else:
        game_data['winner'] = 'white'
    game_data['status'] = 'resigned'
    _save_finished_game(game_data)
    rd.setex(f'game:{game_id}', 3600, json.dumps(game_data))
    emit('game_state', game_data, room=f'game_{game_id}')


@socketio.on('offer_draw')
def handle_offer_draw(data):
    game_id = data.get('game_id')
    emit('draw_offered', {'by': current_user.username}, room=f'game_{game_id}')


@socketio.on('accept_draw')
def handle_accept_draw(data):
    game_id = data.get('game_id')
    game_json = rd.get(f'game:{game_id}')
    if not game_json:
        return
    game_data = json.loads(game_json)
    game_data['status'] = 'draw'
    game_data['winner'] = 'draw'
    _save_finished_game(game_data)
    rd.setex(f'game:{game_id}', 3600, json.dumps(game_data))
    emit('game_state', game_data, room=f'game_{game_id}')


# ─── Betting Matchmaking ───────────────────────────────────────────

@socketio.on('find_match')
def handle_find_match(data):
    bet_usd = data.get('bet_usd', 0)

    # Validate bet tier
    if bet_usd not in BET_TIERS:
        emit('error', {'message': f'Invalid bet amount. Choose from: {BET_TIERS}'})
        return

    bet_sol = usd_to_sol(bet_usd)

    # Check balance
    user_data = db.users.find_one({'_id': ObjectId(current_user.id)})
    balance = user_data.get('sol_balance', 0)
    if balance < bet_sol:
        emit('error', {'message': f'Insufficient balance. Need {bet_sol:.4f} SOL (${bet_usd}). You have {balance:.4f} SOL.'})
        return

    player = {
        'sid': request.sid,
        'user_id': current_user.id,
        'username': current_user.username,
        'rating': user_data.get('rating', 1200),
        'bet_usd': bet_usd,
        'bet_sol': bet_sol,
    }

    tier_key = str(bet_usd)
    if tier_key not in matchmaking_queues:
        matchmaking_queues[tier_key] = []
    queue = matchmaking_queues[tier_key]

    # Check if already in any queue
    for tkey, q in matchmaking_queues.items():
        for p in q:
            if p['user_id'] == current_user.id:
                emit('match_status', {'status': 'waiting', 'bet_usd': bet_usd})
                return

    # Try to find a match in same tier
    best_match = None
    best_diff = float('inf')
    for p in queue:
        diff = abs(p['rating'] - player['rating'])
        if diff < best_diff:
            best_diff = diff
            best_match = p

    if best_match:
        queue[:] = [p for p in queue if p['sid'] != best_match['sid']]

        # Deduct from matched player (if escrowed, already deducted)
        if not best_match.get('escrowed'):
            db.users.update_one(
                {'_id': ObjectId(best_match['user_id'])},
                {'$inc': {'sol_balance': -bet_sol, 'total_wagered': bet_sol}}
            )
        else:
            db.users.update_one(
                {'_id': ObjectId(best_match['user_id'])},
                {'$inc': {'total_wagered': bet_sol}}
            )

        # Deduct from current player
        db.users.update_one(
            {'_id': ObjectId(current_user.id)},
            {'$inc': {'sol_balance': -bet_sol, 'total_wagered': bet_sol}}
        )

        _record_transaction(best_match['user_id'], 'bet_placed', -bet_sol,
                            f'Bet placed: ${bet_usd} game vs {current_user.username}')
        _record_transaction(current_user.id, 'bet_placed', -bet_sol,
                            f'Bet placed: ${bet_usd} game vs {best_match["username"]}')

        # Create game
        game_id = secrets.token_urlsafe(12)
        game_data = {
            'game_id': game_id,
            'type': 'ranked',
            'white_id': best_match['user_id'],
            'black_id': current_user.id,
            'white_name': best_match['username'],
            'black_name': current_user.username,
            'fen': chess.STARTING_FEN,
            'moves': [],
            'status': 'active',
            'bet_amount': bet_sol,
            'bet_usd': bet_usd,
            'created_at': datetime.utcnow().isoformat(),
        }
        rd.setex(f'game:{game_id}', 7200, json.dumps(game_data))
        socketio.emit('match_found', {'game_id': game_id, 'bet_usd': bet_usd}, to=best_match['sid'])
        emit('match_found', {'game_id': game_id, 'bet_usd': bet_usd})
    else:
        # Escrow: deduct bet now (will be refunded if cancelled)
        db.users.update_one(
            {'_id': ObjectId(current_user.id)},
            {'$inc': {'sol_balance': -bet_sol}}
        )
        player['escrowed'] = True
        queue.append(player)
        emit('match_status', {'status': 'waiting', 'bet_usd': bet_usd, 'bet_sol': round(bet_sol, 4)})


@socketio.on('cancel_match')
def handle_cancel_match(data):
    for tier_key, queue in matchmaking_queues.items():
        for p in queue:
            if p['sid'] == request.sid:
                if p.get('escrowed'):
                    db.users.update_one(
                        {'_id': ObjectId(p['user_id'])},
                        {'$inc': {'sol_balance': p['bet_sol']}}
                    )
                queue[:] = [x for x in queue if x['sid'] != request.sid]
                break
    emit('match_status', {'status': 'cancelled'})


# ─── Chat Socket Events ────────────────────────────────────────────

@socketio.on('join_chat')
def handle_join_chat(data):
    room = data.get('room')
    join_room(room)


@socketio.on('send_message')
def handle_send_message(data):
    room = data.get('room')
    message = data.get('message', '').strip()
    if not message:
        return
    msg_doc = {
        'room': room,
        'sender_id': current_user.id,
        'sender_name': current_user.username,
        'message': message,
        'created_at': datetime.utcnow(),
    }
    db.messages.insert_one(msg_doc)
    emit('new_message', {
        'sender_name': current_user.username,
        'message': message,
        'created_at': datetime.utcnow().isoformat(),
    }, room=room)


# ─── Game Chat ──────────────────────────────────────────────────────

@socketio.on('game_chat')
def handle_game_chat(data):
    game_id = data.get('game_id')
    message = data.get('message', '').strip()
    if not message:
        return
    emit('game_chat_msg', {
        'sender': current_user.username,
        'message': message,
    }, room=f'game_{game_id}')


# ─── Challenge Friend ──────────────────────────────────────────────

@socketio.on('challenge_friend')
def handle_challenge_friend(data):
    friend_id = data.get('friend_id')
    bet_usd = data.get('bet_usd', 0)
    for sid, uid in online_users.items():
        if uid == friend_id:
            socketio.emit('game_challenge', {
                'from_id': current_user.id,
                'from_name': current_user.username,
                'bet_usd': bet_usd,
            }, to=sid)
            break


@socketio.on('accept_challenge')
def handle_accept_challenge(data):
    challenger_id = data.get('from_id')
    bet_usd = data.get('bet_usd', 0)
    bet_sol = usd_to_sol(bet_usd) if bet_usd > 0 else 0

    if bet_sol > 0:
        challenger_data = db.users.find_one({'_id': ObjectId(challenger_id)})
        accepter_data = db.users.find_one({'_id': ObjectId(current_user.id)})
        if challenger_data.get('sol_balance', 0) < bet_sol:
            emit('error', {'message': 'Challenger has insufficient balance.'})
            return
        if accepter_data.get('sol_balance', 0) < bet_sol:
            emit('error', {'message': 'You have insufficient balance.'})
            return
        db.users.update_one({'_id': ObjectId(challenger_id)},
                            {'$inc': {'sol_balance': -bet_sol, 'total_wagered': bet_sol}})
        db.users.update_one({'_id': ObjectId(current_user.id)},
                            {'$inc': {'sol_balance': -bet_sol, 'total_wagered': bet_sol}})

    game_id = secrets.token_urlsafe(12)
    challenger = db.users.find_one({'_id': ObjectId(challenger_id)})
    game_data = {
        'game_id': game_id,
        'type': 'ranked' if bet_sol > 0 else 'online',
        'white_id': challenger_id,
        'black_id': current_user.id,
        'white_name': challenger['username'] if challenger else 'Unknown',
        'black_name': current_user.username,
        'fen': chess.STARTING_FEN,
        'moves': [],
        'status': 'active',
        'bet_amount': bet_sol,
        'bet_usd': bet_usd,
        'created_at': datetime.utcnow().isoformat(),
    }
    rd.setex(f'game:{game_id}', 7200, json.dumps(game_data))
    for sid, uid in online_users.items():
        if uid == challenger_id:
            socketio.emit('match_found', {'game_id': game_id}, to=sid)
    emit('match_found', {'game_id': game_id})


if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
