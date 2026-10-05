# ruff: noqa
"""Users module package.

Defines the users Blueprint and wires route handlers.
"""

from flask import Blueprint

user_bp = Blueprint('users', __name__, url_prefix='/users')

from . import routes
