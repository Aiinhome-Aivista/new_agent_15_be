"""
JiraService — wraps Atlassian REST API v3 calls.
Falls back gracefully if JIRA_API_TOKEN is not configured.
"""
import requests
from requests.auth import HTTPBasicAuth
from flask import current_app
import logging

logger = logging.getLogger(__name__)


class JiraService:

    @staticmethod
    def _auth():
        return HTTPBasicAuth(
            current_app.config['JIRA_EMAIL'],
            current_app.config['JIRA_API_TOKEN']
        )

    @staticmethod
    def _base():
        return current_app.config['JIRA_BASE_URL']

    @staticmethod
    def _is_configured():
        return bool(
            current_app.config.get('JIRA_BASE_URL') and
            current_app.config.get('JIRA_API_TOKEN') and
            current_app.config.get('JIRA_EMAIL')
        )

    @classmethod
    def get_issue(cls, jira_key: str) -> dict | None:
        """Fetch a Jira issue by key. Returns None if not configured or not found."""
        if not cls._is_configured():
            logger.warning("Jira not configured. Skipping get_issue.")
            return None
        url = f"{cls._base()}/rest/api/3/issue/{jira_key}"
        resp = requests.get(url, auth=cls._auth(), headers={"Accept": "application/json"})
        if resp.status_code == 200:
            return resp.json()
        logger.error(f"Jira get_issue failed: {resp.status_code} {resp.text}")
        return None

    @classmethod
    def add_comment(cls, jira_key: str, comment_body: str) -> str | None:
        """Post a comment to a Jira issue. Returns comment ID or None."""
        if not cls._is_configured():
            logger.warning("Jira not configured. Comment logged to DB only.")
            return None
        url = f"{cls._base()}/rest/api/3/issue/{jira_key}/comment"
        payload = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [{
                    "type": "paragraph",
                    "content": [{"type": "text", "text": comment_body}]
                }]
            }
        }
        resp = requests.post(url, json=payload, auth=cls._auth(),
                             headers={"Content-Type": "application/json"})
        if resp.status_code == 201:
            return resp.json().get('id')
        logger.error(f"Jira add_comment failed: {resp.status_code} {resp.text}")
        return None

    @classmethod
    def transition_issue(cls, jira_key: str, target_status: str) -> bool:
        """Transition a Jira issue to a given status name. Returns True on success."""
        if not cls._is_configured():
            logger.warning("Jira not configured. Status transition skipped.")
            return False

        # Get available transitions
        url = f"{cls._base()}/rest/api/3/issue/{jira_key}/transitions"
        resp = requests.get(url, auth=cls._auth(), headers={"Accept": "application/json"})
        if resp.status_code != 200:
            return False

        transitions = resp.json().get('transitions', [])
        transition_id = None
        for t in transitions:
            if t['name'].lower() == target_status.lower():
                transition_id = t['id']
                break

        if not transition_id:
            logger.error(f"No Jira transition found for status '{target_status}'")
            return False

        # Execute transition
        resp2 = requests.post(url, json={"transition": {"id": transition_id}},
                              auth=cls._auth(),
                              headers={"Content-Type": "application/json"})
        return resp2.status_code == 204

    @classmethod
    def search_stories(cls, project_key: str, status: str = "To Do") -> list:
        """Search for stories in a project with a given status."""
        if not cls._is_configured():
            return []
        jql = f'project = "{project_key}" AND status = "{status}" ORDER BY created DESC'
        url = f"{cls._base()}/rest/api/3/search"
        resp = requests.get(url, auth=cls._auth(),
                            params={"jql": jql, "maxResults": 50},
                            headers={"Accept": "application/json"})
        if resp.status_code == 200:
            return resp.json().get('issues', [])
        logger.error(f"Jira search failed: {resp.status_code} {resp.text}")
        return []
