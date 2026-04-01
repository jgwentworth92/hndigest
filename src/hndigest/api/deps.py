"""FastAPI dependency functions for hndigest route handlers.

Provides injectable dependencies for accessing the supervisor and
database connection stored in ``app.state`` during the lifespan.
"""

from __future__ import annotations

import sqlite3

from fastapi import Request

from hndigest.supervisor import Supervisor


def get_supervisor(request: Request) -> Supervisor:
    """Return the running supervisor instance from app state.

    Args:
        request: The incoming FastAPI request.

    Returns:
        The active Supervisor managing all agents.
    """
    return request.app.state.supervisor


def get_db(request: Request) -> sqlite3.Connection:
    """Return the active database connection.

    Prefers the supervisor's own connection (created during
    ``supervisor.start()``) over the initial connection created in the
    lifespan, since the supervisor connection has the real bus wired up.
    Falls back to ``app.state.db_conn`` when the supervisor is ``None``
    (e.g. in test mode).

    Args:
        request: The incoming FastAPI request.

    Returns:
        An open SQLite connection with all migrations applied.
    """
    supervisor: Supervisor | None = request.app.state.supervisor
    if supervisor is not None and supervisor.db is not None:
        return supervisor.db
    return request.app.state.db_conn
