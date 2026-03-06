import secrets
import json
from datetime import datetime

import chess
from flask_socketio import emit
from flask_login import current_user
from flask import request
from bson import ObjectId

from extensions import (
    socketio, db, rd, online_users, matchmaking_queues,
    usd_to_sol, record_transaction,
)
from wallet import BET_TIERS


@socketio.on('find_match')
def handle_find_match(data):
    bet_usd = data.get('bet_usd', 0)

    if bet_usd not in BET_TIERS:
        emit('error', {'message': f'Invalid bet amount. Choose from: {BET_TIERS}'})
        return

    bet_sol = usd_to_sol(bet_usd)

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

        db.users.update_one(
            {'_id': ObjectId(current_user.id)},
            {'$inc': {'sol_balance': -bet_sol, 'total_wagered': bet_sol}}
        )

        record_transaction(best_match['user_id'], 'bet_placed', -bet_sol,
                           f'Bet placed: ${bet_usd} game vs {current_user.username}')
        record_transaction(current_user.id, 'bet_placed', -bet_sol,
                           f'Bet placed: ${bet_usd} game vs {best_match["username"]}')

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
