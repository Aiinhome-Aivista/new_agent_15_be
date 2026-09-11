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

            for task in external_tasks:
                # Check if it already exists locally
                existing = Story.query.filter_by(external_task_id=task.external_id).first()
                # Also check legacy jira_story_key just in case
                if not existing:
                    existing = Story.query.filter_by(jira_story_key=task.external_id).first()

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
                    if existing.status == 'INVALID':
                        existing.status = 'TO-DO'
                        updated = True
                    if updated:
                        db.session.commit()
                    skipped_count += 1
                    continue
                
                provider_name = current_app.config.get('ACTIVE_TASK_PROVIDER', 'manual').lower()

                # Create a new local DEVAA Story based on the external task
                new_story = Story(
                    external_task_id=task.external_id,
                    external_provider=provider_name,
                    jira_story_key=task.external_id if provider_name == 'jira' else None, # For backwards UI compatibility
                    title=task_title,
                    description=task_desc,
                    acceptance_criteria=task_ac,
                    source_branch=default_base_branch,
                    assignee_id=user.id, # Map back to local DEVAA user
                    owner_id=user.id, # Consider the user initiating the sync as the owner
                    status='TO-DO',
                    repository_details=[{"name": "main-repo", "url": default_repo_url, "branch": default_base_branch, "external_assignee": task.assignee_email}] # Store assignee here for UI
                )
                db.session.add(new_story)
                created_count += 1
                
            db.session.commit()
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
