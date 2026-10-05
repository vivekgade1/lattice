# ruff: noqa
# -*- coding: utf-8 -*-
"""User models."""
import datetime as dt

from bench.import_repos.realworld.conduit.database import Column, Model, SurrogatePK, db  # LATTICE MOD (was `from conduit.database import Column, Model, SurrogatePK, db`: import path)
from bench.import_repos.realworld.conduit.extensions import bcrypt  # LATTICE MOD (was `from conduit.extensions import bcrypt`: import path)


class User(SurrogatePK, Model):

    __tablename__ = 'users'
    username = Column(db.String(80), unique=True, nullable=False)
    email = Column(db.String(100), unique=True, nullable=False)
    password = Column(db.LargeBinary(128), nullable=True)  # LATTICE MOD (was `password = Column(db.Binary(128), nullable=True)`: SQLAlchemy 1.4/2 removed the Binary alias; LargeBinary is the same type)
    created_at = Column(db.DateTime, nullable=False, default=dt.datetime.utcnow)
    updated_at = Column(db.DateTime, nullable=False, default=dt.datetime.utcnow)
    bio = Column(db.String(300), nullable=True)
    image = Column(db.String(120), nullable=True)
    token: str = ''

    def __init__(self, username, email, password=None, **kwargs):
        """Create instance."""
        db.Model.__init__(self, username=username, email=email, **kwargs)
        if password:
            self.set_password(password)
        else:
            self.password = None

    def set_password(self, password):
        """Set password."""
        self.password = bcrypt.generate_password_hash(password)

    def check_password(self, value):
        """Check password."""
        return bcrypt.check_password_hash(self.password, value)

    def __repr__(self):
        """Represent instance as a unique string."""
        return '<User({username!r})>'.format(username=self.username)
