"""Agent and health endpoints: agent registry, system health, and category breakdown."""

from __future__ import annotations

import logging
import sqlite3
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from hndigest.api.deps import get_db, get_supervisor
from hndigest.api.schemas import AgentStatus, CategoryCount, HealthResponse
from hndigest.supervisor import Supervisor

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agents"])


@router.get("/health", response_model=HealthResponse)
async def health(
    request: Request,
    supervisor: Supervisor = Depends(get_supervisor),
) -> dict[str, Any]:
    """Return system health including agent statuses and uptime.

    Computes uptime from the monotonic start time stored in app.state
    during the lifespan startup. Reports overall status as "healthy"
    when all agents are running, or "degraded" otherwise.

    Args:
        request: The incoming FastAPI request (used to access app.state).
        supervisor: Injected supervisor instance.

    Returns:
        Health response with overall status, uptime, and per-agent statuses.
    """
    if supervisor is None:
        started_at = getattr(request.app.state, "started_at_monotonic", None)
        uptime = round(time.monotonic() - started_at, 1) if started_at else 0.0
        return {"status": "no_supervisor", "uptime_seconds": uptime, "agents": {}}

    statuses = supervisor.agent_statuses

    # Compute uptime from the monotonic start time stored during lifespan.
    started_at = getattr(request.app.state, "started_at_monotonic", None)
    if started_at is not None:
        uptime_seconds = round(time.monotonic() - started_at, 1)
    else:
        uptime_seconds = 0.0

    # Determine overall status.
    all_running = all(s["status"] == "running" for s in statuses.values())
    overall = "healthy" if all_running else "degraded"

    agents = {
        name: AgentStatus(
            name=name,
            status=info["status"],
            last_heartbeat_ago=info["last_heartbeat_ago"],
            messages_processed=info["messages_processed"],
            restart_count=info["restart_count"],
        )
        for name, info in statuses.items()
    }

    return {
        "status": overall,
        "uptime_seconds": uptime_seconds,
        "agents": agents,
    }


@router.get("/agents", response_model=dict[str, AgentStatus])
async def list_agents(
    supervisor: Supervisor = Depends(get_supervisor),
) -> dict[str, AgentStatus]:
    """Return the current status of all registered agents.

    Args:
        supervisor: Injected supervisor instance.

    Returns:
        Mapping of agent name to its current status snapshot.
    """
    statuses = supervisor.agent_statuses
    return {
        name: AgentStatus(
            name=name,
            status=info["status"],
            last_heartbeat_ago=info["last_heartbeat_ago"],
            messages_processed=info["messages_processed"],
            restart_count=info["restart_count"],
        )
        for name, info in statuses.items()
    }


@router.get("/categories", response_model=list[CategoryCount])
async def category_breakdown(
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return the count of distinct stories per category for today.

    Groups categories assigned today by category slug and counts distinct
    story IDs in each group.

    Args:
        db: Injected database connection.

    Returns:
        List of category-count pairs ordered by count descending.
    """
    try:
        db.row_factory = sqlite3.Row
        cursor = db.execute(
            "SELECT category, COUNT(DISTINCT story_id) AS count "
            "FROM categories "
            "WHERE date(categorized_at) = date('now') "
            "GROUP BY category "
            "ORDER BY count DESC"
        )
        rows = cursor.fetchall()
        return [{"category": row["category"], "count": row["count"]} for row in rows]
    except sqlite3.Error as exc:
        logger.exception("Failed to query category breakdown")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
