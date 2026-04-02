"""FastAPI dependency functions for hndigest route handlers.

Provides injectable dependencies for the database connection and
message bus stored in ``app.state`` during the lifespan.
"""

from __future__ import annotations

import sqlite3

from fastapi import Request

from hndigest.bus import MessageBus


def get_db(request: Request) -> sqlite3.Connection:
    """Return the active database connection from app state.

    Args:
        request: The incoming FastAPI request.

    Returns:
        An open SQLite connection with all migrations applied.
    """
    return request.app.state.db_conn


def get_bus(request: Request) -> MessageBus:
    """Return the message bus from app state.

    Args:
        request: The incoming FastAPI request.

    Returns:
        The shared MessageBus instance.
    """
    return request.app.state.bus
