"""Database initialization and migration runner for hndigest.

Uses stdlib sqlite3 only. Migrations are plain SQL files stored in
db/migrations/, executed in filename order on startup. A schema_version
table (bootstrapped in code) tracks which migrations have been applied.
"""

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

from hndigest.paths import MIGRATIONS_DIR

_MIGRATIONS_DIR = MIGRATIONS_DIR


def _bootstrap_schema_version(conn: sqlite3.Connection) -> None:
    """Create the schema_version table if it does not exist.

    This table is managed directly in code rather than via a migration
    file so that the migration runner itself has somewhere to record
    applied migrations from the very first run.

    Args:
        conn: An open SQLite connection.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            filename    TEXT    NOT NULL UNIQUE,
            applied_at  TEXT    NOT NULL
        )
        """
    )
    conn.commit()


def _applied_migrations(conn: sqlite3.Connection) -> set[str]:
    """Return the set of migration filenames already applied.

    Args:
        conn: An open SQLite connection with the schema_version table.

    Returns:
        A set of filename strings that have already been executed.
    """
    cursor = conn.execute("SELECT filename FROM schema_version")
    return {row[0] for row in cursor.fetchall()}


def _discover_migrations(migrations_dir: Path) -> list[Path]:
    """Scan the migrations directory for .sql files, sorted by name.

    Args:
        migrations_dir: Path to the directory containing SQL migration files.

    Returns:
        A sorted list of Path objects for each .sql file found.
    """
    if not migrations_dir.is_dir():
        logger.warning("Migrations directory not found: %s", migrations_dir)
        return []
    return sorted(migrations_dir.glob("*.sql"))


def _apply_migration(conn: sqlite3.Connection, path: Path) -> None:
    """Execute a single migration file and record it in schema_version.

    The migration SQL is executed via ``executescript`` which handles
    multiple statements and implicit transaction management.  After the
    migration DDL succeeds, the filename is recorded in schema_version
    within its own transaction so the two operations are atomic with
    respect to future startup checks.

    Args:
        conn: An open SQLite connection.
        path: Path to the .sql migration file.

    Raises:
        Exception: Re-raised after logging if the migration fails.
    """
    sql = path.read_text(encoding="utf-8")
    now = datetime.now(timezone.utc).isoformat()

    try:
        # executescript commits any open transaction then runs the SQL.
        # This is appropriate for DDL migrations.
        conn.executescript(sql)
        # Record the migration in a separate transaction.
        conn.execute(
            "INSERT INTO schema_version (filename, applied_at) VALUES (?, ?)",
            (path.name, now),
        )
        conn.commit()
        logger.info("Applied migration: %s", path.name)
    except Exception:
        logger.exception("Failed to apply migration: %s", path.name)
        raise


def init_db(
    db_path: str | Path,
    migrations_dir: Path | None = None,
) -> sqlite3.Connection:
    """Open or create the SQLite database and run pending migrations.

    Enables WAL mode and foreign key enforcement. Bootstraps the
    schema_version tracking table, then scans the migrations directory
    for .sql files and applies any that have not yet been recorded.

    Args:
        db_path: Filesystem path for the SQLite database file.
            Use ":memory:" for an in-memory database (useful in tests).
        migrations_dir: Optional override for the migrations directory.
            Defaults to ``<project_root>/db/migrations/``.

    Returns:
        An open ``sqlite3.Connection`` with all migrations applied,
        WAL mode enabled, and foreign keys enforced.
    """
    if migrations_dir is None:
        migrations_dir = _MIGRATIONS_DIR

    logger.info("Opening database: %s", db_path)
    conn = sqlite3.connect(str(db_path))

    # Enable WAL mode for better concurrent read performance.
    conn.execute("PRAGMA journal_mode=WAL")
    # Enforce foreign key constraints.
    conn.execute("PRAGMA foreign_keys=ON")

    _bootstrap_schema_version(conn)

    applied = _applied_migrations(conn)
    pending = [
        p for p in _discover_migrations(migrations_dir) if p.name not in applied
    ]

    if not pending:
        logger.info("Database schema is up to date.")
    else:
        logger.info("%d pending migration(s) to apply.", len(pending))
        for migration_path in pending:
            _apply_migration(conn, migration_path)

    return conn
