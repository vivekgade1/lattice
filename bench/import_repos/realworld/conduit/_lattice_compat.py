"""Lattice-owned (not upstream): flask-jwt-extended 3.x names on flask-jwt-extended 4.x.

Upstream was written against flask-jwt-extended 3.10 (``Pipfile.lock``). Version 4 changed
three names the views and models import, without changing what they check:

- ``jwt_required`` became a decorator *factory* (``@jwt_required()``); 3.x used it bare.
- ``jwt_optional`` was removed; 4.x spells it ``@jwt_required(optional=True)``. Both
  versions let a request without a token through and refuse a malformed or invalid one.
- ``current_user`` outside a JWT-decorated view: 3.x answered ``None``; 4.x raises
  ``RuntimeError``. Upstream relies on the 3.x answer in ``UserProfile.following`` and
  ``Article.favorited`` when they are serialised by an undecorated view
  (``GET /api/articles/<slug>/comments``). This proxy keeps the 3.x answer.

Each vendored file that imported these names from ``flask_jwt_extended`` imports them from
here instead (``LATTICE MOD``, see ``PROVENANCE.md``). ``create_access_token`` and
``JWTManager`` still come from flask-jwt-extended itself.
"""

from __future__ import annotations

from flask_jwt_extended import get_current_user
from flask_jwt_extended import jwt_required as _jwt_required_factory
from werkzeug.local import LocalProxy


def jwt_required(fn):
    """3.x ``@jwt_required``: a valid access token is required."""
    return _jwt_required_factory()(fn)


def jwt_optional(fn):
    """3.x ``@jwt_optional``: no token is fine; a bad token is still refused."""
    return _jwt_required_factory(optional=True)(fn)


def _current_user_or_none():
    try:
        return get_current_user()
    except RuntimeError:  # no JWT-decorated view in this request (3.x answered None)
        return None


current_user = LocalProxy(_current_user_or_none)
