"""
Storage Service Facade
Provides a unified interface for persisting and managing files across
Local, AWS S3, and Azure Blob storage providers based on the CLOUD_PROVIDER configuration.
"""

from typing import Optional, Union, Any
from app.services.storage.factory import StorageFactory
from app.services.storage.base import BaseStorageProvider


def get_storage_provider(provider_type: Optional[str] = None) -> BaseStorageProvider:
    """
    Returns the active storage provider instance based on CLOUD_PROVIDER or explicit override.
    """
    return StorageFactory.get_provider(provider_type)


def save_file(
    file_name: str,
    content: Union[bytes, str],
    project_id: Optional[Union[str, int]] = None,
    project_name: Optional[str] = None,
    **kwargs: Any
) -> str:
    """
    Saves a file using the active cloud/local storage provider.

    :param file_name: Name of the file to save.
    :param content: Binary or string content of the file.
    :param project_id: Optional project ID for subfolder organization.
    :param project_name: Optional project name for subfolder organization.
    :param kwargs: Additional provider-specific parameters (e.g., content_type).
    :return: URI or path of the saved file.
    """
    provider = get_storage_provider()
    return provider.save_file(
        file_name=file_name,
        content=content,
        project_id=project_id,
        project_name=project_name,
        **kwargs
    )


def get_file(file_path_or_uri: str, provider_type: Optional[str] = None) -> bytes:
    """
    Retrieves file contents as bytes from the specified or active storage provider.
    """
    provider = get_storage_provider(provider_type)
    return provider.get_file(file_path_or_uri)


def delete_file(file_path_or_uri: str, provider_type: Optional[str] = None) -> bool:
    """
    Deletes a file from the specified or active storage provider.
    """
    provider = get_storage_provider(provider_type)
    return provider.delete_file(file_path_or_uri)


def file_exists(file_path_or_uri: str, provider_type: Optional[str] = None) -> bool:
    """
    Checks if a file exists in the specified or active storage provider.
    """
    provider = get_storage_provider(provider_type)
    return provider.file_exists(file_path_or_uri)
