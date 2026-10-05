# ruff: noqa
"""Authentication data validators."""

import re

from bench.import_repos.projmgmt.app.users.models import UserType  # LATTICE MOD (was `from app.users.models import UserType`: import path)

MAX_EMAIL_LENGTH = 120
MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_LENGTH = 128
MAX_NAME_LENGTH = 100


def _check_is_dict(data: object) -> str | None:
    """Check if data is a dictionary."""
    if not isinstance(data, dict):
        return 'Request data must be a JSON object'
    return None


def _check_required_fields(
    data: dict[str, object],
    required_fields: list[str],
) -> str | None:
    """Check if all required fields are present."""
    missing_fields = [f for f in required_fields if f not in data]
    if missing_fields:
        return f'Missing required fields: {", ".join(missing_fields)}'
    return None


def _check_email(email: object) -> str | None:
    """Validate email format."""
    if not isinstance(email, str):
        return 'Email must be a string'
    if not email.strip():
        return 'Email cannot be empty'

    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        return 'Invalid email format'
    if len(email) > MAX_EMAIL_LENGTH:
        return f'Email must be {MAX_EMAIL_LENGTH} characters or less'
    return None


def _check_password(password: object) -> str | None:
    """Validate password."""
    if not isinstance(password, str):
        return 'Password must be a string'
    if not password:
        return 'Password cannot be empty'
    if len(password) < MIN_PASSWORD_LENGTH:
        return (
            f'Password must be at least {MIN_PASSWORD_LENGTH} characters long'
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        return f'Password must be {MAX_PASSWORD_LENGTH} characters or less'
    return None


def validate_login_data(data: dict[str, object]) -> str | None:
    """Validate login request data."""
    error = _check_is_dict(data)
    if error:
        return error

    error = _check_required_fields(data, ['email', 'password'])
    if error:
        return error

    return _check_email(data['email']) or _check_password(data['password'])


def validate_register_data(data: dict[str, object]) -> str | None:
    """Validate registration data."""
    errors = []

    error = _check_is_dict(data)
    if error:
        errors.append(error)
    else:
        required_error = _check_required_fields(
            data,
            ['name', 'email', 'password'],
        )
        if required_error:
            errors.append(required_error)

        name = data.get('name')
        if not isinstance(name, str):
            errors.append('Name must be a string')
        elif not name.strip():
            errors.append('Name cannot be empty')
        elif len(name) > MAX_NAME_LENGTH:
            errors.append(f'Name must be {MAX_NAME_LENGTH} characters or less')

        email_error = _check_email(data.get('email'))
        if email_error:
            errors.append(email_error)

        password_error = _check_password(data.get('password'))
        if password_error:
            errors.append(password_error)

        user_type = data.get('user_type')
        if user_type and user_type not in [
            user_type.value for user_type in UserType
        ]:
            errors.append('User type must be either "manager" or "employee"')

    return errors[0] if errors else None
