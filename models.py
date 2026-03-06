from flask_login import UserMixin
from bson import ObjectId


class User(UserMixin):
    def __init__(self, user_data):
        self._data = user_data
        self._id = str(user_data['_id'])

    def get_id(self):
        return self._id

    @property
    def id(self):
        return self._id

    @property
    def username(self):
        return self._data.get('username', '')

    @property
    def email(self):
        return self._data.get('email', '')

    @property
    def rating(self):
        return self._data.get('rating', 1200)

    @property
    def sol_balance(self):
        return self._data.get('sol_balance', 0.0)

    @property
    def wallet_address(self):
        return self._data.get('wallet_address', '')

    @property
    def data(self):
        return self._data
