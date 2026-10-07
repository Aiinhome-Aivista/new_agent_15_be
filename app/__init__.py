from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS
from app.config.settings import Config

from app.extensions.db import db
migrate = Migrate()

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # Initialize extensions
    db.init_app(app)
    migrate.init_app(app, db)
    CORS(app, resources={r"/api/*": {"origins": app.config["ALLOWED_ORIGINS"]}})

    # Register blueprints
    from app.routes.auth import auth_bp
    # from app.routes.workflows import workflows_bp  # Standalone workflows disabled
    from app.routes.stories import stories_bp
    from app.routes.pull_requests import pull_requests_bp
    from app.routes.qa import qa_bp
    from app.routes.admin import admin_bp
    from app.routes.connectors import connectors_bp

    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    # app.register_blueprint(workflows_bp, url_prefix='/api/workflows')  # Standalone workflows disabled
    app.register_blueprint(stories_bp, url_prefix='/api/stories')
    app.register_blueprint(pull_requests_bp, url_prefix='/api/pull-requests')
    app.register_blueprint(qa_bp, url_prefix='/api/qa')
    app.register_blueprint(admin_bp, url_prefix='/api/admin')
    app.register_blueprint(connectors_bp, url_prefix='/api/connectors')

    # Ensure schema migrations like evidence_report column exist
    with app.app_context():
        try:
            from sqlalchemy import text
            with db.engine.connect() as conn:
                conn.execute(text("ALTER TABLE pull_requests ADD COLUMN evidence_report JSON NULL"))
                conn.commit()
        except Exception:
            pass

    @app.route('/health')
    def health_check():
        from app.utils.responses import api_response
        return api_response(200, True, "Service is healthy", {"service": app.config["PROJECT_NAME"]})

    from app.utils.responses import api_response

    @app.errorhandler(400)
    def bad_request_error(e):
        return api_response(400, False, str(e.description) if hasattr(e, 'description') else "Bad Request")

    @app.errorhandler(404)
    def not_found_error(e):
        return api_response(404, False, "Resource not found")

    @app.errorhandler(500)
    def internal_error(e):
        return api_response(500, False, "Internal server error")

    @app.errorhandler(Exception)
    def unhandled_exception(e):
        return api_response(500, False, str(e))

    return app
