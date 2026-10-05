# ruff: noqa
from bench.import_repos.realworld.conduit._lattice_compat import current_user  # LATTICE MOD (was `from flask_jwt_extended import current_user`: flask-jwt-extended 4 renamed jwt_required/jwt_optional and raises on current_user outside a JWT view; 3.x semantics from _lattice_compat.py)

from bench.import_repos.realworld.conduit.database import (Model, SurrogatePK, db,  # LATTICE MOD (was `from conduit.database import (Model, SurrogatePK, db,`: import path)
                              reference_col, relationship)


followers_assoc = db.Table("followers_assoc",
                           db.Column("follower", db.Integer, db.ForeignKey("userprofile.user_id")),
                           db.Column("followed_by", db.Integer, db.ForeignKey("userprofile.user_id")))


class UserProfile(Model, SurrogatePK):
    __tablename__ = 'userprofile'

    # id is needed for primary join, it does work with SurrogatePK class
    id = db.Column(db.Integer, primary_key=True)

    user_id = reference_col('users', nullable=False)
    user = relationship('User', backref=db.backref('profile', uselist=False))
    follows = relationship('UserProfile',
                           secondary=followers_assoc,
                           primaryjoin=id == followers_assoc.c.follower,
                           secondaryjoin=id == followers_assoc.c.followed_by,
                           backref='followed_by',
                           lazy='dynamic')

    def __init__(self, user, **kwargs):
        db.Model.__init__(self, user=user, **kwargs)

    def is_following(self, profile):
        return bool(self.follows.filter(followers_assoc.c.followed_by == profile.id).count())

    def follow(self, profile):
        if self is not profile and not self.is_following(profile):
            self.follows.append(profile)
            return True
        return False

    def unfollow(self, profile):
        if self is not profile and self.is_following(profile):
            self.follows.remove(profile)
            return True
        return False

    @property
    def following(self):
        if current_user:
            return current_user.profile.is_following(self)
        return False

    @property
    def username(self):
        return self.user.username

    @property
    def bio(self):
        return self.user.bio

    @property
    def image(self):
        return self.user.image

    @property
    def email(self):
        return self.user.email
