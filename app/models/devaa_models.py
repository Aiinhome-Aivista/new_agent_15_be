from app import db
from datetime import datetime


class PullRequest(db.Model):
    __tablename__ = 'pull_requests'

    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey('workflows.id'), nullable=False)
    story_id = db.Column(db.Integer, db.ForeignKey('stories.id'), nullable=True)
    branch_name = db.Column(db.String(255), nullable=True)
    pr_url = db.Column(db.String(500), nullable=True)
    pr_number = db.Column(db.Integer, nullable=True)
    pr_status = db.Column(db.String(50), default='open')  # open | merged | rejected | closed
    pr_summary = db.Column(db.Text, nullable=True)
    changed_files = db.Column(db.JSON, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    merged_by = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    merged_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relationships
    qa_reviews = db.relationship('QAReview', backref='pull_request', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'workflow_id': self.workflow_id,
            'story_id': self.story_id,
            'branch_name': self.branch_name,
            'pr_url': self.pr_url,
            'pr_number': self.pr_number,
            'pr_status': self.pr_status,
            'pr_summary': self.pr_summary,
            'changed_files': self.changed_files,
            'created_by': self.created_by,
            'merged_by': self.merged_by,
            'merged_at': self.merged_at.isoformat() if self.merged_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class QAReview(db.Model):
    __tablename__ = 'qa_reviews'

    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey('workflows.id'), nullable=False)
    pr_id = db.Column(db.Integer, db.ForeignKey('pull_requests.id'), nullable=True)
    story_id = db.Column(db.Integer, db.ForeignKey('stories.id'), nullable=True)
    reviewer_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    decision = db.Column(db.String(50), nullable=False)  # approved | rejected
    comments = db.Column(db.Text, nullable=True)
    is_rework = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    reviewer = db.relationship('User', backref='qa_reviews')

    def to_dict(self):
        return {
            'id': self.id,
            'workflow_id': self.workflow_id,
            'pr_id': self.pr_id,
            'story_id': self.story_id,
            'reviewer_id': self.reviewer_id,
            'decision': self.decision,
            'comments': self.comments,
            'is_rework': self.is_rework,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class GuardrailEvent(db.Model):
    __tablename__ = 'guardrail_events'

    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey('workflows.id'), nullable=True)
    story_id = db.Column(db.Integer, db.ForeignKey('stories.id'), nullable=True)
    rail_type = db.Column(db.String(50), nullable=False)  # input | dialog | retrieval | execution | output
    action_blocked = db.Column(db.String(255), nullable=True)
    reason = db.Column(db.Text, nullable=True)
    triggered_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'workflow_id': self.workflow_id,
            'story_id': self.story_id,
            'rail_type': self.rail_type,
            'action_blocked': self.action_blocked,
            'reason': self.reason,
            'triggered_at': self.triggered_at.isoformat() if self.triggered_at else None,
        }


class SuccessMetric(db.Model):
    __tablename__ = 'success_metrics'

    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey('workflows.id'), nullable=True)
    story_id = db.Column(db.Integer, db.ForeignKey('stories.id'), nullable=True)
    metric_name = db.Column(db.String(100), nullable=False)
    metric_value = db.Column(db.Numeric(10, 4), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    measured_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'workflow_id': self.workflow_id,
            'story_id': self.story_id,
            'metric_name': self.metric_name,
            'metric_value': float(self.metric_value) if self.metric_value else None,
            'notes': self.notes,
            'measured_at': self.measured_at.isoformat() if self.measured_at else None,
        }


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'

    id = db.Column(db.Integer, primary_key=True)
    workflow_id = db.Column(db.Integer, db.ForeignKey('workflows.id'), nullable=True)
    story_id = db.Column(db.Integer, db.ForeignKey('stories.id'), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    event_type = db.Column(db.String(100), nullable=False)
    event_data = db.Column(db.JSON, nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id': self.id,
            'workflow_id': self.workflow_id,
            'story_id': self.story_id,
            'user_id': self.user_id,
            'event_type': self.event_type,
            'event_data': self.event_data,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class PipelineLog(db.Model):
    """
    Real-time pipeline event log — written by Orchestrator and agents.
    Polled by the frontend every 2 s during a live run to display the
    animated activity feed in the PO Dashboard.

    level:  'info' | 'success' | 'warning' | 'error' | 'jira'
    agent:  e.g. 'Intake', 'RepoAnalysis', 'Developer', 'Validator',
                 'BranchPR', 'Comment', 'Orchestrator'
    """
    __tablename__ = 'pipeline_logs'

    id          = db.Column(db.Integer, primary_key=True)
    story_id    = db.Column(db.Integer, db.ForeignKey('stories.id'), nullable=False)
    workflow_id = db.Column(db.Integer, db.ForeignKey('workflows.id'), nullable=True)
    agent       = db.Column(db.String(100), nullable=False, default='Orchestrator')
    level       = db.Column(db.String(20),  nullable=False, default='info')
    message     = db.Column(db.Text, nullable=False)
    detail      = db.Column(db.Text, nullable=True)   # optional extra context / JSON
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            'id':          self.id,
            'story_id':    self.story_id,
            'workflow_id': self.workflow_id,
            'agent':       self.agent,
            'level':       self.level,
            'message':     self.message,
            'detail':      self.detail,
            'created_at':  self.created_at.isoformat() if self.created_at else None,
        }
