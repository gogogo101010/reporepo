from datetime import datetime

from flask_socketio import emit, join_room
from flask_login import current_user

from extensions import socketio, db


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


@socketio.on('game_chat')
def handle_game_chat(data):
    game_id = data.get('game_id')
    message = data.get('message', '').strip()
    if not message or len(message) > 500:
        return
    msg_doc = {
        'game_id': game_id,
        'sender_id': current_user.id,
        'sender': current_user.username,
        'message': message,
        'created_at': datetime.utcnow(),
    }
    db.game_messages.insert_one(msg_doc)
    emit('game_chat_msg', {
        'sender': current_user.username,
        'message': message,
        'time': datetime.utcnow().strftime('%H:%M'),
    }, room=f'game_{game_id}')
