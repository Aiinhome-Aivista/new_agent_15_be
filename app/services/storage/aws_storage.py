import io
import logging
from typing import Optional, Union, Any
from app.services.storage.base import BaseStorageProvider
from app.config.settings import Config

logger = logging.getLogger(__name__)


class AWSStorageProvider(BaseStorageProvider):
    """
    Implements saving files to AWS S3 using boto3.
    """

    def __init__(
        self,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        region_name: Optional[str] = None,
        bucket_name: Optional[str] = None,
        base_folder: Optional[str] = None,
        agent_folder: Optional[str] = None
    ):
        self.access_key_id = access_key_id or getattr(Config, "AWS_ACCESS_KEY_ID", "")
        self.secret_access_key = secret_access_key or getattr(Config, "AWS_SECRET_ACCESS_KEY", "")
        self.region_name = region_name or getattr(Config, "AWS_DEFAULT_REGION", "us-east-1")
        self.bucket_name = bucket_name or getattr(Config, "AWS_S3_BUCKET_NAME", "")
        self.base_folder = base_folder or getattr(Config, "AWS_S3_BASE_FOLDER", "")
        self.agent_folder = agent_folder or getattr(Config, "AWS_S3_AGENT_FOLDER", "")
        self._s3_client = None

    def _get_client(self):
        if self._s3_client is None:
            try:
                import boto3
            except ImportError as e:
                raise ImportError(
                    "The 'boto3' library is required for AWS storage. Please install it via 'pip install boto3'."
                ) from e

            client_kwargs: dict[str, Any] = {"region_name": self.region_name}
            if self.access_key_id and self.secret_access_key:
                client_kwargs["aws_access_key_id"] = self.access_key_id
                client_kwargs["aws_secret_access_key"] = self.secret_access_key

            self._s3_client = boto3.client("s3", **client_kwargs)
        return self._s3_client

    def _build_key(
        self,
        file_name: str,
        project_id: Optional[Union[str, int]] = None,
        project_name: Optional[str] = None
    ) -> str:
        parts = []
        if self.base_folder:
            parts.append(self.base_folder.strip("/"))
        if self.agent_folder:
            parts.append(self.agent_folder.strip("/"))

        folder_segment = project_name or (str(project_id) if project_id is not None else "")
        if folder_segment:
            parts.append(folder_segment.strip("/"))

        parts.append(file_name.lstrip("/"))
        return "/".join(p for p in parts if p)

    def _parse_s3_uri(self, uri: str) -> tuple[str, str]:
        """Parses s3://bucket/key or returns (self.bucket_name, key)."""
        if uri.startswith("s3://"):
            remainder = uri[5:]
            bucket, _, key = remainder.partition("/")
            return bucket, key
        return self.bucket_name, uri.lstrip("/")

    def save_file(
        self,
        file_name: str,
        content: Union[bytes, str],
        project_id: Optional[Union[str, int]] = None,
        project_name: Optional[str] = None,
        **kwargs: Any
    ) -> str:
        if not self.bucket_name:
            raise ValueError("[AWSStorage] AWS_S3_BUCKET_NAME is not configured.")

        client = self._get_client()
        s3_key = self._build_key(file_name, project_id, project_name)

        if isinstance(content, str):
            body_bytes = content.encode("utf-8")
        else:
            body_bytes = bytes(content)

        put_kwargs: dict[str, Any] = {
            "Bucket": self.bucket_name,
            "Key": s3_key,
            "Body": body_bytes,
        }
        if "content_type" in kwargs:
            put_kwargs["ContentType"] = kwargs["content_type"]

        client.put_object(**put_kwargs)
        s3_uri = f"s3://{self.bucket_name}/{s3_key}"
        logger.info(f"[AWSStorage] Uploaded '{file_name}' to {s3_uri}")
        return s3_uri

    def get_file(self, file_path_or_uri: str) -> bytes:
        client = self._get_client()
        bucket, key = self._parse_s3_uri(file_path_or_uri)
        response = client.get_object(Bucket=bucket, Key=key)
        return response["Body"].read()

    def delete_file(self, file_path_or_uri: str) -> bool:
        client = self._get_client()
        bucket, key = self._parse_s3_uri(file_path_or_uri)
        try:
            client.delete_object(Bucket=bucket, Key=key)
            logger.info(f"[AWSStorage] Deleted object {key} from bucket {bucket}")
            return True
        except Exception as e:
            logger.warning(f"[AWSStorage] Failed deleting object {key}: {e}")
            return False

    def file_exists(self, file_path_or_uri: str) -> bool:
        client = self._get_client()
        bucket, key = self._parse_s3_uri(file_path_or_uri)
        try:
            client.head_object(Bucket=bucket, Key=key)
            return True
        except Exception:
            return False
