# ruff: noqa
"""User data validators."""

import re

from loguru import logger

from bench.import_repos.projmgmt.app.users.models import UserType  # LATTICE MOD (was `from app.users.models import UserType`: import path)

EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')
MAX_NAME_LEN = 100


def _check_is_dict(data: object) -> str | None:
    if not isinstance(data, dict):
        return 'Request body must be JSON'
    return None


def _check_required_fields(
    data: dict[str, object],
    required: tuple[str, ...],
) -> str | None:
    for field in required:
        if field not in data:
            return f"Field '{field}' is required"
    return None


def _check_name(name: object) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return "Field 'name' must be a non-empty string"
    if len(name) > MAX_NAME_LEN:
        return f"Field 'name' must be at most {MAX_NAME_LEN} characters"
    return None


def _check_email(email: object) -> str | None:
    if not isinstance(email, str) or not EMAIL_RE.match(email):
        return 'Invalid email format'
    return None


def _check_user_type(user_type: object) -> str | None:
    valid = [t.value for t in UserType]
    logger.debug(f'Valid user types: {valid}')
    if user_type not in valid:
        return f"Field 'user_type' must be one of: {', '.join(valid)}"
    return None


def validate_user_data(
    data: dict[str, object],
    *,
    is_update: bool = False,
) -> tuple[bool, str | None]:
    """Validate user creation/update payload.

    Returns (is_valid, error_message_or_none).
    """
    err = _check_is_dict(data)
    if err:
        return False, err

    if not is_update:
        err = _check_required_fields(data, ('name', 'email', 'user_type'))
        if err:
            return False, err

    if 'name' in data:
        err = _check_name(data['name'])
        if err:
            return False, err

    if 'email' in data:
        err = _check_email(data['email'])
        if err:
            return False, err

    if 'user_type' in data:
        err = _check_user_type(data['user_type'])
        if err:
            return False, err

    return True, None
