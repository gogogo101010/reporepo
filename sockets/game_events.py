import json

import chess
from flask_socketio import emit, join_room
from flask_login import current_user
from flask import request
from bson import ObjectId

from extensions import socketio, db, rd, online_users, save_finished_game
from game_engine import BotEngine


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
        # Refund escrowed bets
        from extensions import matchmaking_queues
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

    if board.is_checkmate():
        winner = 'black' if board.turn == chess.WHITE else 'white'
        game_data['status'] = 'checkmate'
        game_data['winner'] = winner
        save_finished_game(game_data)
    elif board.is_stalemate():
        game_data['status'] = 'stalemate'
        game_data['winner'] = 'draw'
        save_finished_game(game_data)
    elif board.is_insufficient_material():
        game_data['status'] = 'draw'
        game_data['winner'] = 'draw'
        save_finished_game(game_data)
    elif board.can_claim_fifty_moves():
        game_data['status'] = 'draw'
        game_data['winner'] = 'draw'
        save_finished_game(game_data)

    rd.setex(f'game:{game_id}', 3600, json.dumps(game_data))
    emit('game_state', game_data, room=f'game_{game_id}')

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
            save_finished_game(game_data)
        elif board.is_stalemate():
            game_data['status'] = 'stalemate'
            game_data['winner'] = 'draw'
            save_finished_game(game_data)
        elif board.is_insufficient_material():
            game_data['status'] = 'draw'
            game_data['winner'] = 'draw'
            save_finished_game(game_data)

        rd.setex(f'game:{game_id}', 3600, json.dumps(game_data))
        socketio.emit('game_state', game_data, room=f'game_{game_id}')


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
    save_finished_game(game_data)
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
    save_finished_game(game_data)
    rd.setex(f'game:{game_id}', 3600, json.dumps(game_data))
    emit('game_state', game_data, room=f'game_{game_id}')
