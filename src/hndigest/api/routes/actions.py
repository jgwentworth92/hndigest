"""Action endpoints — trigger agent work on demand (fire-and-forget).

These endpoints spawn agent work as background asyncio tasks, return
``202 Accepted`` immediately with a ``run_id``, and stream progress
to connected WebSocket clients via the message bus.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from hndigest.agents.categorizer import CategorizerAgent
from hndigest.agents.collector import CollectorAgent
from hndigest.agents.fetcher import FetcherAgent
from hndigest.agents.orchestrator import OrchestratorAgent
from hndigest.agents.report_builder import ReportBuilderAgent
from hndigest.agents.scorer import ScorerAgent
from hndigest.agents.summarizer import SummarizerAgent
from hndigest.agents.validator import ValidatorAgent
from hndigest.api.deps import get_bus, get_db
from hndigest.api.schemas import PipelineProgress, RunStatus
from hndigest.bus import (
    CHANNEL_ARTICLE,
    CHANNEL_FETCH_REQUEST,
    CHANNEL_SCORE,
    CHANNEL_STORY,
    CHANNEL_SUMMARIZE_REQUEST,
    CHANNEL_SUMMARY,
    CHANNEL_SYSTEM,
    CHANNEL_VALIDATED_SUMMARY,
    MessageBus,
)
from hndigest.models import (
    BusMessage,
    FetchRequestPayload,
    PipelineProgressPayload,
    ScoreComponents,
    ScorePayload,
    StoryPayload,
    SummarizeRequestPayload,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["actions"])


@dataclasses.dataclass
class RunEntry:
    """Metadata for a tracked background run."""

    task: asyncio.Task[None]
    run_type: str
    started_at: str
    progress: PipelineProgress | None = None


def _new_run_id() -> str:
    """Generate a short unique run identifier.

    Returns:
        A 12-character hex string derived from a UUID4.
    """
    return uuid.uuid4().hex[:12]


def _register_run(
    request: Request,
    run_id: str,
    task: asyncio.Task[None],
    run_type: str,
    progress: PipelineProgress | None = None,
) -> None:
    """Store a RunEntry in app.state.active_runs.

    Args:
        request: The incoming FastAPI request (for app.state access).
        run_id: Unique run identifier.
        task: The background asyncio task.
        run_type: Action type (e.g. "collect", "pipeline").
        progress: Optional pipeline progress tracker.
    """
    request.app.state.active_runs[run_id] = RunEntry(
        task=task,
        run_type=run_type,
        started_at=datetime.now(timezone.utc).isoformat(),
        progress=progress,
    )


def _remove_run(request: Request, run_id: str) -> None:
    """Remove a completed run from app.state.active_runs.

    Args:
        request: The incoming FastAPI request.
        run_id: The run to remove.
    """
    request.app.state.active_runs.pop(run_id, None)


def _task_status(task: asyncio.Task[None]) -> str:
    """Determine the status string for a background task.

    Handles CancelledError safely when checking for exceptions.

    Args:
        task: The asyncio task to inspect.

    Returns:
        "running", "completed", "cancelled", or "failed".
    """
    if not task.done():
        return "running"
    if task.cancelled():
        return "cancelled"
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return "cancelled"
    return "failed" if exc else "completed"


@router.post("/collect", status_code=202)
async def collect(
    request: Request,
    max_stories: int = Query(default=10, ge=1, le=500),
    db: sqlite3.Connection = Depends(get_db),
    bus: MessageBus = Depends(get_bus),
) -> dict[str, str]:
    """Trigger a collector poll cycle as a background task.

    Spawns an asyncio task that fetches top stories from the HN API,
    persists new ones, and publishes to the story channel. Progress
    streams to WebSocket clients via the bus.

    Args:
        request: The incoming FastAPI request (for app.state access).
        max_stories: Maximum stories to collect this cycle.
        db: Database connection.
        bus: Message bus.

    Returns:
        A dict with ``run_id`` and ``status`` ("started").
    """
    run_id = _new_run_id()

    async def _run() -> None:
        try:
            collector = CollectorAgent(bus=bus, db_conn=db, max_stories=max_stories)
            collector._session = aiohttp.ClientSession()
            try:
                await collector._poll_once()
            finally:
                await collector._session.close()
        except Exception:
            logger.exception("collect run %s failed", run_id)
        finally:
            _remove_run(request, run_id)

    task = asyncio.create_task(_run())
    _register_run(request, run_id, task, "collect")
    return {"run_id": run_id, "status": "started"}


@router.post("/score", status_code=202)
async def score(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    bus: MessageBus = Depends(get_bus),
) -> dict[str, str]:
    """Score all unscored stories as a background task.

    Queries stories without a score and runs the scorer on each.
    Progress streams to WebSocket clients via the bus.

    Args:
        request: The incoming FastAPI request (for app.state access).
        db: Database connection.
        bus: Message bus.

    Returns:
        A dict with ``run_id`` and ``status`` ("started").
    """
    run_id = _new_run_id()

    async def _run() -> None:
        try:
            scorer = ScorerAgent(bus=bus, db_conn=db)

            rows = db.execute(
                "SELECT s.id, s.title, s.url, s.score, s.comments, s.posted_at, "
                "s.hn_type, s.endpoints "
                "FROM stories s "
                "LEFT JOIN scores sc ON sc.story_id = s.id "
                "WHERE sc.id IS NULL"
            ).fetchall()

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
        except Exception:
            logger.exception("score run %s failed", run_id)
        finally:
            _remove_run(request, run_id)

    task = asyncio.create_task(_run())
    _register_run(request, run_id, task, "score")
    return {"run_id": run_id, "status": "started"}


@router.post("/categorize", status_code=202)
async def categorize(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    bus: MessageBus = Depends(get_bus),
) -> dict[str, str]:
    """Categorize all uncategorized stories as a background task.

    Progress streams to WebSocket clients via the bus.

    Args:
        request: The incoming FastAPI request (for app.state access).
        db: Database connection.
        bus: Message bus.

    Returns:
        A dict with ``run_id`` and ``status`` ("started").
    """
    run_id = _new_run_id()

    async def _run() -> None:
        try:
            categorizer = CategorizerAgent(bus=bus, db_conn=db)

            rows = db.execute(
                "SELECT s.id, s.title, s.url, s.hn_type "
                "FROM stories s "
                "LEFT JOIN categories c ON c.story_id = s.id "
                "WHERE c.id IS NULL"
            ).fetchall()

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
        except Exception:
            logger.exception("categorize run %s failed", run_id)
        finally:
            _remove_run(request, run_id)

    task = asyncio.create_task(_run())
    _register_run(request, run_id, task, "categorize")
    return {"run_id": run_id, "status": "started"}


@router.post("/orchestrate", status_code=202)
async def orchestrate(
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    bus: MessageBus = Depends(get_bus),
) -> dict[str, str]:
    """Run orchestrator on scored stories as a background task.

    Evaluates priority and budget, dispatches fetch requests for
    qualifying stories. Progress streams to WebSocket clients via
    the bus.

    Args:
        request: The incoming FastAPI request (for app.state access).
        db: Database connection.
        bus: Message bus.

    Returns:
        A dict with ``run_id`` and ``status`` ("started").
    """
    run_id = _new_run_id()

    async def _run() -> None:
        try:
            orchestrator = OrchestratorAgent(bus=bus, db_conn=db)

            rows = db.execute(
                "SELECT s.id, s.title, s.url, s.hn_text, s.score, s.comments, "
                "s.posted_at, s.hn_type, s.endpoints, sc.composite "
                "FROM stories s "
                "JOIN scores sc ON sc.story_id = s.id "
                "LEFT JOIN orchestrator_decisions od ON od.story_id = s.id "
                "WHERE od.id IS NULL "
                "ORDER BY sc.composite DESC"
            ).fetchall()

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
        except Exception:
            logger.exception("orchestrate run %s failed", run_id)
        finally:
            _remove_run(request, run_id)

    task = asyncio.create_task(_run())
    _register_run(request, run_id, task, "orchestrate")
    return {"run_id": run_id, "status": "started"}


@router.post("/fetch/{story_id}", status_code=202)
async def fetch_story(
    story_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    bus: MessageBus = Depends(get_bus),
) -> dict[str, str]:
    """Fetch and extract article text for a specific story as a background task.

    Progress streams to WebSocket clients via the bus.

    Args:
        story_id: The HN story ID to fetch.
        request: The incoming FastAPI request (for app.state access).
        db: Database connection.
        bus: Message bus.

    Returns:
        A dict with ``run_id`` and ``status`` ("started").

    Raises:
        HTTPException: If the story is not found (404).
    """
    row = db.execute(
        "SELECT id, title, url, hn_text FROM stories WHERE id = ?",
        (story_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Story {story_id} not found")

    run_id = _new_run_id()

    async def _run() -> None:
        try:
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
        except Exception:
            logger.exception("fetch run %s for story %d failed", run_id, story_id)
        finally:
            _remove_run(request, run_id)

    task = asyncio.create_task(_run())
    _register_run(request, run_id, task, "fetch")
    return {"run_id": run_id, "status": "started"}


@router.post("/summarize/{story_id}", status_code=202)
async def summarize_story(
    story_id: int,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    bus: MessageBus = Depends(get_bus),
) -> dict[str, str]:
    """Summarize and validate a specific story as a background task.

    Runs both summarizer and validator. Progress streams to WebSocket
    clients via the bus.

    Args:
        story_id: The HN story ID to summarize.
        request: The incoming FastAPI request (for app.state access).
        db: Database connection.
        bus: Message bus.

    Returns:
        A dict with ``run_id`` and ``status`` ("started").

    Raises:
        HTTPException: If the story has no fetched article (400).
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

    run_id = _new_run_id()

    async def _run() -> None:
        try:
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
        except Exception:
            logger.exception("summarize run %s for story %d failed", run_id, story_id)
        finally:
            _remove_run(request, run_id)

    task = asyncio.create_task(_run())
    _register_run(request, run_id, task, "summarize")
    return {"run_id": run_id, "status": "started"}


@router.post("/pipeline/run", status_code=202)
async def run_pipeline(
    request: Request,
    max_stories: int = Query(default=10, ge=1, le=100),
    db: sqlite3.Connection = Depends(get_db),
    bus: MessageBus = Depends(get_bus),
) -> dict[str, str]:
    """Run the full pipeline as a background task.

    Executes: collect, score, categorize, orchestrate, fetch,
    summarize, validate, digest. Progress streams to WebSocket
    clients via the bus as each agent publishes to its channel.

    Args:
        request: The incoming FastAPI request (for app.state access).
        max_stories: Maximum stories to collect.
        db: Database connection.
        bus: Message bus.

    Returns:
        A dict with ``run_id`` and ``status`` ("started").
    """
    run_id = _new_run_id()
    progress = PipelineProgress()

    async def _emit_progress(bus: MessageBus, progress: PipelineProgress) -> None:
        """Publish a pipeline_progress event to the system channel."""
        await bus.publish(
            CHANNEL_SYSTEM,
            BusMessage(
                type="pipeline_progress",
                timestamp=datetime.now(timezone.utc),
                source="api",
                payload=PipelineProgressPayload(
                    run_id=run_id,
                    collected=progress.collected,
                    scored=progress.scored,
                    categorized=progress.categorized,
                    fetched=progress.fetched,
                    summarized=progress.summarized,
                    validated=progress.validated,
                    total_stories=progress.total_stories,
                ),
            ),
        )

    async def _run() -> None:
        try:
            # Publish pipeline_started
            await bus.publish(
                CHANNEL_SYSTEM,
                BusMessage(
                    type="pipeline_started",
                    timestamp=datetime.now(timezone.utc),
                    source="api",
                    payload=PipelineProgressPayload(run_id=run_id),
                ),
            )

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

            progress.collected = len(story_msgs)
            progress.total_stories = len(story_msgs)
            await _emit_progress(bus, progress)

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

            progress.scored = len(score_msgs)
            progress.categorized = len(story_msgs)
            await _emit_progress(bus, progress)

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

            await _emit_progress(bus, progress)

            # 4. Fetch
            article_queue = bus.subscribe(CHANNEL_ARTICLE)
            fetcher = FetcherAgent(bus=bus, db_conn=db)
            fetcher._session = aiohttp.ClientSession()
            try:
                for msg in fetch_msgs:
                    await fetcher.process(CHANNEL_FETCH_REQUEST, msg)
                    progress.fetched += 1
                    await _emit_progress(bus, progress)
            finally:
                await fetcher._session.close()

            article_msgs: list[BusMessage] = []
            while not article_queue.empty():
                article_msgs.append(article_queue.get_nowait())

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
                    progress.summarized += 1
                    await _emit_progress(bus, progress)

                while not summary_queue.empty():
                    summary_msg = summary_queue.get_nowait()
                    await validator.process(CHANNEL_SUMMARY, summary_msg)
                    progress.validated += 1
                    await _emit_progress(bus, progress)
            finally:
                await summarizer._llm.close()
                await validator._llm.close()

            # 7. Build digest
            report_builder = ReportBuilderAgent(bus=bus, db_conn=db)
            digest = await report_builder._build_digest()

            if digest and digest.get("story_count", 0) > 0:
                await report_builder._persist_and_publish(digest)

            # Publish pipeline_completed
            await bus.publish(
                CHANNEL_SYSTEM,
                BusMessage(
                    type="pipeline_completed",
                    timestamp=datetime.now(timezone.utc),
                    source="api",
                    payload=PipelineProgressPayload(
                        run_id=run_id,
                        collected=progress.collected,
                        scored=progress.scored,
                        categorized=progress.categorized,
                        fetched=progress.fetched,
                        summarized=progress.summarized,
                        validated=progress.validated,
                        total_stories=progress.total_stories,
                    ),
                ),
            )

        except Exception:
            logger.exception("pipeline run %s failed", run_id)
        finally:
            _remove_run(request, run_id)

    task = asyncio.create_task(_run())
    _register_run(request, run_id, task, "pipeline", progress)
    return {"run_id": run_id, "status": "started"}


@router.get("/runs")
async def list_runs(request: Request) -> list[RunStatus]:
    """List all active and recently completed runs.

    Returns active runs tracked in ``app.state.active_runs`` with
    their type, status, start time, and progress (if applicable).

    Args:
        request: The incoming FastAPI request (for app.state access).

    Returns:
        A list of RunStatus models for all tracked runs.
    """
    runs: dict[str, RunEntry] = getattr(request.app.state, "active_runs", {})
    result: list[RunStatus] = []
    for rid, entry in runs.items():
        status = _task_status(entry.task)
        result.append(
            RunStatus(
                run_id=rid,
                type=entry.run_type,
                status=status,
                started_at=entry.started_at,
                progress=entry.progress,
            )
        )
    result.sort(key=lambda r: r.started_at, reverse=True)
    return result


@router.get("/runs/{run_id}")
async def get_run_status(run_id: str, request: Request) -> RunStatus:
    """Check the status of a background run.

    Looks up the run in ``app.state.active_runs`` and returns its
    current status: running, completed, failed, or completed_or_unknown
    (if the run ID is not found, meaning it either completed and was
    cleaned up, or never existed).

    Args:
        run_id: The run identifier returned by an action endpoint.
        request: The incoming FastAPI request (for app.state access).

    Returns:
        A RunStatus with run_id, type, status, and started_at.
    """
    runs: dict[str, RunEntry] = getattr(request.app.state, "active_runs", {})
    entry = runs.get(run_id)
    if entry is None:
        return RunStatus(
            run_id=run_id,
            type="unknown",
            status="completed_or_unknown",
            started_at="",
        )
    return RunStatus(
        run_id=run_id,
        type=entry.run_type,
        status=_task_status(entry.task),
        started_at=entry.started_at,
        progress=entry.progress,
    )
