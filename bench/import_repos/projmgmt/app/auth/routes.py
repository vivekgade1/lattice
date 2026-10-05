# ruff: noqa
"""Authentication views for login, logout, and token management.

This module provides endpoints for user authentication using JWT tokens.
"""

from flask import Blueprint, Response, jsonify, request

from bench.import_repos.projmgmt.app import db  # LATTICE MOD (was `from app import db`: import path)
from bench.import_repos.projmgmt.app.auth.services import AuthService, TokenType  # LATTICE MOD (was `from app.auth.services import AuthService, TokenType`: import path)
from bench.import_repos.projmgmt.app.auth.validators import validate_login_data  # LATTICE MOD (was `from app.auth.validators import validate_login_data`: import path)
from bench.import_repos.projmgmt.app.middleware.auth import token_required  # LATTICE MOD (was `from app.middleware.auth import token_required`: import path)
from bench.import_repos.projmgmt.app.users.models import User  # LATTICE MOD (was `from app.users.models import User`: import path)

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


@auth_bp.route('/login', methods=['POST'])
def login() -> tuple[Response, int]:  # noqa: PLR0911
    """Authenticate user and return JWT tokens.

    Expected JSON payload:
    {
        "email": "user@example.com",
        "password": "password123"
    }

    Returns:
        JSON response with access and refresh tokens or error message
    """
    try:
        # Try to get JSON data, handle invalid JSON
        try:
            data = request.get_json(force=True)
        except Exception:
            return jsonify({'error': 'Invalid JSON format'}), 400

        if data is None:
            return jsonify({'error': 'Invalid request data'}), 400

        validation_error = validate_login_data(data)
        if validation_error:
            return jsonify({'error': validation_error}), 400

        email = data['email']
        password = data['password']

        user = AuthService.authenticate_user(email, password)
        if not user:
            return jsonify({'error': 'Invalid email or password'}), 401

        access_token = AuthService.generate_access_token(user)
        refresh_token = AuthService.generate_refresh_token(user)

        return jsonify(
            {
                'message': 'Login successful',
                'access_token': access_token,
                'refresh_token': refresh_token,
                'user': {
                    'id': user.id,
                    'name': user.name,
                    'email': user.email,
                    'user_type': user.user_type.value,
                },
            },
        ), 200

    except KeyError as e:
        return jsonify({'error': f'Missing required field: {e!s}'}), 400
    except Exception as e:
        return jsonify({'error': f'Login failed: {e!s}'}), 500


@auth_bp.route('/register', methods=['POST'])
def register() -> tuple[Response, int]:
    """Register a new user account.

    Expected JSON payload:
    {
        "name": "John Doe",
        "email": "john@example.com",
        "password": "password123",
        "user_type": "employee"  // optional, defaults to "employee"
    }

    Returns:
        JSON response with user data or error message
    """
    if not request.is_json or request.json is None:
        return jsonify({'error': 'Request must be in JSON format'}), 400

    try:
        response_data, status_code = AuthService.register_user(request.json)
        return jsonify(response_data), status_code

    except Exception as e:
        return jsonify({'error': f'Registration failed: {e!s}'}), 500


@auth_bp.route('/refresh', methods=['POST'])
def refresh_token() -> tuple[Response, int]:
    """Refresh access token using refresh token.

    Expected JSON payload:
    {
        "refresh_token": "jwt_refresh_token_here"
    }

    Returns:
        JSON response with new access token or error message
    """
    try:
        data = request.json
        if not data or 'refresh_token' not in data:
            return jsonify({'error': 'Refresh token is required'}), 400

        refresh_token = data['refresh_token']

        payload = AuthService.verify_token(
            refresh_token,
            TokenType.REFRESH,
        )
        if not payload:
            return jsonify({'error': 'Invalid or expired refresh token'}), 401

        user_id = payload.get('user_id')

        user = db.session.get(User, user_id)

        if not user:
            return jsonify({'error': 'User not found'}), 404

        new_access_token = AuthService.generate_access_token(user)

        return jsonify(
            {
                'message': 'Token refreshed successfully',
                'access_token': new_access_token,
            },
        ), 200

    except Exception as e:
        return jsonify({'error': f'Token refresh failed: {e!s}'}), 500


@auth_bp.route('/logout', methods=['POST'])
@token_required
def logout() -> tuple[Response, int]:
    """Logout user (client-side token removal).

    Note: Since JWT tokens are stateless, logout is handled client-side
    by removing the tokens. This endpoint serves as a confirmation.

    Returns:
        JSON response confirming logout
    """
    return jsonify({'message': 'Logout successful'}), 200


@auth_bp.route('/verify', methods=['GET'])
def verify_token() -> tuple[Response, int]:
    """Verify if the provided access token is valid.

    Expected header:
    Authorization: Bearer <access_token>

    Returns:
        JSON response with user info if token is valid
    """
    try:
        auth_header = request.headers.get('Authorization')
        if not auth_header or not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Authorization header required'}), 401

        token = auth_header.split(' ')[1]
        user = AuthService.get_user_from_token(token)

        if not user:
            return jsonify({'error': 'Invalid or expired token'}), 401

        return jsonify(
            {
                'valid': True,
                'user': {
                    'id': user.id,
                    'name': user.name,
                    'email': user.email,
                    'user_type': user.user_type.value,
                },
            },
        ), 200

    except Exception as e:
        return jsonify({'error': f'Token verification failed: {e!s}'}), 500
