import logging
from typing import Optional, Union, Any
from app.services.storage.base import BaseStorageProvider
from app.config.settings import Config

logger = logging.getLogger(__name__)


class AzureStorageProvider(BaseStorageProvider):
    """
    Implements saving files to Azure Blob Storage using azure-storage-blob.
    """

    def __init__(
        self,
        connection_string: Optional[str] = None,
        container_name: Optional[str] = None
    ):
        self.connection_string = connection_string or getattr(Config, "AZURE_STORAGE_CONNECTION_STRING", "")
        self.container_name = container_name or getattr(Config, "AZURE_CONTAINER_NAME", "agent-artifacts")
        self._blob_service_client = None
        self._container_client = None

    def _get_container_client(self):
        if self._container_client is None:
            if not self.connection_string:
                raise ValueError("[AzureStorage] AZURE_STORAGE_CONNECTION_STRING is not configured.")

            try:
                from azure.storage.blob import BlobServiceClient
            except ImportError as e:
                raise ImportError(
                    "The 'azure-storage-blob' library is required for Azure storage. "
                    "Please install it via 'pip install azure-storage-blob'."
                ) from e

            self._blob_service_client = BlobServiceClient.from_connection_string(self.connection_string)
            self._container_client = self._blob_service_client.get_container_client(self.container_name)

            try:
                if not self._container_client.exists():
                    self._container_client.create_container()
            except Exception as e:
                logger.debug(f"[AzureStorage] Container existence check/creation note: {e}")

        return self._container_client

    def _build_blob_name(
        self,
        file_name: str,
        project_id: Optional[Union[str, int]] = None,
        project_name: Optional[str] = None
    ) -> str:
        parts = []
        folder_segment = project_name or (str(project_id) if project_id is not None else "")
        if folder_segment:
            parts.append(folder_segment.strip("/"))
        parts.append(file_name.lstrip("/"))
        return "/".join(p for p in parts if p)

    def _parse_blob_name(self, uri: str) -> str:
        """Extracts blob name from URL or URI format."""
        if uri.startswith("azure://"):
            remainder = uri[8:]
            _, _, blob_name = remainder.partition("/")
            return blob_name
        # If it's a full HTTPS URL
        if "/" in uri and "blob.core.windows.net" in uri:
            parts = uri.split(f"/{self.container_name}/", 1)
            if len(parts) > 1:
                return parts[1]
        return uri.lstrip("/")

    def save_file(
        self,
        file_name: str,
        content: Union[bytes, str],
        project_id: Optional[Union[str, int]] = None,
        project_name: Optional[str] = None,
        **kwargs: Any
    ) -> str:
        container = self._get_container_client()
        blob_name = self._build_blob_name(file_name, project_id, project_name)
        blob_client = container.get_blob_client(blob_name)

        if isinstance(content, str):
            data = content.encode("utf-8")
        else:
            data = bytes(content)

        upload_kwargs: dict[str, Any] = {"overwrite": True}
        if "content_type" in kwargs:
            from azure.storage.blob import ContentSettings
            upload_kwargs["content_settings"] = ContentSettings(content_type=kwargs["content_type"])

        blob_client.upload_blob(data, **upload_kwargs)
        blob_url = blob_client.url
        logger.info(f"[AzureStorage] Uploaded '{file_name}' to {blob_url}")
        return blob_url

    def get_file(self, file_path_or_uri: str) -> bytes:
        container = self._get_container_client()
        blob_name = self._parse_blob_name(file_path_or_uri)
        blob_client = container.get_blob_client(blob_name)
        downloader = blob_client.download_blob()
        return downloader.readall()

    def delete_file(self, file_path_or_uri: str) -> bool:
        container = self._get_container_client()
        blob_name = self._parse_blob_name(file_path_or_uri)
        blob_client = container.get_blob_client(blob_name)
        try:
            blob_client.delete_blob()
            logger.info(f"[AzureStorage] Deleted blob: {blob_name}")
            return True
        except Exception as e:
            logger.warning(f"[AzureStorage] Failed deleting blob {blob_name}: {e}")
            return False

    def file_exists(self, file_path_or_uri: str) -> bool:
        container = self._get_container_client()
        blob_name = self._parse_blob_name(file_path_or_uri)
        blob_client = container.get_blob_client(blob_name)
        return blob_client.exists()
