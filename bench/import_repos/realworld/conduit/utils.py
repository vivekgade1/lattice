# ruff: noqa
# -*- coding: utf-8 -*-
"""Helper utilities and decorators."""
from bench.import_repos.realworld.conduit.user.models import User  # noqa  # LATTICE MOD (was `from conduit.user.models import User`: import path)


def jwt_identity(payload):
    return User.get_by_id(payload)


def identity_loader(user):
    return user.id
