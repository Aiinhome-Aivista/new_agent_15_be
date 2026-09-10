import os
import time
from flask import Blueprint, request, jsonify, current_app
from werkzeug.utils import secure_filename
from app import db
from app.models.story import Story
from app.models.workflow import Workflow
from app.models.user import User
from app.utils.auth import require_auth, require_role
from app.services.sync_service import SyncService
from app.services.jira_service import JiraService
from app.services.task_providers.factory import TaskProviderFactory
import logging

stories_bp = Blueprint('stories', __name__)
logger = logging.getLogger(__name__)

@stories_bp.route('/sync', methods=['POST'])
@require_auth
@require_role(['Product Owner', 'Admin'])
def sync_stories():
    """
    Manually trigger a sync of tasks from the configured external provider (e.g. Jira).
    """
    result = SyncService.sync_assigned_tasks(user_id=request.current_user.id)
    if "error" in result:
        return jsonify(result), 500
    return jsonify(result), 200

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
    """Create a new story with attachments and Jira sync. Product Owner / Admin only."""
    files = []
    if request.is_json:
        data = request.get_json() or {}
    else:
        # multipart/form-data or form-urlencoded
        data = request.form.to_dict()
        if 'sync_to_jira' in data:
            data['sync_to_jira'] = str(data['sync_to_jira']).lower() in ('true', '1', 'yes')
        files = request.files.getlist('attachments')
        if not files:
            files = request.files.getlist('files')

    title = data.get('title', '').strip()
    if not title:
        return jsonify({"error": "title is required"}), 400

    description = data.get('description', '')
    acceptance_criteria = data.get('acceptance_criteria', '')
    priority = data.get('priority', 'Medium')
    assignee = (data.get('assignee') or '').strip()
    assignee_account_id = (data.get('assignee_account_id') or '').strip() or None
    issue_type = data.get('issue_type', 'Story')
    status = data.get('status', 'TO-DO')
    start_date = data.get('start_date')
    due_date = data.get('due_date')
    story_points = data.get('story_points')
    labels = data.get('labels')
    sprint_id = data.get('sprint_id')
    sync_to_jira = data.get('sync_to_jira', True)
    project_key = data.get('project_key')

    jira_key = data.get('jira_story_key')
    external_task_id = None
    external_provider = 'manual'
    jira_warning = None

    # If requested to sync directly with Jira
    if sync_to_jira or (data.get('create_in_jira') is True):
        provider = TaskProviderFactory.get_provider()
        if provider:
            # Map DEVAA status to Jira status
            target_jira_status = "To Do"
            norm_status = (status or '').lower().replace('_', '-')
            if norm_status in ['in-progress', 'in progress']:
                target_jira_status = current_app.config.get('JIRA_STATUS_IN_PROGRESS', 'In Progress')
            elif norm_status in ['qa-testing', 'qa testing']:
                target_jira_status = current_app.config.get('JIRA_STATUS_QA_TESTING', 'QA Testing')
            elif norm_status == 'done':
                target_jira_status = current_app.config.get('JIRA_STATUS_DONE', 'Done')

            jira_res = provider.create_task(
                title=title,
                description=description,
                acceptance_criteria=acceptance_criteria,
                priority=priority,
                assignee_email=assignee if assignee else None,
                assignee_account_id=assignee_account_id,
                issue_type=issue_type,
                project_key=project_key,
                target_status=target_jira_status,
                start_date=start_date,
                sprint_id=sprint_id,
                due_date=due_date,
                story_points=story_points,
                labels=labels
            )

            if jira_res and isinstance(jira_res, dict) and jira_res.get('external_id'):
                jira_key = jira_res['external_id']
                external_task_id = jira_key
                external_provider = (current_app.config.get('ACTIVE_TASK_PROVIDER') or 'jira').lower()
                logger.info(f"Created Jira issue {jira_key} for story '{title}'")
            else:
                err_msg = jira_res.get('error') if isinstance(jira_res, dict) else 'Could not create task in external provider'
                logger.warning(f"Failed to create story in Jira: {err_msg}")
                jira_warning = f"Story saved in DEVAA, but Jira issue creation failed: {err_msg}"

    # Handle Attachments upload to Jira and local tracking
    uploaded_attachments = []
    if files:
        upload_dir = os.path.join(current_app.instance_path, 'uploads', 'stories')
        try:
            os.makedirs(upload_dir, exist_ok=True)
        except Exception:
            pass

        for f in files:
            if not f or not f.filename:
                continue
            fname = secure_filename(f.filename)
            try:
                content = f.read()
                f_size = len(content)
                # Save locally if needed
                local_fpath = os.path.join(upload_dir, f"{int(time.time())}_{fname}")
                try:
                    with open(local_fpath, "wb") as out_f:
                        out_f.write(content)
                except Exception as save_err:
                    logger.warning(f"Could not write local attachment cache: {save_err}")

                jira_synced = False
                if jira_key:
                    try:
                        att_res = JiraService.add_attachment(
                            issue_key=jira_key,
                            filename=fname,
                            file_data=content,
                            mime_type=f.mimetype
                        )
                        if att_res and not att_res.get('error'):
                            jira_synced = True
                            logger.info(f"Attached '{fname}' to Jira issue {jira_key}")
                        else:
                            logger.warning(f"Jira attachment failed for {fname}: {att_res}")
                    except Exception as jira_att_err:
                        logger.warning(f"Exception attaching '{fname}' to Jira: {jira_att_err}")

                uploaded_attachments.append({
                    "filename": fname,
                    "size": f_size,
                    "mimetype": f.mimetype,
                    "jira_synced": jira_synced
                })
            except Exception as read_err:
                logger.warning(f"Failed processing file {fname}: {read_err}")

    repository_details = data.get('repository_details')
    if not repository_details:
        repository_details = [{
            "name": "",
            "url": "",
            "branch": data.get('source_branch') or 'main',
            "external_assignee": assignee,
            "priority": priority,
            "start_date": start_date,
            "due_date": due_date,
            "story_points": story_points,
            "labels": labels,
            "attachments": uploaded_attachments,
            "created_in_devaa": True,
            "origin": "devaa"
        }]
    elif isinstance(repository_details, list) and len(repository_details) > 0 and isinstance(repository_details[0], dict):
        repository_details[0]["created_in_devaa"] = True
        repository_details[0]["origin"] = "devaa"


    story = Story(
        title=title,
        jira_story_key=jira_key,
        external_task_id=external_task_id or jira_key,
        external_provider=external_provider,
        description=description,
        acceptance_criteria=acceptance_criteria,
        repository_details=repository_details,
        source_branch=data.get('source_branch'),
        assignee_id=data.get('assignee_id'),
        owner_id=request.current_user.id,
        status=status.upper() if status else 'TO-DO'
    )
    db.session.add(story)
    db.session.commit()

    logger.info(f"Story created: {story.id} (Jira key: {story.jira_story_key}) by user {request.current_user.id}")
    resp_dict = story.to_dict()
    resp_dict['attachments'] = uploaded_attachments
    if jira_warning:
        resp_dict['warning'] = jira_warning
    return jsonify(resp_dict), 201


@stories_bp.route('/<int:story_id>/attachments', methods=['POST'])
@require_auth
@require_role(['Product Owner', 'Admin'])
def upload_story_attachments(story_id):
    """Upload attachments to an existing story and its linked Jira issue."""
    story = Story.query.get_or_404(story_id)
    files = request.files.getlist('attachments')
    if not files:
        files = request.files.getlist('files')
    if not files:
        return jsonify({"error": "No files provided in 'attachments' or 'files'"}), 400

    uploaded = []
    for f in files:
        if not f or not f.filename:
            continue
        fname = secure_filename(f.filename)
        content = f.read()
        jira_synced = False
        if story.jira_story_key:
            res = JiraService.add_attachment(story.jira_story_key, fname, content, f.mimetype)
            if res and not res.get('error'):
                jira_synced = True

        uploaded.append({
            "filename": fname,
            "size": len(content),
            "mimetype": f.mimetype,
            "jira_synced": jira_synced
        })

    # Update story repository_details
    details = list(story.repository_details or [])
    if not details:
        details = [{}]
    curr_atts = details[0].get('attachments', [])
    curr_atts.extend(uploaded)
    details[0]['attachments'] = curr_atts
    story.repository_details = details
    db.session.commit()

    return jsonify({
        "message": f"Successfully uploaded {len(uploaded)} attachment(s)",
        "attachments": uploaded
    }), 200




@stories_bp.route('/<int:story_id>', methods=['PUT'])
@require_auth
@require_role(['Product Owner', 'Admin'])
def update_story(story_id):
    """Update a story. Editable only when in TO-DO status. Only owner or Admin."""
    story = Story.query.get_or_404(story_id)
    user = request.current_user
    from app.models.role import Role
    role = Role.query.get(user.role_id)

    if story.owner_id != user.id and (role and role.name != 'Admin'):
        return jsonify({"error": "You can only edit your own stories"}), 403

    # Enforce TO-DO status rule
    norm_status = (story.status or '').upper().replace('-', '').replace('_', '').replace(' ', '')
    if norm_status not in ['TODO', 'OPEN']:
        return jsonify({"error": f"Story cannot be edited because it is in '{story.status}' status. Only TO-DO stories can be edited."}), 400


    data = request.get_json() or {}

    title = data.get('title')
    description = data.get('description')
    acceptance_criteria = data.get('acceptance_criteria')
    priority = data.get('priority')
    assignee = data.get('assignee')
    assignee_account_id = data.get('assignee_account_id')
    story_points = data.get('story_points')
    due_date = data.get('due_date')
    labels = data.get('labels')
    source_branch = data.get('source_branch')

    if title is not None:
        if not title.strip():
            return jsonify({"error": "Title cannot be empty"}), 400
        story.title = title.strip()

    if description is not None:
        story.description = description
    if acceptance_criteria is not None:
        story.acceptance_criteria = acceptance_criteria
    if source_branch is not None:
        story.source_branch = source_branch

    # Update repository_details metadata
    details = list(story.repository_details or [{}])
    if not details:
        details = [{}]
    first_det = dict(details[0])

    if priority is not None:
        first_det['priority'] = priority
    if assignee is not None:
        first_det['external_assignee'] = assignee
    if story_points is not None:
        first_det['story_points'] = story_points
    if due_date is not None:
        first_det['due_date'] = due_date
    if labels is not None:
        first_det['labels'] = labels

    details[0] = first_det
    story.repository_details = details

    # Sync updates directly to Jira Cloud if linked
    jira_update_res = None
    if story.jira_story_key:
        try:
            jira_update_res = JiraService.update_issue(
                issue_key=story.jira_story_key,
                title=title,
                description=description,
                acceptance_criteria=acceptance_criteria,
                priority=priority,
                assignee=assignee,
                assignee_account_id=assignee_account_id,
                due_date=due_date,
                story_points=story_points,
                labels=labels
            )
            logger.info(f"Synced story {story.id} updates to Jira {story.jira_story_key}: {jira_update_res}")
        except Exception as ex:
            logger.warning(f"Failed to sync story updates to Jira: {ex}")

    db.session.commit()
    res_dict = story.to_dict()
    if jira_update_res and jira_update_res.get('error'):
        res_dict['warning'] = f"Updated locally, but Jira update had warning: {jira_update_res['error']}"
    return jsonify(res_dict), 200



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
        workflow_type='standard',   # explicit — not a rework
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
