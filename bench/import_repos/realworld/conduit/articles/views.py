# ruff: noqa
# coding: utf-8

import datetime as dt

from flask import Blueprint, jsonify
from flask_apispec import marshal_with, use_kwargs
from bench.import_repos.realworld.conduit._lattice_compat import current_user, jwt_required, jwt_optional  # LATTICE MOD (was `from flask_jwt_extended import current_user, jwt_required, jwt_optional`: flask-jwt-extended 4 renamed jwt_required/jwt_optional and raises on current_user outside a JWT view; 3.x semantics from _lattice_compat.py)
from marshmallow import fields

from bench.import_repos.realworld.conduit.exceptions import InvalidUsage  # LATTICE MOD (was `from conduit.exceptions import InvalidUsage`: import path)
from bench.import_repos.realworld.conduit.user.models import User  # LATTICE MOD (was `from conduit.user.models import User`: import path)
from .models import Article, Tags, Comment
from .serializers import (article_schema, articles_schema, comment_schema,
                          comments_schema)

blueprint = Blueprint('articles', __name__)


##########
# Articles
##########

@blueprint.route('/api/articles', methods=('GET',))
@jwt_optional
@use_kwargs(location='query', args={'tag': fields.Str(), 'author': fields.Str(),  # LATTICE MOD (was `@use_kwargs({'tag': fields.Str(), 'author': fields.Str(),`: webargs 3 read these from the query string by default; webargs 8 / flask-apispec 0.11 default to the JSON body)
             'favorited': fields.Str(), 'limit': fields.Int(), 'offset': fields.Int()})
@marshal_with(articles_schema)
def get_articles(tag=None, author=None, favorited=None, limit=20, offset=0):
    res = Article.query
    if tag:
        res = res.filter(Article.tagList.any(Tags.tagname == tag))
    if author:
        res = res.join(Article.author).join(User).filter(User.username == author)
    if favorited:
        res = res.join(Article.favoriters).filter(User.username == favorited)
    return res.offset(offset).limit(limit).all()


@blueprint.route('/api/articles', methods=('POST',))
@jwt_required
@use_kwargs(article_schema)
@marshal_with(article_schema)
def make_article(body, title, description, tagList=None):
    article = Article(title=title, description=description, body=body,
                      author=current_user.profile)
    if tagList is not None:
        for tag in tagList:
            mtag = Tags.query.filter_by(tagname=tag).first()
            if not mtag:
                mtag = Tags(tag)
                mtag.save()
            article.add_tag(mtag)
    article.save()
    return article


@blueprint.route('/api/articles/<slug>', methods=('PUT',))
@jwt_required
@use_kwargs(article_schema)
@marshal_with(article_schema)
def update_article(slug, **kwargs):
    article = Article.query.filter_by(slug=slug, author_id=current_user.profile.id).first()
    if not article:
        raise InvalidUsage.article_not_found()
    article.update(updatedAt=dt.datetime.utcnow(), **kwargs)
    article.save()
    return article


@blueprint.route('/api/articles/<slug>', methods=('DELETE',))
@jwt_required
def delete_article(slug):
    article = Article.query.filter_by(slug=slug, author_id=current_user.profile.id).first()
    article.delete()
    return '', 200


@blueprint.route('/api/articles/<slug>', methods=('GET',))
@jwt_optional
@marshal_with(article_schema)
def get_article(slug):
    article = Article.query.filter_by(slug=slug).first()
    if not article:
        raise InvalidUsage.article_not_found()
    return article


@blueprint.route('/api/articles/<slug>/favorite', methods=('POST',))
@jwt_required
@marshal_with(article_schema)
def favorite_an_article(slug):
    profile = current_user.profile
    article = Article.query.filter_by(slug=slug).first()
    if not article:
        raise InvalidUsage.article_not_found()
    article.favourite(profile)
    article.save()
    return article


@blueprint.route('/api/articles/<slug>/favorite', methods=('DELETE',))
@jwt_required
@marshal_with(article_schema)
def unfavorite_an_article(slug):
    profile = current_user.profile
    article = Article.query.filter_by(slug=slug).first()
    if not article:
        raise InvalidUsage.article_not_found()
    article.unfavourite(profile)
    article.save()
    return article


@blueprint.route('/api/articles/feed', methods=('GET',))
@jwt_required
@use_kwargs({'limit': fields.Int(), 'offset': fields.Int()}, location='query')  # LATTICE MOD (was `@use_kwargs({'limit': fields.Int(), 'offset': fields.Int()})`: webargs 3 read these from the query string by default; webargs 8 / flask-apispec 0.11 default to the JSON body)
@marshal_with(articles_schema)
def articles_feed(limit=20, offset=0):
    # LATTICE MOD (next line, was `return Article.query.join(current_user.profile.follows). \`: SQLAlchemy 1.x turned a Query join target into a subquery implicitly; 2.0 needs .subquery())
    return Article.query.join(current_user.profile.follows.subquery()). \
        order_by(Article.createdAt.desc()).offset(offset).limit(limit).all()


######
# Tags
######

@blueprint.route('/api/tags', methods=('GET',))
def get_tags():
    return jsonify({'tags': [tag.tagname for tag in Tags.query.all()]})


##########
# Comments
##########


@blueprint.route('/api/articles/<slug>/comments', methods=('GET',))
@marshal_with(comments_schema)
def get_comments(slug):
    article = Article.query.filter_by(slug=slug).first()
    if not article:
        raise InvalidUsage.article_not_found()
    return article.comments


@blueprint.route('/api/articles/<slug>/comments', methods=('POST',))
@jwt_required
@use_kwargs(comment_schema)
@marshal_with(comment_schema)
def make_comment_on_article(slug, body, **kwargs):
    article = Article.query.filter_by(slug=slug).first()
    if not article:
        raise InvalidUsage.article_not_found()
    comment = Comment(article, current_user.profile, body, **kwargs)
    comment.save()
    return comment


@blueprint.route('/api/articles/<slug>/comments/<cid>', methods=('DELETE',))
@jwt_required
def delete_comment_on_article(slug, cid):
    article = Article.query.filter_by(slug=slug).first()
    if not article:
        raise InvalidUsage.article_not_found()

    comment = article.comments.filter_by(id=cid, author=current_user.profile).first()
    comment.delete()
    return '', 200
