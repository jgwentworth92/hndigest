"""Digest endpoints: list, detail, and on-demand generation."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from hndigest.api.deps import get_bus, get_db
from hndigest.api.schemas import DigestDetail, DigestGenerateResponse, DigestSummary
from hndigest.agents.report_builder import ReportBuilderAgent
from hndigest.bus import MessageBus

logger = logging.getLogger(__name__)

router = APIRouter(tags=["digests"])


def _row_to_summary(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a digests table row to a DigestSummary-compatible dict.

    Args:
        row: A sqlite3.Row from the digests table.

    Returns:
        Dict with keys matching DigestSummary fields.
    """
    return {
        "id": row["id"],
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "story_count": row["story_count"],
        "created_at": row["created_at"],
    }


def _row_to_detail(row: sqlite3.Row) -> dict[str, Any]:
    """Convert a digests table row to a DigestDetail-compatible dict.

    Args:
        row: A sqlite3.Row from the digests table.

    Returns:
        Dict with keys matching DigestDetail fields, with content_json parsed.
    """
    content_json_raw = row["content_json"]
    try:
        content_json = json.loads(content_json_raw)
    except (json.JSONDecodeError, TypeError):
        content_json = {}

    return {
        "id": row["id"],
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "story_count": row["story_count"],
        "created_at": row["created_at"],
        "content_json": content_json,
        "content_md": row["content_md"],
    }


@router.get("/digests", response_model=list[DigestSummary])
async def list_digests(
    limit: int = Query(default=20, ge=1, le=100),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, Any]]:
    """List recent digests ordered by creation time descending.

    Args:
        limit: Maximum number of digests to return (1-100, default 20).
        db: Injected database connection.

    Returns:
        List of digest summaries.
    """
    try:
        db.row_factory = sqlite3.Row
        cursor = db.execute(
            "SELECT id, period_start, period_end, story_count, created_at "
            "FROM digests ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        rows = cursor.fetchall()
        return [_row_to_summary(row) for row in rows]
    except sqlite3.Error as exc:
        logger.exception("Failed to query digests")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/digests/latest", response_model=DigestDetail)
async def get_latest_digest(
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Return the most recently created digest.

    Args:
        db: Injected database connection.

    Returns:
        Full digest detail including parsed JSON content and markdown.

    Raises:
        HTTPException: 404 if no digests exist.
    """
    try:
        db.row_factory = sqlite3.Row
        cursor = db.execute(
            "SELECT id, period_start, period_end, story_count, created_at, "
            "content_json, content_md "
            "FROM digests ORDER BY created_at DESC LIMIT 1"
        )
        row = cursor.fetchone()
    except sqlite3.Error as exc:
        logger.exception("Failed to query latest digest")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if row is None:
        raise HTTPException(status_code=404, detail="No digests found")

    return _row_to_detail(row)


@router.get("/digests/{digest_id}", response_model=DigestDetail)
async def get_digest(
    digest_id: int,
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Return a specific digest by its database ID.

    Args:
        digest_id: Primary key of the digest.
        db: Injected database connection.

    Returns:
        Full digest detail including parsed JSON content and markdown.

    Raises:
        HTTPException: 404 if the digest does not exist.
    """
    try:
        db.row_factory = sqlite3.Row
        cursor = db.execute(
            "SELECT id, period_start, period_end, story_count, created_at, "
            "content_json, content_md "
            "FROM digests WHERE id = ?",
            (digest_id,),
        )
        row = cursor.fetchone()
    except sqlite3.Error as exc:
        logger.exception("Failed to query digest %d", digest_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    if row is None:
        raise HTTPException(status_code=404, detail=f"Digest {digest_id} not found")

    return _row_to_detail(row)


@router.post("/digests/generate", response_model=DigestGenerateResponse, status_code=201)
async def generate_digest(
    db: sqlite3.Connection = Depends(get_db),
    bus: MessageBus = Depends(get_bus),
) -> dict[str, Any]:
    """Trigger on-demand digest generation from current data.

    Args:
        db: Injected database connection.
        bus: Injected message bus.

    Returns:
        Generated digest metadata including story count and markdown content.
    """
    try:
        builder = ReportBuilderAgent(bus=bus, db_conn=db)
        digest = await builder._build_digest()

        if digest["story_count"] > 0:
            await builder._persist_and_publish(digest)

        return {
            "story_count": digest["story_count"],
            "content_md": digest["content_md"],
            "period_start": digest["period_start"],
            "period_end": digest["period_end"],
        }
    except Exception as exc:
        logger.exception("Failed to generate digest")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
