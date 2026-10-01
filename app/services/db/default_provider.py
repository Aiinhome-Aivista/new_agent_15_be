import os
import logging
from urllib.parse import quote_plus
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from app.services.db.base import BaseDBProvider
from app.config.settings import Config

logger = logging.getLogger(__name__)


class DefaultDBProvider(BaseDBProvider):
    """
    Initializes a MySQL connection pool for local / default MySQL database.
    """

    def __init__(
        self,
        host: str = None,
        port: str = None,
        user: str = None,
        password: str = None,
        database: str = None
    ):
        super().__init__()
        self.host = host or getattr(Config, "DB_HOST", None) or os.getenv("MYSQL_HOST", "localhost")
        self.port = str(port or getattr(Config, "DB_PORT", None) or os.getenv("MYSQL_PORT", "3306"))
        self.user = user or getattr(Config, "DB_USER", None) or os.getenv("MYSQL_USER", "root")
        self.password = password if password is not None else (
            getattr(Config, "DB_PASSWORD", None) if getattr(Config, "DB_PASSWORD", None) is not None else os.getenv("MYSQL_PASSWORD", "")
        )
        self.database = database or getattr(Config, "DB_NAME", None) or os.getenv("MYSQL_DATABASE", "devaa_db")

    def get_db_url(self) -> str:
        safe_password = quote_plus(self.password) if self.password else ""
        return f"mysql+pymysql://{self.user}:{safe_password}@{self.host}:{self.port}/{self.database}"

    def init_pool(self) -> Engine:
        db_url = self.get_db_url()
        logger.info(f"[DefaultDBProvider] Initializing MySQL connection pool for {self.user}@{self.host}:{self.port}/{self.database}")
        engine = create_engine(
            db_url,
            pool_size=10,
            max_overflow=20,
            pool_recycle=280,
            pool_pre_ping=True
        )
        return engine
