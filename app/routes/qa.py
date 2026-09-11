from flask import Blueprint, request, jsonify
from app import db
from app.models.devaa_models import QAReview, PullRequest, AuditLog
from app.models.story import Story
from app.models.workflow import Workflow
from app.models.user import User
from app.utils.auth import require_auth, require_role
import logging

qa_bp = Blueprint('qa', __name__)
logger = logging.getLogger(__name__)


@qa_bp.route('/queue', methods=['GET'])
@require_auth
@require_role(['QA Reviewer', 'Admin'])
def qa_queue():
    """
    QA queue — stories in QA-TESTING status with open PRs.
    QA Reviewer / Admin only.
    """
    stories_in_qa = Story.query.filter_by(status='QA-TESTING').order_by(Story.updated_at.desc()).all()

    result = []
    for story in stories_in_qa:
        # Get the most recent workflow
        workflow = Workflow.query.filter_by(story_id=story.id).order_by(Workflow.created_at.desc()).first()
        pr = PullRequest.query.filter_by(story_id=story.id, pr_status='open').first()

        result.append({
            **story.to_dict(),
            'workflow': {
                'id': workflow.id,
                'status': workflow.status,
                'current_agent': workflow.current_agent,
                'loop_iteration': workflow.loop_iteration,
            } if workflow else None,
            'pr': pr.to_dict() if pr else None,
        })

    return jsonify(result), 200


@qa_bp.route('/approved', methods=['GET'])
@require_auth
@require_role(['QA Reviewer', 'Admin', 'Product Owner', 'Engineering Lead'])
def qa_approved():
    """
    List all stories approved by QA with review details, reviewer, PR, and workflow metadata.
    """
    reviews = QAReview.query.filter_by(decision='approved').order_by(QAReview.created_at.desc()).all()

    result = []
    seen_story_ids = set()

    for review in reviews:
        story = Story.query.get(review.story_id) if review.story_id else None
        if not story:
            continue
        seen_story_ids.add(story.id)

        pr = PullRequest.query.get(review.pr_id) if review.pr_id else None
        if not pr and review.story_id:
            pr = PullRequest.query.filter_by(story_id=review.story_id).order_by(PullRequest.id.desc()).first()

        workflow = Workflow.query.get(review.workflow_id) if review.workflow_id else None
        reviewer = User.query.get(review.reviewer_id) if review.reviewer_id else None

        result.append({
            'review_id': review.id,
            'story_id': story.id,
            'story': story.to_dict(),
            'decision': review.decision,
            'comments': review.comments or '',
            'approved_at': review.created_at.isoformat() if review.created_at else None,
            'reviewer': {
                'id': reviewer.id,
                'name': reviewer.name,
                'email': reviewer.email
            } if reviewer else None,
            'pr': pr.to_dict() if pr else None,
            'workflow': {
                'id': workflow.id,
                'status': workflow.status,
                'current_agent': workflow.current_agent,
                'created_at': workflow.created_at.isoformat() if workflow.created_at else None,
            } if workflow else None,
        })

    # Include any DONE stories not yet in seen_story_ids
    done_stories = Story.query.filter_by(status='DONE').order_by(Story.updated_at.desc()).all()
    for story in done_stories:
        if story.id in seen_story_ids:
            continue

        pr = PullRequest.query.filter_by(story_id=story.id).order_by(PullRequest.id.desc()).first()
        workflow = Workflow.query.filter_by(story_id=story.id).order_by(Workflow.id.desc()).first()

        result.append({
            'review_id': None,
            'story_id': story.id,
            'story': story.to_dict(),
            'decision': 'approved',
            'comments': 'Approved and merged to target branch.',
            'approved_at': (story.updated_at or story.created_at).isoformat() if (story.updated_at or story.created_at) else None,
            'reviewer': {
                'id': None,
                'name': 'QA Reviewer',
                'email': 'qa@devaa.local'
            },
            'pr': pr.to_dict() if pr else None,
            'workflow': {
                'id': workflow.id,
                'status': workflow.status,
                'current_agent': workflow.current_agent,
                'created_at': workflow.created_at.isoformat() if workflow.created_at else None,
            } if workflow else None,
        })

    return jsonify(result), 200



@qa_bp.route('/<int:story_id>/decision', methods=['POST'])
@require_auth
@require_role(['QA Reviewer'])
def submit_qa_decision(story_id):
    """
    Submit a QA approve or reject decision for a story.
    This is the HUMAN-IN-THE-LOOP gate per Section 7 of the DEVAA spec.
    QA Reviewer ONLY — no other role (including Admin) can merge.
    """
    story = Story.query.get_or_404(story_id)
    if story.status != 'QA-TESTING':
        return jsonify({"error": f"Story is not in QA-TESTING state (current: {story.status})"}), 409

    data = request.get_json() or {}
    decision = data.get('decision', '').lower()
    comments = data.get('comments', '').strip()

    if decision not in ('approved', 'rejected'):
        return jsonify({"error": "decision must be 'approved' or 'rejected'"}), 400

    if decision == 'rejected' and not comments:
        return jsonify({"error": "Comments are required for rejection to enable rework."}), 400

    # Find the open PR for this story
    pr = PullRequest.query.filter_by(story_id=story_id, pr_status='open').first()
    if not pr:
        return jsonify({"error": "No open PR found for this story."}), 404

    # Delegate to approve/reject endpoints via internal logic
    if decision == 'approved':
        pr.pr_status = 'merged'
        from datetime import datetime
        pr.merged_by = request.current_user.id
        pr.merged_at = datetime.utcnow()
        story.status = 'DONE'
        workflow = Workflow.query.get(pr.workflow_id)
        if workflow:
            workflow.status = 'Completed'
        new_status = 'DONE'
    else:
        pr.pr_status = 'rejected'
        story.status = 'TO-DO'
        new_status = 'TO-DO'

    db.session.commit()

    # Log QA review
    review = QAReview(
        workflow_id=pr.workflow_id,
        pr_id=pr.id,
        story_id=story_id,
        reviewer_id=request.current_user.id,
        decision=decision,
        comments=comments,
        is_rework=(decision == 'rejected')
    )
    db.session.add(review)
    db.session.commit()

    # Post Jira comment
    try:
        from app.agents.comment_agent import CommentAgent
        comment_type = 'done' if decision == 'approved' else 'rework_triggered'
        CommentAgent(db=db, config={}).run({
            'story': story,
            'comment_type': comment_type,
            'new_status': new_status,
            'workflow_id': pr.workflow_id,
            'extra': {
                'pr_url': pr.pr_url or '#',
                'qa_feedback': comments
            }
        }, workflow_id=pr.workflow_id)
    except Exception as e:
        logger.warning(f"QA decision comment failed: {e}")

    # Audit
    try:
        log = AuditLog(
            workflow_id=pr.workflow_id, story_id=story_id,
            user_id=request.current_user.id,
            event_type=f'qa_{decision}',
            event_data={'decision': decision, 'comments': comments, 'pr_id': pr.id},
            ip_address=request.remote_addr
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        logger.warning(f"Audit log failed: {e}")

    return jsonify({
        "message": f"QA decision '{decision}' recorded.",
        "story_id": story_id,
        "story_status": new_status,
        "pr_status": pr.pr_status,
        "is_rework": decision == 'rejected'
    }), 200


@qa_bp.route('/<int:story_id>/rework', methods=['POST'])
@require_auth
@require_role(['Product Owner', 'Admin'])
def trigger_rework(story_id):
    """
    Trigger a rework run for a QA-rejected story.
    Reads QA comments and re-runs the orchestrator from Step 2.
    """
    story = Story.query.get_or_404(story_id)

    if story.status != 'TO-DO':
        return jsonify({"error": f"Story must be in TO-DO state to trigger rework (current: {story.status})"}), 409

    # Check QA rejections exist
    rejections = QAReview.query.filter_by(story_id=story_id, decision='rejected', is_rework=True).all()
    if not rejections:
        return jsonify({"error": "No QA rejection found for this story."}), 404

    # Create a new workflow for the rework
    workflow = Workflow(
        story_id=story.id,
        title=story.title,          # clean title — no [REWORK] prefix
        workflow_type='rework',     # explicit type flag (replaces [REWORK] hack)
        requirements_doc=story.description,
        owner_id=request.current_user.id,
        status='Planning',
        current_agent='ReworkHandler'
    )
    db.session.add(workflow)
    db.session.commit()

    # Run orchestrator (it will read QA feedback via ReworkHandler automatically)
    try:
        from app.agents.orchestrator import Orchestrator
        orchestrator = Orchestrator()
        result = orchestrator.run(
            workflow_id=workflow.id,
            triggered_by_user_id=request.current_user.id
        )
        return jsonify({
            "message": "Rework run complete.",
            "workflow_id": workflow.id,
            "status": result.get('status'),
            "pr_url": result.get('pr_url'),
            "success": result.get('success', False)
        }), 200 if result.get('success') else 500
    except Exception as e:
        logger.exception(f"Rework orchestrator failed: {e}")
        return jsonify({"error": str(e), "workflow_id": workflow.id}), 500
