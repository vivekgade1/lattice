# ruff: noqa
"""Projects module package.

Defines the projects Blueprint and wires route handlers.
"""

from flask import Blueprint

project_bp = Blueprint('projects', __name__, url_prefix='/projects')

from . import routes
