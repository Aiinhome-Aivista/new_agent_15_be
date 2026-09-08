from app import db
from datetime import datetime


class Story(db.Model):
    __tablename__ = 'stories'

    id = db.Column(db.Integer, primary_key=True)
    jira_story_key = db.Column(db.String(50), unique=True, nullable=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    acceptance_criteria = db.Column(db.Text, nullable=True)
    repository_details = db.Column(db.JSON, nullable=True)  # [{url, name, branch}]
    source_branch = db.Column(db.String(255), nullable=True)
    assignee_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    owner_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    status = db.Column(db.String(50), default='TO-DO')  # TO-DO | IN-PROGRESS | QA-TESTING | DONE | INVALID
    jira_comment_id = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    owner = db.relationship('User', foreign_keys=[owner_id], backref='owned_stories')
    assignee = db.relationship('User', foreign_keys=[assignee_id], backref='assigned_stories')
    workflows = db.relationship(
        'Workflow',
        foreign_keys='Workflow.story_id',
        backref=db.backref('linked_story', lazy=True),
        lazy=True
    )

    def to_dict(self):
        return {
            'id': self.id,
            'jira_story_key': self.jira_story_key,
            'title': self.title,
            'description': self.description,
            'acceptance_criteria': self.acceptance_criteria,
            'repository_details': self.repository_details,
            'source_branch': self.source_branch,
            'assignee_id': self.assignee_id,
            'owner_id': self.owner_id,
            'status': self.status,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }
