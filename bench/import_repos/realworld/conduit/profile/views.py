# ruff: noqa
# coding: utf-8

from flask import Blueprint
from flask_apispec import marshal_with
from bench.import_repos.realworld.conduit._lattice_compat import current_user, jwt_required, jwt_optional  # LATTICE MOD (was `from flask_jwt_extended import current_user, jwt_required, jwt_optional`: flask-jwt-extended 4 renamed jwt_required/jwt_optional and raises on current_user outside a JWT view; 3.x semantics from _lattice_compat.py)

from bench.import_repos.realworld.conduit.exceptions import InvalidUsage  # LATTICE MOD (was `from conduit.exceptions import InvalidUsage`: import path)
from bench.import_repos.realworld.conduit.user.models import User  # LATTICE MOD (was `from conduit.user.models import User`: import path)
from .serializers import profile_schema

blueprint = Blueprint('profiles', __name__)


@blueprint.route('/api/profiles/<username>', methods=('GET',))
@jwt_optional
@marshal_with(profile_schema)
def get_profile(username):
    user = User.query.filter_by(username=username).first()
    if not user:
        raise InvalidUsage.user_not_found()
    return user.profile


@blueprint.route('/api/profiles/<username>/follow', methods=('POST',))
@jwt_required
@marshal_with(profile_schema)
def follow_user(username):
    user = User.query.filter_by(username=username).first()
    if not user:
        raise InvalidUsage.user_not_found()
    current_user.profile.follow(user.profile)
    current_user.profile.save()
    return user.profile


@blueprint.route('/api/profiles/<username>/follow', methods=('DELETE',))
@jwt_required
@marshal_with(profile_schema)
def unfollow_user(username):
    user = User.query.filter_by(username=username).first()
    if not user:
        raise InvalidUsage.user_not_found()
    current_user.profile.unfollow(user.profile)
    current_user.profile.save()
    return user.profile
