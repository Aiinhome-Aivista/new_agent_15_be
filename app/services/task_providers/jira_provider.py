import requests
from requests.auth import HTTPBasicAuth
from flask import current_app
import logging
from typing import List, Optional
from app.services.task_providers.base_provider import BaseTaskProvider, TaskModel

logger = logging.getLogger(__name__)

class JiraTaskProvider(BaseTaskProvider):
    
    def _auth(self):
        return HTTPBasicAuth(
            current_app.config['JIRA_EMAIL'],
            current_app.config['JIRA_API_TOKEN']
        )

    def _base(self):
        return current_app.config['JIRA_BASE_URL']

    def _is_configured(self):
        return bool(
            current_app.config.get('JIRA_BASE_URL') and
            current_app.config.get('JIRA_API_TOKEN') and
            current_app.config.get('JIRA_EMAIL')
        )

    def fetch_tasks(self, assignee_email: str, min_priority: str = "High", status: str = "To Do") -> List[TaskModel]:
        if not self._is_configured():
            logger.warning("Jira not configured. Skipping fetch_tasks.")
            return []

        # JQL to fetch tickets assigned to the user, with min priority, in specific status
        jql = f'assignee = "{assignee_email}" AND priority >= "{min_priority}" AND status = "{status}" ORDER BY created DESC'
        url = f"{self._base()}/rest/api/3/search"
        
        try:
            resp = requests.get(url, auth=self._auth(),
                                params={"jql": jql, "maxResults": 50},
                                headers={"Accept": "application/json"})
            
            if resp.status_code == 200:
                issues = resp.json().get('issues', [])
                tasks = []
                for issue in issues:
                    fields = issue.get('fields', {})
                    # Parsing Atlassian Document Format (ADF) description to plain text roughly
                    # A robust implementation would parse ADF, but here we just take text if available
                    desc_adf = fields.get('description')
                    description_text = ""
                    if desc_adf and isinstance(desc_adf, dict):
                        for content_block in desc_adf.get('content', []):
                            for text_block in content_block.get('content', []):
                                if 'text' in text_block:
                                    description_text += text_block['text'] + "\n"
                    
                    tasks.append(TaskModel(
                        external_id=issue.get('key'),
                        title=fields.get('summary', ''),
                        description=description_text,
                        acceptance_criteria="", # Jira might use custom fields for this
                        assignee_email=assignee_email,
                        priority=fields.get('priority', {}).get('name'),
                        status=fields.get('status', {}).get('name', status)
                    ))
                return tasks
            else:
                logger.error(f"Jira fetch_tasks failed: {resp.status_code} {resp.text}")
                return []
        except Exception as e:
            logger.exception(f"Exception during Jira fetch_tasks: {e}")
            return []

    def add_comment(self, task_id: str, comment_body: str) -> Optional[str]:
        if not self._is_configured():
            logger.warning("Jira not configured. Comment skipped.")
            return None
        
        url = f"{self._base()}/rest/api/3/issue/{task_id}/comment"
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
        try:
            resp = requests.post(url, json=payload, auth=self._auth(),
                                 headers={"Content-Type": "application/json"})
            if resp.status_code == 201:
                return resp.json().get('id')
            logger.error(f"Jira add_comment failed: {resp.status_code} {resp.text}")
            return None
        except Exception as e:
            logger.exception(f"Exception during Jira add_comment: {e}")
            return None

    def update_status(self, task_id: str, target_status: str) -> bool:
        if not self._is_configured():
            logger.warning("Jira not configured. Status transition skipped.")
            return False

        # Get available transitions
        url = f"{self._base()}/rest/api/3/issue/{task_id}/transitions"
        try:
            resp = requests.get(url, auth=self._auth(), headers={"Accept": "application/json"})
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
                                  auth=self._auth(),
                                  headers={"Content-Type": "application/json"})
            return resp2.status_code == 204
        except Exception as e:
            logger.exception(f"Exception during Jira update_status: {e}")
            return False
