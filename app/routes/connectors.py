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
    
    response_data = {
        "active_provider": provider,
        "connected": False,
        "project": current_app.config.get('JIRA_PROJECT_KEY', ''),
        "details": {}
    }

    if provider == 'jira':
        # Mask the email and token for security
        email = current_app.config.get('JIRA_EMAIL', '')
        masked_email = f"{email[:3]}***@{email.split('@')[-1]}" if '@' in email else "***"
        response_data["connected"] = True
        response_data["details"] = {
            "base_url": current_app.config.get('JIRA_BASE_URL', ''),
            "account": masked_email,
            "project": current_app.config.get('JIRA_PROJECT_KEY', '')
        }

    # ── GitHub Connector Details ──
    github_token = (current_app.config.get('GITHUB_TOKEN') or os.getenv('GITHUB_TOKEN') or '').strip()
    github_base_url = (current_app.config.get('GITHUB_BASE_URL') or os.getenv('GITHUB_BASE_URL') or '').strip()
    github_org = (current_app.config.get('GITHUB_ORG') or os.getenv('GITHUB_ORG') or '').strip()
    github_default_branch = (current_app.config.get('GITHUB_DEFAULT_BASE_BRANCH') or os.getenv('GITHUB_DEFAULT_BASE_BRANCH') or 'main').strip()

    masked_token = None
    if github_token:
        if len(github_token) > 12:
            masked_token = f"{github_token[:8]}...{github_token[-4:]}"
        else:
            masked_token = "********"

    # Extract repository name from github_base_url if present
    repo_name = None
    if github_base_url and 'github.com' in github_base_url:
        parts = github_base_url.split('github.com/')[-1].replace('.git', '').strip('/').split('/')
        if len(parts) >= 2:
            if not github_org or github_org == 'your-org-or-username':
                github_org = parts[0]
            repo_name = parts[1]

    response_data["github"] = {
        "connected": bool(github_token),
        "org": github_org or "Not configured",
        "repo_name": repo_name or "Not configured",
        "base_url": github_base_url or "https://api.github.com",
        "default_branch": github_default_branch,
        "token_configured": bool(github_token),
        "masked_token": masked_token or "Not Set",
        "full_repo_path": f"{github_org}/{repo_name}" if (github_org and repo_name) else (github_org or repo_name or "N/A"),
        "provider": "github"
    }

    return jsonify(response_data), 200


@connectors_bp.route('/github/test', methods=['POST'])
@require_auth
@require_role(['Product Owner', 'Admin'])
def test_github_connection():
    """
    Test GitHub token and repository reachability.
    """
    import os, requests
    github_token = (current_app.config.get('GITHUB_TOKEN') or os.getenv('GITHUB_TOKEN') or '').strip()
    github_base_url = (current_app.config.get('GITHUB_BASE_URL') or os.getenv('GITHUB_BASE_URL') or '').strip()
    github_org = (current_app.config.get('GITHUB_ORG') or os.getenv('GITHUB_ORG') or '').strip()
    github_default_branch = (current_app.config.get('GITHUB_DEFAULT_BASE_BRANCH') or os.getenv('GITHUB_DEFAULT_BASE_BRANCH') or 'main').strip()

    if not github_token:
        return jsonify({
            "success": False,
            "connected": False,
            "error": "GITHUB_TOKEN is not configured in backend/.env"
        }), 400

    repo_name = None
    if github_base_url and 'github.com' in github_base_url:
        parts = github_base_url.split('github.com/')[-1].replace('.git', '').strip('/').split('/')
        if len(parts) >= 2:
            if not github_org or github_org == 'your-org-or-username':
                github_org = parts[0]
            repo_name = parts[1]

    user_info = None
    repo_info = None
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "DEVAA-Platform"
    }

    try:
        r_user = requests.get("https://api.github.com/user", headers=headers, timeout=5)
        if r_user.status_code == 200:
            u = r_user.json()
            user_info = {
                "login": u.get("login"),
                "name": u.get("name"),
                "avatar_url": u.get("avatar_url"),
                "html_url": u.get("html_url"),
                "public_repos": u.get("public_repos")
            }
        
        if github_org and repo_name:
            r_repo = requests.get(f"https://api.github.com/repos/{github_org}/{repo_name}", headers=headers, timeout=5)
            if r_repo.status_code == 200:
                rp = r_repo.json()
                repo_info = {
                    "full_name": rp.get("full_name"),
                    "default_branch": rp.get("default_branch"),
                    "private": rp.get("private"),
                    "html_url": rp.get("html_url")
                }
    except Exception as e:
        logger.warning(f"GitHub API reachability check warning: {e}")

    return jsonify({
        "success": True,
        "connected": True,
        "user": user_info,
        "repo": repo_info,
        "org": github_org,
        "repo_name": repo_name,
        "default_branch": github_default_branch,
        "repo_url": github_base_url
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

