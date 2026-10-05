# ruff: noqa
"""Validation utilities for task data.

This module provides helper functions for validating incoming JSON
data related to tasks, ensuring field presence, type, and value constraints.

Constants:
    MAX_TASK_TITLE_LEN (int): Maximum allowed length for a task title.

Functions:
    validate_task_data: Validate task creation or update input.
"""

from bench.import_repos.projmgmt.app.tasks.models import Task  # LATTICE MOD (was `from app.tasks.models import Task`: import path)

MAX_TASK_TITLE_LEN = 200


def _check_is_dict(data: object) -> str | None:
    """Ensure the provided data is a dictionary.

    Args:
        data (object): The object to validate.

    Returns:
        str | None: An error message if validation fails, otherwise None.
    """
    if not isinstance(data, dict):
        return 'Request body must be JSON'
    return None


def _check_required_title(
    data: dict[str, object],
    *,
    is_update: bool,
) -> str | None:
    """Ensure the 'title' field is present when creating a task.

    Args:
        data (dict[str, object]): Input data to validate.
        is_update (bool): Whether the operation is an update.

    Returns:
        str | None: An error message if validation fails, otherwise None.
    """
    if not is_update and 'title' not in data:
        return "Field 'title' is required"
    return None


def _check_title(title: object) -> str | None:
    """Validate the 'title' field for type, emptiness, and length.

    Args:
        title (object): The provided title value.

    Returns:
        str | None: An error message if invalid, otherwise None.
    """
    if not isinstance(title, str) or not title.strip():
        return "Field 'title' must be a non-empty string"
    if len(title) > MAX_TASK_TITLE_LEN:
        return f"Field 'title' must be at most {MAX_TASK_TITLE_LEN} characters"
    return None


def _check_description(description: object) -> str | None:
    """Validate the 'description' field if present.

    Args:
        description (object): The provided description value.

    Returns:
        str | None: An error message if invalid, otherwise None.
    """
    if description is not None and not isinstance(description, str):
        return "Field 'description' must be a string"
    return None


def _check_status(status: object) -> str | None:
    """Validate the 'status' field against allowed task statuses.

    Args:
        status (object): The provided task status value.

    Returns:
        str | None: An error message if invalid, otherwise None.
    """
    if status not in Task.VALID_STATUSES:
        return (
            f"Field 'status' must be one of: {', '.join(Task.VALID_STATUSES)}"
        )
    return None


def validate_task_data(
    data: dict[str, object],
    *,
    is_update: bool = False,
) -> tuple[bool, str | None]:
    """Validate task input data for creation or update.

    Performs structure, type, and field-level validation on task input data.
    For creation, ensures required fields are present.

    Args:
        data (dict[str, object]): The request body to validate.
        is_update (bool, optional): Whether the operation is an update.
            Defaults to False.

    Returns:
        tuple[bool, str | None]: (is_valid, error_message).
        The error message is None if validation succeeds.
    """
    err = _check_is_dict(data)
    if err:
        return False, err

    err = _check_required_title(data, is_update=is_update)
    if err:
        return False, err

    if 'title' in data:
        err = _check_title(data['title'])
        if err:
            return False, err

    if 'description' in data:
        err = _check_description(data['description'])
        if err:
            return False, err

    if 'status' in data:
        err = _check_status(data['status'])
        if err:
            return False, err

    return True, None
