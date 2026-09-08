from app import db
from datetime import datetime

class Workflow(db.Model):
    __tablename__ = 'workflows'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(50), nullable=False, default='Pending')
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    story_id = db.Column(db.Integer, db.ForeignKey('stories.id'), nullable=True)  # link to story
    requirements_doc = db.Column(db.Text, nullable=True)
    current_agent = db.Column(db.String(100), nullable=True)   # which agent is running
    loop_iteration = db.Column(db.Integer, default=1)          # dev↔validator loop counter
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    owner = db.relationship('User', backref='workflows', foreign_keys=[owner_id])

class WorkflowStep(db.Model):
    __tablename__ = 'workflow_steps'

    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey('workflows.id'), nullable=False)
    step_type = db.Column(db.String(50), nullable=False)
    agent_prompt = db.Column(db.Text, nullable=False)
    agent_response = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), nullable=False, default='In Progress')
    token_count = db.Column(db.Integer, default=0)
    cost_usd = db.Column(db.Numeric(10, 6), default=0)
    loop_iteration = db.Column(db.Integer, default=1)
    guardrail_triggered = db.Column(db.Boolean, default=False)
    guardrail_reason = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    workflow = db.relationship('Workflow', backref=db.backref('steps', lazy=True, cascade='all, delete-orphan'))
