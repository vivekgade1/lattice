# ruff: noqa
"""Initialize the Flask application package.

This package sets up the Flask app, including configuration, logging,
database initialization, and blueprint registration.

Modules:
    auth: Authentication routes and logic.
    users: User management routes.
    projects: Project-related routes.
    tasks: Task management routes.

Attributes:
    db (SQLAlchemy): The SQLAlchemy database instance shared across the app.
"""

import os

from flask import Flask
from flask_sqlalchemy import SQLAlchemy

from bench.import_repos.projmgmt.app.logging_config import setup_logging  # LATTICE MOD (was `from app.logging_config import setup_logging`: import path)
from bench.import_repos.projmgmt.config import config  # LATTICE MOD (was `from config import config`: import path)

db = SQLAlchemy()


def create_app() -> Flask:
    """Application factory for creating Flask app instances."""
    app = Flask(__name__)
    setup_logging()
    app.logger.info('App initialized.')

    config_name = os.getenv('FLASK_ENV', 'development')
    app.config.from_object(config[config_name])

    db.init_app(app)

    from bench.import_repos.projmgmt.app.auth.routes import auth_bp  # LATTICE MOD (was `from app.auth.routes import auth_bp`: import path)
    from bench.import_repos.projmgmt.app.projects import project_bp  # LATTICE MOD (was `from app.projects import project_bp`: import path)
    from bench.import_repos.projmgmt.app.tasks import task_bp  # LATTICE MOD (was `from app.tasks import task_bp`: import path)
    from bench.import_repos.projmgmt.app.users import user_bp  # LATTICE MOD (was `from app.users import user_bp`: import path)

    app.register_blueprint(auth_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(project_bp)
    app.register_blueprint(task_bp)

    with app.app_context():
        db.create_all()

    @app.route('/')
    def index() -> dict[str, object]:
        """Root endpoint providing API information."""
        return {
            'message': 'Project Management API',
            'endpoints': {
                'users': '/users',
                'projects': '/projects',
                'tasks': '/projects/<id>/tasks',
            },
        }

    @app.errorhandler(404)
    def not_found(_error: Exception) -> tuple[dict[str, str], int]:
        """Handle 404 errors."""
        return {'error': 'Resource not found'}, 404

    @app.errorhandler(500)
    def internal_error(_error: Exception) -> tuple[dict[str, str], int]:
        """Handle 500 errors."""
        db.session.rollback()
        return {'error': 'Internal server error'}, 500

    return app
