"""Fernet encryption of the stored Garmin session token."""
from cryptography.fernet import Fernet, InvalidToken

from .config import env


def _fernet() -> Fernet:
    return Fernet(env("ENCRYPTION_KEY", required=True).encode())


def generate_key() -> str:
    return Fernet.generate_key().decode()


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    """Return '' if the token cannot be decrypted (wrong key or corrupted data)."""
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError):
        return ""
