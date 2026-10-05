# ruff: noqa
"""Tasks module package.

Defines the tasks Blueprint and wires route handlers.
"""

from flask import Blueprint

task_bp = Blueprint('tasks', __name__)

from . import routes
