# ruff: noqa
"""Validation utilities for project data.

This module provides functions for validating incoming JSON data related to
projects, ensuring required fields exist and meet type and length constraints.

Constants:
    MAX_PROJECT_NAME_LEN (int): Maximum allowed length for project names.
    MIN_USER_ID (int): Minimum valid value for user IDs.

Functions:
    validate_project_data: Validate project creation or update input.
"""

MAX_PROJECT_NAME_LEN = 200
MIN_USER_ID = 1


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


def _check_required_fields(
    data: dict[str, object],
    required: tuple[str, ...],
) -> str | None:
    """Check that all required fields are present in the input data.

    Args:
        data (dict[str, object]): Input data to validate.
        required (tuple[str, ...]): Required field names.

    Returns:
        str | None: An error message if any field is missing, otherwise None.
    """
    for field in required:
        if field not in data:
            return f"Field '{field}' is required"
    return None


def _check_name(name: object) -> str | None:
    """Validate the 'name' field for type, emptiness, and length.

    Args:
        name (object): The provided name value.

    Returns:
        str | None: An error message if invalid, otherwise None.
    """
    if not isinstance(name, str) or not name.strip():
        return "Field 'name' must be a non-empty string"
    if len(name) > MAX_PROJECT_NAME_LEN:
        return (
            f"Field 'name' must be at most {MAX_PROJECT_NAME_LEN} characters"
        )
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


def _check_user_id(user_id: object) -> str | None:
    """Validate the 'user_id' field for type and minimum value.

    Args:
        user_id (object): The provided user ID value.

    Returns:
        str | None: An error message if invalid, otherwise None.
    """
    if not isinstance(user_id, int) or user_id < MIN_USER_ID:
        return "Field 'user_id' must be a positive integer"
    return None


def validate_project_data(
    data: dict[str, object],
    *,
    is_update: bool = False,
) -> tuple[bool, str | None]:
    """Validate project input data for creation or update.

    Performs structure, type, and field-level validation on a project's input
    data. For creation requests, ensures all required fields are present.
    Returns a boolean indicating success and an optional error message.

    Args:
        data (dict[str, object]): The request body to validate.
        is_update (bool, optional): Whether the operation is an update.
            Defaults to False.

    Returns:
        tuple[bool, str | None]: (is_valid, error_message). The error message
        is None if validation succeeds.
    """
    err = _check_is_dict(data)
    if err:
        return False, err

    if not is_update:
        err = _check_required_fields(data, ('name', 'user_id'))
        if err:
            return False, err

    if 'name' in data:
        err = _check_name(data['name'])
        if err:
            return False, err

    if 'description' in data:
        err = _check_description(data['description'])
        if err:
            return False, err

    if 'user_id' in data:
        err = _check_user_id(data['user_id'])
        if err:
            return False, err

    return True, None
