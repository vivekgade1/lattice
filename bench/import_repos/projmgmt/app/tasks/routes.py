# ruff: noqa
"""Task management routes.

This module defines all endpoints for task operations within projects.
"""

from flask import Response, jsonify, request

from bench.import_repos.projmgmt.app import db  # LATTICE MOD (was `from app import db`: import path)
from bench.import_repos.projmgmt.app.middleware import manager_required  # LATTICE MOD (was `from app.middleware import manager_required`: import path)
from bench.import_repos.projmgmt.app.middleware.auth import token_required  # LATTICE MOD (was `from app.middleware.auth import token_required`: import path)
from bench.import_repos.projmgmt.app.projects.models import Project  # LATTICE MOD (was `from app.projects.models import Project`: import path)
from bench.import_repos.projmgmt.app.tasks.models import Task  # LATTICE MOD (was `from app.tasks.models import Task`: import path)
from bench.import_repos.projmgmt.app.tasks.validators import validate_task_data  # LATTICE MOD (was `from app.tasks.validators import validate_task_data`: import path)

from . import task_bp


@task_bp.route('/tasks', methods=['GET'])
@token_required
def get_all_tasks() -> tuple[Response, int]:
    """Retrieve all tasks from all projects.

    Requires authentication (both managers and employees can access).

    Query Parameters:
        status (optional): Filter by task status
        project_id (optional): Filter by project ID
        limit (optional): Limit number of results
        offset (optional): Offset for pagination

    Returns:
        200: List of tasks
        400: Invalid query parameters

    Example:
        GET /tasks?status=pending&limit=10
    """
    try:
        query = Task.query

        # Filter by status if provided
        status_filter = request.args.get('status')
        if status_filter:
            if status_filter not in Task.VALID_STATUSES:
                return jsonify(
                    {
                        'error': 'Invalid status. Must be one of: '
                        + ', '.join(Task.VALID_STATUSES),
                    },
                ), 400
            query = query.filter_by(status=status_filter)

        # Filter by project_id if provided
        project_id_filter = request.args.get('project_id', type=int)
        if project_id_filter:
            query = query.filter_by(project_id=project_id_filter)

        # Pagination
        limit = request.args.get('limit', type=int)
        offset = request.args.get('offset', type=int, default=0)

        if limit:
            query = query.limit(limit)
        query = query.offset(offset)

        tasks = query.all()

        return jsonify(
            {
                'tasks': [task.to_dict() for task in tasks],
                'count': len(tasks),
            },
        ), 200

    except Exception as e:
        return jsonify({'error': f'Failed to retrieve tasks: {e!s}'}), 500


@task_bp.route('/tasks', methods=['POST'])
@token_required
def create_task_simple() -> tuple[Response, int]:
    """Create a new task.

    Requires authentication (both managers and employees can create tasks).

    Request Body:
        {
            "title": "Task title",
            "description": "Task description (optional)",
            "status": "pending" | "in_progress" | "completed" (optional, default: pending),
            "project_id": 1  // ID of the project to add task to
        }

    Returns:
        201: Task created successfully
        400: Invalid request data
        404: Project not found

    Example:
        POST /tasks
        {
            "title": "Design homepage",
            "description": "Create mockup for homepage",
            "status": "pending",
            "project_id": 1
        }
    """  # noqa: E501
    data = request.get_json()

    is_valid, error = validate_task_data(data)
    if not is_valid:
        return jsonify({'error': error}), 400

    # Validate project exists
    project_id = data.get('project_id')
    if not project_id:
        return jsonify({'error': 'project_id is required'}), 400

    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    try:
        task = Task()
        task.title = data['title']
        task.description = data.get('description')
        task.status = data.get('status', 'pending')
        task.project_id = project_id

        db.session.add(task)
        db.session.commit()

        return jsonify(task.to_dict()), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to create task: {e!s}'}), 500


@task_bp.route('/projects/<int:project_id>/tasks', methods=['POST'])
@manager_required
def create_task(project_id: int) -> tuple[Response, int]:
    """Add a new task to a project.

    Requires manager role.

    Args:
        project_id (int): Project ID to add task to

    Request Body:
        {
            "title": "Task title",
            "description": "Task description (optional)",
            "status": "pending" | "in_progress" | "completed" (optional,
            default: pending),
            "user_id": 1  // ID of the manager making the request
        }

    Returns:
        201: Task created successfully
        400: Invalid request data
        403: Access denied
        404: Project not found

    Example:
        POST /projects/1/tasks
        {
            "title": "Design homepage",
            "description": "Create mockup for homepage",
            "status": "pending",
            "user_id": 1
        }
    """
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    data = request.get_json()

    is_valid, error = validate_task_data(data)
    if not is_valid:
        return jsonify({'error': error}), 400

    try:
        task = Task()
        task.title = data['title']
        task.description = data.get('description')
        task.status = data.get('status', 'pending')
        task.project_id = project_id

        db.session.add(task)
        db.session.commit()

        return jsonify(task.to_dict()), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to create task: {e!s}'}), 500


@task_bp.route('/projects/<int:project_id>/tasks', methods=['GET'])
def get_project_tasks(project_id: int) -> tuple[Response, int]:
    """Retrieve all tasks for a specific project.

    Args:
        project_id (int): Project ID

    Query Parameters:
        status (optional): Filter by task status
        limit (optional): Limit number of results
        offset (optional): Offset for pagination

    Returns:
        200: List of tasks
        400: Invalid query parameters
        404: Project not found

    Example:
        GET /projects/1/tasks?status=pending&limit=10
    """
    project = db.session.get(Project, project_id)
    if not project:
        return jsonify({'error': 'Project not found'}), 404

    try:
        query = Task.query.filter_by(project_id=project_id)

        status_filter = request.args.get('status')
        if status_filter:
            if status_filter not in Task.VALID_STATUSES:
                return jsonify(
                    {
                        'error': 'Invalid status. Must be one of: '
                        + ', '.join(
                            Task.VALID_STATUSES,
                        ),
                    },
                ), 400
            query = query.filter_by(status=status_filter)

        limit = request.args.get('limit', type=int)
        offset = request.args.get('offset', type=int, default=0)

        if limit:
            query = query.limit(limit)
        query = query.offset(offset)

        tasks = query.all()

        return jsonify(
            {
                'tasks': [task.to_dict() for task in tasks],
                'count': len(tasks),
                'project_id': project_id,
            },
        ), 200

    except Exception as e:
        return jsonify({'error': f'Failed to retrieve tasks: {e!s}'}), 500


@task_bp.route('/tasks/<int:task_id>', methods=['GET'])
def get_task(task_id: int) -> tuple[Response, int]:
    """Retrieve a specific task by ID.

    Args:
        task_id (int): Task ID

    Returns:
        200: Task data
        404: Task not found

    Example:
        GET /tasks/1
    """
    task = db.session.get(Task, task_id)

    if not task:
        return jsonify({'error': 'Task not found'}), 404

    return jsonify(task.to_dict()), 200


@task_bp.route('/tasks/<int:task_id>', methods=['PUT'])
@token_required
def update_task(task_id: int) -> tuple[Response, int]:
    """Update an existing task.

    Requires authentication (both managers and employees can update tasks).

    Args:
        task_id (int): Task ID to update

    Request Body (all fields optional):
        {
            "title": "Updated title",
            "description": "Updated description",
            "status": "in_progress",
            "user_id": 1  // ID of the manager making the request
        }

    Returns:
        200: Task updated successfully
        400: Invalid request data
        403: Access denied
        404: Task not found

    Example:
        PUT /tasks/1
        {
            "status": "completed",
            "user_id": 1
        }
    """
    task = db.session.get(Task, task_id)

    if not task:
        return jsonify({'error': 'Task not found'}), 404

    data = request.get_json()

    is_valid, error = validate_task_data(data, is_update=True)
    if not is_valid:
        return jsonify({'error': error}), 400

    try:
        if 'title' in data:
            task.title = data['title']

        if 'description' in data:
            task.description = data['description']

        if 'status' in data:
            task.status = data['status']

        db.session.commit()

        updated_task = db.session.get(Task, task_id)
        if not updated_task:
            return jsonify({'error': 'Task not found after update'}), 404

        return jsonify(updated_task.to_dict()), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to update task: {e!s}'}), 500


@task_bp.route('/tasks/<int:task_id>', methods=['DELETE'])
@token_required
def delete_task(task_id: int) -> tuple[Response, int]:
    """Delete a task.

    Requires authentication (both managers and employees can delete tasks).

    Args:
        task_id (int): Task ID to delete

    Query Parameters/Body:
        user_id: ID of the manager making the request

    Returns:
        200: Task deleted successfully
        403: Access denied
        404: Task not found

    Example:
        DELETE /tasks/1?user_id=1
    """
    task = db.session.get(Task, task_id)

    if not task:
        return jsonify({'error': 'Task not found'}), 404

    try:
        db.session.delete(task)
        db.session.commit()

        return jsonify({'message': 'Task deleted successfully'}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': f'Failed to delete task: {e!s}'}), 500
