"""Categorizer agent — assigns topic categories to stories via deterministic rules."""

from __future__ import annotations

import logging
import pathlib
import sqlite3
from datetime import datetime, timezone
from urllib.parse import urlparse

from hndigest.agents.base import BaseAgent
from hndigest.bus import CHANNEL_CATEGORY, CHANNEL_STORY, MessageBus
from hndigest.config import CategoriesConfig, CategoryRule
from hndigest.models import BusMessage, CategoryAssignment, CategoryPayload, StoryPayload

logger = logging.getLogger(__name__)

# Project root: three levels up from this file (src/hndigest/agents/ -> project root).
_PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[3]
_DEFAULT_CONFIG_PATH = _PROJECT_ROOT / "config" / "categories.yaml"


class CategorizerAgent(BaseAgent):
    """Agent that assigns topic categories to each story.

    Runs three deterministic rule types in order: domain mapping, keyword
    matching, and HN type mapping. A story can match multiple categories.
    If no rules match, the story is assigned "uncategorized". All rules
    are loaded from a YAML config file.

    Args:
        bus: The shared message bus.
        db_conn: An open sqlite3 connection.
        config_path: Path to categories YAML config. Defaults to
            ``config/categories.yaml`` relative to project root.
    """

    def __init__(
        self,
        bus: MessageBus,
        db_conn: sqlite3.Connection,
        config_path: str | pathlib.Path | None = None,
    ) -> None:
        super().__init__(
            name="categorizer",
            bus=bus,
            subscriptions=[CHANNEL_STORY],
            publications=[CHANNEL_CATEGORY],
        )
        self.db_conn = db_conn

        resolved_path = pathlib.Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        self._config: CategoriesConfig = CategoriesConfig.from_yaml(resolved_path)
        self._categories: dict[str, CategoryRule] = self._config.categories
        self._hn_type_mappings: dict[str, str] = self._config.hn_type_mappings
        self._default_category: str = self._config.default_category

        logger.info(
            "Loaded %d categories and %d HN type mappings",
            len(self._categories),
            len(self._hn_type_mappings),
        )

    @staticmethod
    def _extract_domain(url: str | None) -> str | None:
        """Extract the domain from a URL.

        Args:
            url: A full URL string, or None.

        Returns:
            The domain (e.g. ``"github.com"``), or None if the URL is
            None or cannot be parsed.
        """
        if not url:
            return None
        try:
            parsed = urlparse(url)
            hostname = parsed.hostname
            if hostname:
                return hostname.lower()
        except Exception:
            logger.debug("Failed to parse URL: %s", url)
        return None

    async def process(self, channel: str, message: BusMessage) -> None:
        """Categorize a story and persist/publish the results.

        Runs three rule types in order, collecting all matching categories:
        1. Domain mapping — extract domain from URL, check against each
           category's domains list.
        2. Keyword matching — check lowercased title against each
           category's keywords list.
        3. HN type mapping — map hn_type to a category via config.

        If no categories match, assigns the default category ("uncategorized").

        Args:
            channel: The channel the message arrived on.
            message: The typed bus message envelope containing a StoryPayload.
        """
        payload: StoryPayload = message.payload  # type: ignore[assignment]
        story_id: int = payload.story_id
        title: str = payload.title
        url: str | None = payload.url
        hn_type: str = payload.hn_type

        matched: list[CategoryAssignment] = []

        # 1. Domain mapping
        domain = self._extract_domain(url)
        if domain:
            for cat_name, cat_rules in self._categories.items():
                cat_domains = cat_rules.domains
                if domain in cat_domains:
                    matched.append(CategoryAssignment(category=cat_name, method="domain"))

        # 2. Keyword matching
        title_lower = title.lower()
        for cat_name, cat_rules in self._categories.items():
            cat_keywords = cat_rules.keywords
            for keyword in cat_keywords:
                if keyword.lower() in title_lower:
                    # Avoid duplicate category from same rule type
                    if not any(
                        m.category == cat_name and m.method == "keyword"
                        for m in matched
                    ):
                        matched.append(CategoryAssignment(category=cat_name, method="keyword"))
                    break

        # 3. HN type mapping
        mapped_category = self._hn_type_mappings.get(hn_type)
        if mapped_category:
            matched.append(CategoryAssignment(category=mapped_category, method="hn_type"))

        # Default if nothing matched
        if not matched:
            matched.append(CategoryAssignment(category=self._default_category, method="uncategorized"))

        # Persist to categories table
        now_iso = datetime.now(timezone.utc).isoformat()
        for entry in matched:
            try:
                self.db_conn.execute(
                    """INSERT INTO categories (story_id, category, method, categorized_at)
                       VALUES (?, ?, ?, ?)""",
                    (story_id, entry.category, entry.method, now_iso),
                )
            except sqlite3.Error:
                logger.exception(
                    "categorizer: failed to insert category %s for story %d",
                    entry.category,
                    story_id,
                )
        try:
            self.db_conn.commit()
        except sqlite3.Error:
            logger.exception("categorizer: failed to commit categories for story %d", story_id)

        # Publish to category channel
        category_payload = CategoryPayload(
            story_id=story_id,
            categories=matched,
        )
        await self.publish(CHANNEL_CATEGORY, category_payload, msg_type="category")

        category_names = [m.category for m in matched]
        logger.info(
            "categorizer: story %d -> %s",
            story_id,
            ", ".join(category_names),
        )
