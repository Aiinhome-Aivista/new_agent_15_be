from app import create_app, db
# Import all models so SQLAlchemy registers them
from app.models.role import Role
from app.models.user import User
from app.models.workflow import Workflow, WorkflowStep
from app.models.story import Story
from app.models.devaa_models import PullRequest, QAReview, GuardrailEvent, SuccessMetric, AuditLog

app = create_app()

with app.app_context():
    db.create_all()
    print("All tables created/verified successfully!")
    # Print all registered tables
    print("Registered tables:", list(db.metadata.tables.keys()))
