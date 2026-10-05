import enum
import hashlib
import hmac

from flask_toy import db


class Kind(enum.Enum):
    MANAGER = "manager"
    EMPLOYEE = "employee"


def _digest(password, salt):
    return hashlib.sha256((salt + password).encode()).hexdigest()


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    kind = db.Column(db.Enum(Kind), nullable=False, default=Kind.EMPLOYEE)

    # hashing of its own (no library hasher the front end knows by name): the seed has
    # to go through set_password
    def set_password(self, password):
        self.password_hash = "toy$" + _digest(password, "pepper")

    def check_password(self, password):
        return hmac.compare_digest(self.password_hash, "toy$" + _digest(password, "pepper"))


class Note(db.Model):
    __tablename__ = "notes"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    title = db.Column(db.String(100), nullable=False)
