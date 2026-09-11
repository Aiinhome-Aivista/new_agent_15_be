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

    def _extract_adf_text(self, adf_node) -> str:
        if not adf_node:
            return ""
        if isinstance(adf_node, str):
            return adf_node
        if isinstance(adf_node, dict):
            text = adf_node.get('text', '')
            content = adf_node.get('content', [])
            inner_text = " ".join(self._extract_adf_text(child) for child in content if child).strip()
            return f"{text} {inner_text}".strip()
        if isinstance(adf_node, list):
            return " ".join(self._extract_adf_text(item) for item in adf_node if item).strip()
        return ""

    def _build_jql(self, min_priority: str = "High", status: str = "To Do") -> str:
        conditions = []
        
        # Priority mapping: Jira does NOT support >= operator on priority
        priority_levels = ["Lowest", "Low", "Medium", "High", "Highest"]
        min_p = (min_priority or "").capitalize()
        if min_p in priority_levels:
            idx = priority_levels.index(min_p)
            allowed = priority_levels[idx:]
            if "Highest" in allowed:
                allowed.extend(["Critical", "Blocker"])
            p_list = ", ".join(f'"{p}"' for p in allowed)
            conditions.append(f'priority in ({p_list})')
        elif min_p and min_p.lower() != "all":
            conditions.append(f'priority = "{min_priority}"')

        # Status condition
        if status:
            conditions.append(f'status in ("{status}", "To Do", "TODO", "Open")')

        jql_body = " AND ".join(conditions)
        return f"{jql_body} ORDER BY created DESC" if jql_body else "ORDER BY created DESC"

    def fetch_tasks(self, assignee_email: str, min_priority: str = "High", status: str = "To Do") -> List[TaskModel]:
        if not self._is_configured():
            logger.warning("Jira not configured. Skipping fetch_tasks.")
            return []

        jql = self._build_jql(min_priority=min_priority, status=status)
        url = f"{self._base().rstrip('/')}/rest/api/3/search/jql"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        fields = ["summary", "description", "status", "priority", "assignee", "key"]
        
        try:
            payload = {
                "jql": jql,
                "maxResults": 50,
                "fields": fields
            }
            resp = requests.post(url, auth=self._auth(), json=payload, headers=headers)
            
            if resp.status_code != 200:
                logger.warning(f"Jira query with jql '{jql}' returned {resp.status_code}: {resp.text}. Trying fallback query...")
                # Fallback 1: status only
                fallback_payload = {
                    "jql": f'status = "{status}" ORDER BY created DESC',
                    "maxResults": 50,
                    "fields": fields
                }
                resp = requests.post(url, auth=self._auth(), json=fallback_payload, headers=headers)
                if resp.status_code != 200:
                    logger.warning(f"Fallback 1 failed. Trying broad fallback ORDER BY created DESC...")
                    fallback_payload2 = {
                        "jql": "ORDER BY created DESC",
                        "maxResults": 50,
                        "fields": fields
                    }
                    resp = requests.post(url, auth=self._auth(), json=fallback_payload2, headers=headers)

            if resp.status_code == 200:
                issues = resp.json().get('issues', [])
                tasks = []
                seen_keys = set()
                for issue in issues:
                    issue_key = (issue.get('key') or issue.get('id') or '').strip()
                    if not issue_key or issue_key.lower() in seen_keys:
                        continue
                    seen_keys.add(issue_key.lower())

                    fields_data = issue.get('fields', {})
                    desc_adf = fields_data.get('description')
                    description_text = self._extract_adf_text(desc_adf)
                    
                    assignee_obj = fields_data.get('assignee')
                    actual_assignee = (
                        assignee_obj.get('displayName') or 
                        assignee_obj.get('emailAddress') or 
                        "Unassigned"
                    ) if assignee_obj else "Unassigned"

                    status_name = fields_data.get('status', {}).get('name', status)
                    priority_name = fields_data.get('priority', {}).get('name') or "High"
                    title = fields_data.get('summary') or f"Task {issue_key}"

                    tasks.append(TaskModel(
                        external_id=issue_key,
                        title=title,
                        description=description_text,
                        acceptance_criteria="",
                        assignee_email=actual_assignee,
                        priority=priority_name,
                        status=status_name
                    ))
                logger.info(f"Successfully fetched {len(tasks)} issues from Jira using /rest/api/3/search/jql.")
                return tasks
            else:
                logger.error(f"Jira fetch_tasks failed completely: {resp.status_code} {resp.text}")
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

    def create_task(
        self,
        title: str,
        description: str = "",
        acceptance_criteria: str = "",
        priority: str = "Medium",
        assignee_email: str = None,
        assignee_account_id: str = None,
        issue_type: str = "Story",
        project_key: str = None,
        target_status: str = None,
        start_date: str = None,
        sprint_id: int | str = None,
        due_date: str = None,
        story_points: float | int | str = None,
        labels: list | str = None
    ) -> Optional[dict]:
        from app.services.jira_service import JiraService
        result = JiraService.create_issue(
            title=title,
            description=description,
            acceptance_criteria=acceptance_criteria,
            priority=priority,
            assignee=assignee_email,
            assignee_account_id=assignee_account_id,
            issue_type=issue_type,
            project_key=project_key,
            due_date=due_date,
            story_points=story_points,
            labels=labels,
            target_status=target_status,
            start_date=start_date,
            sprint_id=sprint_id
        )
        if result and "key" in result:
            return {
                "external_id": result["key"],
                "key": result["key"],
                "id": result.get("id"),
                "raw": result
            }
        return result

    def add_attachment(self, task_id: str, filename: str, file_data: bytes, mime_type: str = None) -> Optional[dict]:
        from app.services.jira_service import JiraService
        return JiraService.add_attachment(task_id, filename, file_data, mime_type)


