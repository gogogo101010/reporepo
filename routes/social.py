from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from bson import ObjectId

from extensions import db, SOL_USD_RATE

social_bp = Blueprint('social', __name__)


@social_bp.route('/friends')
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


@social_bp.route('/friends/add', methods=['POST'])
@login_required
def add_friend():
    username = request.form.get('username', '').strip()
    target = db.users.find_one({'username_lower': username.lower()})
    if not target:
        flash('User not found.', 'error')
        return redirect(url_for('social.friends_page'))
    if str(target['_id']) == current_user.id:
        flash('You cannot add yourself.', 'error')
        return redirect(url_for('social.friends_page'))
    user_data = db.users.find_one({'_id': ObjectId(current_user.id)})
    if str(target['_id']) in user_data.get('friends', []):
        flash('Already friends!', 'info')
        return redirect(url_for('social.friends_page'))
    if current_user.id in target.get('friend_requests', []):
        flash('Friend request already sent.', 'info')
        return redirect(url_for('social.friends_page'))
    db.users.update_one({'_id': target['_id']}, {'$addToSet': {'friend_requests': current_user.id}})
    flash(f'Friend request sent to {target["username"]}!', 'success')
    return redirect(url_for('social.friends_page'))


@social_bp.route('/friends/accept/<user_id>', methods=['POST'])
@login_required
def accept_friend(user_id):
    db.users.update_one({'_id': ObjectId(current_user.id)},
                        {'$pull': {'friend_requests': user_id}, '$addToSet': {'friends': user_id}})
    db.users.update_one({'_id': ObjectId(user_id)},
                        {'$addToSet': {'friends': current_user.id}})
    flash('Friend request accepted!', 'success')
    return redirect(url_for('social.friends_page'))


@social_bp.route('/friends/decline/<user_id>', methods=['POST'])
@login_required
def decline_friend(user_id):
    db.users.update_one({'_id': ObjectId(current_user.id)},
                        {'$pull': {'friend_requests': user_id}})
    flash('Friend request declined.', 'info')
    return redirect(url_for('social.friends_page'))


@social_bp.route('/friends/remove/<user_id>', methods=['POST'])
@login_required
def remove_friend(user_id):
    db.users.update_one({'_id': ObjectId(current_user.id)}, {'$pull': {'friends': user_id}})
    db.users.update_one({'_id': ObjectId(user_id)}, {'$pull': {'friends': current_user.id}})
    flash('Friend removed.', 'info')
    return redirect(url_for('social.friends_page'))


@social_bp.route('/chat/<friend_id>')
@login_required
def chat_page(friend_id):
    friend = db.users.find_one({'_id': ObjectId(friend_id)}, {'username': 1, 'online': 1})
    if not friend:
        flash('User not found.', 'error')
        return redirect(url_for('social.friends_page'))
    room = '_'.join(sorted([current_user.id, friend_id]))
    messages = list(db.messages.find({'room': room}).sort('created_at', 1).limit(100))
    return render_template('chat.html', friend=friend, messages=messages, room=room)


@social_bp.route('/profile/<username>')
@login_required
def profile(username):
    user_data = db.users.find_one({'username_lower': username.lower()})
    if not user_data:
        flash('User not found.', 'error')
        return redirect(url_for('game.dashboard'))
    games = list(db.games.find(
        {'$or': [{'white_id': str(user_data['_id'])}, {'black_id': str(user_data['_id'])}]},
    ).sort('created_at', -1).limit(20))
    return render_template('profile.html', profile_user=user_data, games=games,
                           sol_rate=SOL_USD_RATE)


@social_bp.route('/leaderboard')
@login_required
def leaderboard():
    top_players = list(db.users.find(
        {}, {'username': 1, 'rating': 1, 'wins': 1, 'losses': 1, 'draws': 1,
             'games_played': 1, 'total_won': 1, 'net_profit': 1, 'sol_balance': 1}
    ).sort('rating', -1).limit(50))
    return render_template('leaderboard.html', players=top_players, sol_rate=SOL_USD_RATE)


@social_bp.route('/referrals')
@login_required
def referrals():
    user_data = db.users.find_one({'_id': ObjectId(current_user.id)})
    referred_users = list(db.users.find(
        {'referred_by': current_user.id},
        {'username': 1, 'created_at': 1, 'games_played': 1, 'rating': 1}
    ).sort('created_at', -1))
    return render_template('referrals.html', user=user_data, referred_users=referred_users)
