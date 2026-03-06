"""
db/database.py

Responsibility: Creates the SQLite engine, session factory, and exposes
create_all() for startup table initialisation.
Does NOT: define table models, run queries, or contain business logic.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Generator

from sqlmodel import Session, SQLModel, create_engine

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

# NOTE: /config is the Docker volume mount point so the DB survives restarts.
# During local dev, the path resolves to config/ddns.db inside the project.
_DB_PATH = os.getenv("DB_PATH", "/config/ddns.db")
_DB_URL = f"sqlite:///{_DB_PATH}"

# check_same_thread=False is required for SQLite + FastAPI's async workers.
engine = create_engine(
    _DB_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def init_db() -> None:
    """
    Creates all tables defined in SQLModel metadata if they don't exist,
    then runs incremental column migrations for existing databases.

    Called once from the FastAPI lifespan function in app.py.

    Returns:
        None
    """
    # Ensure the config directory exists (needed when running outside Docker)
    db_dir = os.path.dirname(_DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    SQLModel.metadata.create_all(engine)
    _run_migrations()
    logger.info("Database initialised at %s", _DB_PATH)


def _run_migrations() -> None:
    """
    Applies incremental schema changes to existing SQLite databases.

    SQLAlchemy's create_all() does not add new columns to existing tables,
    so each new column must be added here with an existence check.

    Returns:
        None
    """
    with engine.connect() as conn:
        # NOTE: Using raw SQL for ALTER TABLE is the accepted SQLite migration
        # pattern — SQLModel/Alembic would be overkill for a single container app.
        existing = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info(appconfig)")
        }
        # NOTE: Keep kubeconfig_path migration so existing databases that already
        # have the column do not fail. No-op if the column is already present.
        if "kubeconfig_path" not in existing:
            conn.exec_driver_sql(
                "ALTER TABLE appconfig ADD COLUMN kubeconfig_path TEXT NOT NULL DEFAULT ''"
            )
            logger.info("Migration: added 'kubeconfig_path' column to appconfig table.")
        if "k8s_enabled" not in existing:
            conn.exec_driver_sql(
                "ALTER TABLE appconfig ADD COLUMN k8s_enabled INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Migration: added 'k8s_enabled' column to appconfig table.")
        if "unifi_api_key" not in existing:
            conn.exec_driver_sql(
                "ALTER TABLE appconfig ADD COLUMN unifi_api_key TEXT NOT NULL DEFAULT ''"
            )
            logger.info("Migration: added 'unifi_api_key' column to appconfig table.")
        if "unifi_site_id" not in existing:
            conn.exec_driver_sql(
                "ALTER TABLE appconfig ADD COLUMN unifi_site_id TEXT NOT NULL DEFAULT ''"
            )
            logger.info("Migration: added 'unifi_site_id' column to appconfig table.")
        if "unifi_host" not in existing:
            conn.exec_driver_sql(
                "ALTER TABLE appconfig ADD COLUMN unifi_host TEXT NOT NULL DEFAULT ''"
            )
            logger.info("Migration: added 'unifi_host' column to appconfig table.")
        if "unifi_default_ip" not in existing:
            conn.exec_driver_sql(
                "ALTER TABLE appconfig ADD COLUMN unifi_default_ip TEXT NOT NULL DEFAULT ''"
            )
            logger.info("Migration: added 'unifi_default_ip' column to appconfig table.")
        if "unifi_enabled" not in existing:
            conn.exec_driver_sql(
                "ALTER TABLE appconfig ADD COLUMN unifi_enabled INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Migration: added 'unifi_enabled' column to appconfig table.")

        # --- recordconfig table migrations ---
        rc_existing = {
            row[1] for row in conn.exec_driver_sql("PRAGMA table_info(recordconfig)")
        }
        if "unifi_static_ip" not in rc_existing:
            conn.exec_driver_sql(
                "ALTER TABLE recordconfig ADD COLUMN unifi_static_ip TEXT NOT NULL DEFAULT ''"
            )
            logger.info("Migration: added 'unifi_static_ip' column to recordconfig table.")
        if "unifi_local_enabled" not in rc_existing:
            conn.exec_driver_sql(
                "ALTER TABLE recordconfig ADD COLUMN unifi_local_enabled INTEGER NOT NULL DEFAULT 0"
            )
            logger.info("Migration: added 'unifi_local_enabled' column to recordconfig table.")
        if "unifi_local_static_ip" not in rc_existing:
            conn.exec_driver_sql(
                "ALTER TABLE recordconfig ADD COLUMN unifi_local_static_ip TEXT NOT NULL DEFAULT ''"
            )
            logger.info("Migration: added 'unifi_local_static_ip' column to recordconfig table.")

        # WORKAROUND: cf_enabled was incorrectly defaulted to False in earlier versions.
        # Any row written by the old model has cf_enabled=0, which silently disabled
        # Cloudflare DDNS for every record.  Correct all such rows on startup.
        if "cf_enabled" in rc_existing:
            conn.exec_driver_sql("UPDATE recordconfig SET cf_enabled = 1 WHERE cf_enabled = 0")
            logger.debug("Migration: corrected cf_enabled=0 rows in recordconfig table.")

        conn.commit()


def get_session() -> Generator[Session, None, None]:
    """
    FastAPI dependency that yields a SQLModel Session for the current request.

    Usage in route handlers:
        session: Session = Depends(get_session)

    The session is automatically closed when the request completes.

    Yields:
        A SQLModel Session bound to the application engine.
    """
    with Session(engine) as session:
        yield session
