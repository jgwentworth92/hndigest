"""Orchestrator agent — priority dispatch and daily token budget gating.

Sits between the collector and downstream agents, making dispatch
decisions based on composite priority scores and a daily token budget.
Stories above the threshold are dispatched for fetching and summarization;
stories below are logged as skipped. An optional LLM mode resolves
ambiguous cases near the threshold boundary.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from hndigest.agents.base import BaseAgent
from hndigest.bus import (
    CHANNEL_ARTICLE,
    CHANNEL_FETCH_REQUEST,
    CHANNEL_SCORE,
    CHANNEL_STORY,
    CHANNEL_SUMMARIZE_REQUEST,
    MessageBus,
)

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "orchestrator.yaml"


class OrchestratorAgent(BaseAgent):
    """Gates expensive pipeline operations behind priority thresholds and a token budget.

    Subscribes to the story, score, and article channels. Stories are
    collected and held until their score arrives. The orchestrator then
    evaluates whether to dispatch the story for fetching based on the
    composite score, the configured threshold, and remaining daily budget.
    When an article arrives (after fetching), a summarize_request is
    published to trigger downstream summarization.

    Args:
        bus: The shared message bus instance.
        db_conn: An open sqlite3 connection to the hndigest database.
        config_path: Path to the orchestrator YAML config file. Defaults
            to ``config/orchestrator.yaml`` relative to the project root.
    """

    def __init__(
        self,
        bus: MessageBus,
        db_conn: sqlite3.Connection,
        config_path: str | Path | None = None,
    ) -> None:
        super().__init__(
            name="orchestrator",
            bus=bus,
            subscriptions=[CHANNEL_STORY, CHANNEL_SCORE, CHANNEL_ARTICLE],
            publications=[CHANNEL_FETCH_REQUEST, CHANNEL_SUMMARIZE_REQUEST],
        )
        self.db_conn = db_conn
        self._config = self._load_config(config_path)

        # In-memory state: stories awaiting their score
        self._pending_stories: dict[int, dict[str, Any]] = {}

        # Budget tracking
        self._daily_budget_remaining: int = self._config["budget"]["daily_token_budget"]
        self._budget_reset_date: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._tokens_per_article: int = self._config["budget"]["tokens_per_article"]

        # Priority thresholds
        self._min_composite_score: float = self._config["priority"]["min_composite_score"]
        self._ambiguity_range: float = self._config["priority"]["ambiguity_range"]

        # Optional LLM mode
        self._use_llm: bool = self._config["llm"]["use_llm"]
        self._llm_adapter: Any = None

        if self._use_llm:
            self._init_llm_adapter()

        logger.info(
            "OrchestratorAgent initialized: threshold=%.1f, ambiguity=%.1f, "
            "budget=%d, tokens_per_article=%d, use_llm=%s",
            self._min_composite_score,
            self._ambiguity_range,
            self._daily_budget_remaining,
            self._tokens_per_article,
            self._use_llm,
        )

    def _load_config(self, config_path: str | Path | None) -> dict[str, Any]:
        """Load and return the orchestrator configuration from YAML.

        Args:
            config_path: Explicit path, or None to use the default.

        Returns:
            Parsed YAML config as a dict.

        Raises:
            FileNotFoundError: If the config file does not exist.
        """
        path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        logger.debug("Loading orchestrator config from %s", path)
        with open(path, "r", encoding="utf-8") as fh:
            config: dict[str, Any] = yaml.safe_load(fh)
        return config

    def _init_llm_adapter(self) -> None:
        """Initialize the LLM adapter for ambiguous-case relevance checks."""
        from hndigest.mcp.llm_mcp import LLMAdapter

        self._llm_adapter = LLMAdapter()
        logger.info("OrchestratorAgent LLM mode enabled")

    async def process(self, channel: str, message: dict[str, Any]) -> None:
        """Route an inbound message based on its channel.

        Args:
            channel: The channel the message arrived on.
            message: The bus message dict with a ``payload`` containing
                channel-specific fields.
        """
        payload: dict[str, Any] = message["payload"]

        if channel == CHANNEL_STORY:
            story_id: int = payload["story_id"]
            self._pending_stories[story_id] = payload
            logger.debug("Stored pending story %d", story_id)

        elif channel == CHANNEL_SCORE:
            story_id = payload["story_id"]
            story = self._pending_stories.get(story_id)
            if story is None:
                logger.warning(
                    "Score arrived for story %d but no pending story found; skipping",
                    story_id,
                )
                return
            await self._evaluate_and_dispatch(story, payload)

        elif channel == CHANNEL_ARTICLE:
            story_id = payload["story_id"]
            await self.publish(
                CHANNEL_SUMMARIZE_REQUEST,
                {
                    "story_id": story_id,
                    "title": payload.get("title", ""),
                    "url": payload.get("url", ""),
                    "article_text": payload.get("article_text", ""),
                },
                msg_type="summarize_request",
            )
            logger.info("Published summarize_request for story %d", story_id)

    async def _evaluate_and_dispatch(
        self, story: dict[str, Any], score_data: dict[str, Any]
    ) -> None:
        """Evaluate a story's priority and decide whether to dispatch it.

        Applies threshold checks, optional LLM relevance assessment for
        ambiguous scores, and budget enforcement before dispatching.

        Args:
            story: The story payload dict stored from the story channel.
            score_data: The score payload dict from the score channel,
                containing at least ``story_id`` and ``composite``.
        """
        story_id: int = score_data["story_id"]
        composite: float = score_data["composite"]

        # Reset budget if we've crossed into a new UTC day
        self._reset_budget_if_needed()

        low_threshold = self._min_composite_score - self._ambiguity_range
        high_threshold = self._min_composite_score + self._ambiguity_range

        used_llm = False

        if composite < low_threshold:
            # Clearly below threshold
            self._log_decision(
                story_id=story_id,
                decision="skipped",
                reason="below threshold",
                priority_score=composite,
                used_llm=False,
            )
            logger.info(
                "Story %d skipped: composite=%.2f below threshold %.2f",
                story_id, composite, low_threshold,
            )

        elif composite >= high_threshold or (
            composite >= self._min_composite_score and not self._use_llm
        ):
            # Clearly above threshold (or above min with LLM off)
            await self._try_dispatch(story_id, story, composite, used_llm=False)

        elif self._use_llm and self._llm_adapter is not None:
            # Within ambiguity range and LLM mode is on
            used_llm = True
            is_relevant = await self._llm_relevance_check(story, composite)
            if is_relevant:
                await self._try_dispatch(story_id, story, composite, used_llm=True)
            else:
                self._log_decision(
                    story_id=story_id,
                    decision="skipped",
                    reason="LLM deemed not relevant",
                    priority_score=composite,
                    used_llm=True,
                )
                logger.info(
                    "Story %d skipped by LLM: composite=%.2f in ambiguity range",
                    story_id, composite,
                )

        else:
            # Within ambiguity range but LLM is off — conservative skip
            self._log_decision(
                story_id=story_id,
                decision="skipped",
                reason="in ambiguity range, LLM disabled",
                priority_score=composite,
                used_llm=False,
            )
            logger.info(
                "Story %d skipped (ambiguity range, no LLM): composite=%.2f",
                story_id, composite,
            )

        # Remove from pending regardless of decision
        self._pending_stories.pop(story_id, None)

    async def _try_dispatch(
        self,
        story_id: int,
        story: dict[str, Any],
        composite: float,
        used_llm: bool,
    ) -> None:
        """Attempt to dispatch a story, checking the token budget first.

        Args:
            story_id: The HN story ID.
            story: The story payload dict.
            composite: The composite priority score.
            used_llm: Whether LLM was consulted for this decision.
        """
        if self._daily_budget_remaining < self._tokens_per_article:
            self._log_decision(
                story_id=story_id,
                decision="budget_exceeded",
                reason="insufficient daily token budget",
                priority_score=composite,
                used_llm=used_llm,
            )
            logger.warning(
                "Story %d budget exceeded: remaining=%d, needed=%d",
                story_id,
                self._daily_budget_remaining,
                self._tokens_per_article,
            )
            return

        # Dispatch: publish fetch_request and deduct budget
        await self.publish(
            CHANNEL_FETCH_REQUEST,
            {
                "story_id": story_id,
                "title": story.get("title", ""),
                "url": story.get("url", ""),
                "priority": composite,
            },
            msg_type="fetch_request",
        )
        self._daily_budget_remaining -= self._tokens_per_article

        self._log_decision(
            story_id=story_id,
            decision="dispatched",
            reason="above threshold",
            priority_score=composite,
            used_llm=used_llm,
        )
        logger.info(
            "Story %d dispatched: composite=%.2f, budget_remaining=%d",
            story_id, composite, self._daily_budget_remaining,
        )

    def _log_decision(
        self,
        story_id: int,
        decision: str,
        reason: str,
        priority_score: float,
        used_llm: bool,
    ) -> None:
        """Persist an orchestrator decision to the database.

        Args:
            story_id: The HN story ID.
            decision: One of "dispatched", "skipped", or "budget_exceeded".
            reason: Human-readable explanation of the decision.
            priority_score: The composite score at decision time.
            used_llm: Whether the LLM was consulted.
        """
        decided_at = datetime.now(timezone.utc).isoformat()
        self.db_conn.execute(
            """INSERT INTO orchestrator_decisions
               (story_id, decision, reason, priority_score,
                budget_remaining, used_llm, decided_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                story_id,
                decision,
                reason,
                round(priority_score, 2),
                self._daily_budget_remaining,
                1 if used_llm else 0,
                decided_at,
            ),
        )
        self.db_conn.commit()

    def _reset_budget_if_needed(self) -> None:
        """Reset the daily token budget if the UTC date has changed."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._budget_reset_date:
            self._daily_budget_remaining = self._config["budget"]["daily_token_budget"]
            self._budget_reset_date = today
            logger.info(
                "Budget reset for new day %s: %d tokens available",
                today,
                self._daily_budget_remaining,
            )

    async def _llm_relevance_check(
        self, story: dict[str, Any], composite: float
    ) -> bool:
        """Ask the LLM whether an ambiguous story is worth fetching.

        Uses the ``orchestrator_relevance`` prompt template from
        config/prompts.yaml. Parses the response for a YES/NO answer.

        Args:
            story: The story payload dict with at least ``title``.
            composite: The composite priority score.

        Returns:
            True if the LLM considers the story relevant, False otherwise.
        """
        if self._llm_adapter is None:
            return False

        title = story.get("title", "Unknown")
        templates = self._llm_adapter._prompts.get("orchestrator_relevance", {})
        system_prompt = templates.get("system", "").strip()
        user_prompt = templates.get("user", "").format(
            title=title,
            composite_score=composite,
        ).strip()

        try:
            response = await self._llm_adapter._call_llm(user_prompt, system_prompt)
            response_upper = response.strip().upper()

            # Check if the response starts with or contains YES
            is_relevant = response_upper.startswith("YES")
            logger.info(
                "LLM relevance check for story '%s': %s (response: %s)",
                title[:60],
                "relevant" if is_relevant else "not relevant",
                response.strip()[:100],
            )
            return is_relevant
        except Exception:
            logger.exception(
                "LLM relevance check failed for story '%s'; defaulting to skip",
                title[:60],
            )
            return False
