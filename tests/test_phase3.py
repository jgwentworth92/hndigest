"""End-to-end verification tests for Phase 3 orchestrator agent.

Tests exercise the real orchestrator, message bus, database, and downstream
agents with no mocking — real bus, real DB, real agents — per CLAUDE.md
testing conventions.
"""

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp

from hndigest.bus import (
    CHANNEL_ARTICLE,
    CHANNEL_FETCH_REQUEST,
    CHANNEL_SCORE,
    CHANNEL_STORY,
    MessageBus,
)
from hndigest.db import init_db
from hndigest.agents.orchestrator import OrchestratorAgent
from hndigest.agents.scorer import ScorerAgent

_WORKTREE_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _WORKTREE_ROOT / "db" / "migrations"
_ORCHESTRATOR_CONFIG = _WORKTREE_ROOT / "config" / "orchestrator.yaml"
_SCORING_CONFIG = _WORKTREE_ROOT / "config" / "scoring.yaml"
_ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"


def _write_artifact(name: str, data: dict[str, Any]) -> Path:
    """Write a JSON artifact file with timestamped name.

    Args:
        name: Base name for the artifact file.
        data: Dict to serialize as JSON.

    Returns:
        Path to the written artifact file.
    """
    _ARTIFACTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = _ARTIFACTS_DIR / f"{ts}_{name}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _seed_story(
    conn: sqlite3.Connection,
    story_id: int,
    title: str,
    url: str | None,
    score: int = 100,
    comments: int = 20,
    author: str = "testuser",
    hn_type: str = "story",
    hn_text: str | None = None,
    hours_ago: float = 2.0,
) -> str:
    """Insert a story row into the database and return the posted_at timestamp.

    Args:
        conn: An open sqlite3 connection.
        story_id: HN story ID.
        title: Story title.
        url: Story URL (None for Ask HN).
        score: HN upvote score.
        comments: Number of comments.
        author: Story author.
        hn_type: One of "story", "show", "ask", "job".
        hn_text: Optional HN text field for self-posts.
        hours_ago: How many hours ago the story was posted.

    Returns:
        ISO 8601 UTC posted_at timestamp.
    """
    now = datetime.now(timezone.utc)
    posted_at = (now - timedelta(hours=hours_ago)).isoformat()
    first_seen = (now - timedelta(hours=hours_ago - 0.1)).isoformat()
    last_updated = now.isoformat()

    conn.execute(
        """INSERT INTO stories
           (id, title, url, hn_text, score, comments, author,
            posted_at, hn_type, endpoints, first_seen, last_updated)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            story_id,
            title,
            url,
            hn_text,
            score,
            comments,
            author,
            posted_at,
            hn_type,
            json.dumps(["topstories"]),
            first_seen,
            last_updated,
        ),
    )
    conn.commit()
    return posted_at


def _make_story_message(
    story_id: int,
    title: str,
    url: str | None,
    score: int = 100,
    comments: int = 20,
) -> dict[str, Any]:
    """Build a story bus message.

    Args:
        story_id: HN story ID.
        title: Story title.
        url: Story URL.
        score: HN upvote score.
        comments: Number of comments.

    Returns:
        A properly formatted bus message dict for the story channel.
    """
    return {
        "type": "story",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "test",
        "payload": {
            "id": story_id,
            "title": title,
            "url": url,
            "score": score,
            "comments": comments,
        },
    }


def _make_score_message(story_id: int, composite: float) -> dict[str, Any]:
    """Build a score bus message.

    Args:
        story_id: HN story ID.
        composite: The composite priority score.

    Returns:
        A properly formatted bus message dict for the score channel.
    """
    return {
        "type": "score",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "test",
        "payload": {
            "story_id": story_id,
            "composite": composite,
            "components": {
                "score_velocity": 20.0,
                "comment_velocity": 10.0,
                "front_page_presence": 50,
                "recency": 80.0,
            },
        },
    }


# ------------------------------------------------------------------
# Test 1: Orchestrator dispatches high-score stories
# ------------------------------------------------------------------


class TestOrchestratorDispatchesHighScoreStories:
    """Verify the orchestrator dispatches stories above the threshold."""

    async def test_orchestrator_dispatches_high_score_stories(self) -> None:
        """Send a story + score above threshold; verify fetch_request and DB row."""
        conn = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)
        bus = MessageBus()

        # Subscribe to fetch_request to capture dispatched stories.
        fetch_queue = bus.subscribe(CHANNEL_FETCH_REQUEST)

        orchestrator = OrchestratorAgent(
            bus=bus,
            db_conn=conn,
            config_path=_ORCHESTRATOR_CONFIG,
        )

        story_id = 60001
        title = "Rust 2.0 Released with Major GC Improvements"
        url = "https://blog.rust-lang.org/2026/rust-2.0"
        _seed_story(conn, story_id, title=title, url=url, score=200, comments=80)

        # Send story message to orchestrator.
        story_msg = _make_story_message(story_id, title, url, score=200, comments=80)
        await orchestrator.process(CHANNEL_STORY, story_msg)

        # Send score message with composite=50 (above threshold of 30).
        score_msg = _make_score_message(story_id, composite=50.0)
        await orchestrator.process(CHANNEL_SCORE, score_msg)

        # Verify: fetch_request was published to bus.
        fetch_msg = await asyncio.wait_for(fetch_queue.get(), timeout=2.0)
        assert fetch_msg["type"] == "fetch_request"
        assert fetch_msg["payload"]["story_id"] == story_id
        assert fetch_msg["payload"]["url"] == url
        assert fetch_msg["payload"]["priority"] == 50.0

        # Verify: orchestrator_decisions table has row with decision="dispatched".
        row = conn.execute(
            "SELECT story_id, decision, reason, priority_score FROM orchestrator_decisions WHERE story_id = ?",
            (story_id,),
        ).fetchone()
        assert row is not None, "Decision row should exist in orchestrator_decisions"
        assert row[0] == story_id
        assert row[1] == "dispatched"
        assert row[2] == "above threshold"
        assert row[3] == 50.0

        conn.close()


# ------------------------------------------------------------------
# Test 2: Orchestrator skips low-score stories
# ------------------------------------------------------------------


class TestOrchestratorSkipsLowScoreStories:
    """Verify the orchestrator skips stories below the threshold."""

    async def test_orchestrator_skips_low_score_stories(self) -> None:
        """Send a story + score below threshold; verify no fetch_request and skipped row."""
        conn = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)
        bus = MessageBus()

        fetch_queue = bus.subscribe(CHANNEL_FETCH_REQUEST)

        orchestrator = OrchestratorAgent(
            bus=bus,
            db_conn=conn,
            config_path=_ORCHESTRATOR_CONFIG,
        )

        story_id = 60002
        title = "Minor CSS Framework Update v0.2.1"
        url = "https://example.com/css-update"
        _seed_story(conn, story_id, title=title, url=url, score=5, comments=2)

        # Send story message.
        story_msg = _make_story_message(story_id, title, url, score=5, comments=2)
        await orchestrator.process(CHANNEL_STORY, story_msg)

        # Send score message with composite=10 (below threshold of 30).
        score_msg = _make_score_message(story_id, composite=10.0)
        await orchestrator.process(CHANNEL_SCORE, score_msg)

        # Verify: NO fetch_request was published.
        assert fetch_queue.empty(), "No fetch_request should be published for low-score story"

        # Verify: orchestrator_decisions table has row with decision="skipped".
        row = conn.execute(
            "SELECT story_id, decision, reason, priority_score FROM orchestrator_decisions WHERE story_id = ?",
            (story_id,),
        ).fetchone()
        assert row is not None, "Decision row should exist in orchestrator_decisions"
        assert row[0] == story_id
        assert row[1] == "skipped"
        assert "below threshold" in row[2]
        assert row[3] == 10.0

        conn.close()


# ------------------------------------------------------------------
# Test 3: Orchestrator budget exhaustion
# ------------------------------------------------------------------


class TestOrchestratorBudgetExhaustion:
    """Verify the orchestrator respects the daily token budget."""

    async def test_orchestrator_budget_exhaustion(self) -> None:
        """Set tiny budget, send high-score story; verify budget_exceeded decision."""
        conn = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)
        bus = MessageBus()

        fetch_queue = bus.subscribe(CHANNEL_FETCH_REQUEST)

        orchestrator = OrchestratorAgent(
            bus=bus,
            db_conn=conn,
            config_path=_ORCHESTRATOR_CONFIG,
        )

        # Override budget to be smaller than tokens_per_article (1000).
        orchestrator._daily_budget_remaining = 500
        orchestrator._tokens_per_article = 1000

        story_id = 60003
        title = "Major Kubernetes Security Vulnerability Discovered"
        url = "https://example.com/k8s-vuln"
        _seed_story(conn, story_id, title=title, url=url, score=300, comments=150)

        # Send story message.
        story_msg = _make_story_message(story_id, title, url, score=300, comments=150)
        await orchestrator.process(CHANNEL_STORY, story_msg)

        # Send high score (composite=80, well above threshold).
        score_msg = _make_score_message(story_id, composite=80.0)
        await orchestrator.process(CHANNEL_SCORE, score_msg)

        # Verify: NO fetch_request was published despite high score.
        assert fetch_queue.empty(), "No fetch_request should be published when budget is exceeded"

        # Verify: orchestrator_decisions table has decision="budget_exceeded".
        row = conn.execute(
            "SELECT story_id, decision, reason, budget_remaining FROM orchestrator_decisions WHERE story_id = ?",
            (story_id,),
        ).fetchone()
        assert row is not None, "Decision row should exist in orchestrator_decisions"
        assert row[0] == story_id
        assert row[1] == "budget_exceeded"
        assert "budget" in row[2].lower()
        assert row[3] == 500  # Budget remaining at time of decision.

        conn.close()


# ------------------------------------------------------------------
# Test 4: Orchestrator budget reset
# ------------------------------------------------------------------


class TestOrchestratorBudgetReset:
    """Verify the orchestrator resets the budget when the UTC day changes."""

    async def test_orchestrator_budget_reset(self) -> None:
        """Set budget to 0 with yesterday's date; verify reset restores budget."""
        conn = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)
        bus = MessageBus()

        orchestrator = OrchestratorAgent(
            bus=bus,
            db_conn=conn,
            config_path=_ORCHESTRATOR_CONFIG,
        )

        # Record original daily budget from config.
        full_budget = orchestrator._config["budget"]["daily_token_budget"]

        # Exhaust the budget and set reset date to yesterday.
        orchestrator._daily_budget_remaining = 0
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        orchestrator._budget_reset_date = yesterday

        # Call the reset method.
        orchestrator._reset_budget_if_needed()

        # Verify budget was restored to the full daily amount.
        assert orchestrator._daily_budget_remaining == full_budget, (
            f"Budget should reset to {full_budget}, "
            f"got {orchestrator._daily_budget_remaining}"
        )

        # Verify the reset date was updated to today.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert orchestrator._budget_reset_date == today

        conn.close()


# ------------------------------------------------------------------
# Test 5: Migration includes orchestrator_decisions table
# ------------------------------------------------------------------


class TestMigrationIncludesOrchestratorDecisions:
    """Verify the 002 migration creates the orchestrator_decisions table."""

    def test_migration_includes_orchestrator_decisions(self) -> None:
        """Call init_db with :memory: and verify orchestrator_decisions exists with correct columns."""
        conn = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)

        # Verify orchestrator_decisions table exists.
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='orchestrator_decisions'"
        ).fetchall()
        table_names = {row[0] for row in rows}
        assert "orchestrator_decisions" in table_names, (
            "orchestrator_decisions table should exist after running migrations"
        )

        # Verify correct columns via PRAGMA table_info.
        columns = conn.execute("PRAGMA table_info(orchestrator_decisions)").fetchall()
        column_names = {col[1] for col in columns}
        expected_columns = {
            "id",
            "story_id",
            "decision",
            "reason",
            "priority_score",
            "budget_remaining",
            "used_llm",
            "decided_at",
        }
        assert expected_columns == column_names, (
            f"Expected columns {expected_columns}, got {column_names}"
        )

        # Verify the migration was recorded in schema_version.
        applied = conn.execute("SELECT filename FROM schema_version").fetchall()
        filenames = {row[0] for row in applied}
        assert "002_orchestrator_decisions.sql" in filenames, (
            f"002 migration not in schema_version: {filenames}"
        )

        # Verify indexes exist.
        indexes = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='orchestrator_decisions'"
        ).fetchall()
        index_names = {row[0] for row in indexes}
        assert "idx_orchestrator_decisions_story_id" in index_names
        assert "idx_orchestrator_decisions_decision" in index_names
        assert "idx_orchestrator_decisions_decided_at" in index_names

        conn.close()


# ------------------------------------------------------------------
# Test 6: Full pipeline with orchestrator (live)
# ------------------------------------------------------------------


class TestFullPipelineWithOrchestrator:
    """Live end-to-end: collector -> scorer -> orchestrator -> fetcher."""

    async def test_full_pipeline_with_orchestrator(self) -> None:
        """Use real HN API, run stories through scorer and orchestrator, verify decisions."""
        from hndigest.agents.collector import CollectorAgent
        from hndigest.agents.fetcher import FetcherAgent

        conn = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)
        bus = MessageBus()

        artifact: dict[str, Any] = {
            "test": "test_full_pipeline_with_orchestrator",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stages": {},
        }

        # Subscribe to channels to observe pipeline output.
        score_queue = bus.subscribe(CHANNEL_SCORE)
        fetch_queue = bus.subscribe(CHANNEL_FETCH_REQUEST)
        article_queue = bus.subscribe(CHANNEL_ARTICLE)

        # --- Stage 1: Collect real stories from HN API ---
        collector = CollectorAgent(bus=bus, db_conn=conn, poll_interval=600)
        collector._session = aiohttp.ClientSession()
        try:
            await collector._poll_once()
        finally:
            await collector._session.close()
            collector._session = None

        story_count = conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
        assert story_count > 0, "Expected at least one story from HN API"

        # Drain story messages from the bus (collector published them).
        story_queue = bus.subscribe(CHANNEL_STORY)
        # Re-read stories from DB since the collector already published them
        # before our subscription. We'll manually feed them through the pipeline.
        stories_from_db = conn.execute(
            "SELECT id, title, url, score, comments, posted_at, endpoints FROM stories LIMIT 15"
        ).fetchall()

        artifact["stages"]["collector"] = {
            "stories_collected": len(stories_from_db),
        }

        # --- Stage 2: Score each story ---
        scorer = ScorerAgent(bus=bus, db_conn=conn, config_path=_SCORING_CONFIG)

        scored_stories: list[dict[str, Any]] = []
        for row in stories_from_db:
            sid, title, url, hn_score, comments, posted_at, endpoints = row
            story_msg: dict[str, Any] = {
                "type": "story",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "test",
                "payload": {
                    "id": sid,
                    "title": title,
                    "url": url,
                    "score": hn_score,
                    "comments": comments,
                    "posted_at": posted_at,
                    "endpoints": endpoints,
                },
            }
            await scorer.process(CHANNEL_STORY, story_msg)

            # Read composite from DB.
            score_row = conn.execute(
                "SELECT composite FROM scores WHERE story_id = ?", (sid,)
            ).fetchone()
            if score_row:
                scored_stories.append({
                    "story_id": sid,
                    "title": title,
                    "url": url,
                    "composite": score_row[0],
                })

        assert len(scored_stories) > 0, "Expected at least one scored story"

        artifact["stages"]["scorer"] = {
            "stories_scored": len(scored_stories),
            "score_range": {
                "min": min(s["composite"] for s in scored_stories),
                "max": max(s["composite"] for s in scored_stories),
            },
        }

        # --- Stage 3: Run orchestrator on scored stories ---
        orchestrator = OrchestratorAgent(
            bus=bus,
            db_conn=conn,
            config_path=_ORCHESTRATOR_CONFIG,
        )

        # Drain score_queue from scorer output and feed to orchestrator.
        score_messages: list[dict[str, Any]] = []
        while not score_queue.empty():
            score_messages.append(score_queue.get_nowait())

        # First, send all story messages to orchestrator so they are pending.
        for row in stories_from_db:
            sid, title, url, hn_score, comments, posted_at, endpoints = row
            story_msg = _make_story_message(sid, title, url, score=hn_score, comments=comments)
            await orchestrator.process(CHANNEL_STORY, story_msg)

        # Then, send all score messages.
        for smsg in score_messages:
            await orchestrator.process(CHANNEL_SCORE, smsg)

        # Read decisions from DB.
        decision_rows = conn.execute(
            "SELECT story_id, decision, reason, priority_score FROM orchestrator_decisions"
        ).fetchall()

        dispatched = [r for r in decision_rows if r[1] == "dispatched"]
        skipped = [r for r in decision_rows if r[1] == "skipped"]
        budget_exceeded = [r for r in decision_rows if r[1] == "budget_exceeded"]

        assert len(decision_rows) > 0, "Expected at least one orchestrator decision"
        # With real HN data, we expect a mix of dispatched and skipped.
        # At minimum, some should be processed.

        artifact["stages"]["orchestrator"] = {
            "total_decisions": len(decision_rows),
            "dispatched": len(dispatched),
            "skipped": len(skipped),
            "budget_exceeded": len(budget_exceeded),
            "dispatched_stories": [
                {"story_id": r[0], "score": r[3]} for r in dispatched[:5]
            ],
            "skipped_stories": [
                {"story_id": r[0], "score": r[3]} for r in skipped[:5]
            ],
        }

        # --- Stage 4: Verify fetcher receives fetch_request and produces article ---
        fetcher = FetcherAgent(bus=bus, db_conn=conn)
        fetcher._session = aiohttp.ClientSession()

        articles_fetched: list[dict[str, Any]] = []
        fetch_count = 0
        try:
            while not fetch_queue.empty():
                fetch_msg = fetch_queue.get_nowait()
                assert fetch_msg["type"] == "fetch_request"
                # Process at most 3 fetch requests to keep test duration reasonable.
                if fetch_count < 3:
                    await fetcher.process(CHANNEL_FETCH_REQUEST, fetch_msg)
                    fetch_count += 1

                    payload = fetch_msg["payload"]
                    article_row = conn.execute(
                        "SELECT fetch_status, LENGTH(text) FROM articles WHERE story_id = ?",
                        (payload["story_id"],),
                    ).fetchone()
                    if article_row:
                        articles_fetched.append({
                            "story_id": payload["story_id"],
                            "title": payload.get("title", ""),
                            "url": payload.get("url", ""),
                            "fetch_status": article_row[0],
                            "text_length": article_row[1] or 0,
                        })
        finally:
            await fetcher._session.close()
            fetcher._session = None

        # If any stories were dispatched, verify fetcher produced results.
        if len(dispatched) > 0:
            assert fetch_count > 0, (
                "Fetcher should have received at least one fetch_request"
            )

        artifact["stages"]["fetcher"] = {
            "fetch_requests_received": fetch_count,
            "articles_fetched": articles_fetched,
        }

        artifact["overall_result"] = "PASS"
        artifact_path = _write_artifact("pipeline_phase3", artifact)
        print(f"\n--- Artifact written to: {artifact_path}")
        print(f"--- Stories collected: {len(stories_from_db)}")
        print(f"--- Stories scored: {len(scored_stories)}")
        print(f"--- Decisions: {len(decision_rows)} total "
              f"({len(dispatched)} dispatched, {len(skipped)} skipped, "
              f"{len(budget_exceeded)} budget_exceeded)")
        print(f"--- Articles fetched: {len(articles_fetched)}")

        conn.close()
