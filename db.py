import os
from pymongo import MongoClient
import redis


_db = None
_indexes_created = False


def get_db():
    global _db, _indexes_created
    if _db is None:
        client = MongoClient(
            os.environ.get('MONGO_URI', 'mongodb://localhost:27017/'),
            serverSelectionTimeoutMS=5000,
        )
        _db = client[os.environ.get('MONGO_DB', 'chess_app')]
    if not _indexes_created:
        try:
            _db.users.create_index('username_lower', unique=True)
            _db.users.create_index('email', unique=True)
            _db.games.create_index('game_id', unique=True)
            _db.games.create_index([('white_id', 1), ('created_at', -1)])
            _db.games.create_index([('black_id', 1), ('created_at', -1)])
            _db.messages.create_index([('room', 1), ('created_at', 1)])
            _indexes_created = True
        except Exception:
            pass  # Indexes will be created on next successful connection
    return _db


def get_redis():
    return redis.Redis(
        host=os.environ.get('REDIS_HOST', 'localhost'),
        port=int(os.environ.get('REDIS_PORT', 6379)),
        db=int(os.environ.get('REDIS_DB', 0)),
        decode_responses=True,
    )
