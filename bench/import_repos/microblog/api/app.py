# ruff: noqa
from flask import Flask, redirect, url_for, request
from alchemical.flask import Alchemical
from flask_marshmallow import Marshmallow
from flask_cors import CORS
from flask_mail import Mail
from apifairy import APIFairy
from bench.import_repos.microblog.config import Config  # LATTICE MOD (was `from config import Config`: import path)

db = Alchemical()
ma = Marshmallow()
cors = CORS()
mail = Mail()
apifairy = APIFairy()


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # extensions
    from bench.import_repos.microblog.api import models  # LATTICE MOD (was `from api import models`: import path)
    db.init_app(app)
    ma.init_app(app)
    if app.config['USE_CORS']:  # pragma: no branch
        cors.init_app(app)
    mail.init_app(app)
    apifairy.init_app(app)

    # blueprints
    from bench.import_repos.microblog.api.errors import errors  # LATTICE MOD (was `from api.errors import errors`: import path)
    app.register_blueprint(errors)
    from bench.import_repos.microblog.api.tokens import tokens  # LATTICE MOD (was `from api.tokens import tokens`: import path)
    app.register_blueprint(tokens, url_prefix='/api')
    from bench.import_repos.microblog.api.users import users  # LATTICE MOD (was `from api.users import users`: import path)
    app.register_blueprint(users, url_prefix='/api')
    from bench.import_repos.microblog.api.posts import posts  # LATTICE MOD (was `from api.posts import posts`: import path)
    app.register_blueprint(posts, url_prefix='/api')
    # from api.fake import fake  # LATTICE MOD (removed: api/fake.py (Faker-based CLI seeding commands, no routes) is not vendored)
    # app.register_blueprint(fake)  # LATTICE MOD (removed: api/fake.py (Faker-based CLI seeding commands, no routes) is not vendored)

    # define the shell context
    @app.shell_context_processor
    def shell_context():  # pragma: no cover
        ctx = {'db': db}
        for attr in dir(models):
            model = getattr(models, attr)
            if hasattr(model, '__bases__') and \
                    db.Model in getattr(model, '__bases__'):
                ctx[attr] = model
        return ctx

    @app.route('/')
    def index():  # pragma: no cover
        return redirect(url_for('apifairy.docs'))

    @app.after_request
    def after_request(response):
        # Werkzeug sometimes does not flush the request body so we do it here
        request.get_data()
        return response

    return app
