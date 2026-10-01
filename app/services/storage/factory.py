import logging
from typing import Dict, Optional
from app.config.settings import Config
from app.services.storage.base import BaseStorageProvider
from app.services.storage.local_storage import LocalStorageProvider
from app.services.storage.aws_storage import AWSStorageProvider
from app.services.storage.azure_storage import AzureStorageProvider

logger = logging.getLogger(__name__)


class StorageFactory:
    """
    Factory to resolve and provide the appropriate StorageProvider.
    Maintains cached provider instances to avoid repeated client initializations.
    """

    _instances: Dict[str, BaseStorageProvider] = {}

    @classmethod
    def get_provider(cls, provider_type: Optional[str] = None) -> BaseStorageProvider:
        raw_provider = provider_type or getattr(Config, "CLOUD_PROVIDER", "DEFAULT")
        provider_key = (raw_provider or "DEFAULT").strip().upper()

        if provider_key not in cls._instances:
            if provider_key in ("DEFAULT", "LOCAL"):
                logger.info("[StorageFactory] Initializing LocalStorageProvider")
                cls._instances[provider_key] = LocalStorageProvider()
            elif provider_key in ("AWS", "S3"):
                logger.info("[StorageFactory] Initializing AWSStorageProvider")
                cls._instances[provider_key] = AWSStorageProvider()
            elif provider_key in ("AZURE", "BLOB"):
                logger.info("[StorageFactory] Initializing AzureStorageProvider")
                cls._instances[provider_key] = AzureStorageProvider()
            else:
                logger.warning(
                    f"[StorageFactory] Unknown CLOUD_PROVIDER '{provider_key}'. Falling back to LocalStorageProvider."
                )
                cls._instances[provider_key] = LocalStorageProvider()

        return cls._instances[provider_key]

    @classmethod
    def reset(cls) -> None:
        """Clears cached provider instances (useful for testing or hot-reload)."""
        cls._instances.clear()
