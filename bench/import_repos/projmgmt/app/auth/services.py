# ruff: noqa
"""Authentication service using JWT tokens.

This module provides secure authentication functionality using
JSON Web Tokens (JWT) for stateless authentication.
"""

import os
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

import jwt
from flask import current_app
from sqlalchemy.exc import IntegrityError

from bench.import_repos.projmgmt.app import db  # LATTICE MOD (was `from app import db`: import path)
from bench.import_repos.projmgmt.app.auth.validators import validate_register_data  # LATTICE MOD (was `from app.auth.validators import validate_register_data`: import path)
from bench.import_repos.projmgmt.app.users.models import User, UserType  # LATTICE MOD (was `from app.users.models import User, UserType`: import path)


class TokenType(Enum):
    """Enumeration of JWT token types used by the authentication service.

    Attributes:
        ACCESS: Short-lived token used to access protected resources.
        REFRESH: Long-lived token used to obtain new access tokens.
    """

    ACCESS = 'access'
    REFRESH = 'refresh'


class AuthService:
    """Service class for handling authentication operations."""

    @staticmethod
    def generate_access_token(user: User) -> str:
        """Generate a JWT access token for the user.

        Args:
            user: User instance to generate token for

        Returns:
            str: JWT access token
        """
        payload = {
            'user_id': user.id,
            'email': user.email,
            'user_type': user.user_type.value,
            'exp': datetime.now(UTC)
            + timedelta(hours=1),  # Token expires in 1 hour
            'iat': datetime.now(UTC),
            'type': 'access',
        }

        secret_key = current_app.config.get('SECRET_KEY') or os.getenv(
            'SECRET_KEY',
            'dev-secret-key',
        )
        return jwt.encode(payload, secret_key, algorithm='HS256')

    @staticmethod
    def generate_refresh_token(user: User) -> str:
        """Generate a JWT refresh token for the user.

        Args:
            user: User instance to generate token for

        Returns:
            str: JWT refresh token
        """
        payload = {
            'user_id': user.id,
            'exp': datetime.now(UTC)
            + timedelta(days=30),  # Refresh token expires in 30 days
            'iat': datetime.now(UTC),
            'type': 'refresh',
        }

        secret_key = current_app.config.get('SECRET_KEY') or os.getenv(
            'SECRET_KEY',
            'dev-secret-key',
        )
        return jwt.encode(payload, secret_key, algorithm='HS256')

    @staticmethod
    @staticmethod
    def verify_token(
        token: str,
        token_type: TokenType = TokenType.ACCESS,
    ) -> dict[str, Any] | None:
        """Verify and decode a JWT token.

        Args:
            token: JWT token to verify
            token_type: Type of token (TokenType.ACCESS or TokenType.REFRESH)

        Returns:
            dict: Decoded token payload if valid, None otherwise
        """
        try:
            secret_key = current_app.config.get('SECRET_KEY') or os.getenv(
                'SECRET_KEY',
                'dev-secret-key',
            )
            payload: dict[str, Any] = jwt.decode(
                token,
                secret_key,
                algorithms=['HS256'],
            )

            if payload.get('type') != token_type.value:
                return None

        except jwt.ExpiredSignatureError:
            return None
        except jwt.InvalidTokenError:
            return None

        return payload

    @staticmethod
    def authenticate_user(email: str, password: str) -> User | None:
        """Authenticate a user with email and password.

        Args:
            email: User's email address
            password: User's plain text password

        Returns:
            User: User instance if authentication successful, None otherwise
        """
        user: User | None = User.query.filter_by(email=email).first()

        if user is not None and user.check_password(password):
            return user

        return None

    @staticmethod
    def get_user_from_token(token: str) -> User | None:
        """Retrieve a user instance from a valid JWT access token.

        Args:
            token (str): Encoded JWT access token.

        Returns:
            User | None: User instance if token is valid, otherwise None.
        """
        payload = AuthService.verify_token(token, TokenType.ACCESS)
        if not payload:
            return None

        user_id = payload.get('user_id')
        if not user_id:
            return None

        return db.session.get(User, user_id) if user_id is not None else None

    @staticmethod
    def is_manager(user: User) -> bool:
        """Check if user has manager privileges.

        Args:
            user: User instance to check

        Returns:
            bool: True if user is a manager, False otherwise
        """
        return bool(user.user_type == UserType.MANAGER)

    @staticmethod
    def register_user(data: dict[str, Any]) -> tuple[dict[str, Any], int]:
        """Register a new user with password.

        Args:
            data: User registration data dictionary including password

        Returns:
            Tuple of (response_data, status_code)
        """
        error = validate_register_data(data)
        if error:
            return {'error': error}, 400

        try:
            user = User()
            user.name = data['name']
            user.email = data['email']
            user.set_password(data['password'])
            user.user_type = UserType(data.get('user_type', 'employee'))

            db.session.add(user)
            db.session.commit()

            user_data = user.to_dict()

        except IntegrityError:
            db.session.rollback()
            return {'error': 'Email already exists'}, 409

        except Exception as e:  # noqa: BLE001
            db.session.rollback()
            return {'error': f'Failed to register user: {e!s}'}, 500

        return {
            'message': 'User registered successfully',
            'user': user_data,
        }, 201
