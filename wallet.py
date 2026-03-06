"""Solana wallet generation and helpers."""
from solders.keypair import Keypair
import base58


def generate_wallet():
    """Generate a new Solana keypair. Returns (public_key, secret_key_b58)."""
    kp = Keypair()
    public_key = str(kp.pubkey())
    secret_key = base58.b58encode(bytes(kp)).decode('utf-8')
    return public_key, secret_key


# Valid bet tiers in USD equivalent (SOL amount calculated at runtime)
BET_TIERS = [1, 5, 10, 25, 50, 100, 1000]

# House edge: 10% of total pot
HOUSE_EDGE_PERCENT = 10
WINNER_PAYOUT_PERCENT = 90
