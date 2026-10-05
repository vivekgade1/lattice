# ruff: noqa
# -*- coding: utf-8 -*-
"""User views."""
from flask import Blueprint, request
from flask_apispec import use_kwargs, marshal_with
from flask_jwt_extended import create_access_token  # LATTICE MOD (was `from flask_jwt_extended import jwt_required, jwt_optional, create_access_token, current_user`: flask-jwt-extended 4; jwt_required, jwt_optional and current_user come from _lattice_compat.py on the next line)
from bench.import_repos.realworld.conduit._lattice_compat import jwt_required, jwt_optional, current_user  # LATTICE MOD (added: see the line above)
from sqlalchemy.exc import IntegrityError

from bench.import_repos.realworld.conduit.database import db  # LATTICE MOD (was `from conduit.database import db`: import path)
from bench.import_repos.realworld.conduit.exceptions import InvalidUsage  # LATTICE MOD (was `from conduit.exceptions import InvalidUsage`: import path)
from bench.import_repos.realworld.conduit.profile.models import UserProfile  # LATTICE MOD (was `from conduit.profile.models import UserProfile`: import path)
from .models import User
from .serializers import user_schema

blueprint = Blueprint('user', __name__)


@blueprint.route('/api/users', methods=('POST',))
@use_kwargs(user_schema)
@marshal_with(user_schema)
def register_user(username, password, email, **kwargs):
    try:
        userprofile = UserProfile(User(username, email, password=password, **kwargs).save()).save()
        userprofile.user.token = create_access_token(identity=userprofile.user)
    except IntegrityError:
        db.session.rollback()
        raise InvalidUsage.user_already_registered()
    return userprofile.user


@blueprint.route('/api/users/login', methods=('POST',))
@jwt_optional
@use_kwargs(user_schema)
@marshal_with(user_schema)
def login_user(email, password, **kwargs):
    user = User.query.filter_by(email=email).first()
    if user is not None and user.check_password(password):
        user.token = create_access_token(identity=user, fresh=True)
        return user
    else:
        raise InvalidUsage.user_not_found()


@blueprint.route('/api/user', methods=('GET',))
@jwt_required
@marshal_with(user_schema)
def get_user():
    user = current_user
    # Not sure about this
    user.token = request.headers.environ['HTTP_AUTHORIZATION'].split('Token ')[1]
    return current_user


@blueprint.route('/api/user', methods=('PUT',))
@jwt_required
@use_kwargs(user_schema)
@marshal_with(user_schema)
def update_user(**kwargs):
    user = current_user
    # take in consideration the password
    password = kwargs.pop('password', None)
    if password:
        user.set_password(password)
    if 'updated_at' in kwargs:
        kwargs['updated_at'] = user.created_at.replace(tzinfo=None)
    user.update(**kwargs)
    return user
