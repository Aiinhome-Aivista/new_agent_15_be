from flask import Blueprint, jsonify, current_app
from app.utils.auth import require_auth, require_role
import logging

connectors_bp = Blueprint('connectors', __name__)
logger = logging.getLogger(__name__)

@connectors_bp.route('/status', methods=['GET'])
@require_auth
@require_role(['Product Owner', 'Admin'])
def get_connector_status():
    """
    Returns the status and configuration of external SaaS connectors.
    """
    import os
    provider = (current_app.config.get('ACTIVE_TASK_PROVIDER') or os.getenv('ACTIVE_TASK_PROVIDER') or 'jira').strip().lower()
    
    if provider == 'jira':
        # Mask the email and token for security
        email = current_app.config.get('JIRA_EMAIL', '')
        masked_email = f"{email[:3]}***@{email.split('@')[-1]}" if '@' in email else "***"
        
        return jsonify({
            "active_provider": "jira",
            "connected": True,
            "project": current_app.config.get('JIRA_PROJECT_KEY', ''),
            "details": {
                "base_url": current_app.config.get('JIRA_BASE_URL', ''),
                "account": masked_email,
                "project": current_app.config.get('JIRA_PROJECT_KEY', '')
            }
        }), 200
        
    return jsonify({
        "active_provider": provider,
        "connected": False,
        "details": {}
    }), 200


@connectors_bp.route('/jira/resources', methods=['GET'])
@require_auth
@require_role(['Product Owner', 'Admin'])
def get_jira_resources():
    """
    Fetch available Jira projects and assignable users for dropdowns.
    """
    from flask import request
    from app.services.jira_service import JiraService

    project_key = request.args.get('project_key')
    projects = JiraService.get_projects()
    users = JiraService.get_assignable_users(project_key=project_key)
    sprints = JiraService.get_sprints(project_key=project_key)

    return jsonify({
        "projects": projects,
        "users": users,
        "sprints": sprints,
        "default_project": current_app.config.get('JIRA_PROJECT_KEY', '')
    }), 200

