"""Fetcher agent — retrieves article text from story URLs."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import sqlite3
from datetime import datetime, timezone

import aiohttp

from hndigest.agents.base import BaseAgent
from hndigest.bus import CHANNEL_ARTICLE, CHANNEL_FETCH_REQUEST, MessageBus
from hndigest.mcp import web_mcp
from hndigest.models import ArticlePayload, BusMessage, FetchRequestPayload

logger = logging.getLogger(__name__)


class FetcherAgent(BaseAgent):
    """Agent that fetches article text from story URLs and persists results.

    Subscribes to the fetch_request channel, fetches the article content at
    each request's URL (or uses the HN text field for self-posts), computes a
    SHA-256 hash, persists to the articles table, and publishes to the
    article channel.

    The orchestrator dispatches individual fetch requests with priority
    gating, so this agent no longer consumes the raw story stream directly.

    Concurrency is bounded by an asyncio.Semaphore to avoid overwhelming
    remote servers.

    Args:
        bus: The shared message bus instance.
        db_conn: An open sqlite3 connection.
        max_concurrent: Maximum number of concurrent HTTP fetches.
    """

    def __init__(
        self,
        bus: MessageBus,
        db_conn: sqlite3.Connection,
        max_concurrent: int = 10,
    ) -> None:
        super().__init__(
            name="fetcher",
            bus=bus,
            subscriptions=[CHANNEL_FETCH_REQUEST],
            publications=[CHANNEL_ARTICLE],
        )
        self.db_conn = db_conn
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._session: aiohttp.ClientSession | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Subscribe to channels, open HTTP session, and enter the run loop."""
        self._session = aiohttp.ClientSession()
        try:
            await super().start()
        finally:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Message processing
    # ------------------------------------------------------------------

    async def process(self, channel: str, message: BusMessage) -> None:
        """Handle a fetch_request message by fetching its article content.

        The orchestrator dispatches fetch_request messages containing:
        story_id, url, hn_text, title, and priority.

        Args:
            channel: The channel the message arrived on.
            message: The typed bus message envelope containing a FetchRequestPayload.
        """
        payload: FetchRequestPayload = message.payload  # type: ignore[assignment]
        story_id: int = payload.story_id
        url: str | None = payload.url
        hn_text: str | None = payload.hn_text
        title: str = payload.title
        priority: float = payload.priority

        logger.debug(
            "fetcher: received fetch_request for story %d (title=%s, priority=%s)",
            story_id, title, priority,
        )

        # Check if article already exists for this story
        if self._article_exists(story_id):
            logger.debug("fetcher: article already exists for story %d, skipping", story_id)
            return

        now_iso = datetime.now(timezone.utc).isoformat()

        # Stories with no URL (Ask HN, some jobs) use the HN text field
        if not url:
            text = hn_text or ""
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            fetch_status = "no_url"
            self._persist_article(story_id, text, text_hash, fetch_status, now_iso)
            await self._publish_article(story_id, text, text_hash, fetch_status)
            logger.info(
                "fetcher: story %d has no URL, used hn_text (%d chars)",
                story_id, len(text),
            )
            return

        # Fetch the article from the web, bounded by semaphore
        text = ""
        fetch_status = "success"

        async with self._semaphore:
            try:
                assert self._session is not None
                html, url_status = await web_mcp.fetch_url(self._session, url)
                if url_status != "success" or not html:
                    fetch_status = url_status if url_status != "success" else "failed"
                    text = ""
                else:
                    extracted = web_mcp.extract_article_text(html)
                    if not extracted:
                        fetch_status = "failed"
                        text = ""
                    else:
                        text = extracted
            except Exception:
                logger.exception(
                    "fetcher: failed to fetch story %d from %s", story_id, url
                )
                fetch_status = "failed"
                text = ""

        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self._persist_article(story_id, text, text_hash, fetch_status, now_iso)
        await self._publish_article(story_id, text, text_hash, fetch_status)

        logger.info(
            "fetcher: story %d fetch_status=%s (%d chars) from %s",
            story_id, fetch_status, len(text), url,
        )

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def _article_exists(self, story_id: int) -> bool:
        """Check if an article already exists in the database for a story.

        Args:
            story_id: The HN story ID.

        Returns:
            True if an article row already exists for this story_id.
        """
        cursor = self.db_conn.execute(
            "SELECT 1 FROM articles WHERE story_id = ?",
            (story_id,),
        )
        return cursor.fetchone() is not None

    def _persist_article(
        self,
        story_id: int,
        text: str,
        text_hash: str,
        fetch_status: str,
        fetched_at: str,
    ) -> None:
        """Insert an article row into the articles table.

        Args:
            story_id: The HN story ID.
            text: The extracted article text (may be empty on failure).
            text_hash: SHA-256 hex digest of the text.
            fetch_status: One of "success", "failed", "no_url".
            fetched_at: ISO 8601 UTC timestamp.
        """
        try:
            self.db_conn.execute(
                """INSERT INTO articles
                   (story_id, text, text_hash, fetch_status, fetched_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (story_id, text, text_hash, fetch_status, fetched_at),
            )
            self.db_conn.commit()
        except sqlite3.Error:
            logger.exception(
                "fetcher: failed to persist article for story %d", story_id
            )

    # ------------------------------------------------------------------
    # Publishing helper
    # ------------------------------------------------------------------

    async def _publish_article(
        self,
        story_id: int,
        text: str,
        text_hash: str,
        fetch_status: str,
    ) -> None:
        """Publish an article message to the article channel.

        Args:
            story_id: The HN story ID.
            text: The extracted article text.
            text_hash: SHA-256 hex digest of the text.
            fetch_status: The fetch outcome status string.
        """
        article_payload = ArticlePayload(
            story_id=story_id,
            text=text,
            text_hash=text_hash,
            fetch_status=fetch_status,
        )
        await self.publish(CHANNEL_ARTICLE, article_payload, msg_type="article")
