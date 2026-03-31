"""Summarizer agent — generates concise summaries of fetched articles via LLM."""

import hashlib
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from hndigest.agents.base import BaseAgent
from hndigest.bus import CHANNEL_SUMMARIZE_REQUEST, CHANNEL_SUMMARY, MessageBus
from hndigest.mcp.llm_mcp import LLMAdapter

logger = logging.getLogger(__name__)

_MIN_ARTICLE_LENGTH = 100


class SummarizerAgent(BaseAgent):
    """Generates 2-3 sentence summaries for articles using an LLM.

    Subscribes to the summarize_request channel (dispatched by the
    orchestrator), calls the LLM adapter to produce a summary, persists
    the result to the summaries table, and publishes to the summary
    channel for downstream validation.

    Args:
        bus: The shared message bus instance.
        db_conn: An open sqlite3 connection for persistence.
        llm_config_path: Optional path to the LLM YAML config file.
        prompts_path: Optional path to the prompts YAML config file.
    """

    def __init__(
        self,
        bus: MessageBus,
        db_conn: sqlite3.Connection,
        llm_config_path: str | Path | None = None,
        prompts_path: str | Path | None = None,
    ) -> None:
        super().__init__(
            name="summarizer",
            bus=bus,
            subscriptions=[CHANNEL_SUMMARIZE_REQUEST],
            publications=[CHANNEL_SUMMARY],
        )
        self._db = db_conn
        self._llm = LLMAdapter(
            config_path=llm_config_path,
            prompts_path=prompts_path,
        )

    async def start(self) -> None:
        """Start the agent run loop and close the LLM adapter on shutdown."""
        try:
            await super().start()
        finally:
            await self._llm.close()
            logger.info("summarizer: LLM adapter session closed")

    async def process(self, channel: str, message: dict) -> None:
        """Handle a summarize_request message.

        Loads the article text, generates a summary via the LLM adapter,
        computes a source text hash, persists the summary to SQLite, and
        publishes to the summary channel.

        Args:
            channel: The channel the message arrived on.
            message: The message payload dict containing story_id.
        """
        payload = message.get("payload", {})
        story_id: int = payload["story_id"]
        logger.info("summarizer: processing story_id=%d", story_id)

        result = self._load_article(story_id)
        if result is None:
            logger.warning(
                "summarizer: no article found for story_id=%d, skipping",
                story_id,
            )
            return

        article_text, title = result

        now = datetime.now(timezone.utc).isoformat()

        # Short articles get marked as no_summary without LLM call
        if len(article_text) < _MIN_ARTICLE_LENGTH:
            logger.info(
                "summarizer: article too short (%d chars) for story_id=%d, "
                "marking no_summary",
                len(article_text),
                story_id,
            )
            source_text_hash = hashlib.sha256(
                article_text.encode("utf-8")
            ).hexdigest()
            self._db.execute(
                "INSERT INTO summaries "
                "(story_id, summary_text, source_text_hash, status, generated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (story_id, "", source_text_hash, "no_summary", now),
            )
            self._db.commit()
            return

        # Generate summary via LLM
        summary_text = await self._llm.generate_summary(article_text, title)

        source_text_hash = hashlib.sha256(
            article_text.encode("utf-8")
        ).hexdigest()

        # Persist to summaries table
        self._db.execute(
            "INSERT INTO summaries "
            "(story_id, summary_text, source_text_hash, status, generated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (story_id, summary_text, source_text_hash, "pending_validation", now),
        )
        self._db.commit()

        logger.info(
            "summarizer: persisted summary for story_id=%d (status=pending_validation)",
            story_id,
        )

        # Publish to summary channel for the validator
        await self.publish(
            CHANNEL_SUMMARY,
            {
                "story_id": story_id,
                "summary_text": summary_text,
                "source_text_hash": source_text_hash,
            },
            msg_type="summary",
        )

    def _load_article(self, story_id: int) -> tuple[str, str] | None:
        """Load article text and story title from the database.

        Args:
            story_id: The HN story ID to look up.

        Returns:
            A tuple of (article_text, title), or None if the article or
            story was not found.
        """
        row = self._db.execute(
            "SELECT a.text, s.title "
            "FROM articles a "
            "JOIN stories s ON s.id = a.story_id "
            "WHERE a.story_id = ? "
            "ORDER BY a.fetched_at DESC LIMIT 1",
            (story_id,),
        ).fetchone()

        if row is None:
            return None

        return (row[0], row[1])
