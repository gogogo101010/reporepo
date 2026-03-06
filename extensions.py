"""Shared app extensions and helpers — imported by routes and sockets."""
import os
from datetime import datetime

from flask_socketio import SocketIO
from flask_login import LoginManager
from bson import ObjectId

from db import get_db, get_redis
from wallet import HOUSE_EDGE_PERCENT, WINNER_PAYOUT_PERCENT

socketio = SocketIO(cors_allowed_origins="*", async_mode='eventlet')
login_manager = LoginManager()
login_manager.login_view = 'auth.login'

rd = get_redis()
db = get_db()

SOL_USD_RATE = float(os.environ.get('SOL_USD_RATE', '150.0'))

# Shared in-memory state
online_users = {}           # sid -> user_id
matchmaking_queues = {}     # str(bet_usd) -> list of player dicts


# ─── Helpers ────────────────────────────────────────────────────────

def usd_to_sol(usd_amount):
    return round(usd_amount / SOL_USD_RATE, 6)


def sol_to_usd(sol_amount):
    return round(sol_amount * SOL_USD_RATE, 2)


def json_serial(obj):
    if isinstance(obj, ObjectId):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


def record_bet(player_id, game_id, bet_usd, bet_sol, result, profit, payout):
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


def record_transaction(user_id, tx_type, amount, description):
    db.transactions.insert_one({
        'user_id': user_id,
        'type': tx_type,
        'amount': amount,
        'description': description,
        'created_at': datetime.utcnow(),
    })


def opponent_name(game_data, player_id):
    if player_id == game_data['white_id']:
        return game_data['black_name']
    return game_data['white_name']


def save_finished_game(game_data):
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

    for pid, color in [(game_data['white_id'], 'white'), (game_data['black_id'], 'black')]:
        if pid == 'bot':
            continue

        update = {'$inc': {'games_played': 1}}

        if game_data.get('winner') == 'draw':
            update['$inc']['draws'] = 1
            if is_paid_game:
                refund = bet_sol
                update['$inc']['sol_balance'] = refund
                update['$inc']['bet_draws'] = 1
                record_bet(pid, game_data['game_id'], bet_usd, bet_sol, 'draw', 0, refund)
                record_transaction(pid, 'bet_refund', refund,
                                   f'Draw refund - ${bet_usd} game vs {opponent_name(game_data, pid)}')
        elif game_data.get('winner') == color:
            update['$inc']['wins'] = 1
            update['$inc']['rating'] = 15
            if is_paid_game:
                total_pot = bet_sol * 2
                payout = round(total_pot * WINNER_PAYOUT_PERCENT / 100, 6)
                profit = round(payout - bet_sol, 6)
                update['$inc']['sol_balance'] = payout
                update['$inc']['total_won'] = payout
                update['$inc']['bet_wins'] = 1
                update['$inc']['net_profit'] = profit
                record_bet(pid, game_data['game_id'], bet_usd, bet_sol, 'won', profit, payout)
                record_transaction(pid, 'bet_win', payout,
                                   f'Won ${bet_usd} game vs {opponent_name(game_data, pid)} (+{profit:.4f} SOL)')
        else:
            update['$inc']['losses'] = 1
            update['$inc']['rating'] = -10
            if is_paid_game:
                update['$inc']['total_lost'] = bet_sol
                update['$inc']['bet_losses'] = 1
                update['$inc']['net_profit'] = -bet_sol
                record_bet(pid, game_data['game_id'], bet_usd, bet_sol, 'lost', -bet_sol, 0)
                record_transaction(pid, 'bet_loss', 0,
                                   f'Lost ${bet_usd} game vs {opponent_name(game_data, pid)} (-{bet_sol:.4f} SOL)')

        db.users.update_one({'_id': ObjectId(pid)}, update)

    if is_paid_game and game_data.get('winner') != 'draw':
        house_cut = round(bet_sol * 2 * HOUSE_EDGE_PERCENT / 100, 6)
        db.house_revenue.insert_one({
            'game_id': game_data['game_id'],
            'amount': house_cut,
            'bet_usd': bet_usd,
            'created_at': datetime.utcnow(),
        })
