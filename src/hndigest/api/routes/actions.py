"""Action endpoints — trigger agent work on demand.

These endpoints instantiate agents, run them for one cycle, and return
results. No agents auto-run in server mode. The frontend controls when
work happens.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query

from hndigest.agents.categorizer import CategorizerAgent
from hndigest.agents.collector import CollectorAgent
from hndigest.agents.fetcher import FetcherAgent
from hndigest.agents.orchestrator import OrchestratorAgent
from hndigest.agents.report_builder import ReportBuilderAgent
from hndigest.agents.scorer import ScorerAgent
from hndigest.agents.summarizer import SummarizerAgent
from hndigest.agents.validator import ValidatorAgent
from hndigest.api.deps import get_bus, get_db
from hndigest.bus import (
    CHANNEL_ARTICLE,
    CHANNEL_FETCH_REQUEST,
    CHANNEL_SCORE,
    CHANNEL_STORY,
    CHANNEL_SUMMARIZE_REQUEST,
    CHANNEL_SUMMARY,
    CHANNEL_VALIDATED_SUMMARY,
    MessageBus,
)
from hndigest.models import BusMessage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["actions"])


@router.post("/collect", status_code=200)
async def collect(
    max_stories: int = Query(default=10, ge=1, le=500),
    db: sqlite3.Connection = Depends(get_db),
    bus: MessageBus = Depends(get_bus),
) -> dict[str, Any]:
    """Run one collector poll cycle against the HN API.

    Fetches top stories, persists new ones, publishes to story channel.

    Args:
        max_stories: Maximum stories to collect this cycle.
        db: Database connection.
        bus: Message bus.

    Returns:
        Count of stories collected and list of story IDs.
    """
    story_queue = bus.subscribe(CHANNEL_STORY)
    collector = CollectorAgent(bus=bus, db_conn=db, max_stories=max_stories)
    collector._session = aiohttp.ClientSession()

    try:
        await collector._poll_once()
    finally:
        await collector._session.close()

    collected_ids: list[int] = []
    while not story_queue.empty():
        msg: BusMessage = story_queue.get_nowait()
        collected_ids.append(msg.payload.story_id)

    return {"stories_collected": len(collected_ids), "story_ids": collected_ids}


@router.post("/score", status_code=200)
async def score(
    db: sqlite3.Connection = Depends(get_db),
    bus: MessageBus = Depends(get_bus),
) -> dict[str, Any]:
    """Score all unscored stories.

    Queries stories that don't have a score yet and runs the scorer
    on each.

    Returns:
        Count of stories scored.
    """
    scorer = ScorerAgent(bus=bus, db_conn=db)

    rows = db.execute(
        "SELECT s.id, s.title, s.url, s.score, s.comments, s.posted_at, "
        "s.hn_type, s.endpoints "
        "FROM stories s "
        "LEFT JOIN scores sc ON sc.story_id = s.id "
        "WHERE sc.id IS NULL"
    ).fetchall()

    from hndigest.models import StoryPayload

    scored_count = 0
    for row in rows:
        story_msg = BusMessage(
            type="story",
            timestamp=datetime.now(timezone.utc),
            source="api",
            payload=StoryPayload(
                story_id=row[0],
                title=row[1] or "",
                url=row[2],
                score=row[3] or 0,
                comments=row[4] or 0,
                posted_at=row[5] or "",
                hn_type=row[6] or "story",
                endpoints=json.loads(row[7]) if row[7] else [],
            ),
        )
        await scorer.process(CHANNEL_STORY, story_msg)
        scored_count += 1

    return {"stories_scored": scored_count}


@router.post("/categorize", status_code=200)
async def categorize(
    db: sqlite3.Connection = Depends(get_db),
    bus: MessageBus = Depends(get_bus),
) -> dict[str, Any]:
    """Categorize all uncategorized stories.

    Returns:
        Count of stories categorized.
    """
    categorizer = CategorizerAgent(bus=bus, db_conn=db)

    rows = db.execute(
        "SELECT s.id, s.title, s.url, s.hn_type "
        "FROM stories s "
        "LEFT JOIN categories c ON c.story_id = s.id "
        "WHERE c.id IS NULL"
    ).fetchall()

    from hndigest.models import StoryPayload

    categorized_count = 0
    for row in rows:
        story_msg = BusMessage(
            type="story",
            timestamp=datetime.now(timezone.utc),
            source="api",
            payload=StoryPayload(
                story_id=row[0],
                title=row[1] or "",
                url=row[2],
                hn_type=row[3] or "story",
            ),
        )
        await categorizer.process(CHANNEL_STORY, story_msg)
        categorized_count += 1

    return {"stories_categorized": categorized_count}


@router.post("/orchestrate", status_code=200)
async def orchestrate(
    db: sqlite3.Connection = Depends(get_db),
    bus: MessageBus = Depends(get_bus),
) -> dict[str, Any]:
    """Run orchestrator on scored stories without dispatch decisions.

    Evaluates priority and budget, dispatches fetch requests for
    qualifying stories.

    Returns:
        Counts of dispatched, skipped, and budget_exceeded stories.
    """
    orchestrator = OrchestratorAgent(bus=bus, db_conn=db)
    fetch_queue = bus.subscribe(CHANNEL_FETCH_REQUEST)

    # Find stories that have scores but no orchestrator decision yet
    rows = db.execute(
        "SELECT s.id, s.title, s.url, s.hn_text, s.score, s.comments, "
        "s.posted_at, s.hn_type, s.endpoints, sc.composite "
        "FROM stories s "
        "JOIN scores sc ON sc.story_id = s.id "
        "LEFT JOIN orchestrator_decisions od ON od.story_id = s.id "
        "WHERE od.id IS NULL "
        "ORDER BY sc.composite DESC"
    ).fetchall()

    from hndigest.models import ScoreComponents, ScorePayload, StoryPayload

    for row in rows:
        story_msg = BusMessage(
            type="story",
            timestamp=datetime.now(timezone.utc),
            source="api",
            payload=StoryPayload(
                story_id=row[0],
                title=row[1] or "",
                url=row[2],
                hn_text=row[3],
                score=row[4] or 0,
                comments=row[5] or 0,
                posted_at=row[6] or "",
                hn_type=row[7] or "story",
                endpoints=json.loads(row[8]) if row[8] else [],
            ),
        )
        await orchestrator.process(CHANNEL_STORY, story_msg)

        score_msg = BusMessage(
            type="score",
            timestamp=datetime.now(timezone.utc),
            source="api",
            payload=ScorePayload(
                story_id=row[0],
                composite=row[9] or 0.0,
                components=ScoreComponents(
                    score_velocity=0, comment_velocity=0,
                    front_page_presence=0, recency=0,
                ),
            ),
        )
        await orchestrator.process(CHANNEL_SCORE, score_msg)

    # Count decisions
    decisions = db.execute(
        "SELECT decision, COUNT(*) FROM orchestrator_decisions "
        "GROUP BY decision"
    ).fetchall()
    result = {row[0]: row[1] for row in decisions}

    dispatched = 0
    while not fetch_queue.empty():
        fetch_queue.get_nowait()
        dispatched += 1

    return {
        "dispatched": dispatched,
        "skipped": result.get("skipped", 0),
        "budget_exceeded": result.get("budget_exceeded", 0),
    }


@router.post("/fetch/{story_id}", status_code=200)
async def fetch_story(
    story_id: int,
    db: sqlite3.Connection = Depends(get_db),
    bus: MessageBus = Depends(get_bus),
) -> dict[str, Any]:
    """Fetch and extract article text for a specific story.

    Args:
        story_id: The HN story ID to fetch.

    Returns:
        Fetch status and article text length.
    """
    row = db.execute(
        "SELECT id, title, url, hn_text FROM stories WHERE id = ?",
        (story_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Story {story_id} not found")

    from hndigest.models import FetchRequestPayload

    fetcher = FetcherAgent(bus=bus, db_conn=db)
    fetcher._session = aiohttp.ClientSession()

    fetch_msg = BusMessage(
        type="fetch_request",
        timestamp=datetime.now(timezone.utc),
        source="api",
        payload=FetchRequestPayload(
            story_id=row[0],
            url=row[2],
            hn_text=row[3],
            title=row[1] or "",
            priority=0.0,
        ),
    )

    try:
        await fetcher.process(CHANNEL_FETCH_REQUEST, fetch_msg)
    finally:
        await fetcher._session.close()

    article = db.execute(
        "SELECT fetch_status, LENGTH(text) FROM articles WHERE story_id = ?",
        (story_id,),
    ).fetchone()

    return {
        "story_id": story_id,
        "fetch_status": article[0] if article else "not_fetched",
        "text_length": article[1] if article else 0,
    }


@router.post("/summarize/{story_id}", status_code=200)
async def summarize_story(
    story_id: int,
    db: sqlite3.Connection = Depends(get_db),
    bus: MessageBus = Depends(get_bus),
) -> dict[str, Any]:
    """Summarize a specific story via LLM and validate the summary.

    Runs both summarizer and validator. Returns the final summary
    status (validated, rejected, or no_summary).

    Args:
        story_id: The HN story ID to summarize.

    Returns:
        Summary text and validation status.
    """
    article = db.execute(
        "SELECT story_id FROM articles WHERE story_id = ? AND fetch_status = 'success'",
        (story_id,),
    ).fetchone()
    if article is None:
        raise HTTPException(
            status_code=400,
            detail=f"Story {story_id} has no fetched article. Fetch it first.",
        )

    from hndigest.models import SummarizeRequestPayload

    # Summarize
    summarizer = SummarizerAgent(bus=bus, db_conn=db)
    summary_queue = bus.subscribe(CHANNEL_SUMMARY)

    summarize_msg = BusMessage(
        type="summarize_request",
        timestamp=datetime.now(timezone.utc),
        source="api",
        payload=SummarizeRequestPayload(story_id=story_id, priority=0.0),
    )

    try:
        await summarizer.process(CHANNEL_SUMMARIZE_REQUEST, summarize_msg)
    finally:
        await summarizer._llm.close()

    # Validate if summary was produced
    if not summary_queue.empty():
        summary_msg = summary_queue.get_nowait()

        validator = ValidatorAgent(bus=bus, db_conn=db)
        try:
            await validator.process(CHANNEL_SUMMARY, summary_msg)
        finally:
            await validator._llm.close()

    # Return final state
    row = db.execute(
        "SELECT summary_text, status FROM summaries WHERE story_id = ? "
        "ORDER BY generated_at DESC LIMIT 1",
        (story_id,),
    ).fetchone()

    return {
        "story_id": story_id,
        "summary_text": row[0] if row else "",
        "status": row[1] if row else "no_summary",
    }


@router.post("/pipeline/run", status_code=200)
async def run_pipeline(
    max_stories: int = Query(default=10, ge=1, le=100),
    db: sqlite3.Connection = Depends(get_db),
    bus: MessageBus = Depends(get_bus),
) -> dict[str, Any]:
    """Run the full pipeline once: collect → score → categorize → orchestrate → fetch → summarize → validate → digest.

    This is the main endpoint for the frontend to trigger a complete
    pipeline run and get a digest back.

    Args:
        max_stories: Maximum stories to collect.

    Returns:
        Pipeline results including story count, fetch count, summary count,
        and the generated digest markdown.
    """
    results: dict[str, Any] = {}

    # 1. Collect
    story_queue = bus.subscribe(CHANNEL_STORY)
    collector = CollectorAgent(bus=bus, db_conn=db, max_stories=max_stories)
    collector._session = aiohttp.ClientSession()
    try:
        await collector._poll_once()
    finally:
        await collector._session.close()

    story_msgs: list[BusMessage] = []
    while not story_queue.empty():
        story_msgs.append(story_queue.get_nowait())
    results["collected"] = len(story_msgs)

    # 2. Score + Categorize each story
    scorer = ScorerAgent(bus=bus, db_conn=db)
    categorizer = CategorizerAgent(bus=bus, db_conn=db)
    score_queue = bus.subscribe(CHANNEL_SCORE)

    for msg in story_msgs:
        await scorer.process(CHANNEL_STORY, msg)
        await categorizer.process(CHANNEL_STORY, msg)

    score_msgs: list[BusMessage] = []
    while not score_queue.empty():
        score_msgs.append(score_queue.get_nowait())
    results["scored"] = len(score_msgs)

    # 3. Orchestrate
    orchestrator = OrchestratorAgent(bus=bus, db_conn=db)
    fetch_queue = bus.subscribe(CHANNEL_FETCH_REQUEST)

    for msg in story_msgs:
        await orchestrator.process(CHANNEL_STORY, msg)
    for msg in score_msgs:
        await orchestrator.process(CHANNEL_SCORE, msg)

    fetch_msgs: list[BusMessage] = []
    while not fetch_queue.empty():
        fetch_msgs.append(fetch_queue.get_nowait())
    results["fetch_requests"] = len(fetch_msgs)

    # 4. Fetch
    article_queue = bus.subscribe(CHANNEL_ARTICLE)
    fetcher = FetcherAgent(bus=bus, db_conn=db)
    fetcher._session = aiohttp.ClientSession()
    try:
        for msg in fetch_msgs:
            await fetcher.process(CHANNEL_FETCH_REQUEST, msg)
    finally:
        await fetcher._session.close()

    article_msgs: list[BusMessage] = []
    while not article_queue.empty():
        article_msgs.append(article_queue.get_nowait())
    results["articles_fetched"] = len(article_msgs)

    # 5. Orchestrator dispatches summarize requests for fetched articles
    summarize_queue = bus.subscribe(CHANNEL_SUMMARIZE_REQUEST)
    for msg in article_msgs:
        await orchestrator.process(CHANNEL_ARTICLE, msg)

    summarize_msgs: list[BusMessage] = []
    while not summarize_queue.empty():
        summarize_msgs.append(summarize_queue.get_nowait())

    # 6. Summarize + Validate
    summary_queue = bus.subscribe(CHANNEL_SUMMARY)
    summarizer = SummarizerAgent(bus=bus, db_conn=db)
    validator = ValidatorAgent(bus=bus, db_conn=db)

    try:
        for msg in summarize_msgs:
            await summarizer.process(CHANNEL_SUMMARIZE_REQUEST, msg)

        validated_count = 0
        while not summary_queue.empty():
            summary_msg = summary_queue.get_nowait()
            await validator.process(CHANNEL_SUMMARY, summary_msg)
            validated_count += 1
        results["summaries_processed"] = validated_count
    finally:
        await summarizer._llm.close()
        await validator._llm.close()

    # 7. Build digest
    report_builder = ReportBuilderAgent(bus=bus, db_conn=db)
    digest = await report_builder._build_digest()

    if digest and digest.get("story_count", 0) > 0:
        await report_builder._persist_and_publish(digest)
        results["digest_story_count"] = digest["story_count"]
        results["digest_md"] = digest["content_md"]
    else:
        results["digest_story_count"] = 0
        results["digest_md"] = ""

    return results
