from flask_rel_toy import db

post_tags = db.Table(
    "post_tags",
    db.Column("post_id", db.Integer, db.ForeignKey("posts.id"), primary_key=True),
    db.Column("tag_id", db.Integer, db.ForeignKey("tags.id"), primary_key=True),
)
likes = db.Table(
    "likes",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id"), primary_key=True),
    db.Column("post_id", db.Integer, db.ForeignKey("posts.id"), primary_key=True),
)
stars = db.Table(
    "stars",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id"), primary_key=True),
    db.Column("post_id", db.Integer, db.ForeignKey("posts.id"), primary_key=True),
)


class Updateable:
    def update(self, **data):
        for attr, value in data.items():
            setattr(self, attr, value)

    def set_fields(self, data):
        for attr in ("name",):
            setattr(self, attr, data[attr])


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)


class Tag(Updateable, db.Model):
    __tablename__ = "tags"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(40), nullable=False)


class Post(Updateable, db.Model):
    __tablename__ = "posts"

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    tags = db.relationship("Tag", secondary=post_tags, backref="posts")
    likers = db.relationship("User", secondary=likes)
    starrers = db.relationship("User", secondary=stars, lazy="dynamic")

    def star(self, user):
        if user not in self.starrers:
            self.starrers.append(user)


class Archive(db.Model):
    __tablename__ = "archive"

    id = db.Column(db.Integer, primary_key=True)
    note = db.Column(db.String(100))
