from flask import Blueprint, request, jsonify
from app.utils.auth import require_auth, require_role
from app import db
import logging

admin_bp = Blueprint('admin', __name__)
logger = logging.getLogger(__name__)


@admin_bp.route('/metrics', methods=['GET'])
@require_auth
@require_role(['Admin'])
def get_metrics():
    """
    Aggregated success metrics dashboard — Admin only.
    Pulls from success_metrics table.
    """
    from app.models.devaa_models import SuccessMetric
    from sqlalchemy import func

    # Aggregate by metric_name — average values
    rows = db.session.query(
        SuccessMetric.metric_name,
        func.avg(SuccessMetric.metric_value).label('avg_value'),
        func.count(SuccessMetric.id).label('sample_count'),
        func.max(SuccessMetric.measured_at).label('last_measured')
    ).group_by(SuccessMetric.metric_name).all()

    metrics = [{
        'metric_name': r.metric_name,
        'avg_value': float(r.avg_value) if r.avg_value is not None else None,
        'sample_count': r.sample_count,
        'last_measured': r.last_measured.isoformat() if r.last_measured else None
    } for r in rows]

    # System summary counts
    from app.models.story import Story
    from app.models.workflow import Workflow
    from app.models.devaa_models import PullRequest

    summary = {
        'total_stories': Story.query.count(),
        'stories_done': Story.query.filter_by(status='DONE').count(),
        'stories_in_progress': Story.query.filter_by(status='IN-PROGRESS').count(),
        'stories_qa_testing': Story.query.filter_by(status='QA-TESTING').count(),
        'stories_invalid': Story.query.filter_by(status='INVALID').count(),
        'total_workflows': Workflow.query.count(),
        'active_workflows': Workflow.query.filter(Workflow.status.in_(['Running', 'Awaiting QA'])).count(),
        'failed_workflows': Workflow.query.filter_by(status='Failed').count(),
        'total_prs': PullRequest.query.count(),
        'merged_prs': PullRequest.query.filter_by(pr_status='merged').count(),
        'rejected_prs': PullRequest.query.filter_by(pr_status='rejected').count(),
    }

    return jsonify({
        'summary': summary,
        'metrics': metrics
    }), 200


@admin_bp.route('/audit-logs', methods=['GET'])
@require_auth
@require_role(['Admin', 'Engineering Lead'])
def get_audit_logs():
    """
    Full audit trail — Admin and Engineering Lead.
    Supports optional filters: workflow_id, story_id, event_type.
    """
    from app.models.devaa_models import AuditLog

    workflow_id = request.args.get('workflow_id', type=int)
    story_id = request.args.get('story_id', type=int)
    event_type = request.args.get('event_type')
    limit = min(request.args.get('limit', 100, type=int), 500)

    query = AuditLog.query
    if workflow_id:
        query = query.filter_by(workflow_id=workflow_id)
    if story_id:
        query = query.filter_by(story_id=story_id)
    if event_type:
        query = query.filter_by(event_type=event_type)

    logs = query.order_by(AuditLog.created_at.desc()).limit(limit).all()
    return jsonify([l.to_dict() for l in logs]), 200


@admin_bp.route('/guardrail-events', methods=['GET'])
@require_auth
@require_role(['Admin'])
def get_guardrail_events():
    """
    Guardrail violation log — Admin only.
    """
    from app.models.devaa_models import GuardrailEvent

    workflow_id = request.args.get('workflow_id', type=int)
    rail_type = request.args.get('rail_type')
    limit = min(request.args.get('limit', 100, type=int), 500)

    query = GuardrailEvent.query
    if workflow_id:
        query = query.filter_by(workflow_id=workflow_id)
    if rail_type:
        query = query.filter_by(rail_type=rail_type)

    events = query.order_by(GuardrailEvent.triggered_at.desc()).limit(limit).all()
    return jsonify([e.to_dict() for e in events]), 200


@admin_bp.route('/workflows', methods=['GET'])
@require_auth
@require_role(['Admin'])
def list_all_workflows():
    """Full workflow list with step counts — Admin only."""
    from app.models.workflow import Workflow, WorkflowStep
    from sqlalchemy import func

    workflows = Workflow.query.order_by(Workflow.created_at.desc()).limit(100).all()
    result = []
    for w in workflows:
        step_count = WorkflowStep.query.filter_by(workflow_id=w.id).count()
        result.append({
            'id': w.id,
            'story_id': w.story_id,
            'title': w.title,
            'status': w.status,
            'current_agent': w.current_agent,
            'loop_iteration': w.loop_iteration,
            'step_count': step_count,
            'owner_id': w.owner_id,
            'created_at': w.created_at.isoformat() if w.created_at else None
        })
    return jsonify(result), 200


@admin_bp.route('/workflows/<int:workflow_id>/steps', methods=['GET'])
@require_auth
@require_role(['Admin', 'Engineering Lead'])
def get_workflow_steps(workflow_id):
    """Per-workflow step-by-step execution log — Admin / Engineering Lead."""
    from app.models.workflow import WorkflowStep

    steps = WorkflowStep.query.filter_by(workflow_id=workflow_id).order_by(WorkflowStep.created_at.asc()).all()
    return jsonify([{
        'id': s.id,
        'step_type': s.step_type,
        'status': s.status,
        'agent_prompt': s.agent_prompt,
        'agent_response': s.agent_response,
        'token_count': s.token_count,
        'cost_usd': float(s.cost_usd) if s.cost_usd else 0.0,
        'loop_iteration': s.loop_iteration,
        'guardrail_triggered': s.guardrail_triggered,
        'guardrail_reason': s.guardrail_reason,
        'created_at': s.created_at.isoformat() if s.created_at else None
    } for s in steps]), 200


@admin_bp.route('/users', methods=['GET'])
@require_auth
@require_role(['Admin'])
def list_users():
    """List all users with roles — Admin only."""
    from app.models.user import User
    from app.models.role import Role

    users = User.query.all()
    return jsonify([{
        'id': u.id,
        'name': u.name,
        'email': u.email,
        'role': Role.query.get(u.role_id).name if u.role_id else None,
        'is_active': u.is_active,
        'created_at': u.created_at.isoformat() if u.created_at else None
    } for u in users]), 200
