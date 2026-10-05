# ruff: noqa
# -*- coding: utf-8 -*-
"""Extensions module. Each extension is initialized in the app factory located in app.py."""

from flask_bcrypt import Bcrypt
from flask_caching import Cache
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy  # LATTICE MOD (was `from flask_sqlalchemy import SQLAlchemy, Model`: Flask-SQLAlchemy 3 no longer exports Model at the top level)
from flask_sqlalchemy.model import Model  # LATTICE MOD (added: see the line above)


class CRUDMixin(Model):
    """Mixin that adds convenience methods for CRUD (create, read, update, delete) operations."""

    @classmethod
    def create(cls, **kwargs):
        """Create a new record and save it the database."""
        instance = cls(**kwargs)
        return instance.save()

    def update(self, commit=True, **kwargs):
        """Update specific fields of a record."""
        for attr, value in kwargs.items():
            setattr(self, attr, value)
        return commit and self.save() or self

    def save(self, commit=True):
        """Save the record."""
        db.session.add(self)
        if commit:
            db.session.commit()
        return self

    def delete(self, commit=True):
        """Remove the record from the database."""
        db.session.delete(self)
        return commit and db.session.commit()


bcrypt = Bcrypt()
db = SQLAlchemy(model_class=CRUDMixin)
migrate = Migrate()
cache = Cache()
cors = CORS()

from bench.import_repos.realworld.conduit.utils import jwt_identity, identity_loader  # noqa  # LATTICE MOD (was `from conduit.utils import jwt_identity, identity_loader`: import path)

jwt = JWTManager()
jwt.user_lookup_loader(lambda _jwt_header, jwt_data: jwt_identity(jwt_data['sub']))  # LATTICE MOD (was `jwt.user_loader_callback_loader(jwt_identity)`: flask-jwt-extended 4 renamed the hook and passes (header, claims) instead of the identity; the identity is the `sub` claim)
jwt.user_identity_loader(identity_loader)
