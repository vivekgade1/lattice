from functools import wraps

import jwt
from flask import Blueprint, g, jsonify, request

from flask_toy import db
from flask_toy.models import Note, User

SECRET = "flask-toy-secret-0000000000000000"
bp = Blueprint("api", __name__)


def token_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization")
        if not header:
            return jsonify({"error": "Authorization header is required"}), 401
        try:
            uid = int(jwt.decode(header.split(" ")[1], SECRET, ["HS256"])["sub"])
        except Exception:
            return jsonify({"error": "Invalid or expired token"}), 401
        user = db.session.get(User, uid)
        if user is None:
            return jsonify({"error": "Invalid or expired token"}), 401
        g.current_user = user
        return f(*args, **kwargs)

    return wrapper


@bp.route("/auth/login", methods=["POST"])
def login():
    data = request.get_json(force=True)
    if data is None:
        return jsonify({"error": "Invalid request data"}), 400
    user = User.query.filter_by(email=data["email"]).first()
    if user is None or not user.check_password(data["password"]):
        return jsonify({"error": "Invalid email or password"}), 401
    token = jwt.encode({"sub": str(user.id)}, SECRET, "HS256")
    return jsonify({"message": "Login successful", "access_token": token,
                    "user": {"id": user.id, "email": user.email}}), 200


@bp.route("/me", methods=["GET"])
@token_required
def me():
    user = g.current_user
    return jsonify({"id": user.id, "email": user.email, "kind": user.kind.value})


@bp.route("/notes", methods=["GET"])
@token_required
def notes():
    rows = Note.query.filter_by(user_id=g.current_user.id).all()
    return jsonify([{"id": n.id, "user_id": n.user_id, "title": n.title} for n in rows])
