"""Dialect-aware column types for cross-database compatibility."""
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB as PG_JSONB


def JSONB() -> type:  # type: ignore[return]
    """Returns JSONB for PostgreSQL, JSON for everything else (SQLite for tests)."""
    from app.core.config import get_settings
    settings = get_settings()
    if "postgresql" in settings.database_url:
        return PG_JSONB
    return JSON
