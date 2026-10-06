from flask import Blueprint, jsonify, request

from flask_rel_toy import db
from flask_rel_toy.models import Post, Tag, User

bp = Blueprint("api", __name__)
extra = Blueprint("extra", __name__)


@bp.route("/posts", methods=["GET"])
def posts():
    return jsonify([{"id": p.id, "title": p.title} for p in Post.query.all()])


@bp.route("/posts/<int:id>/tags/<int:tag_id>", methods=["POST"])
def tag_post(id, tag_id):
    post = Post.query.get_or_404(id)
    tag = db.session.get(Tag, tag_id)
    post.tags.append(tag)
    db.session.commit()
    return jsonify({"id": post.id})


@bp.route("/posts/<int:id>/like/<int:user_id>", methods=["POST"])
def like_post(id, user_id):
    post = Post.query.get_or_404(id)
    user = db.session.get(User, user_id)
    post.likers = [*post.likers, user]
    db.session.commit()
    return jsonify({"id": post.id})


@bp.route("/posts/<int:id>/star/<int:user_id>", methods=["POST"])
def star_post(id, user_id):
    post = Post.query.get_or_404(id)
    post.star(db.session.get(User, user_id))
    db.session.commit()
    return jsonify({"id": post.id})


@bp.route("/tags/<int:id>", methods=["PUT"])
def rename_tag(id):
    tag = Tag.query.get_or_404(id)
    tag.set_fields(request.get_json())
    db.session.commit()
    return jsonify({"id": tag.id})


@extra.route("/posts/<int:id>", methods=["PUT"])
def edit_post(id):
    post = Post.query.get_or_404(id)
    post.update(**request.get_json())
    db.session.commit()
    return jsonify({"id": post.id})


@extra.route("/posts/<int:id>/attach/<field>", methods=["POST"])
def attach(id, field):
    post = Post.query.get_or_404(id)
    getattr(post, field).append(request.get_json()["item"])
    db.session.commit()
    return jsonify({"id": post.id})
