"""HN Collector agent — polls Hacker News and persists stories to SQLite."""

import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp

from hndigest.agents.base import BaseAgent, HEARTBEAT_INTERVAL_SECONDS
from hndigest.bus import CHANNEL_STORY, MessageBus
from hndigest.mcp.hn_mcp import fetch_item, fetch_top_stories

logger = logging.getLogger(__name__)

# How often to check for shutdown while sleeping between polls (seconds).
_SHUTDOWN_CHECK_INTERVAL = 1.0


def _unix_to_iso(ts: int | float) -> str:
    """Convert a Unix timestamp to an ISO 8601 UTC string.

    Args:
        ts: Unix timestamp (seconds since epoch).

    Returns:
        ISO 8601 formatted string in UTC.
    """
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _classify_hn_type(item: dict[str, Any]) -> str:
    """Determine the hn_type category for an HN item.

    Args:
        item: The raw item dict from the HN API.

    Returns:
        One of "ask", "show", "job", or "story".
    """
    title = (item.get("title") or "").lower()
    item_type = (item.get("type") or "").lower()
    if item_type == "job":
        return "job"
    if title.startswith("ask hn"):
        return "ask"
    if title.startswith("show hn"):
        return "show"
    return "story"


class CollectorAgent(BaseAgent):
    """Agent that polls the HN API and persists stories to the database.

    Runs a polling loop instead of the base class message-waiting loop.
    On each cycle it fetches top story IDs, retrieves metadata for new
    stories, upserts into the stories table, records score snapshots for
    existing stories, and publishes new stories to the story channel.

    Args:
        bus: The shared message bus.
        db_conn: An open sqlite3 connection (with WAL mode recommended).
        poll_interval: Seconds between polling cycles (default 600 = 10 min).
    """

    def __init__(
        self,
        bus: MessageBus,
        db_conn: sqlite3.Connection,
        poll_interval: int = 600,
    ) -> None:
        super().__init__(
            name="collector",
            bus=bus,
            subscriptions=[],
            publications=[CHANNEL_STORY],
        )
        self.db_conn = db_conn
        self.poll_interval = poll_interval
        self._session: aiohttp.ClientSession | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Subscribe to system channel, open HTTP session, and enter polling loop."""
        # Subscribe to system channel for shutdown signals
        from hndigest.agents.base import SYSTEM_CHANNEL

        queue = self.bus.subscribe(SYSTEM_CHANNEL)
        self._queues[SYSTEM_CHANNEL] = queue

        self.status = "running"
        logger.info(
            "collector agent started, poll_interval=%ds", self.poll_interval
        )

        self._session = aiohttp.ClientSession()
        try:
            await self._polling_loop()
        finally:
            await self._session.close()
            self._session = None
            self.status = "stopped"
            logger.info("collector agent stopped")

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    async def _polling_loop(self) -> None:
        """Main loop: poll HN, persist, publish, sleep, repeat."""
        last_heartbeat = time.monotonic()

        while not self._shutdown:
            # ----- poll -----
            try:
                await self._poll_once()
            except Exception:
                logger.exception("collector: error during poll cycle")

            # ----- heartbeat -----
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                await self._emit_heartbeat()
                last_heartbeat = now

            # ----- interruptible sleep -----
            await self._interruptible_sleep(self.poll_interval)

            # ----- heartbeat after sleep -----
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                await self._emit_heartbeat()
                last_heartbeat = now

    async def _interruptible_sleep(self, duration: float) -> None:
        """Sleep for *duration* seconds while checking for shutdown signals.

        Checks the system queue every ``_SHUTDOWN_CHECK_INTERVAL`` seconds
        so the agent can respond to shutdown promptly.

        Args:
            duration: Total seconds to sleep.
        """
        from hndigest.agents.base import SYSTEM_CHANNEL

        system_queue = self._queues.get(SYSTEM_CHANNEL)
        elapsed = 0.0

        while elapsed < duration and not self._shutdown:
            chunk = min(_SHUTDOWN_CHECK_INTERVAL, duration - elapsed)
            if system_queue is not None:
                try:
                    msg = await asyncio.wait_for(system_queue.get(), timeout=chunk)
                    if (
                        isinstance(msg, dict)
                        and msg.get("type") == "shutdown"
                    ):
                        logger.info("collector received shutdown signal")
                        self._shutdown = True
                        self.status = "stopping"
                        return
                except TimeoutError:
                    pass
            else:
                await asyncio.sleep(chunk)
            elapsed += chunk

    # ------------------------------------------------------------------
    # Core logic
    # ------------------------------------------------------------------

    async def _poll_once(self) -> None:
        """Execute a single poll cycle: fetch IDs, fetch items, persist."""
        assert self._session is not None

        story_ids = await fetch_top_stories(self._session)
        if not story_ids:
            logger.warning("collector: received empty story list, skipping cycle")
            return

        logger.info("collector: processing %d top story IDs", len(story_ids))
        now_iso = datetime.now(timezone.utc).isoformat()

        for story_id in story_ids:
            if self._shutdown:
                break

            existing = self._get_existing_story(story_id)
            item = await fetch_item(self._session, story_id)
            if item is None:
                continue

            # Skip deleted or dead items
            if item.get("deleted") or item.get("dead"):
                continue

            # Only process stories (not comments, polls, etc.)
            if item.get("type") not in ("story", "job"):
                continue

            score = item.get("score", 0)
            comments = item.get("descendants", 0)

            if existing is None:
                await self._insert_new_story(item, now_iso)
            else:
                self._update_existing_story(story_id, score, comments, now_iso)
                self._insert_score_snapshot(story_id, score, comments, now_iso)

    def _get_existing_story(self, story_id: int) -> dict[str, Any] | None:
        """Check if a story already exists in the database.

        Args:
            story_id: The HN item ID.

        Returns:
            A dict with the existing row data, or None if not found.
        """
        cursor = self.db_conn.execute(
            "SELECT id, score, comments FROM stories WHERE id = ?",
            (story_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return {"id": row[0], "score": row[1], "comments": row[2]}

    async def _insert_new_story(
        self, item: dict[str, Any], now_iso: str
    ) -> None:
        """Insert a new story and publish it to the story channel.

        Args:
            item: The raw HN item dict.
            now_iso: Current timestamp in ISO 8601 UTC.
        """
        story_id = item["id"]
        title = item.get("title", "")
        url = item.get("url")
        hn_text = item.get("text")
        score = item.get("score", 0)
        comments = item.get("descendants", 0)
        author = item.get("by", "unknown")
        posted_at = _unix_to_iso(item.get("time", 0))
        hn_type = _classify_hn_type(item)
        endpoints = json.dumps(["topstories"])

        try:
            cursor = self.db_conn.execute(
                """INSERT OR IGNORE INTO stories
                   (id, title, url, hn_text, score, comments, author,
                    posted_at, hn_type, endpoints, first_seen, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    story_id, title, url, hn_text, score, comments, author,
                    posted_at, hn_type, endpoints, now_iso, now_iso,
                ),
            )
            self.db_conn.commit()
        except sqlite3.Error:
            logger.exception("collector: failed to insert story %d", story_id)
            return

        # Only publish if the row was actually inserted (not ignored)
        if cursor.rowcount > 0:
            payload = {
                "story_id": story_id,
                "title": title,
                "url": url,
                "hn_text": hn_text,
                "score": score,
                "comments": comments,
                "author": author,
                "posted_at": posted_at,
                "hn_type": hn_type,
                "endpoints": ["topstories"],
            }
            await self.publish(CHANNEL_STORY, payload, msg_type="story")
            logger.info(
                "collector: new story %d — %s (score=%d)",
                story_id, title, score,
            )

    def _update_existing_story(
        self,
        story_id: int,
        score: int,
        comments: int,
        now_iso: str,
    ) -> None:
        """Update score and comments for an existing story.

        Args:
            story_id: The HN item ID.
            score: Current score from the API.
            comments: Current comment count from the API.
            now_iso: Current timestamp in ISO 8601 UTC.
        """
        try:
            self.db_conn.execute(
                """UPDATE stories
                   SET score = ?, comments = ?, last_updated = ?
                   WHERE id = ?""",
                (score, comments, now_iso, story_id),
            )
            self.db_conn.commit()
        except sqlite3.Error:
            logger.exception(
                "collector: failed to update story %d", story_id
            )

    def _insert_score_snapshot(
        self,
        story_id: int,
        score: int,
        comments: int,
        now_iso: str,
    ) -> None:
        """Record a point-in-time score snapshot for velocity calculations.

        Args:
            story_id: The HN item ID.
            score: Current score.
            comments: Current comment count.
            now_iso: Current timestamp in ISO 8601 UTC.
        """
        try:
            self.db_conn.execute(
                """INSERT INTO score_snapshots
                   (story_id, score, comments, snapshot_at)
                   VALUES (?, ?, ?, ?)""",
                (story_id, score, comments, now_iso),
            )
            self.db_conn.commit()
        except sqlite3.Error:
            logger.exception(
                "collector: failed to insert score snapshot for story %d",
                story_id,
            )

    # ------------------------------------------------------------------
    # Abstract method (required by BaseAgent, but not used in poll mode)
    # ------------------------------------------------------------------

    async def process(self, channel: str, message: dict[str, Any]) -> None:
        """Process inbound messages (unused — collector uses polling loop).

        Args:
            channel: The channel the message arrived on.
            message: The message payload dict.
        """
