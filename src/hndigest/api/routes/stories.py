"""Story endpoints: list with filters and full detail."""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from hndigest.api.deps import get_db
from hndigest.api.schemas import StoryDetail, StorySummary

logger = logging.getLogger(__name__)

router = APIRouter(tags=["stories"])


@router.get("/stories", response_model=list[StorySummary])
async def list_stories(
    category: str | None = None,
    min_score: float | None = None,
    since: str | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    db: sqlite3.Connection = Depends(get_db),
) -> list[dict[str, Any]]:
    """List stories with optional filters for category, minimum score, and date.

    Joins stories with the latest score per story and assigned categories.
    Results are ordered by composite score descending.

    Args:
        category: Filter by category slug (e.g. "ai_ml", "webdev").
        min_score: Minimum composite score threshold.
        since: ISO 8601 UTC timestamp; only return stories posted at or after this time.
        limit: Maximum number of stories to return (1-500, default 50).
        db: Injected database connection.

    Returns:
        List of story summaries with composite score and categories.
    """
    try:
        db.row_factory = sqlite3.Row

        # Build the query with optional WHERE clauses.
        query = """
            SELECT
                s.id,
                s.title,
                s.url,
                s.score,
                s.comments,
                s.hn_type,
                s.posted_at,
                COALESCE(sc.composite, NULL) AS composite_score
            FROM stories s
            LEFT JOIN (
                SELECT story_id, composite,
                       ROW_NUMBER() OVER (PARTITION BY story_id ORDER BY scored_at DESC) AS rn
                FROM scores
            ) sc ON sc.story_id = s.id AND sc.rn = 1
        """

        conditions: list[str] = []
        params: list[Any] = []

        if category is not None:
            conditions.append(
                "s.id IN (SELECT story_id FROM categories WHERE category = ?)"
            )
            params.append(category)

        if min_score is not None:
            conditions.append("COALESCE(sc.composite, 0.0) >= ?")
            params.append(min_score)

        if since is not None:
            conditions.append("s.posted_at >= ?")
            params.append(since)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY COALESCE(sc.composite, 0.0) DESC LIMIT ?"
        params.append(limit)

        cursor = db.execute(query, params)
        rows = cursor.fetchall()

        # Collect all story IDs to batch-fetch categories.
        story_ids = [row["id"] for row in rows]
        categories_map: dict[int, list[str]] = {}

        if story_ids:
            placeholders = ",".join("?" * len(story_ids))
            cat_cursor = db.execute(
                f"SELECT story_id, category FROM categories WHERE story_id IN ({placeholders})",
                story_ids,
            )
            for cat_row in cat_cursor.fetchall():
                sid = cat_row["story_id"]
                if sid not in categories_map:
                    categories_map[sid] = []
                categories_map[sid].append(cat_row["category"])

        result = []
        for row in rows:
            result.append({
                "id": row["id"],
                "title": row["title"],
                "url": row["url"],
                "score": row["score"],
                "comments": row["comments"],
                "hn_type": row["hn_type"],
                "posted_at": row["posted_at"],
                "composite_score": row["composite_score"],
                "categories": categories_map.get(row["id"], []),
            })

        return result
    except sqlite3.Error as exc:
        logger.exception("Failed to query stories")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/stories/{story_id}", response_model=StoryDetail)
async def get_story(
    story_id: int,
    db: sqlite3.Connection = Depends(get_db),
) -> dict[str, Any]:
    """Return full detail for a specific story including article, summary, and scoring data.

    Joins the story with its article (latest fetch), summary (latest),
    validation (latest for the summary), score breakdown (latest), and
    all assigned categories.

    Args:
        story_id: Hacker News item ID.
        db: Injected database connection.

    Returns:
        Full story detail with article text, summary, validation, and score components.

    Raises:
        HTTPException: 404 if the story does not exist.
    """
    try:
        db.row_factory = sqlite3.Row

        # Fetch the base story.
        cursor = db.execute(
            "SELECT id, title, url, score, comments, hn_type, posted_at "
            "FROM stories WHERE id = ?",
            (story_id,),
        )
        story_row = cursor.fetchone()

        if story_row is None:
            raise HTTPException(status_code=404, detail=f"Story {story_id} not found")

        # Fetch latest article.
        art_cursor = db.execute(
            "SELECT text, fetch_status FROM articles "
            "WHERE story_id = ? ORDER BY fetched_at DESC LIMIT 1",
            (story_id,),
        )
        art_row = art_cursor.fetchone()

        # Fetch latest summary.
        sum_cursor = db.execute(
            "SELECT id, summary_text, status FROM summaries "
            "WHERE story_id = ? ORDER BY generated_at DESC LIMIT 1",
            (story_id,),
        )
        sum_row = sum_cursor.fetchone()

        # Fetch validation for the latest summary.
        validation_result: str | None = None
        if sum_row is not None:
            val_cursor = db.execute(
                "SELECT result FROM validations "
                "WHERE summary_id = ? ORDER BY validated_at DESC LIMIT 1",
                (sum_row["id"],),
            )
            val_row = val_cursor.fetchone()
            if val_row is not None:
                validation_result = val_row["result"]

        # Fetch latest score breakdown.
        score_cursor = db.execute(
            "SELECT composite, score_velocity, comment_velocity, "
            "front_page_presence, recency FROM scores "
            "WHERE story_id = ? ORDER BY scored_at DESC LIMIT 1",
            (story_id,),
        )
        score_row = score_cursor.fetchone()

        # Fetch categories.
        cat_cursor = db.execute(
            "SELECT category FROM categories WHERE story_id = ?",
            (story_id,),
        )
        categories = [r["category"] for r in cat_cursor.fetchall()]

        # Build score components dict.
        score_components: dict[str, Any] | None = None
        composite_score: float | None = None
        if score_row is not None:
            composite_score = score_row["composite"]
            score_components = {
                "score_velocity": score_row["score_velocity"],
                "comment_velocity": score_row["comment_velocity"],
                "front_page_presence": score_row["front_page_presence"],
                "recency": score_row["recency"],
            }

        return {
            "id": story_row["id"],
            "title": story_row["title"],
            "url": story_row["url"],
            "score": story_row["score"],
            "comments": story_row["comments"],
            "hn_type": story_row["hn_type"],
            "posted_at": story_row["posted_at"],
            "composite_score": composite_score,
            "categories": categories,
            "article_text": art_row["text"] if art_row else None,
            "article_fetch_status": art_row["fetch_status"] if art_row else None,
            "summary_text": sum_row["summary_text"] if sum_row else None,
            "summary_status": sum_row["status"] if sum_row else None,
            "validation_result": validation_result,
            "score_components": score_components,
        }
    except HTTPException:
        raise
    except sqlite3.Error as exc:
        logger.exception("Failed to query story %d", story_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
