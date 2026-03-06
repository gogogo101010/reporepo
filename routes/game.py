import secrets
import json
from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from bson import ObjectId

from extensions import db, rd, SOL_USD_RATE
from game_engine import ChessGame, BotEngine
from wallet import BET_TIERS, HOUSE_EDGE_PERCENT

game_bp = Blueprint('game', __name__)


@game_bp.route('/dashboard')
@login_required
def dashboard():
    user_data = db.users.find_one({'_id': ObjectId(current_user.id)})
    recent_games = list(db.games.find(
        {'$or': [{'white_id': current_user.id}, {'black_id': current_user.id}]},
    ).sort('created_at', -1).limit(10))
    friend_requests = []
    for uid in user_data.get('friend_requests', []):
        fr = db.users.find_one({'_id': ObjectId(uid)}, {'username': 1, 'rating': 1})
        if fr:
            friend_requests.append(fr)
    friends_online = []
    for fid in user_data.get('friends', []):
        fr = db.users.find_one({'_id': ObjectId(fid)}, {'username': 1, 'rating': 1, 'online': 1})
        if fr:
            friends_online.append(fr)
    recent_bets = list(db.bets.find(
        {'player_id': current_user.id},
    ).sort('created_at', -1).limit(5))
    return render_template('dashboard.html', user=user_data, recent_games=recent_games,
                           friend_requests=friend_requests, friends=friends_online,
                           recent_bets=recent_bets, sol_rate=SOL_USD_RATE)


@game_bp.route('/play/bot', methods=['GET', 'POST'])
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
        return redirect(url_for('game.game_page', game_id=game_id))
    return render_template('play_bot.html')


@game_bp.route('/play/online')
@login_required
def play_online():
    user_data = db.users.find_one({'_id': ObjectId(current_user.id)})
    balance = user_data.get('sol_balance', 0)
    return render_template('play_online.html', bet_tiers=BET_TIERS,
                           sol_rate=SOL_USD_RATE, balance=balance,
                           house_edge=HOUSE_EDGE_PERCENT)


@game_bp.route('/game/<game_id>')
@login_required
def game_page(game_id):
    game_json = rd.get(f'game:{game_id}')
    if not game_json:
        game_data = db.games.find_one({'game_id': game_id})
        if not game_data:
            flash('Game not found.', 'error')
            return redirect(url_for('game.dashboard'))
        game_data['_id'] = str(game_data['_id'])
    else:
        game_data = json.loads(game_json)
    chat_messages = list(db.game_messages.find(
        {'game_id': game_id}
    ).sort('created_at', 1).limit(200))
    return render_template('game.html', game=game_data, game_id=game_id,
                           house_edge=HOUSE_EDGE_PERCENT, sol_rate=SOL_USD_RATE,
                           chat_messages=chat_messages)
