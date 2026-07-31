from __future__ import annotations

import os
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import settings
from .models import Base

# NOTE: no filesystem side effects at import time. Creating the data directory
# here would make the module unimportable wherever /data isn't writable --
# breaking tests, tooling and any non-container run. init_db() owns that.

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# Columns added after v0.1.0. create_all() only creates missing *tables*, so
# existing databases need these patched in by hand. Keeping this tiny and
# additive avoids pulling in Alembic for a single-user SQLite app.
_ADDED_COLUMNS: list[tuple[str, str, str]] = [
    # (table, column, DDL type + default)
    ("sessions", "summarized_upto_id", "INTEGER NOT NULL DEFAULT 0"),
]


def _apply_additive_migrations() -> None:
    if not settings.database_url.startswith("sqlite"):
        return
    with engine.begin() as conn:
        for table, column, ddl in _ADDED_COLUMNS:
            existing = {
                row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            if not existing:  # table doesn't exist yet; create_all handles it
                continue
            if column not in existing:
                conn.exec_driver_sql(
                    f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"
                )


def init_db() -> None:
    """Create the data dir, tables, and apply additive migrations.

    Called from the app lifespan -- i.e. at startup, not at import.
    """
    os.makedirs(settings.data_dir, exist_ok=True)
    Base.metadata.create_all(engine)
    _apply_additive_migrations()


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
