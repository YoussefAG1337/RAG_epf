"""Database engine and SQLAlchemy declarative base helpers."""

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from app.config import Settings


def create_database_engine(settings: Settings) -> Engine:
    """Create a process-local engine without opening a connection at import time."""

    return create_engine(settings.database_url, pool_pre_ping=True)
