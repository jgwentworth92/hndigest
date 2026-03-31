"""Validator agent — checks summary faithfulness and owns the retry loop.

Subscribes to the summary channel, validates each summary against its
source article via the LLM adapter, and publishes validated summaries
to the validated_summary channel. On validation failure, retries once
with a tighter prompt before rejecting.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hndigest.agents.base import BaseAgent
from hndigest.bus import CHANNEL_SUMMARY, CHANNEL_VALIDATED_SUMMARY, MessageBus
from hndigest.mcp.llm_mcp import LLMAdapter

logger = logging.getLogger(__name__)


class ValidatorAgent(BaseAgent):
    """Agent that validates summaries against source articles.

    Receives summaries from the summary channel, checks each claim
    against the source article text using the LLM validator prompt,
    and publishes passing summaries to the validated_summary channel.
    Owns a single-retry loop: on first validation failure, re-generates
    the summary with a tighter prompt and validates again.

    Args:
        bus: The shared message bus instance.
        db_conn: An open sqlite3 connection for persistence.
        llm_config_path: Optional path to LLM config YAML.
        prompts_path: Optional path to prompts YAML.
    """

    def __init__(
        self,
        bus: MessageBus,
        db_conn: sqlite3.Connection,
        llm_config_path: str | Path | None = None,
        prompts_path: str | Path | None = None,
    ) -> None:
        super().__init__(
            name="validator",
            bus=bus,
            subscriptions=[CHANNEL_SUMMARY],
            publications=[CHANNEL_VALIDATED_SUMMARY],
        )
        self._db = db_conn
        self._llm = LLMAdapter(
            config_path=llm_config_path,
            prompts_path=prompts_path,
        )

    async def start(self) -> None:
        """Start the agent and ensure the LLM adapter is closed on exit."""
        try:
            await super().start()
        finally:
            await self._llm.close()
            logger.info("validator: LLM adapter closed")

    async def process(self, channel: str, message: dict[str, Any]) -> None:
        """Handle an inbound summary message.

        Extracts the summary and source article, validates faithfulness
        via the LLM, and either publishes a validated summary or retries
        once with a tighter prompt before rejecting.

        Args:
            channel: The channel the message arrived on.
            message: The message payload dict.
        """
        payload = message.get("payload", {})
        story_id: int = payload["story_id"]
        summary_text: str = payload["summary_text"]
        source_text_hash: str = payload["source_text_hash"]

        logger.info("validator: processing summary for story_id=%d", story_id)

        # Load the source article text
        article_text = self._load_article_text(story_id)
        if article_text is None:
            logger.warning(
                "validator: no article found for story_id=%d, skipping",
                story_id,
            )
            return

        # First validation attempt
        validation = await self._llm.validate_summary(summary_text, article_text)
        result: str = validation["result"]
        details: list[dict[str, Any]] = validation["details"]

        summary_id = self._get_summary_id(story_id)

        if result == "pass":
            logger.info("validator: summary PASSED for story_id=%d", story_id)
            self._update_summary_status(story_id, "validated")
            if summary_id is not None:
                self._persist_validation(summary_id, "pass", details)
            await self.publish(
                CHANNEL_VALIDATED_SUMMARY,
                {
                    "story_id": story_id,
                    "summary_text": summary_text,
                    "validation_result": "pass",
                },
                msg_type="validated_summary",
            )
            return

        # First attempt failed — persist the failure, then retry
        logger.info(
            "validator: summary FAILED for story_id=%d, attempting retry",
            story_id,
        )
        if summary_id is not None:
            self._persist_validation(summary_id, "fail", details)

        # Retry: generate a new summary with the tighter prompt
        retry_summary = await self._generate_retry_summary(story_id, article_text)

        # Validate the retry summary
        retry_validation = await self._llm.validate_summary(
            retry_summary, article_text
        )
        retry_result: str = retry_validation["result"]
        retry_details: list[dict[str, Any]] = retry_validation["details"]

        if retry_result == "pass":
            logger.info(
                "validator: retry summary PASSED for story_id=%d", story_id
            )
            self._update_summary_status(story_id, "validated", new_text=retry_summary)
            # Re-fetch summary_id in case it changed
            summary_id = self._get_summary_id(story_id)
            if summary_id is not None:
                self._persist_validation(summary_id, "pass", retry_details)
            await self.publish(
                CHANNEL_VALIDATED_SUMMARY,
                {
                    "story_id": story_id,
                    "summary_text": retry_summary,
                    "validation_result": "pass",
                },
                msg_type="validated_summary",
            )
        else:
            logger.warning(
                "validator: retry summary also FAILED for story_id=%d, rejecting",
                story_id,
            )
            self._update_summary_status(story_id, "rejected")
            summary_id = self._get_summary_id(story_id)
            if summary_id is not None:
                self._persist_validation(summary_id, "fail", retry_details)

    def _load_article_text(self, story_id: int) -> str | None:
        """Load article text from the articles table.

        Args:
            story_id: The HN story ID.

        Returns:
            The article text, or None if not found.
        """
        cursor = self._db.execute(
            "SELECT text FROM articles WHERE story_id = ? AND fetch_status = 'success' "
            "ORDER BY fetched_at DESC LIMIT 1",
            (story_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def _get_summary_id(self, story_id: int) -> int | None:
        """Get the latest summary ID for a story.

        Args:
            story_id: The HN story ID.

        Returns:
            The summary ID, or None if not found.
        """
        cursor = self._db.execute(
            "SELECT id FROM summaries WHERE story_id = ? "
            "ORDER BY generated_at DESC LIMIT 1",
            (story_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else None

    def _update_summary_status(
        self,
        story_id: int,
        status: str,
        new_text: str | None = None,
    ) -> None:
        """Update the status of a pending summary.

        Args:
            story_id: The HN story ID.
            status: New status value ("validated" or "rejected").
            new_text: Optional replacement summary text (for retry).
        """
        if new_text is not None:
            self._db.execute(
                "UPDATE summaries SET status = ?, summary_text = ? "
                "WHERE story_id = ? AND status = 'pending_validation'",
                (status, new_text, story_id),
            )
        else:
            self._db.execute(
                "UPDATE summaries SET status = ? "
                "WHERE story_id = ? AND status = 'pending_validation'",
                (status, story_id),
            )
        self._db.commit()
        logger.debug(
            "validator: updated summary status to %r for story_id=%d",
            status,
            story_id,
        )

    def _persist_validation(
        self,
        summary_id: int,
        result: str,
        details: list[dict[str, Any]],
    ) -> None:
        """Insert a validation record into the validations table.

        Args:
            summary_id: The summary ID being validated.
            result: "pass" or "fail".
            details: List of per-claim check results.
        """
        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            "INSERT INTO validations (summary_id, result, details, validated_at) "
            "VALUES (?, ?, ?, ?)",
            (summary_id, result, json.dumps(details), now),
        )
        self._db.commit()
        logger.debug(
            "validator: persisted validation result=%r for summary_id=%d",
            result,
            summary_id,
        )

    async def _generate_retry_summary(
        self, story_id: int, article_text: str
    ) -> str:
        """Generate a retry summary using the tighter summarizer_retry prompt.

        Loads the summarizer_retry templates from the LLM adapter's prompt
        config, formats them with the story title and article text, and
        calls the LLM directly.

        Args:
            story_id: The HN story ID (used to look up the title).
            article_text: The source article text.

        Returns:
            The retry summary text.
        """
        title = self._get_story_title(story_id)
        templates = self._llm._prompts["summarizer_retry"]
        max_chars: int = self._llm._prompts.get("max_article_chars", 8000)

        system_prompt = templates["system"].strip()
        prompt = templates["user"].format(
            title=title,
            article_text=article_text[:max_chars],
        ).strip()

        result = await self._llm._call_llm(prompt, system_prompt)
        logger.info(
            "validator: generated retry summary for story_id=%d (%d chars)",
            story_id,
            len(result),
        )
        return result.strip()

    def _get_story_title(self, story_id: int) -> str:
        """Look up a story title from the stories table.

        Args:
            story_id: The HN story ID.

        Returns:
            The story title, or "Unknown" if not found.
        """
        cursor = self._db.execute(
            "SELECT title FROM stories WHERE id = ?",
            (story_id,),
        )
        row = cursor.fetchone()
        return row[0] if row else "Unknown"
