# ruff: noqa
"""Project management routes.

This module defines all endpoints for project CRUD operations.
"""

from flask import Response, jsonify, request

from bench.import_repos.projmgmt.app import db  # LATTICE MOD (was `from app import db`: import path)
from bench.import_repos.projmgmt.app.projects.models import Project  # LATTICE MOD (was `from app.projects.models import Project`: import path)
from bench.import_repos.projmgmt.app.projects.validators import validate_project_data  # LATTICE MOD (was `from app.projects.validators import validate_project_data`: import path)
from bench.import_repos.projmgmt.app.users.models import User  # LATTICE MOD (was `from app.users.models import User`: import path)

from ..middleware.auth import manager_required  # noqa: TID252
from . import project_bp


@project_bp.route('', methods=['POST'])
@manager_required
def create_project() -> tuple[Response, int]:
    """Create a new project.

    Requires manager role.

    Request Body:
        {
            "name": "Project Name",
            "description": "Project description (optional)",
            "user_id": 1  // Owner of the project
        }

    Returns:
        201: Project created successfully
        400: Invalid request data
        403: Access denied
        404: User not found

    Example:
        POST /projects
        {
            "name": "New Website",
            "description": "Build a new website",
            "user_id": 2
        }
    """
    data = request.get_json()

    is_valid, error = validate_project_data(data)
    if not is_valid:
        return jsonify({'error': error}), 400

    user = db.session.get(User, data['user_id'])
    if not user:
        return jsonify({'error': 'User not found'}), 404

    try:
        project = Project()
        project.name = data['name']
        project.description = data.get('description')
        project.user_id = data['user_id']

        db.session.add(project)
        db.session.commit()

        return jsonify(project.to_dict()), 201

    except Exception as e:
        db.session.rollback()

        return jsonify({'error': f'Failed to create project: {e!s}'}), 500


@project_bp.route('', methods=['GET'])
def get_projects() -> tuple[Response, int]:
    """Retrieve a list of all projects.

    Query Parameters:
        user_id (optional): Filter by owner user ID
        include_tasks (optional): Include project tasks (true/false)
        limit (optional): Limit number of results
        offset (optional): Offset for pagination

    Returns:
        200: List of projects

    Example:
        GET /projects?user_id=1&include_tasks=true
    """
    try:
        query = Project.query

        user_id_filter = request.args.get('user_id', type=int)
        if user_id_filter:
            query = query.filter_by(user_id=user_id_filter)

        limit = request.args.get('limit', type=int)
        offset = request.args.get('offset', type=int, default=0)

        if limit:
            query = query.limit(limit)
        query = query.offset(offset)

        projects = query.all()

        include_tasks = request.args.get('include_tasks', '').lower() == 'true'

        return jsonify(
            {
                'projects': [
                    project.to_dict(include_tasks=include_tasks)
                    for project in projects
                ],
                'count': len(projects),
            },
        ), 200

    except Exception as e:
        return jsonify({'error': f'Failed to retrieve projects: {e!s}'}), 500


@project_bp.route('/<int:project_id>', methods=['GET'])
def get_project(project_id: int) -> tuple[Response, int]:
    """Retrieve a specific project by ID.

    Args:
        project_id (int): Project ID

    Query Parameters:
        include_tasks (optional): Include project tasks (true/false)

    Returns:
        200: Project data
        404: Project not found

    Example:
        GET /projects/1?include_tasks=true
    """
    project = db.session.get(Project, project_id)

    if not project:
        return jsonify({'error': 'Project not found'}), 404

    include_tasks = request.args.get('include_tasks', '').lower() == 'true'

    return jsonify(project.to_dict(include_tasks=include_tasks)), 200


@project_bp.route('/<int:project_id>', methods=['PUT'])
@manager_required
def update_project(project_id: int) -> tuple[Response, int]:
    """Update an existing project.

    Requires manager role.

    Args:
        project_id (int): Project ID to update

    Request Body (all fields optional):
        {
            "name": "Updated Name",
            "description": "Updated description",
            "user_id": 1  // ID of the manager making the request
        }

    Returns:
        200: Project updated successfully
        400: Invalid request data
        403: Access denied
        404: Project not found

    Example:
        PUT /projects/1
        {
            "name": "Updated Project Name",
            "user_id": 1
        }
    """
    project = db.session.get(Project, project_id)

    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = request.get_json()

    is_valid, error = validate_project_data(data, is_update=True)
    if not is_valid:
        return jsonify({'error': error}), 400

    try:
        if 'name' in data:
            project.name = data['name']

        if 'description' in data:
            project.description = data['description']

        db.session.commit()

        return jsonify(project.to_dict()), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to update project: {e!s}'}), 500


@project_bp.route('/<int:project_id>', methods=['DELETE'])
@manager_required
def delete_project(project_id: int) -> tuple[Response, int]:
    """Delete a project.

    Requires manager role. This will also delete all tasks
    belonging to this project (cascade delete).

    Args:
        project_id (int): Project ID to delete

    Query Parameters/Body:
        user_id: ID of the manager making the request

    Returns:
        200: Project deleted successfully
        403: Access denied
        404: Project not found

    Example:
        DELETE /projects/1?user_id=1
    """
    project = db.session.get(Project, project_id)

    if not project:
        return jsonify({'error': 'Project not found'}), 404

    try:
        db.session.delete(project)
        db.session.commit()

        return jsonify({'message': 'Project deleted successfully'}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to delete project: {e!s}'}), 500
