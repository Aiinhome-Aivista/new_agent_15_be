from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_cors import CORS
from app.config.settings import Config

db = SQLAlchemy()
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
    from app.routes.workflows import workflows_bp
    from app.routes.stories import stories_bp
    from app.routes.pull_requests import pull_requests_bp
    from app.routes.qa import qa_bp
    from app.routes.admin import admin_bp
    from app.routes.connectors import connectors_bp

    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(workflows_bp, url_prefix='/api/workflows')
    app.register_blueprint(stories_bp, url_prefix='/api/stories')
    app.register_blueprint(pull_requests_bp, url_prefix='/api/pull-requests')
    app.register_blueprint(qa_bp, url_prefix='/api/qa')
    app.register_blueprint(admin_bp, url_prefix='/api/admin')
    app.register_blueprint(connectors_bp, url_prefix='/api/connectors')

    @app.route('/health')
    def health_check():
        return {"status": "healthy", "service": app.config["PROJECT_NAME"]}

    return app
