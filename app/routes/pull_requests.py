from flask import Blueprint, request, jsonify
from app import db
from app.models.devaa_models import PullRequest, QAReview, AuditLog
from app.models.workflow import Workflow
from app.models.story import Story
from app.utils.auth import require_auth, require_role
import logging

pull_requests_bp = Blueprint('pull_requests', __name__)
logger = logging.getLogger(__name__)


@pull_requests_bp.route('/', methods=['GET'])
@require_auth
@require_role(['Engineering Lead', 'QA Reviewer', 'Admin'])
def list_pull_requests():
    """List all open PRs. Engineering Lead / QA / Admin only."""
    prs = PullRequest.query.order_by(PullRequest.created_at.desc()).all()
    return jsonify([pr.to_dict() for pr in prs]), 200


@pull_requests_bp.route('/<int:pr_id>', methods=['GET'])
@require_auth
@require_role(['Engineering Lead', 'QA Reviewer', 'Admin'])
def get_pull_request(pr_id):
    """Get PR detail including workflow steps and validation results."""
    pr = PullRequest.query.get_or_404(pr_id)
    workflow = Workflow.query.get(pr.workflow_id)
    story = Story.query.get(pr.story_id) if pr.story_id else None

    # Attach workflow steps for the timeline
    from app.models.workflow import WorkflowStep
    steps = WorkflowStep.query.filter_by(workflow_id=pr.workflow_id).order_by(WorkflowStep.created_at.asc()).all() if workflow else []

    result = pr.to_dict()
    result['story'] = story.to_dict() if story else None
    result['workflow_status'] = workflow.status if workflow else None
    result['steps'] = [{
        'id': s.id,
        'type': s.step_type,
        'status': s.status,
        'response': s.agent_response,
        'loop_iteration': s.loop_iteration,
        'guardrail_triggered': s.guardrail_triggered,
        'token_count': s.token_count,
        'created_at': s.created_at.isoformat() if s.created_at else None
    } for s in steps]

    # Fetch previous QA reviews for this PR
    result['qa_reviews'] = [r.to_dict() for r in QAReview.query.filter_by(pr_id=pr_id).all()]

    return jsonify(result), 200


@pull_requests_bp.route('/<int:pr_id>/approve', methods=['POST'])
@require_auth
@require_role(['QA Reviewer'])
def approve_pr(pr_id):
    """
    QA Reviewer approves a PR.
    Human-in-the-loop gate — ONLY QA Reviewer can call this.
    Updates PR status to 'merged' and story to 'DONE'.
    """
    pr = PullRequest.query.get_or_404(pr_id)

    if pr.pr_status != 'open':
        return jsonify({"error": f"PR is already '{pr.pr_status}'"}), 409

    data = request.get_json() or {}
    comments = data.get('comments', '')

    # Update PR status
    pr.pr_status = 'merged'
    pr.merged_by = request.current_user.id
    from datetime import datetime
    pr.merged_at = datetime.utcnow()
    db.session.commit()

    # Log QA review
    review = QAReview(
        workflow_id=pr.workflow_id,
        pr_id=pr.id,
        story_id=pr.story_id,
        reviewer_id=request.current_user.id,
        decision='approved',
        comments=comments,
        is_rework=False
    )
    db.session.add(review)

    # Update story status to DONE
    if pr.story_id:
        story = Story.query.get(pr.story_id)
        if story:
            story.status = 'DONE'
            # Post Jira "done" comment
            try:
                from app.agents.comment_agent import CommentAgent
                CommentAgent(db=db, config={}).run({
                    'story': story,
                    'comment_type': 'done',
                    'new_status': 'DONE',
                    'workflow_id': pr.workflow_id,
                    'extra': {'pr_url': pr.pr_url or '#'}
                }, workflow_id=pr.workflow_id)
            except Exception as e:
                logger.warning(f"Post-approve comment failed: {e}")

    # Update workflow status
    workflow = Workflow.query.get(pr.workflow_id)
    if workflow:
        workflow.status = 'Completed'
        db.session.commit()

    # Audit log
    _audit(pr.workflow_id, pr.story_id, request.current_user.id, 'pr_approved',
           {'pr_id': pr_id, 'pr_url': pr.pr_url})

    return jsonify({
        "message": "PR approved and merged. Story marked DONE.",
        "pr_id": pr_id,
        "pr_status": "merged"
    }), 200


@pull_requests_bp.route('/<int:pr_id>/reject', methods=['POST'])
@require_auth
@require_role(['QA Reviewer'])
def reject_pr(pr_id):
    """
    QA Reviewer rejects a PR with mandatory comments.
    Triggers a rework cycle — story goes back to TO-DO.
    """
    pr = PullRequest.query.get_or_404(pr_id)

    if pr.pr_status != 'open':
        return jsonify({"error": f"PR is already '{pr.pr_status}'"}), 409

    data = request.get_json() or {}
    comments = data.get('comments', '').strip()
    if not comments:
        return jsonify({"error": "Rejection comments are required to trigger rework."}), 400

    # Mark PR as rejected
    pr.pr_status = 'rejected'
    db.session.commit()

    # Log QA review with is_rework=True
    review = QAReview(
        workflow_id=pr.workflow_id,
        pr_id=pr.id,
        story_id=pr.story_id,
        reviewer_id=request.current_user.id,
        decision='rejected',
        comments=comments,
        is_rework=True
    )
    db.session.add(review)

    # Move story back to TO-DO
    story = None
    if pr.story_id:
        story = Story.query.get(pr.story_id)
        if story:
            story.status = 'TO-DO'

    db.session.commit()

    # Post Jira rework comment
    if story:
        try:
            from app.agents.comment_agent import CommentAgent
            CommentAgent(db=db, config={}).run({
                'story': story,
                'comment_type': 'rework_triggered',
                'new_status': 'TO-DO',
                'workflow_id': pr.workflow_id,
                'extra': {'qa_feedback': comments}
            }, workflow_id=pr.workflow_id)
        except Exception as e:
            logger.warning(f"Post-reject comment failed: {e}")

    # Audit log
    _audit(pr.workflow_id, pr.story_id, request.current_user.id, 'pr_rejected',
           {'pr_id': pr_id, 'comments': comments})

    return jsonify({
        "message": "PR rejected. Story moved back to TO-DO. Rework cycle ready.",
        "pr_id": pr_id,
        "pr_status": "rejected",
        "story_status": "TO-DO"
    }), 200


def _audit(workflow_id, story_id, user_id, event_type, event_data):
    try:
        log = AuditLog(workflow_id=workflow_id, story_id=story_id,
                       user_id=user_id, event_type=event_type,
                       event_data=event_data,
                       ip_address=request.remote_addr)
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        logger.warning(f"Audit log failed: {e}")
