import logging
from flask import current_app
from app.services.task_providers.base_provider import BaseTaskProvider
from app.services.task_providers.jira_provider import JiraTaskProvider

logger = logging.getLogger(__name__)

class TaskProviderFactory:
    
    @staticmethod
    def get_provider() -> BaseTaskProvider | None:
        """
        Returns the configured task provider (e.g., Jira, Linear, Asana).
        Reads from ACTIVE_TASK_PROVIDER in the environment/config.
        Returns None if 'manual' or unconfigured.
        """
        active_provider = current_app.config.get('ACTIVE_TASK_PROVIDER', 'manual').lower()
        
        if active_provider == 'jira':
            return JiraTaskProvider()
        elif active_provider == 'manual':
            return None
        else:
            logger.warning(f"Unknown ACTIVE_TASK_PROVIDER: {active_provider}. Falling back to manual.")
            return None
