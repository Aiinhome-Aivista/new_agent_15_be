import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from contextlib import contextmanager
from sqlalchemy import text
from sqlalchemy.engine import Engine, Connection

logger = logging.getLogger(__name__)


class BaseDBProvider(ABC):
    """
    Abstract base class defining the contract for database connection providers.
    All providers manage a connection pool to a MySQL-compatible database.
    """

    def __init__(self):
        self._engine: Optional[Engine] = None

    @abstractmethod
    def init_pool(self) -> Engine:
        """
        Initializes and returns the database connection pool/engine.
        """
        pass

    @abstractmethod
    def get_db_url(self) -> str:
        """
        Returns the SQLAlchemy-compatible database connection URL string.
        """
        pass

    def get_engine(self) -> Engine:
        """
        Returns the active SQLAlchemy Engine instance with connection pooling.
        """
        if self._engine is None:
            self._engine = self.init_pool()
        return self._engine

    @contextmanager
    def get_connection(self):
        """
        Context manager yielding an active database connection from the pool.
        """
        engine = self.get_engine()
        conn: Connection = engine.connect()
        try:
            yield conn
        finally:
            conn.close()

    def query(self, sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Executes a SELECT query against the connection pool and returns rows as dictionaries.

        :param sql: SQL query string.
        :param params: Optional query parameters.
        :return: List of dictionary representations of each row.
        """
        engine = self.get_engine()
        with engine.connect() as conn:
            result = conn.execute(text(sql), params or {})
            if result.returns_rows:
                return [dict(row._mapping) for row in result]
            return []

    def execute(self, sql: str, params: Optional[Dict[str, Any]] = None) -> int:
        """
        Executes an INSERT, UPDATE, or DELETE statement and commits the transaction.

        :param sql: SQL query statement.
        :param params: Optional statement parameters.
        :return: Number of affected rows.
        """
        engine = self.get_engine()
        with engine.begin() as conn:
            result = conn.execute(text(sql), params or {})
            return result.rowcount

    def close(self) -> None:
        """
        Disposes of the connection pool.
        """
        if self._engine is not None:
            try:
                self._engine.dispose()
                logger.info(f"[{self.__class__.__name__}] Connection pool disposed.")
            except Exception as e:
                logger.warning(f"[{self.__class__.__name__}] Error disposing connection pool: {e}")
            self._engine = None
