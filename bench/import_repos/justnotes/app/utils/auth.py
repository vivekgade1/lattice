# ruff: noqa
import bcrypt


def hash_password(password: str) -> str:
    """
    Hash a plain text password using bcrypt.

    Args:
        password: Plain text password to hash

    Returns:
        str: Hashed password as string (decoded from bytes)

    Note:
        Uses bcrypt with automatically generated salt for secure password hashing
    """
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify a plain text password against a hashed password.

    Args:
        plain_password: Plain text password to verify
        hashed_password: Hashed password (string or bytes) to verify against

    Returns:
        bool: True if password matches, False otherwise

    Note:
        Handles both string and bytes input for hashed_password
    """
    # Handle both string and bytes input for hashed_password
    if isinstance(hashed_password, str):
        hashed_password = hashed_password.encode("utf-8")
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password)
