# ruff: noqa
from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from ..config import settings


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """
    Create a JWT access token with specified data and expiration.

    Args:
        data: Dictionary of data to encode in the token (typically user info)
        expires_delta: Optional custom expiration time, defaults to 15 minutes

    Returns:
        str: Encoded JWT token

    Note:
        Token includes 'exp' claim for expiration time
        Uses settings.secret_key and settings.algorithm from configuration
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now() + expires_delta
    else:
        expire = datetime.now() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode, settings.secret_key, algorithm=settings.algorithm
    )
    return encoded_jwt


def verify_token(token: str) -> Optional[dict]:
    """
    Verify and decode a JWT token.

    Args:
        token: JWT token string to verify

    Returns:
        dict: Decoded token payload if valid, None if invalid

    Note:
        Returns None for any JWT error (expired, invalid signature, etc.)
        Uses settings.secret_key and settings.algorithm from configuration
    """
    try:
        payload = jwt.decode(
            token, settings.secret_key, algorithms=[settings.algorithm]
        )
        return payload
    except JWTError:
        return None
