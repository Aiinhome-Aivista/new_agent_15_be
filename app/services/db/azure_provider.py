import os
import logging
from urllib.parse import quote_plus
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from app.services.db.base import BaseDBProvider
from app.config.settings import Config

logger = logging.getLogger(__name__)


class AzureDBProvider(BaseDBProvider):
    """
    Initializes a MySQL connection pool for Azure Database for MySQL Flexible Server.
    """

    def __init__(
        self,
        host: str = None,
        port: str = None,
        database: str = None,
        user: str = None,
        password: str = None
    ):
        super().__init__()
        self.host = host or getattr(Config, "AZURE_DB_HOST", None) or os.getenv("AZURE_DB_HOST", "")
        self.port = str(port or getattr(Config, "AZURE_DB_PORT", None) or os.getenv("AZURE_DB_PORT", "3306"))
        self.database = database or getattr(Config, "AZURE_DB_DATABASE", None) or os.getenv("AZURE_DB_DATABASE", "acse_db")
        self.user = user or getattr(Config, "AZURE_DB_USER", None) or os.getenv("AZURE_DB_USER", "")
        self.password = password if password is not None else (
            getattr(Config, "AZURE_DB_PASSWORD", None) if getattr(Config, "AZURE_DB_PASSWORD", None) is not None else os.getenv("AZURE_DB_PASSWORD", "")
        )

    def get_db_url(self) -> str:
        safe_password = quote_plus(self.password) if self.password else ""
        return f"mysql+pymysql://{self.user}:{safe_password}@{self.host}:{self.port}/{self.database}"

    def init_pool(self) -> Engine:
        if not self.host:
            raise ValueError("[AzureDBProvider] AZURE_DB_HOST is not configured.")

        db_url = self.get_db_url()
        logger.info(f"[AzureDBProvider] Initializing Azure MySQL connection pool for {self.user}@{self.host}:{self.port}/{self.database}")
        engine = create_engine(
            db_url,
            pool_size=10,
            max_overflow=20,
            pool_recycle=280,
            pool_pre_ping=True
        )
        return engine
