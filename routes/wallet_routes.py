from datetime import datetime

from flask import Blueprint, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from bson import ObjectId

from extensions import db, SOL_USD_RATE
from wallet import BET_TIERS

wallet_bp = Blueprint('wallet', __name__)


@wallet_bp.route('/wallet')
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


@wallet_bp.route('/wallet/deposit', methods=['POST'])
@login_required
def deposit():
    try:
        amount = float(request.form.get('amount', 0))
    except (ValueError, TypeError):
        flash('Invalid amount.', 'error')
        return redirect(url_for('wallet.wallet_page'))

    if amount <= 0 or amount > 1000:
        flash('Amount must be between 0 and 1000 SOL.', 'error')
        return redirect(url_for('wallet.wallet_page'))

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
    return redirect(url_for('wallet.wallet_page'))


@wallet_bp.route('/wallet/withdraw', methods=['POST'])
@login_required
def withdraw():
    try:
        amount = float(request.form.get('amount', 0))
    except (ValueError, TypeError):
        flash('Invalid amount.', 'error')
        return redirect(url_for('wallet.wallet_page'))

    user_data = db.users.find_one({'_id': ObjectId(current_user.id)})
    balance = user_data.get('sol_balance', 0)

    if amount <= 0:
        flash('Amount must be positive.', 'error')
        return redirect(url_for('wallet.wallet_page'))

    if amount > balance:
        flash('Insufficient balance.', 'error')
        return redirect(url_for('wallet.wallet_page'))

    withdraw_address = request.form.get('address', '').strip()
    if not withdraw_address:
        flash('Withdrawal address is required.', 'error')
        return redirect(url_for('wallet.wallet_page'))

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
    return redirect(url_for('wallet.wallet_page'))
