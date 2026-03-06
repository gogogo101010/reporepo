import os
import secrets

from flask import Flask
from bson import ObjectId

from extensions import socketio, login_manager, db
from models import User

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['WTF_CSRF_ENABLED'] = False

# Initialize extensions
socketio.init_app(app)
login_manager.init_app(app)


@login_manager.user_loader
def load_user(user_id):
    user_data = db.users.find_one({'_id': ObjectId(user_id)})
    if user_data:
        return User(user_data)
    return None


# Register blueprints
from routes.auth import auth_bp
from routes.game import game_bp
from routes.social import social_bp
from routes.wallet_routes import wallet_bp

app.register_blueprint(auth_bp)
app.register_blueprint(game_bp)
app.register_blueprint(social_bp)
app.register_blueprint(wallet_bp)

# Import socket event handlers (registers them via @socketio.on decorators)
import sockets.game_events
import sockets.matchmaking
import sockets.chat_events


if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
