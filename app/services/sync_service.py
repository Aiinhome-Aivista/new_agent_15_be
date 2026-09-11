import logging
from flask import current_app
from app import db
from app.models.story import Story
from app.models.user import User
from app.services.task_providers.factory import TaskProviderFactory

logger = logging.getLogger(__name__)

class SyncService:

    @staticmethod
    def sync_assigned_tasks(user_id: int) -> dict:
        """
        Syncs high priority external tasks assigned to the user into the local DEVAA database.
        Returns a dict with a summary of the sync operation.
        """
        user = User.query.get(user_id)
        if not user or not user.email:
            return {"error": "User not found or has no email address configured."}

        provider = TaskProviderFactory.get_provider()
        if not provider:
            return {"message": "No active external task provider configured. Skipping sync."}

        min_priority = current_app.config.get('MIN_SYNC_PRIORITY', 'High')
        
        try:
            # Fetch tasks assigned to this user's email
            external_tasks = provider.fetch_tasks(assignee_email=user.email, min_priority=min_priority, status="To Do")
            
            created_count = 0
            skipped_count = 0

            import re
            default_base_branch = current_app.config.get('GITHUB_DEFAULT_BASE_BRANCH', 'main')
            default_repo_url = current_app.config.get('GITHUB_BASE_URL', '')

            seen_keys = set()

            for task in external_tasks:
                raw_key = (task.external_id or '').strip()
                if not raw_key or raw_key.lower() in seen_keys:
                    skipped_count += 1
                    continue
                seen_keys.add(raw_key.lower())

                # Check if it already exists locally (case-insensitive)
                existing = Story.query.filter(
                    (db.func.lower(Story.external_task_id) == raw_key.lower()) |
                    (db.func.lower(Story.jira_story_key) == raw_key.lower())
                ).first()

                # Extract acceptance criteria if present in description
                task_ac = (task.acceptance_criteria or "").strip()
                task_title = (task.title or "").strip()
                task_desc = (task.description or "").strip()

                if not task_ac and task_desc:
                    ac_match = re.search(
                        r'(?:Acceptance Criteria|ACs?)\s*[:\n\-]+(.*?)(?=(?:\n\s*(?:Technical Constraints|Constraints|Expected Outcome|Notes|Expected Output)|$))',
                        task_desc,
                        re.IGNORECASE | re.DOTALL
                    )
                    if not ac_match:
                        ac_match = re.search(r'((?:AC\d+|AC\s*\d+)[\s\S]*)', task_desc, re.IGNORECASE)
                    if ac_match:
                        task_ac = ac_match.group(1).strip()

                if task_title and (task_title.lower().startswith('task ') or task_title.lower().startswith('scrum-')):
                    t_match = re.search(r'(?:^|\n)\s*Title\s*:\s*([^\n\r]+)', task_desc, re.IGNORECASE)
                    if t_match and t_match.group(1).strip():
                        task_title = t_match.group(1).strip()

                # Extract target_branch if specified in description (e.g. "target_branch : devaa1")
                extracted_target_branch = None
                tb_match = re.search(r'target_branch\s*[:=]\s*([^\s\n\r]+)', task_desc, re.IGNORECASE)
                if tb_match:
                    extracted_target_branch = tb_match.group(1).strip()

                if existing:
                    # Update any missing fields on existing story
                    updated = False
                    if not existing.source_branch:
                        existing.source_branch = default_base_branch
                        updated = True
                    if not existing.acceptance_criteria and task_ac:
                        existing.acceptance_criteria = task_ac
                        updated = True
                    if (existing.title.lower().startswith('task ') or existing.title.lower().startswith('scrum-')) and task_title != existing.title:
                        existing.title = task_title
                        updated = True
                    if extracted_target_branch:
                        details = list(existing.repository_details or [{}])
                        if details and isinstance(details[0], dict):
                            if details[0].get('target_branch') != extracted_target_branch:
                                details[0]['target_branch'] = extracted_target_branch
                                existing.repository_details = details
                                updated = True
                    if existing.status == 'INVALID':
                        existing.status = 'TO-DO'
                        updated = True
                    if updated:
                        try:
                            db.session.commit()
                        except Exception as e:
                            db.session.rollback()
                            logger.warning(f"Could not update existing story {existing.id}: {e}")
                    skipped_count += 1
                    continue
                
                provider_name = current_app.config.get('ACTIVE_TASK_PROVIDER', 'manual').lower()

                repo_info = {
                    "name": "main-repo",
                    "url": default_repo_url,
                    "branch": default_base_branch,
                    "external_assignee": task.assignee_email
                }
                if extracted_target_branch:
                    repo_info["target_branch"] = extracted_target_branch

                # Create a new local DEVAA Story based on the external task
                new_story = Story(
                    external_task_id=task.external_id,
                    external_provider=provider_name,
                    jira_story_key=task.external_id if provider_name == 'jira' else None,
                    title=task_title,
                    description=task_desc,
                    acceptance_criteria=task_ac,
                    source_branch=default_base_branch,
                    assignee_id=user.id,
                    owner_id=user.id,
                    status='TO-DO',
                    repository_details=[repo_info]
                )
                try:
                    db.session.add(new_story)
                    db.session.commit()
                    created_count += 1
                except Exception as insert_err:
                    db.session.rollback()
                    logger.warning(f"Failed to insert story {task.external_id} (already exists or constraint violation): {insert_err}")
                    skipped_count += 1
            return {
                "message": "Sync completed successfully.",
                "tasks_fetched": len(external_tasks),
                "stories_created": created_count,
                "stories_skipped": skipped_count
            }
        except Exception as e:
            db.session.rollback()
            logger.exception(f"Error during task sync: {e}")
            return {"error": str(e)}
