from abc import ABC, abstractmethod
from typing import Optional, Union, Dict, Any


class BaseStorageProvider(ABC):
    """
    Abstract base class defining the contract for storage providers.
    Allows dynamic switching between Local, AWS S3, and Azure Blob storage.
    """

    @abstractmethod
    def save_file(
        self,
        file_name: str,
        content: Union[bytes, str],
        project_id: Optional[Union[str, int]] = None,
        project_name: Optional[str] = None,
        **kwargs: Any
    ) -> str:
        """
        Saves file content and returns the file URI or access path.

        :param file_name: The name of the file (e.g. 'evidence_report.json', 'log.txt').
        :param content: Binary or string content to persist.
        :param project_id: Optional project or story identifier.
        :param project_name: Optional project or story name for organizing folders.
        :param kwargs: Provider-specific additional parameters (e.g., content_type).
        :return: URI or path identifying the saved file (e.g. s3://..., azure://..., or local absolute path).
        """
        pass

    @abstractmethod
    def get_file(self, file_path_or_uri: str) -> bytes:
        """
        Retrieves file content as bytes.

        :param file_path_or_uri: The URI or local path to retrieve.
        :return: File bytes.
        """
        pass

    @abstractmethod
    def delete_file(self, file_path_or_uri: str) -> bool:
        """
        Deletes a file given its URI or path.

        :param file_path_or_uri: The URI or local path to delete.
        :return: True if deleted successfully, False otherwise.
        """
        pass

    @abstractmethod
    def file_exists(self, file_path_or_uri: str) -> bool:
        """
        Checks whether a file exists in the storage backend.

        :param file_path_or_uri: The URI or local path to check.
        :return: True if the file exists, False otherwise.
        """
        pass
