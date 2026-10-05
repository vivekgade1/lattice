# ruff: noqa
"""User management routes.

This module defines all endpoints for user CRUD operations.
"""

from flask import Response, jsonify, request
from sqlalchemy.exc import IntegrityError

from bench.import_repos.projmgmt.app import db  # LATTICE MOD (was `from app import db`: import path)
from bench.import_repos.projmgmt.app.middleware.auth import manager_required, token_required  # LATTICE MOD (was `from app.middleware.auth import manager_required, token_required`: import path)
from bench.import_repos.projmgmt.app.users.models import User, UserType  # LATTICE MOD (was `from app.users.models import User, UserType`: import path)
from bench.import_repos.projmgmt.app.users.validators import validate_user_data  # LATTICE MOD (was `from app.users.validators import validate_user_data`: import path)

from . import user_bp


@user_bp.route('', methods=['POST'])
@manager_required
def create_user() -> tuple[Response, int]:
    """Create a new user.

    Requires manager role. Expects JSON body with user data.

    Request Body:
        {
            "name": "John Doe",
            "email": "john@example.com",
            "user_type": "manager" | "employee",
            "user_id": 1  // ID of the manager making the request
        }

    Returns:
        201: User created successfully
        400: Invalid request data
        403: Access denied (not a manager)
        409: Email already exists

    Example:
        POST /users
        {
            "name": "Jane Smith",
            "email": "jane@example.com",
            "user_type": "employee",
            "user_id": 1
        }
    """
    data = request.get_json()

    is_valid, error = validate_user_data(data)
    if not is_valid:
        return jsonify({'error': error}), 400

    try:
        user = User()
        user.name = data['name']
        user.email = data['email']
        user.user_type = UserType(data['user_type'])
        # Set a default password for users created by managers
        user.set_password('defaultpassword123')

        db.session.add(user)
        db.session.commit()

        return jsonify(user.to_dict()), 201

    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'Email already exists'}), 409

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to create user: {e!s}'}), 500


@user_bp.route('', methods=['GET'])
@token_required
def get_users() -> tuple[Response, int]:
    """Retrieve a list of all users.

    Query Parameters:
        user_type (optional): Filter by user type (manager/employee)
        limit (optional): Limit number of results
        offset (optional): Offset for pagination

    Returns:
        200: List of users
        400: Invalid query parameters

    Example:
        GET /users?user_type=manager&limit=10
    """
    try:
        query = User.query

        user_type_filter = request.args.get('user_type')
        if user_type_filter:
            if user_type_filter not in [t.value for t in UserType]:
                return jsonify(
                    {
                        'error': (
                            'Invalid user_type. '
                            'Must be one of: manager, employee'
                        ),
                    },
                ), 400
            query = query.filter_by(user_type=UserType(user_type_filter))

        limit = request.args.get('limit', type=int)
        offset = request.args.get('offset', type=int, default=0)

        if limit:
            query = query.limit(limit)
        query = query.offset(offset)

        users = query.all()

        return jsonify(
            {
                'users': [user.to_dict() for user in users],
                'count': len(users),
            },
        ), 200

    except Exception as e:
        return jsonify({'error': f'Failed to retrieve users: {e!s}'}), 500


@user_bp.route('/<int:user_id>', methods=['GET'])
@token_required
def get_user(user_id: int) -> tuple[Response, int]:
    """Retrieve a specific user by ID.

    Args:
        user_id (int): User ID

    Query Parameters:
        include_projects (optional): Include user's projects (true/false)

    Returns:
        200: User data
        404: User not found

    Example:
        GET /users/1?include_projects=true
    """
    user = db.session.get(User, user_id)

    if not user:
        return jsonify({'error': 'User not found'}), 404

    include_projects = (
        request.args.get('include_projects', '').lower() == 'true'
    )

    return jsonify(user.to_dict(include_projects=include_projects)), 200


@user_bp.route('/<int:user_id>', methods=['PUT'])
@manager_required
def update_user(user_id: int) -> tuple[Response, int]:
    """Update an existing user.

    Requires manager role.

    Args:
        user_id (int): User ID to update

    Request Body (all fields optional):
        {
            "name": "Updated Name",
            "email": "updated@example.com",
            "user_type": "manager" | "employee",
            "user_id": 1  // ID of the manager making the request
        }

    Returns:
        200: User updated successfully
        400: Invalid request data
        403: Access denied
        404: User not found
        409: Email already exists

    Example:
        PUT /users/2
        {
            "name": "John Updated",
            "user_id": 1
        }
    """
    user = db.session.get(User, user_id)

    if not user:
        return jsonify({'error': 'User not found'}), 404

    data = request.get_json()

    is_valid, error = validate_user_data(data, is_update=True)
    if not is_valid:
        return jsonify({'error': error}), 400

    try:
        if 'name' in data:
            user.name = data['name']

        if 'email' in data:
            user.email = data['email']

        if 'user_type' in data:
            user.user_type = UserType(data['user_type'])

        db.session.commit()

        return jsonify(user.to_dict()), 200

    except IntegrityError:
        db.session.rollback()
        return jsonify({'error': 'Email already exists'}), 409

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to update user: {e!s}'}), 500


@user_bp.route('/<int:user_id>', methods=['DELETE'])
@manager_required
def delete_user(user_id: int) -> tuple[Response, int]:
    """Delete a user.

    Requires manager role. This will also delete all projects and tasks
    owned by this user (cascade delete).

    Args:
        user_id (int): User ID to delete

    Query Parameters/Body:
        user_id: ID of the manager making the request

    Returns:
        200: User deleted successfully
        403: Access denied
        404: User not found

    Example:
        DELETE /users/1?user_id=1
    """
    user = db.session.get(User, user_id)

    if not user:
        return jsonify({'error': 'User not found'}), 404

    try:
        db.session.delete(user)
        db.session.commit()

        return jsonify({'message': 'User deleted successfully'}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to delete user: {e!s}'}), 500
