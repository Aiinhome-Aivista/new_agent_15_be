from flask import Blueprint, request, jsonify
from app import db
from app.models.story import Story
from app.models.workflow import Workflow
from app.models.user import User
from app.utils.auth import require_auth, require_role
import logging

stories_bp = Blueprint('stories', __name__)
logger = logging.getLogger(__name__)


@stories_bp.route('/', methods=['GET'])
@require_auth
def list_stories():
    """
    List stories — role-filtered:
    - Product Owner: only their own stories
    - Engineering Lead / QA / Admin: all stories
    """
    user = request.current_user
    from app.models.role import Role
    role = Role.query.get(user.role_id)
    role_name = role.name if role else ''

    if role_name == 'Product Owner':
        stories = Story.query.filter_by(owner_id=user.id).order_by(Story.created_at.desc()).all()
    else:
        stories = Story.query.order_by(Story.created_at.desc()).all()

    return jsonify([s.to_dict() for s in stories]), 200


@stories_bp.route('/<int:story_id>', methods=['GET'])
@require_auth
def get_story(story_id):
    story = Story.query.get_or_404(story_id)

    # Fetch associated workflows
    workflows = Workflow.query.filter_by(story_id=story_id).order_by(Workflow.created_at.desc()).all()

    result = story.to_dict()
    result['workflows'] = [{
        'id': w.id,
        'status': w.status,
        'current_agent': w.current_agent,
        'loop_iteration': w.loop_iteration,
        'created_at': w.created_at.isoformat() if w.created_at else None
    } for w in workflows]

    return jsonify(result), 200


@stories_bp.route('/', methods=['POST'])
@require_auth
@require_role(['Product Owner', 'Admin'])
def create_story():
    """Create a new story. Product Owner / Admin only."""
    data = request.get_json()

    title = data.get('title', '').strip()
    if not title:
        return jsonify({"error": "title is required"}), 400

    story = Story(
        title=title,
        jira_story_key=data.get('jira_story_key'),
        description=data.get('description'),
        acceptance_criteria=data.get('acceptance_criteria'),
        repository_details=data.get('repository_details'),   # expects JSON array
        source_branch=data.get('source_branch'),
        assignee_id=data.get('assignee_id'),
        owner_id=request.current_user.id,
        status='TO-DO'
    )
    db.session.add(story)
    db.session.commit()

    logger.info(f"Story created: {story.id} by user {request.current_user.id}")
    return jsonify(story.to_dict()), 201


@stories_bp.route('/<int:story_id>', methods=['PUT'])
@require_auth
@require_role(['Product Owner', 'Admin'])
def update_story(story_id):
    """Update a story. Only owner or Admin."""
    story = Story.query.get_or_404(story_id)
    user = request.current_user
    from app.models.role import Role
    role = Role.query.get(user.role_id)

    if story.owner_id != user.id and (role and role.name != 'Admin'):
        return jsonify({"error": "You can only edit your own stories"}), 403

    data = request.get_json()
    updatable = ['title', 'description', 'acceptance_criteria', 'repository_details',
                 'source_branch', 'assignee_id', 'jira_story_key']
    for field in updatable:
        if field in data:
            setattr(story, field, data[field])

    db.session.commit()
    return jsonify(story.to_dict()), 200


@stories_bp.route('/<int:story_id>/run', methods=['POST'])
@require_auth
@require_role(['Product Owner', 'Admin'])
def trigger_run(story_id):
    """
    Trigger the full DEVAA multi-agent orchestrator for a story.
    Creates a linked Workflow, then runs all agents.
    Product Owner / Admin only.
    """
    story = Story.query.get_or_404(story_id)

    if story.status in ('IN-PROGRESS', 'QA-TESTING'):
        return jsonify({"error": f"Story is already in '{story.status}' state. Cannot trigger a new run."}), 409

    # Create linked workflow
    workflow = Workflow(
        story_id=story.id,
        title=story.title,
        requirements_doc=story.description,
        owner_id=request.current_user.id,
        status='Planning',
        current_agent='Intake'
    )
    db.session.add(workflow)
    db.session.commit()

    # Run orchestrator
    try:
        from app.agents.orchestrator import Orchestrator
        orchestrator = Orchestrator()
        result = orchestrator.run(
            workflow_id=workflow.id,
            triggered_by_user_id=request.current_user.id
        )

        return jsonify({
            "message": "Orchestrator run complete.",
            "workflow_id": workflow.id,
            "story_id": story_id,
            "status": result.get('status', 'Unknown'),
            "pr_url": result.get('pr_url'),
            "loop_iterations": result.get('loop_iterations'),
            "success": result.get('success', False),
            "error": result.get('error')
        }), 200 if result.get('success') else 500

    except Exception as e:
        logger.exception(f"Orchestrator failed for story {story_id}: {e}")
        workflow.status = 'Failed'
        db.session.commit()
        return jsonify({"error": f"Orchestrator error: {str(e)}", "workflow_id": workflow.id}), 500


@stories_bp.route('/<int:story_id>/status', methods=['GET'])
@require_auth
def get_story_status(story_id):
    """Get the current status and latest workflow for a story."""
    story = Story.query.get_or_404(story_id)
    latest_workflow = Workflow.query.filter_by(story_id=story_id).order_by(Workflow.created_at.desc()).first()

    return jsonify({
        "story_id": story_id,
        "story_status": story.status,
        "workflow": {
            "id": latest_workflow.id,
            "status": latest_workflow.status,
            "current_agent": latest_workflow.current_agent,
            "loop_iteration": latest_workflow.loop_iteration,
        } if latest_workflow else None
    }), 200
