from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class TaskModel:
    external_id: str  # e.g., Jira key like 'PROJ-123'
    title: str
    description: str
    acceptance_criteria: str
    assignee_email: Optional[str] = None
    priority: Optional[str] = None
    status: str = 'TO-DO'


class BaseTaskProvider(ABC):
    """
    Abstract Base Class for integrating external project management tools
    (Jira, Linear, Asana, etc.)
    """

    @abstractmethod
    def fetch_tasks(self, assignee_email: str, min_priority: str, status: str = "To Do") -> List[TaskModel]:
        """
        Fetch tasks assigned to a specific user with a minimum priority.
        """
        pass

    @abstractmethod
    def add_comment(self, task_id: str, comment_body: str) -> Optional[str]:
        """
        Add a comment to an external task. Returns the comment ID if successful.
        """
        pass

    @abstractmethod
    def update_status(self, task_id: str, target_status: str) -> bool:
        """
        Update the status of an external task.
        """
        pass
