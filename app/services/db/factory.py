import logging
from typing import Dict, Optional
from app.config.settings import Config
from app.services.db.base import BaseDBProvider
from app.services.db.default_provider import DefaultDBProvider
from app.services.db.aws_provider import AWSRDSDBProvider
from app.services.db.azure_provider import AzureDBProvider

logger = logging.getLogger(__name__)


class DBFactory:
    """
    Factory to instantiate and return the appropriate DBProvider.
    Caches provider instances so connection pools are reused across requests.
    """

    _instances: Dict[str, BaseDBProvider] = {}

    @classmethod
    def get_provider(cls, provider_type: Optional[str] = None) -> BaseDBProvider:
        raw_provider = provider_type or getattr(Config, "DB_PROVIDER", "DEFAULT")
        provider_key = (raw_provider or "DEFAULT").strip().upper()

        if provider_key not in cls._instances:
            if provider_key in ("DEFAULT", "LOCAL"):
                logger.info("[DBFactory] Initializing DefaultDBProvider (Local MySQL)")
                cls._instances[provider_key] = DefaultDBProvider()
            elif provider_key in ("AWS", "RDS"):
                logger.info("[DBFactory] Initializing AWSRDSDBProvider (AWS RDS)")
                cls._instances[provider_key] = AWSRDSDBProvider()
            elif provider_key in ("AZURE",):
                logger.info("[DBFactory] Initializing AzureDBProvider (Azure MySQL)")
                cls._instances[provider_key] = AzureDBProvider()
            else:
                logger.warning(
                    f"[DBFactory] Unknown DB_PROVIDER '{provider_key}'. Falling back to DefaultDBProvider."
                )
                cls._instances[provider_key] = DefaultDBProvider()

        return cls._instances[provider_key]

    @classmethod
    def reset(cls) -> None:
        """Disposes all provider pools and clears cache."""
        for name, provider in list(cls._instances.items()):
            try:
                provider.close()
            except Exception as e:
                logger.warning(f"[DBFactory] Error closing provider '{name}': {e}")
        cls._instances.clear()
