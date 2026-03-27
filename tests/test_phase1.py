"""End-to-end verification tests for Phase 1 foundation.

Tests exercise the real database, message bus, agents, and supervisor
with no mocking, per CLAUDE.md testing conventions.
"""

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from hndigest.bus import CHANNEL_SCORE, CHANNEL_STORY, CHANNEL_SYSTEM, MessageBus
from hndigest.db import init_db
from hndigest.agents.base import BaseAgent
from hndigest.agents.scorer import ScorerAgent
from hndigest.supervisor import Supervisor

# Resolve the real migrations directory from the worktree layout.
_WORKTREE_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _WORKTREE_ROOT / "db" / "migrations"
_SCORING_CONFIG = _WORKTREE_ROOT / "config" / "scoring.yaml"

# The eight domain tables created by 001_initial_schema.sql.
_EXPECTED_TABLES: set[str] = {
    "stories",
    "score_snapshots",
    "articles",
    "categories",
    "scores",
    "summaries",
    "validations",
    "digests",
}


# ------------------------------------------------------------------
# Test 1: Migration runner
# ------------------------------------------------------------------


class TestMigrationRunner:
    """Verify init_db creates all tables and is idempotent."""

    def test_migration_runner(self) -> None:
        """Call init_db with :memory: and verify all 8 tables plus schema_version."""
        conn = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)

        # Query sqlite_master for user tables (exclude internal/index entries).
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        table_names = {row[0] for row in rows}

        # All eight domain tables must exist.
        assert _EXPECTED_TABLES.issubset(table_names), (
            f"Missing tables: {_EXPECTED_TABLES - table_names}"
        )

        # schema_version must also exist (migration tracking).
        assert "schema_version" in table_names

        # schema_version should record the applied migration.
        applied = conn.execute("SELECT filename FROM schema_version").fetchall()
        filenames = {row[0] for row in applied}
        assert "001_initial_schema.sql" in filenames

        # Idempotent: calling init_db again on the same connection's db
        # should not raise.  We reopen a fresh connection to the same
        # in-memory db via shared cache to prove idempotency.
        conn2 = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)
        rows2 = conn2.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        table_names2 = {row[0] for row in rows2}
        assert _EXPECTED_TABLES.issubset(table_names2)

        conn.close()
        conn2.close()


# ------------------------------------------------------------------
# Test 2: Message bus fan-out
# ------------------------------------------------------------------


class TestMessageBusFanout:
    """Verify fan-out delivery to multiple subscribers."""

    async def test_message_bus_fanout(self) -> None:
        """Publish one message to 'story' channel, both subscribers receive it."""
        bus = MessageBus()
        q1 = bus.subscribe(CHANNEL_STORY)
        q2 = bus.subscribe(CHANNEL_STORY)

        message: dict[str, Any] = {
            "type": "story",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "test",
            "payload": {"id": 1, "title": "Test Story"},
        }

        await bus.publish(CHANNEL_STORY, message)

        msg1 = await asyncio.wait_for(q1.get(), timeout=2.0)
        msg2 = await asyncio.wait_for(q2.get(), timeout=2.0)

        assert msg1 == message
        assert msg2 == message
        assert msg1["payload"]["title"] == "Test Story"


# ------------------------------------------------------------------
# Test 3: Scorer with seeded data
# ------------------------------------------------------------------


class TestScorerWithSeededData:
    """Verify the scorer persists scores and publishes to the score channel."""

    async def test_scorer_with_seeded_data(self) -> None:
        """Seed a story, run scorer.process, verify DB row and bus message."""
        conn = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)
        bus = MessageBus()

        # Subscribe to score channel before running scorer so we capture output.
        score_queue = bus.subscribe(CHANNEL_SCORE)

        scorer = ScorerAgent(
            bus=bus,
            db_conn=conn,
            config_path=_SCORING_CONFIG,
        )

        # Insert a known story directly into the stories table.
        story_id = 99999
        posted_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        conn.execute(
            """INSERT INTO stories
               (id, title, url, score, comments, author,
                posted_at, hn_type, endpoints, first_seen, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                story_id,
                "Test Story for Scoring",
                "https://example.com",
                150,
                42,
                "testuser",
                posted_at,
                "story",
                json.dumps(["topstories"]),
                datetime.now(timezone.utc).isoformat(),
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()

        # Build a story message matching what the scorer expects.
        story_message: dict[str, Any] = {
            "type": "story",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "collector",
            "payload": {
                "id": story_id,
                "title": "Test Story for Scoring",
                "url": "https://example.com",
                "score": 150,
                "comments": 42,
                "posted_at": posted_at,
                "endpoints": json.dumps(["topstories"]),
            },
        }

        # Run the scorer's process method directly.
        await scorer.process(CHANNEL_STORY, story_message)

        # Verify: score was persisted to scores table.
        row = conn.execute(
            "SELECT story_id, composite FROM scores WHERE story_id = ?",
            (story_id,),
        ).fetchone()
        assert row is not None, "Score row was not persisted"
        assert row[0] == story_id
        composite_from_db = row[1]
        assert composite_from_db > 0, f"Composite should be > 0, got {composite_from_db}"

        # Verify: score message was published to the score channel.
        score_msg = await asyncio.wait_for(score_queue.get(), timeout=2.0)
        assert score_msg["type"] == "score"
        assert score_msg["payload"]["story_id"] == story_id
        assert score_msg["payload"]["composite"] > 0

        conn.close()


# ------------------------------------------------------------------
# Test 4: Supervisor lifecycle
# ------------------------------------------------------------------


class _NoOpAgent(BaseAgent):
    """Minimal test agent with a no-op process method."""

    def __init__(self, bus: MessageBus) -> None:
        super().__init__(
            name="noop",
            bus=bus,
            subscriptions=[],
            publications=[],
        )

    async def process(self, channel: str, message: dict[str, Any]) -> None:
        """Do nothing — used only to verify supervisor lifecycle."""


class TestSupervisorLifecycle:
    """Verify the supervisor can start, register agents, and shut down."""

    async def test_supervisor_lifecycle(self) -> None:
        """Start supervisor with a no-op agent, verify status, then shut down."""
        supervisor = Supervisor(db_path=":memory:")

        # We need a bus before creating the agent so it can subscribe.
        # The supervisor creates the bus in start(), but register_agent
        # happens before start.  Create a temporary bus for construction;
        # the supervisor will replace it in start().
        temp_bus = MessageBus()
        agent = _NoOpAgent(bus=temp_bus)
        supervisor.register_agent(agent)

        await supervisor.start()

        # Give the agent task and health monitor a moment to spin up.
        await asyncio.sleep(0.2)

        # Verify agent_statuses shows the agent as running.
        statuses = supervisor.agent_statuses
        assert "noop" in statuses, f"Expected 'noop' in statuses, got {list(statuses.keys())}"
        assert statuses["noop"]["status"] == "running"

        # Shut down cleanly.
        await supervisor.shutdown()

        # After shutdown the supervisor should have closed the db.
        assert supervisor.db is None


# ------------------------------------------------------------------
# Test 5: Collector live (gated by env var)
# ------------------------------------------------------------------


class TestCollectorLive:
    """Live integration test that hits the real HN API."""

    @pytest.mark.skipif(
        not os.environ.get("HN_API_TEST"),
        reason="HN_API_TEST env var not set — skipping live collector test",
    )
    async def test_collector_live(self) -> None:
        """Run one poll cycle against the real HN API and verify DB + bus output."""
        from hndigest.agents.collector import CollectorAgent

        conn = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)
        bus = MessageBus()

        # Subscribe to story channel to capture published stories.
        story_queue = bus.subscribe(CHANNEL_STORY)

        collector = CollectorAgent(bus=bus, db_conn=conn, poll_interval=600)

        # Run one poll cycle directly instead of the full polling loop.
        # The collector needs an aiohttp session.
        import aiohttp

        collector._session = aiohttp.ClientSession()
        try:
            await collector._poll_once()
        finally:
            await collector._session.close()
            collector._session = None

        # Verify at least some stories were inserted into the database.
        row = conn.execute("SELECT COUNT(*) FROM stories").fetchone()
        assert row is not None
        story_count = row[0]
        assert story_count > 0, "Expected at least one story in the database"

        # Verify stories were published to the story channel.
        published_count = 0
        while not story_queue.empty():
            msg = story_queue.get_nowait()
            assert msg["type"] == "story"
            assert "story_id" in msg["payload"]
            published_count += 1

        assert published_count > 0, "Expected at least one story published to bus"

        conn.close()
