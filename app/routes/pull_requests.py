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

    # Attach conversation summary from cached pr_summary field (live fetch available via /conversation endpoint)
    result['conversation_summary'] = pr.pr_summary or ''

    return jsonify(result), 200


@pull_requests_bp.route('/<int:pr_id>/conversation', methods=['GET'])
@require_auth
@require_role(['Engineering Lead', 'QA Reviewer', 'Admin'])
def get_pr_conversation(pr_id):
    """
    Fetch the live GitHub PR conversation history:
    - Issue comments (general discussion thread)
    - PR reviews (APPROVED, CHANGES_REQUESTED, COMMENTED verdicts)
    - Inline review comments on code
    Returns a structured conversation_summary in Markdown plus raw arrays.
    """
    pr = PullRequest.query.get_or_404(pr_id)
    story = Story.query.get(pr.story_id) if pr.story_id else None

    # Determine repo URL from story's repository_details
    repo_url = None
    if story and story.repository_details:
        details = story.repository_details
        if isinstance(details, list) and details and isinstance(details[0], dict):
            repo_url = details[0].get('url', '')

    if not repo_url or not pr.pr_number:
        return jsonify({
            "conversation_summary": pr.pr_summary or "No GitHub conversation available.",
            "issue_comments": [],
            "reviews": [],
            "review_comments": [],
            "pr_number": pr.pr_number,
            "note": "repo_url or pr_number missing — returning cached summary only"
        }), 200

    try:
        from app.services.github_service import GitHubService
        gh = GitHubService.from_app_config(repo_url=repo_url)
        conversation = gh.get_pr_conversation(repo_url, pr.pr_number)
        return jsonify(conversation), 200
    except Exception as e:
        logger.error(f"Failed to fetch GitHub PR conversation for PR {pr_id}: {e}")
        return jsonify({
            "error": f"Failed to fetch GitHub conversation: {str(e)}",
            "conversation_summary": pr.pr_summary or "",
            "pr_number": pr.pr_number
        }), 500




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
    github_merge_result = None
    if pr.pr_number:
        try:
            from app.services.github_service import GitHubService
            from flask import current_app
            repo_url = pr.pr_url.split('/pull/')[0] if (pr.pr_url and '/pull/' in pr.pr_url) else current_app.config.get('GITHUB_BASE_URL', '')
            gh_service = GitHubService.from_app_config(repo_url)
            story = Story.query.get(pr.story_id) if pr.story_id else None
            story_title = story.title if story else f"PR #{pr.pr_number}"
            jira_key = story.jira_story_key if story else ""
            commit_title = f"feat({jira_key or f'PR-{pr.pr_number}'}): {story_title} (PR #{pr.pr_number})"
            commit_msg = f"Approved by QA Reviewer #{request.current_user.id}.\nDEVAA Automated Implementation."
            github_merge_result = gh_service.merge_pr(
                repo_url=repo_url,
                pr_number=pr.pr_number,
                commit_title=commit_title,
                commit_message=commit_msg,
                merge_method="squash"
            )
            if github_merge_result.get("merged"):
                logger.info(f"[PR] Successfully merged PR #{pr.pr_number} into target base branch on GitHub.")
            else:
                logger.warning(f"[PR] GitHub merge was not completed: {github_merge_result.get('error')}")
        except Exception as gh_err:
            logger.error(f"[PR] Error calling GitHub merge API: {gh_err}")
            github_merge_result = {"merged": False, "error": str(gh_err)}

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

    # Clean up mutable workflow overlay (immutable Base Index remains intact)
    try:
        from app.services.rag_service import RagService
        RagService.get_instance().purge_workflow_overlay(workflow_id=pr.workflow_id, story_id=pr.story_id or 0)
        logger.info(f"[PR] Purged workflow overlay for workflow {pr.workflow_id}")
    except Exception as purge_err:
        logger.warning(f"[PR] Could not purge workflow overlay: {purge_err}")

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


@pull_requests_bp.route('/<int:pr_id>/evidence', methods=['GET'])
@require_auth
@require_role(['Engineering Lead', 'QA Reviewer', 'Admin', 'Product Owner'])
def get_pr_evidence(pr_id):
    """
    Return the full evidence report for a PR as JSON.
    The report includes story context, changed files, validation results, QA reviews,
    and workflow step summaries — all stored in the evidence_report column.

    Query params:
      ?format=json  (default) — returns the report as JSON
      ?format=download — triggers a file download of the JSON report
    """
    pr = PullRequest.query.get_or_404(pr_id)
    story = Story.query.get(pr.story_id) if pr.story_id else None
    fmt = request.args.get('format', 'json').lower()

    # If evidence is already stored, return it directly
    if pr.evidence_report:
        report = pr.evidence_report
    else:
        # Build evidence on-the-fly from related data
        from app.models.workflow import Workflow, WorkflowStep
        workflow = Workflow.query.get(pr.workflow_id)
        steps = WorkflowStep.query.filter_by(workflow_id=pr.workflow_id).all() if workflow else []
        qa_reviews = [r.to_dict() for r in QAReview.query.filter_by(pr_id=pr_id).all()]

        report = {
            "generated_by": "DEVAA Evidence Report",
            "story": story.to_dict() if story else {},
            "pull_request": {
                "id": pr.id,
                "pr_url": pr.pr_url,
                "pr_number": pr.pr_number,
                "branch_name": pr.branch_name,
                "pr_status": pr.pr_status,
                "pr_summary": pr.pr_summary,
                "created_at": pr.created_at.isoformat() if pr.created_at else None,
                "merged_at": pr.merged_at.isoformat() if pr.merged_at else None,
            },
            "changed_files": pr.changed_files or [],
            "qa_reviews": qa_reviews,
            "workflow_steps": [
                {
                    "step_type": s.step_type,
                    "status": s.status,
                    "agent_response": (s.agent_response or "")[:2000],
                    "token_count": s.token_count,
                    "cost_usd": float(s.cost_usd) if s.cost_usd else 0,
                    "loop_iteration": s.loop_iteration,
                    "guardrail_triggered": s.guardrail_triggered,
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                }
                for s in steps
            ],
            "workflow_status": workflow.status if workflow else None,
        }

    if fmt == 'download':
        import json
        from flask import make_response
        filename = f"devaa_evidence_pr{pr_id}_{(story.title or 'report').replace(' ', '_')[:40]}.json"
        response = make_response(json.dumps(report, indent=2, ensure_ascii=False))
        response.headers['Content-Type'] = 'application/json'
        response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response

    return jsonify(report), 200

