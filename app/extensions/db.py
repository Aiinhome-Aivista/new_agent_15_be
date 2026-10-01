"""
Database Extension Module
Exposes Flask-SQLAlchemy 'db' instance alongside dynamic direct SQL query/execute utilities
routed to the active DB_PROVIDER (Local, AWS RDS, or Azure Database).
"""

from typing import Any, Dict, List, Optional
from flask_sqlalchemy import SQLAlchemy
from app.services.db.factory import DBFactory
from app.services.db.base import BaseDBProvider

# Global Flask-SQLAlchemy instance (used across ORM models)
db = SQLAlchemy()


def get_active_provider() -> BaseDBProvider:
    """
    Returns the active BaseDBProvider instance corresponding to DB_PROVIDER.
    """
    return DBFactory.get_provider()


def get_db_connection():
    """
    Context manager that yields an active database connection from the active provider's pool.
    Usage:
        with get_db_connection() as conn:
            conn.execute(...)
    """
    return get_active_provider().get_connection()


def query(sql: str, params: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Executes a SELECT query directly against the active provider's pool
    and returns a list of dictionary rows.

    Example:
        results = query("SELECT * FROM users WHERE status = :status", {"status": "active"})
    """
    return get_active_provider().query(sql, params)


def execute(sql: str, params: Optional[Dict[str, Any]] = None) -> int:
    """
    Executes an INSERT, UPDATE, or DELETE statement against the active provider's pool.
    Commits automatically and returns the number of affected rows.

    Example:
        rows_affected = execute("UPDATE stories SET status = :s WHERE id = :id", {"s": "done", "id": 1})
    """
    return get_active_provider().execute(sql, params)
