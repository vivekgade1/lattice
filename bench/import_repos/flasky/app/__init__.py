# ruff: noqa
# Upstream app/__init__.py, cut down to the API: only the extensions the models and the
# `api` blueprint use (db, login_manager) and only the `api` blueprint. The cut lines are
# kept as comments, each marked LATTICE MOD (see PROVENANCE.md).
from flask import Flask
# from flask_bootstrap import Bootstrap  # LATTICE MOD (removed: HTML front end only; app/main and app/auth are not vendored)
# from flask_mail import Mail  # LATTICE MOD (removed: only app/email.py, used by app/auth, sends mail; not vendored)
# from flask_moment import Moment  # LATTICE MOD (removed: HTML front end only)
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
# from flask_pagedown import PageDown  # LATTICE MOD (removed: HTML front end only)
from bench.import_repos.flasky.config import config  # LATTICE MOD (was `from config import config`: import path)

# bootstrap = Bootstrap()  # LATTICE MOD (removed: HTML front end only)
# mail = Mail()  # LATTICE MOD (removed: see the import above)
# moment = Moment()  # LATTICE MOD (removed: HTML front end only)
db = SQLAlchemy()
# pagedown = PageDown()  # LATTICE MOD (removed: HTML front end only)

login_manager = LoginManager()
login_manager.login_view = 'auth.login'


def create_app(config_name):
    app = Flask(__name__)
    app.config.from_object(config[config_name])
    config[config_name].init_app(app)

    # bootstrap.init_app(app)  # LATTICE MOD (removed: HTML front end only)
    # mail.init_app(app)  # LATTICE MOD (removed: see the import above)
    # moment.init_app(app)  # LATTICE MOD (removed: HTML front end only)
    db.init_app(app)
    login_manager.init_app(app)
    # pagedown.init_app(app)  # LATTICE MOD (removed: HTML front end only)

    if app.config['SSL_REDIRECT']:
        from flask_sslify import SSLify
        sslify = SSLify(app)

    # from .main import main as main_blueprint  # LATTICE MOD (removed: HTML blueprint, not vendored)
    # app.register_blueprint(main_blueprint)  # LATTICE MOD (removed: HTML blueprint, not vendored)

    # from .auth import auth as auth_blueprint  # LATTICE MOD (removed: HTML blueprint, not vendored)
    # app.register_blueprint(auth_blueprint, url_prefix='/auth')  # LATTICE MOD (removed: HTML blueprint, not vendored)

    from .api import api as api_blueprint
    app.register_blueprint(api_blueprint, url_prefix='/api/v1')

    return app
