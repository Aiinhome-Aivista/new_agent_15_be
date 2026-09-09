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

            for task in external_tasks:
                # Check if it already exists locally
                existing = Story.query.filter_by(external_task_id=task.external_id).first()
                # Also check legacy jira_story_key just in case
                if not existing:
                    existing = Story.query.filter_by(jira_story_key=task.external_id).first()

                if existing:
                    skipped_count += 1
                    continue
                
                provider_name = current_app.config.get('ACTIVE_TASK_PROVIDER', 'manual').lower()

                # Create a new local DEVAA Story based on the external task
                new_story = Story(
                    external_task_id=task.external_id,
                    external_provider=provider_name,
                    jira_story_key=task.external_id if provider_name == 'jira' else None, # For backwards UI compatibility
                    title=task.title,
                    description=task.description,
                    acceptance_criteria=task.acceptance_criteria,
                    assignee_id=user.id, # Map back to local DEVAA user
                    owner_id=user.id, # Consider the user initiating the sync as the owner
                    status='TO-DO',
                    repository_details=[{"name": "", "url": "", "branch": "main"}] # Default empty structure
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
