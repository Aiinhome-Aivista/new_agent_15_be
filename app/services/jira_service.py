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
    def find_user(cls, query: str) -> dict | None:
        """Search Jira users by email or name. Returns user dict with accountId or None."""
        if not cls._is_configured() or not query:
            return None
        url = f"{cls._base()}/rest/api/3/user/search"
        try:
            resp = requests.get(url, auth=cls._auth(), params={"query": query.strip()},
                                headers={"Accept": "application/json"})
            if resp.status_code == 200:
                users = resp.json()
                if users and isinstance(users, list):
                    return users[0]
            logger.warning(f"Jira find_user returned {resp.status_code} for query '{query}': {resp.text}")
        except Exception as e:
            logger.exception(f"Error searching Jira user: {e}")
        return None

    @classmethod
    def get_projects(cls) -> list:
        """Fetch all visible Jira projects."""
        if not cls._is_configured():
            return []
        url = f"{cls._base().rstrip('/')}/rest/api/3/project"
        try:
            resp = requests.get(url, auth=cls._auth(), headers={"Accept": "application/json"})
            if resp.status_code == 200:
                projects = resp.json()
                return [{"key": p.get("key"), "name": p.get("name"), "id": p.get("id")} for p in projects if isinstance(p, dict)]
            logger.warning(f"Jira get_projects returned {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.exception(f"Error fetching Jira projects: {e}")
        return []

    @classmethod
    def get_assignable_users(cls, project_key: str = None) -> list:
        """Fetch assignable users for a project (or general users)."""
        if not cls._is_configured():
            return []
        base = cls._base().rstrip('/')
        proj_key = project_key or current_app.config.get('JIRA_PROJECT_KEY')

        # 1. Try project-specific assignable users endpoint
        if proj_key:
            url = f"{base}/rest/api/3/user/assignable/search"
            try:
                resp = requests.get(url, auth=cls._auth(), params={"project": proj_key}, headers={"Accept": "application/json"})
                if resp.status_code == 200:
                    users = resp.json()
                    if isinstance(users, list) and users:
                        return [{
                            "accountId": u.get("accountId"),
                            "displayName": u.get("displayName") or u.get("name") or "Unnamed",
                            "emailAddress": u.get("emailAddress", ""),
                            "avatarUrl": (u.get("avatarUrls") or {}).get("24x24", "")
                        } for u in users if isinstance(u, dict) and u.get("accountType") != "app"]
            except Exception as e:
                logger.warning(f"Failed to fetch assignable users for project {proj_key}: {e}")

        # 2. Fallback to general user search
        url_fallback = f"{base}/rest/api/3/users/search"
        try:
            resp = requests.get(url_fallback, auth=cls._auth(), headers={"Accept": "application/json"})
            if resp.status_code == 200:
                users = resp.json()
                return [{
                    "accountId": u.get("accountId"),
                    "displayName": u.get("displayName") or u.get("name") or "Unnamed",
                    "emailAddress": u.get("emailAddress", ""),
                    "avatarUrl": (u.get("avatarUrls") or {}).get("24x24", "")
                } for u in users if isinstance(u, dict) and u.get("accountType") != "app"]
        except Exception as e:
            logger.warning(f"Failed to search general Jira users: {e}")

        return []

    @classmethod
    def find_field_id(cls, name_query: str) -> str | None:
        """Find a Jira field ID by its human-readable name or ID."""
        if not cls._is_configured():
            return None
        base = cls._base().rstrip('/')
        try:
            resp = requests.get(f"{base}/rest/api/3/field", auth=cls._auth(), headers={"Accept": "application/json"})
            if resp.status_code == 200:
                fields = resp.json()
                for f in fields:
                    f_name = (f.get('name') or '').strip().lower()
                    f_id = (f.get('id') or '').strip()
                    if f_id.lower() == name_query.lower() or f_name == name_query.lower():
                        return f_id
        except Exception as e:
            logger.warning(f"Failed to lookup Jira field '{name_query}': {e}")
        return None

    @classmethod
    def get_sprints(cls, project_key: str = None) -> list:
        """Fetch active and future sprints for a project board."""
        if not cls._is_configured():
            return []
        proj_key = (project_key or current_app.config.get('JIRA_PROJECT_KEY') or 'SCRUM').strip()
        base = cls._base().rstrip('/')
        sprints = []
        try:
            boards_url = f"{base}/rest/agile/1.0/board"
            resp = requests.get(boards_url, auth=cls._auth(), params={"projectKeyOrId": proj_key}, headers={"Accept": "application/json"})
            boards = []
            if resp.status_code == 200:
                boards = resp.json().get('values', [])
            else:
                resp2 = requests.get(boards_url, auth=cls._auth(), headers={"Accept": "application/json"})
                if resp2.status_code == 200:
                    boards = resp2.json().get('values', [])

            seen_ids = set()
            for board in boards:
                board_id = board.get('id')
                if not board_id:
                    continue
                sprints_url = f"{base}/rest/agile/1.0/board/{board_id}/sprint"
                sprint_resp = requests.get(sprints_url, auth=cls._auth(), params={"state": "active,future"}, headers={"Accept": "application/json"})
                if sprint_resp.status_code == 200:
                    sprint_vals = sprint_resp.json().get('values', [])
                    for s in sprint_vals:
                        s_id = s.get('id')
                        if s_id and s_id not in seen_ids:
                            seen_ids.add(s_id)
                            sprints.append({
                                "id": s_id,
                                "name": s.get('name'),
                                "state": s.get('state'),
                                "boardId": board_id
                            })
            return sprints
        except Exception as e:
            logger.exception(f"Error fetching sprints: {e}")
            return []

    @classmethod
    def add_to_sprint(cls, sprint_id: int | str, issue_key: str) -> bool:
        """Move an issue into a specific sprint using Jira Agile API."""
        if not cls._is_configured() or not sprint_id or not issue_key:
            return False
        base = cls._base().rstrip('/')
        url = f"{base}/rest/agile/1.0/sprint/{sprint_id}/issue"
        try:
            resp = requests.post(url, auth=cls._auth(), json={"issues": [issue_key]},
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
            if resp.status_code in [204, 200, 201]:
                logger.info(f"Successfully moved {issue_key} to sprint {sprint_id}")
                return True
            logger.warning(f"Failed to add {issue_key} to sprint {sprint_id}: {resp.status_code} {resp.text}")
            return False
        except Exception as e:
            logger.exception(f"Exception adding issue to sprint: {e}")
            return False


    @classmethod
    def create_issue(
        cls,
        title: str,
        description: str = "",
        acceptance_criteria: str = "",
        priority: str = "Medium",
        assignee: str = None,
        assignee_account_id: str = None,
        issue_type: str = "Story",
        project_key: str = None,
        due_date: str = None,
        story_points: float | int | str = None,
        labels: list | str = None,
        target_status: str = None,
        start_date: str = None,
        sprint_id: int | str = None
    ) -> dict | None:
        """
        Create a new issue in Jira Cloud.
        Returns dict with issue details e.g. {'key': 'DEVAA-123', 'id': '...'} or dict with 'error'.
        """
        if not cls._is_configured():
            logger.warning("Jira not configured. Skipping create_issue.")
            return None

        from datetime import datetime
        creation_date_str = datetime.utcnow().strftime("%Y-%m-%d")
        effective_start_date = (start_date or "").strip() or creation_date_str

        proj_key = (project_key or current_app.config.get('JIRA_PROJECT_KEY') or 'SCRUM').strip()
        url = f"{cls._base().rstrip('/')}/rest/api/3/issue"

        # Build ADF content for description + acceptance criteria
        content_nodes = []
        if description:
            content_nodes.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": description}]
            })
        if acceptance_criteria:
            content_nodes.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": f"\nAcceptance Criteria:\n{acceptance_criteria}"}]
            })
        if not content_nodes:
            content_nodes.append({
                "type": "paragraph",
                "content": [{"type": "text", "text": title}]
            })

        fields = {
            "project": {"key": proj_key},
            "summary": title,
            "issuetype": {"name": issue_type or "Story"},
            "description": {
                "type": "doc",
                "version": 1,
                "content": content_nodes
            }
        }

        # Handle priority if provided
        if priority:
            p_val = priority.strip().capitalize()
            fields["priority"] = {"name": p_val}

        # Handle Due Date
        if due_date and str(due_date).strip():
            fields["duedate"] = str(due_date).strip()

        # Handle Labels
        if labels:
            if isinstance(labels, str):
                raw_labels = [l.strip() for l in labels.split(',') if l.strip()]
            elif isinstance(labels, (list, tuple)):
                raw_labels = [str(l).strip() for l in labels if str(l).strip()]
            else:
                raw_labels = []
            clean_labels = [l.replace(' ', '_') for l in raw_labels if l]
            if clean_labels:
                fields["labels"] = clean_labels

        # Handle assignee accountId lookup or direct accountId
        if assignee_account_id and assignee_account_id.strip():
            fields["assignee"] = {"id": assignee_account_id.strip()}
            logger.info(f"Assigned Jira issue directly via accountId: {assignee_account_id.strip()}")
        elif assignee and assignee.strip():
            raw = assignee.strip()
            if raw.startswith("accountid:"):
                fields["assignee"] = {"id": raw.split(":", 1)[1]}
            elif "@" not in raw and len(raw) >= 16 and " " not in raw:
                fields["assignee"] = {"id": raw}
            else:
                user_obj = cls.find_user(raw)
                if user_obj and "accountId" in user_obj:
                    fields["assignee"] = {"id": user_obj["accountId"]}
                    logger.info(f"Assigned Jira issue to accountId: {user_obj['accountId']} ({user_obj.get('displayName')})")
                else:
                    logger.warning(f"Could not find Jira accountId for assignee '{assignee}'. Issue will be unassigned.")

        def _format_error(resp_obj):
            try:
                err_json = resp_obj.json()
                msgs = err_json.get('errorMessages', [])
                err_dict = err_json.get('errors', {})
                combined = list(msgs)
                for f, msg in err_dict.items():
                    combined.append(f"{f}: {msg}")
                if combined:
                    return "; ".join(combined)
            except Exception:
                pass
            return resp_obj.text or f"HTTP {resp_obj.status_code}"

        def _post_create_actions(issue_key_val):
            # 1. Update Start Date
            if effective_start_date and issue_key_val:
                s_field = cls.find_field_id("start date") or "startDate"
                update_url = f"{cls._base().rstrip('/')}/rest/api/3/issue/{issue_key_val}"
                for f_name in [s_field, "startDate"]:
                    try:
                        up_resp = requests.put(update_url, auth=cls._auth(),
                                               json={"fields": {f_name: effective_start_date}},
                                               headers={"Content-Type": "application/json"})
                        if up_resp.status_code in [200, 204]:
                            logger.info(f"Set start date '{effective_start_date}' on {issue_key_val} using {f_name}")
                            break
                    except Exception as e:
                        logger.warning(f"Failed to set start date using {f_name}: {e}")

            # 2. Add to Sprint (defaults to active sprint if not set to 'backlog')
            if issue_key_val:
                target_sprint = sprint_id
                if target_sprint is None or target_sprint == "" or str(target_sprint).lower() == "active":
                    all_sprints = cls.get_sprints(proj_key)
                    active_sprint = next((s for s in all_sprints if s.get('state') == 'active'), None)
                    if active_sprint:
                        target_sprint = active_sprint['id']
                    elif all_sprints and str(target_sprint).lower() == "active":
                        target_sprint = all_sprints[0]['id']

                if target_sprint and str(target_sprint).lower() not in ["none", "backlog"]:
                    cls.add_to_sprint(target_sprint, issue_key_val)

            # 3. Update Story Points if provided
            if story_points is not None and str(story_points).strip() and issue_key_val:
                try:
                    sp_val = float(str(story_points).strip())
                    sp_field = cls.find_field_id("story points") or cls.find_field_id("story point estimate")
                    candidates = [c for c in [sp_field, "customfield_10016", "customfield_10026", "customfield_10028"] if c]
                    update_url = f"{cls._base().rstrip('/')}/rest/api/3/issue/{issue_key_val}"
                    for cand in candidates:
                        try:
                            up_resp = requests.put(update_url, auth=cls._auth(),
                                                   json={"fields": {cand: sp_val}},
                                                   headers={"Content-Type": "application/json"})
                            if up_resp.status_code in [200, 204]:
                                logger.info(f"Set story points '{sp_val}' on {issue_key_val} using {cand}")
                                break
                        except Exception as e:
                            logger.warning(f"Could not set story points using {cand}: {e}")
                except Exception as ex:
                    logger.warning(f"Could not parse/set story points: {ex}")

        payload = {"fields": fields}
        try:
            resp = requests.post(url, json=payload, auth=cls._auth(),
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
            if resp.status_code == 201:
                res_data = resp.json()
                issue_key = res_data.get('key')
                logger.info(f"Successfully created Jira issue: {issue_key}")

                if target_status and issue_key and target_status.lower() not in ['to-do', 'to do', 'todo', 'open']:
                    cls.transition_issue(issue_key, target_status)

                _post_create_actions(issue_key)
                return res_data
            else:
                logger.error(f"Jira create_issue failed ({resp.status_code}): {resp.text}")
                # Fallback: if issuetype 'Story' failed, try 'Task'
                if resp.status_code == 400 and (issue_type or "Story").lower() == "story":
                    logger.info("Retrying Jira issue creation with issuetype 'Task'...")
                    payload["fields"]["issuetype"] = {"name": "Task"}
                    resp_retry = requests.post(url, json=payload, auth=cls._auth(),
                                               headers={"Content-Type": "application/json", "Accept": "application/json"})
                    if resp_retry.status_code == 201:
                        res_data = resp_retry.json()
                        issue_key = res_data.get('key')
                        if target_status and issue_key and target_status.lower() not in ['to-do', 'to do', 'todo', 'open']:
                            cls.transition_issue(issue_key, target_status)
                        _post_create_actions(issue_key)
                        return res_data
                    else:
                        logger.error(f"Jira create_issue retry failed ({resp_retry.status_code}): {resp_retry.text}")
                        return {"error": _format_error(resp_retry), "status_code": resp_retry.status_code}
                return {"error": _format_error(resp), "status_code": resp.status_code}
        except Exception as e:
            logger.exception(f"Exception during Jira create_issue: {e}")
            return {"error": str(e)}

    @classmethod
    def add_attachment(cls, issue_key: str, filename: str, file_data: bytes, mime_type: str = None) -> dict:
        """
        Upload an attachment to a Jira issue.
        POST /rest/api/3/issue/{issueIdOrKey}/attachments
        Requires header: X-Atlassian-Token: no-check
        """
        if not cls._is_configured() or not issue_key:
            return {"error": "Jira not configured or missing issue key"}

        url = f"{cls._base().rstrip('/')}/rest/api/3/issue/{issue_key}/attachments"
        headers = {
            "X-Atlassian-Token": "no-check",
            "Accept": "application/json"
        }
        files = {
            "file": (filename, file_data, mime_type or "application/octet-stream")
        }
        try:
            resp = requests.post(url, auth=cls._auth(), headers=headers, files=files)
            if resp.status_code in [200, 201]:
                logger.info(f"Successfully uploaded attachment '{filename}' to Jira issue {issue_key}")
                data = resp.json()
                return data[0] if isinstance(data, list) and data else data
            else:
                logger.error(f"Failed to upload attachment '{filename}' to Jira {issue_key} ({resp.status_code}): {resp.text}")
                return {"error": resp.text, "status_code": resp.status_code}
        except Exception as e:
            logger.exception(f"Exception uploading attachment to Jira {issue_key}: {e}")
            return {"error": str(e)}

    @classmethod
    def update_issue(
        cls,
        issue_key: str,
        title: str = None,
        description: str = None,
        acceptance_criteria: str = None,
        priority: str = None,
        assignee: str = None,
        assignee_account_id: str = None,
        due_date: str = None,
        story_points: float | int | str = None,
        labels: list | str = None
    ) -> dict:
        """
        Update an existing Jira issue's fields.
        PUT /rest/api/3/issue/{issueIdOrKey}
        """
        if not cls._is_configured() or not issue_key:
            return {"error": "Jira not configured or missing issue key"}

        fields = {}
        if title:
            fields["summary"] = title.strip()

        # Update description + acceptance criteria if provided
        if description is not None or acceptance_criteria is not None:
            content_nodes = []
            if description:
                content_nodes.append({
                    "type": "paragraph",
                    "content": [{"type": "text", "text": description}]
                })
            if acceptance_criteria:
                content_nodes.append({
                    "type": "paragraph",
                    "content": [{"type": "text", "text": f"\nAcceptance Criteria:\n{acceptance_criteria}"}]
                })
            if content_nodes:
                fields["description"] = {
                    "type": "doc",
                    "version": 1,
                    "content": content_nodes
                }

        if priority:
            fields["priority"] = {"name": priority.strip().capitalize()}

        if due_date is not None:
            fields["duedate"] = due_date.strip() if due_date else None

        if labels is not None:
            if isinstance(labels, str):
                raw_labels = [l.strip() for l in labels.split(',') if l.strip()]
            elif isinstance(labels, (list, tuple)):
                raw_labels = [str(l).strip() for l in labels if str(l).strip()]
            else:
                raw_labels = []
            fields["labels"] = [l.replace(' ', '_') for l in raw_labels if l]

        if assignee_account_id and assignee_account_id.strip():
            fields["assignee"] = {"id": assignee_account_id.strip()}
        elif assignee and assignee.strip():
            user_obj = cls.find_user(assignee.strip())
            if user_obj and "accountId" in user_obj:
                fields["assignee"] = {"id": user_obj["accountId"]}

        url = f"{cls._base().rstrip('/')}/rest/api/3/issue/{issue_key}"
        try:
            if fields:
                resp = requests.put(url, auth=cls._auth(), json={"fields": fields},
                                    headers={"Content-Type": "application/json", "Accept": "application/json"})
                if resp.status_code not in [200, 204]:
                    logger.warning(f"Jira update_issue returned {resp.status_code}: {resp.text}")

            # Update story points if provided
            if story_points is not None and str(story_points).strip():
                try:
                    sp_val = float(str(story_points).strip())
                    sp_field = cls.find_field_id("story points") or cls.find_field_id("story point estimate")
                    candidates = [c for c in [sp_field, "customfield_10016", "customfield_10026", "customfield_10028"] if c]
                    for cand in candidates:
                        try:
                            up_resp = requests.put(url, auth=cls._auth(),
                                                   json={"fields": {cand: sp_val}},
                                                   headers={"Content-Type": "application/json"})
                            if up_resp.status_code in [200, 204]:
                                break
                        except Exception:
                            pass
                except Exception as ex:
                    logger.warning(f"Failed to update story points on Jira: {ex}")

            logger.info(f"Updated Jira issue {issue_key}")
            return {"success": True, "key": issue_key}
        except Exception as e:
            logger.exception(f"Error updating Jira issue {issue_key}: {e}")
            return {"error": str(e)}

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




