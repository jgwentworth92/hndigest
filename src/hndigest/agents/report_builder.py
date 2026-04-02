"""Report Builder agent — assembles periodic digests from stories, scores, categories, and summaries."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from hndigest.agents.base import BaseAgent, HEARTBEAT_INTERVAL_SECONDS, SYSTEM_CHANNEL
from hndigest.bus import CHANNEL_DIGEST, MessageBus
from hndigest.models import BusMessage

logger = logging.getLogger(__name__)

# Channel for on-demand digest trigger messages (e.g. from CLI `digest --now`).
CHANNEL_DIGEST_TRIGGER = "digest_trigger"

# How often to check for shutdown/trigger while sleeping between digest cycles.
_SHUTDOWN_CHECK_INTERVAL = 1.0


class ReportBuilderAgent(BaseAgent):
    """Agent that assembles the daily digest from categories, scores, and summaries.

    Runs a timer loop (default every 6 hours) instead of the base class
    message-waiting loop.  Also listens for on-demand trigger messages on
    the ``digest_trigger`` channel so the CLI or API can request an
    immediate digest build.

    Args:
        bus: The shared message bus.
        db_conn: An open sqlite3 connection.
        digest_interval: Seconds between automatic digest builds (default 21600 = 6 hours).
        stories_per_category: Maximum stories to include per category (default 5).
    """

    def __init__(
        self,
        bus: MessageBus,
        db_conn: sqlite3.Connection,
        digest_interval: int = 21600,
        stories_per_category: int = 5,
    ) -> None:
        super().__init__(
            name="report-builder",
            bus=bus,
            subscriptions=[],
            publications=[CHANNEL_DIGEST],
        )
        self.db_conn = db_conn
        self.digest_interval = digest_interval
        self.stories_per_category = stories_per_category

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Subscribe to system and trigger channels, then enter the timer loop."""
        # Subscribe to system channel for shutdown signals.
        queue = self.bus.subscribe(SYSTEM_CHANNEL)
        self._queues[SYSTEM_CHANNEL] = queue

        # Create and subscribe to the on-demand trigger channel.
        self.bus.create_channel(CHANNEL_DIGEST_TRIGGER)
        trigger_queue = self.bus.subscribe(CHANNEL_DIGEST_TRIGGER)
        self._queues[CHANNEL_DIGEST_TRIGGER] = trigger_queue

        self.status = "running"
        logger.info(
            "report-builder agent started, digest_interval=%ds, stories_per_category=%d",
            self.digest_interval,
            self.stories_per_category,
        )

        try:
            await self._timer_loop()
        finally:
            self.status = "stopped"
            logger.info("report-builder agent stopped")

    # ------------------------------------------------------------------
    # Timer loop
    # ------------------------------------------------------------------

    async def _timer_loop(self) -> None:
        """Main loop: sleep for digest_interval, build digest, repeat.

        During sleep the agent checks for shutdown signals and on-demand
        trigger messages every ``_SHUTDOWN_CHECK_INTERVAL`` seconds so it
        can respond promptly to either.
        """
        last_heartbeat = time.monotonic()

        while not self._shutdown:
            # ----- interruptible sleep (may be interrupted by trigger) -----
            triggered = await self._interruptible_sleep(self.digest_interval)

            if self._shutdown:
                break

            # ----- build and publish digest -----
            try:
                digest = await self._build_digest()
                if digest["story_count"] > 0:
                    await self._persist_and_publish(digest)
                    logger.info(
                        "report-builder: digest built with %d stories for period %s -> %s",
                        digest["story_count"],
                        digest["period_start"],
                        digest["period_end"],
                    )
                else:
                    logger.info("report-builder: no stories found for current period, skipping digest")
            except Exception:
                logger.exception("report-builder: error building digest")

            # ----- heartbeat -----
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                await self._emit_heartbeat()
                last_heartbeat = now

    async def _interruptible_sleep(self, duration: float) -> bool:
        """Sleep for *duration* seconds while checking for shutdown and trigger signals.

        Checks the system and trigger queues every ``_SHUTDOWN_CHECK_INTERVAL``
        seconds so the agent can respond to shutdown or on-demand trigger promptly.

        Args:
            duration: Total seconds to sleep.

        Returns:
            True if a trigger message interrupted the sleep, False otherwise.
        """
        system_queue = self._queues.get(SYSTEM_CHANNEL)
        trigger_queue = self._queues.get(CHANNEL_DIGEST_TRIGGER)
        elapsed = 0.0
        last_hb = time.monotonic()

        while elapsed < duration and not self._shutdown:
            # Emit heartbeat during long sleeps
            now_mono = time.monotonic()
            if now_mono - last_hb >= HEARTBEAT_INTERVAL_SECONDS:
                await self._emit_heartbeat()
                last_hb = now_mono

            chunk = min(_SHUTDOWN_CHECK_INTERVAL, duration - elapsed)

            # Build a set of wait tasks for both queues.
            wait_tasks: dict[asyncio.Task, str] = {}
            if system_queue is not None:
                wait_tasks[asyncio.create_task(system_queue.get())] = SYSTEM_CHANNEL
            if trigger_queue is not None:
                wait_tasks[asyncio.create_task(trigger_queue.get())] = CHANNEL_DIGEST_TRIGGER

            if not wait_tasks:
                await asyncio.sleep(chunk)
                elapsed += chunk
                continue

            done, pending = await asyncio.wait(
                wait_tasks.keys(),
                timeout=chunk,
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel pending tasks.
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            for task in done:
                channel = wait_tasks[task]
                try:
                    msg = task.result()
                except Exception:
                    continue

                if channel == SYSTEM_CHANNEL:
                    if isinstance(msg, BusMessage) and msg.type == "shutdown":
                        logger.info("report-builder received shutdown signal")
                        self._shutdown = True
                        self.status = "stopping"
                        return False
                    if isinstance(msg, dict) and msg.get("type") == "shutdown":
                        logger.info("report-builder received shutdown signal")
                        self._shutdown = True
                        self.status = "stopping"
                        return False

                if channel == CHANNEL_DIGEST_TRIGGER:
                    logger.info("report-builder received on-demand trigger")
                    return True

            elapsed += chunk

        return False

    # ------------------------------------------------------------------
    # Core digest building
    # ------------------------------------------------------------------

    async def _build_digest(self) -> dict[str, Any]:
        """Build a digest covering the current period.

        Queries stories from the last 24 hours (or since the last digest),
        joins with scores, categories, and summaries, groups by category,
        ranks by composite score, and formats as JSON and markdown.

        Returns:
            A dict with keys: content_json, content_md, story_count,
            period_start, period_end.
        """
        now = datetime.now(timezone.utc)
        period_end = now.isoformat()

        # Determine period start: use end of last digest, or 24 hours ago.
        period_start = self._get_last_digest_end() or (now - timedelta(hours=24)).isoformat()

        # Query stories with their latest score, categories, and summaries.
        rows = self._query_stories(period_start, period_end)

        # Group by category and rank.
        categories_map: dict[str, list[dict[str, Any]]] = {}
        # Track which story has been placed in its primary (highest-scored) category
        # to handle deduplication per ADR-002.
        story_primary_category: dict[int, str] = {}
        story_all_categories: dict[int, list[str]] = {}

        for row in rows:
            story_id = row["story_id"]
            category = row["category"]
            composite = row["composite"]

            # Collect all categories for this story.
            if story_id not in story_all_categories:
                story_all_categories[story_id] = []
            if category not in story_all_categories[story_id]:
                story_all_categories[story_id].append(category)

            # Track the highest-scored category for this story.
            if story_id not in story_primary_category:
                story_primary_category[story_id] = category
            else:
                # Already assigned; we handle dedup below.
                pass

            if category not in categories_map:
                categories_map[category] = []

            # Avoid duplicating the same story within the same category.
            if any(s["story_id"] == story_id for s in categories_map[category]):
                continue

            categories_map[category].append(row)

        # Deduplicate: show each story only in its highest-scored category.
        # A story's primary category is the first one encountered (rows are
        # ordered by composite DESC, so the first category seen has the
        # highest-scored context).
        deduplicated: dict[str, list[dict[str, Any]]] = {}
        for category, stories in categories_map.items():
            for story in stories:
                sid = story["story_id"]
                if story_primary_category.get(sid) == category:
                    if category not in deduplicated:
                        deduplicated[category] = []
                    deduplicated[category].append(story)

        # Apply per-category limit and build output.
        content_stories: dict[str, list[dict[str, Any]]] = {}
        total_count = 0
        for category in sorted(deduplicated.keys()):
            stories = deduplicated[category]
            # Sort by composite descending (should already be sorted, but ensure).
            stories.sort(key=lambda s: s["composite"], reverse=True)
            limited = stories[: self.stories_per_category]
            formatted = []
            for s in limited:
                entry = {
                    "title": s["title"],
                    "url": s["url"],
                    "hn_url": f"https://news.ycombinator.com/item?id={s['story_id']}",
                    "signal_score": s["composite"],
                    "categories": story_all_categories.get(s["story_id"], [s["category"]]),
                    "summary": s["summary"],
                    "score": s["score"],
                    "comments": s["comments"],
                    "posted_at": s["posted_at"],
                }
                formatted.append(entry)
            content_stories[category] = formatted
            total_count += len(formatted)

        content_json = json.dumps(content_stories, indent=2)
        content_md = self._render_markdown(content_stories)

        return {
            "content_json": content_json,
            "content_md": content_md,
            "story_count": total_count,
            "period_start": period_start,
            "period_end": period_end,
        }

    def _get_last_digest_end(self) -> str | None:
        """Return the period_end of the most recent digest, or None if no digests exist.

        Returns:
            ISO 8601 UTC string of the last digest's period_end, or None.
        """
        cursor = self.db_conn.execute(
            "SELECT period_end FROM digests ORDER BY created_at DESC LIMIT 1"
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def _query_stories(
        self, period_start: str, period_end: str
    ) -> list[dict[str, Any]]:
        """Query stories for the given period with scores, categories, and summaries.

        Performs LEFT JOINs with scores (latest per story), categories, and
        summaries (validated preferred). Stories without a category or score
        are still included with defaults.

        Args:
            period_start: ISO 8601 UTC start of the period.
            period_end: ISO 8601 UTC end of the period.

        Returns:
            A list of dicts, one per story-category combination, ordered
            by composite score descending.
        """
        query = """
            SELECT
                s.id           AS story_id,
                s.title        AS title,
                s.url          AS url,
                s.score        AS score,
                s.comments     AS comments,
                s.posted_at    AS posted_at,
                COALESCE(c.category, 'uncategorized') AS category,
                COALESCE(sc.composite, 0.0)           AS composite,
                COALESCE(
                    CASE
                        WHEN sm.status = 'validated' THEN sm.summary_text
                        ELSE NULL
                    END,
                    'No summary available'
                ) AS summary
            FROM stories s
            LEFT JOIN categories c
                ON c.story_id = s.id
            LEFT JOIN (
                SELECT story_id, composite,
                       ROW_NUMBER() OVER (PARTITION BY story_id ORDER BY scored_at DESC) AS rn
                FROM scores
            ) sc
                ON sc.story_id = s.id AND sc.rn = 1
            LEFT JOIN (
                SELECT story_id, summary_text, status,
                       ROW_NUMBER() OVER (PARTITION BY story_id ORDER BY generated_at DESC) AS rn
                FROM summaries
            ) sm
                ON sm.story_id = s.id AND sm.rn = 1
            WHERE s.first_seen >= ? AND s.first_seen < ?
            ORDER BY composite DESC
        """
        cursor = self.db_conn.execute(query, (period_start, period_end))
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def _render_markdown(self, content: dict[str, list[dict[str, Any]]]) -> str:
        """Render the digest content as a markdown string.

        Args:
            content: Dict mapping category names to lists of story entries.

        Returns:
            A markdown-formatted digest string.
        """
        lines: list[str] = []
        lines.append("# HN Digest")
        lines.append("")

        for category in sorted(content.keys()):
            stories = content[category]
            lines.append(f"## {category}")
            lines.append("")
            for story in stories:
                title = story["title"]
                url = story.get("url") or story["hn_url"]
                hn_url = story["hn_url"]
                signal = story["signal_score"]
                summary = story["summary"]
                score = story["score"]
                comments = story["comments"]
                posted_at = story["posted_at"]
                cats = ", ".join(story.get("categories", []))

                lines.append(f"- **[{title}]({url})**")
                lines.append(f"  [HN Discussion]({hn_url}) | Signal: {signal:.1f} | {cats}")
                lines.append(f"  {summary}")
                lines.append(f"  Score: {score} points | Comments: {comments} | Posted: {posted_at}")
                lines.append("")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Persist and publish
    # ------------------------------------------------------------------

    async def _persist_and_publish(self, digest: dict[str, Any]) -> None:
        """Insert the digest into the database and publish to the digest channel.

        Args:
            digest: The digest dict from ``_build_digest``.
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            self.db_conn.execute(
                """INSERT INTO digests
                   (period_start, period_end, content_json, content_md, story_count, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    digest["period_start"],
                    digest["period_end"],
                    digest["content_json"],
                    digest["content_md"],
                    digest["story_count"],
                    now_iso,
                ),
            )
            self.db_conn.commit()
            logger.info("report-builder: digest persisted to database")
        except sqlite3.Error:
            logger.exception("report-builder: failed to persist digest")

        payload = {
            "period_start": digest["period_start"],
            "period_end": digest["period_end"],
            "story_count": digest["story_count"],
            "content_json": digest["content_json"],
            "content_md": digest["content_md"],
        }
        await self.publish(CHANNEL_DIGEST, payload, msg_type="digest")

    # ------------------------------------------------------------------
    # Abstract method (required by BaseAgent)
    # ------------------------------------------------------------------

    async def process(self, channel: str, message: BusMessage) -> None:
        """Process inbound messages (unused — report-builder uses timer loop).

        Args:
            channel: The channel the message arrived on.
            message: The typed bus message envelope.
        """
