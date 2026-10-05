# ruff: noqa
"""Authentication and authorization middleware.

This module provides decorators for route protection based on user roles
using JWT tokens for secure authentication.
"""

from collections.abc import Callable
from functools import wraps
from typing import Any

from flask import g, jsonify, request

from bench.import_repos.projmgmt.app.users.models import User  # LATTICE MOD (was `from app.users.models import User`: import path)


def token_required(f: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to require valid JWT token for accessing a route.

    This decorator validates the JWT token from the Authorization header
    and makes the current user available to the route function.

    Expected header format:
    Authorization: Bearer <jwt_token>

    Args:
        f (function): The route function to decorate

    Returns:
        function: The decorated function

    Raises:
        401: If token is missing, invalid, or expired
        404: If user is not found

    Example:
        >>> @app.route('/profile', methods=['GET'])
        >>> @token_required
        >>> def get_profile():
        >>>     user = get_current_user()
        >>>     return jsonify(user.to_dict())
    """

    @wraps(f)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        auth_header = request.headers.get('Authorization')

        if not auth_header:
            return jsonify({'error': 'Authorization header is required'}), 401

        if not auth_header.startswith('Bearer '):
            return jsonify(
                {'error': 'Authorization header must start with "Bearer "'},
            ), 401

        try:
            token = auth_header.split(' ')[1]
        except IndexError:
            return jsonify(
                {'error': 'Invalid authorization header format'},
            ), 401

        from bench.import_repos.projmgmt.app.auth.services import AuthService  # noqa: PLC0415  # LATTICE MOD (was `from app.auth.services import AuthService  # noqa: PLC0415`: import path)

        user = AuthService.get_user_from_token(token)

        if not user:
            return jsonify({'error': 'Invalid or expired token'}), 401

        g.current_user = user

        return f(*args, **kwargs)

    return decorated_function


def manager_required(f: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator to require manager role for accessing a route.

    This decorator first validates the JWT token and then checks
    if the authenticated user has manager privileges.

    Expected header format:
    Authorization: Bearer <jwt_token>

    Args:
        f (function): The route function to decorate

    Returns:
        function: The decorated function

    Raises:
        401: If token is missing, invalid, or expired
        403: If user is not a manager
        404: If user is not found

    Example:
        >>> @app.route('/projects', methods=['POST'])
        >>> @manager_required
        >>> def create_project():
        >>>     # Only managers can access this
        >>>     user = get_current_user()
        >>>     pass
    """

    @wraps(f)
    def decorated_function(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        auth_header = request.headers.get('Authorization')

        if not auth_header:
            return jsonify({'error': 'Authorization header is required'}), 401

        if not auth_header.startswith('Bearer '):
            return jsonify(
                {'error': 'Authorization header must start with "Bearer "'},
            ), 401

        try:
            token = auth_header.split(' ')[1]
        except IndexError:
            return jsonify(
                {'error': 'Invalid authorization header format'},
            ), 401

        from bench.import_repos.projmgmt.app.auth.services import AuthService  # noqa: PLC0415  # LATTICE MOD (was `from app.auth.services import AuthService  # noqa: PLC0415`: import path)

        user = AuthService.get_user_from_token(token)

        if not user:
            return jsonify({'error': 'Invalid or expired token'}), 401

        if not AuthService.is_manager(user):
            return jsonify(
                {
                    'error': 'Access denied. Manager role required.',
                    'user_type': user.user_type.value,
                },
            ), 403

        g.current_user = user

        return f(*args, **kwargs)

    return decorated_function


def get_current_user() -> User | None:
    """Get the current authenticated user from the request context.

    This function should be called within routes that are protected
    by @token_required or @manager_required decorators.

    Returns:
        User: The current authenticated user, or None if not authenticated

    Example:
        >>> @token_required
        >>> def get_profile():
        >>>     user = get_current_user()
        >>>     return jsonify(user.to_dict())
    """
    return getattr(g, 'current_user', None)


def get_current_user_id() -> int | None:
    """Get the current authenticated user's ID from the request context.

    This function should be called within routes that are protected
    by @token_required or @manager_required decorators.

    Returns:
        int: The current user's ID, or None if not authenticated

    Example:
        >>> @token_required
        >>> def get_user_projects():
        >>>     user_id = get_current_user_id()
        >>>     projects = Project.query.filter_by(user_id=user_id).all()
        >>>     return jsonify([p.to_dict() for p in projects])
    """
    user = get_current_user()
    return user.id if user else None
