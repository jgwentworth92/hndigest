"""Agent status and system health endpoints.

In server mode, agents don't auto-run. The health endpoint reports
API server status and database connectivity. The agents endpoint
shows the last known state from the database (orchestrator decisions,
story counts, etc.) rather than live agent heartbeats.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request

from hndigest.api.deps import get_db
from hndigest.api.schemas import CategoryCount, HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agents"])


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> dict[str, Any]:
    """Return system health: API uptime and database status.

    Args:
        request: The incoming FastAPI request.

    Returns:
        Health response with status and uptime.
    """
    started_at = getattr(request.app.state, "started_at_monotonic", None)
    uptime = round(time.monotonic() - started_at, 1) if started_at else 0.0

    # Check DB connectivity
    db_ok = False
    try:
        db: sqlite3.Connection = request.app.state.db_conn
        db.execute("SELECT 1").fetchone()
        db_ok = True
    except Exception:
        pass

    return {
        "status": "healthy" if db_ok else "degraded",
        "uptime_seconds": uptime,
        "agents": {},
        "mode": "server",
        "database": "connected" if db_ok else "error",
    }


@router.get("/categories", response_model=list[CategoryCount])
async def category_breakdown(
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return category counts for today's stories.

    Args:
        db: Database connection.

    Returns:
        List of category/count pairs ordered by count descending.
    """
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_start = today_str + "T00:00:00+00:00"

    rows = db.execute(
        "SELECT c.category, COUNT(DISTINCT c.story_id) AS cnt "
        "FROM categories c "
        "JOIN stories s ON s.id = c.story_id "
        "WHERE s.posted_at >= ? "
        "GROUP BY c.category "
        "ORDER BY cnt DESC",
        (today_start,),
    ).fetchall()

    return [{"category": row[0], "count": row[1]} for row in rows]
