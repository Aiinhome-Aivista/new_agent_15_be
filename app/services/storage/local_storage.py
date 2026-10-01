import os
import logging
from typing import Optional, Union, Any
from app.services.storage.base import BaseStorageProvider
from app.config.settings import Config

logger = logging.getLogger(__name__)


class LocalStorageProvider(BaseStorageProvider):
    """
    Implements saving files to the local file system.
    Defaults to UPLOAD_PATH (or 'data/uploads' under the backend directory).
    """

    def __init__(self, upload_path: Optional[str] = None):
        if upload_path:
            self.upload_path = upload_path
        else:
            self.upload_path = getattr(Config, "UPLOAD_PATH", None) or os.path.join(
                os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")),
                "data",
                "uploads"
            )
        try:
            os.makedirs(self.upload_path, exist_ok=True)
        except Exception as e:
            logger.warning(f"[LocalStorage] Failed creating directory {self.upload_path}: {e}")

    def _resolve_dir(self, project_id: Optional[Union[str, int]] = None, project_name: Optional[str] = None) -> str:
        folder_segment = project_name or (str(project_id) if project_id is not None else "")
        if folder_segment:
            # Clean folder name
            folder_clean = "".join(c for c in folder_segment if c.isalnum() or c in ("-", "_", " ")).strip()
            target_dir = os.path.join(self.upload_path, folder_clean or "general")
        else:
            target_dir = self.upload_path
        os.makedirs(target_dir, exist_ok=True)
        return target_dir

    def _normalize_path(self, file_path_or_uri: str) -> str:
        clean = file_path_or_uri
        if clean.startswith("file://"):
            clean = clean[7:]
        if not os.path.isabs(clean):
            clean = os.path.join(self.upload_path, clean)
        return os.path.normpath(clean)

    def save_file(
        self,
        file_name: str,
        content: Union[bytes, str],
        project_id: Optional[Union[str, int]] = None,
        project_name: Optional[str] = None,
        **kwargs: Any
    ) -> str:
        target_dir = self._resolve_dir(project_id, project_name)
        file_path = os.path.join(target_dir, file_name)

        if isinstance(content, str):
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
        else:
            with open(file_path, "wb") as f:
                f.write(content)

        abs_path = os.path.abspath(file_path)
        logger.info(f"[LocalStorage] File saved successfully at: {abs_path}")
        return abs_path

    def get_file(self, file_path_or_uri: str) -> bytes:
        target_path = self._normalize_path(file_path_or_uri)
        if not os.path.exists(target_path):
            raise FileNotFoundError(f"[LocalStorage] File not found: {target_path}")
        with open(target_path, "rb") as f:
            return f.read()

    def delete_file(self, file_path_or_uri: str) -> bool:
        target_path = self._normalize_path(file_path_or_uri)
        if os.path.exists(target_path):
            os.remove(target_path)
            logger.info(f"[LocalStorage] Deleted file: {target_path}")
            return True
        return False

    def file_exists(self, file_path_or_uri: str) -> bool:
        target_path = self._normalize_path(file_path_or_uri)
        return os.path.exists(target_path)
