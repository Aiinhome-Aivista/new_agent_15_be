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

    # Ensure schema migrations like evidence_report and reference_repo columns exist
    with app.app_context():
        try:
            from sqlalchemy import text, inspect
            with db.engine.connect() as conn:
                inspector = inspect(db.engine)
                existing_tables = inspector.get_table_names()

                if 'pull_requests' in existing_tables:
                    pr_cols = [c['name'] for c in inspector.get_columns('pull_requests')]
                    if 'evidence_report' not in pr_cols:
                        conn.execute(text("ALTER TABLE pull_requests ADD COLUMN evidence_report JSON NULL"))

                if 'stories' in existing_tables:
                    story_cols = [c['name'] for c in inspector.get_columns('stories')]
                    if 'reference_repo_url' not in story_cols:
                        conn.execute(text("ALTER TABLE stories ADD COLUMN reference_repo_url VARCHAR(500) NULL"))
                    if 'reference_repo_branch' not in story_cols:
                        conn.execute(text("ALTER TABLE stories ADD COLUMN reference_repo_branch VARCHAR(255) NULL"))
                    if 'reference_repo_metadata' not in story_cols:
                        conn.execute(text("ALTER TABLE stories ADD COLUMN reference_repo_metadata JSON NULL"))

                conn.commit()
        except Exception as e:
            app.logger.warning(f"Auto-migration column check warning: {e}")

    @app.route('/health')
    def health_check():
        return {"status": "healthy", "service": app.config["PROJECT_NAME"]}

    return app
