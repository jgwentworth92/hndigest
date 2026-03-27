"""Categorizer agent — assigns topic categories to stories via deterministic rules."""

import logging
import pathlib
import sqlite3
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import yaml

from hndigest.agents.base import BaseAgent
from hndigest.bus import CHANNEL_CATEGORY, CHANNEL_STORY, MessageBus

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
        self._config = self._load_config(resolved_path)
        self._categories: dict[str, dict[str, list[str]]] = self._config.get("categories", {})
        self._hn_type_mappings: dict[str, str] = self._config.get("hn_type_mappings", {})
        self._default_category: str = self._config.get("default_category", "uncategorized")

    @staticmethod
    def _load_config(path: pathlib.Path) -> dict[str, Any]:
        """Load and return the categories YAML config.

        Args:
            path: Absolute or relative path to the YAML file.

        Returns:
            Parsed config dict.

        Raises:
            FileNotFoundError: If the config file does not exist.
        """
        logger.info("Loading category config from %s", path)
        with open(path, "r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh)
        if not isinstance(config, dict):
            raise ValueError(f"Category config must be a YAML mapping, got {type(config).__name__}")
        logger.info(
            "Loaded %d categories and %d HN type mappings",
            len(config.get("categories", {})),
            len(config.get("hn_type_mappings", {})),
        )
        return config

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

    async def process(self, channel: str, message: dict[str, Any]) -> None:
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
            message: The message payload dict containing story data.
        """
        payload = message.get("payload", {})
        story_id: int = payload.get("story_id")
        title: str = payload.get("title", "")
        url: str | None = payload.get("url")
        hn_type: str = payload.get("hn_type", "story")

        if story_id is None:
            logger.warning("categorizer: received message without story_id, skipping")
            return

        matched: list[dict[str, str]] = []

        # 1. Domain mapping
        domain = self._extract_domain(url)
        if domain:
            for cat_name, cat_rules in self._categories.items():
                cat_domains = cat_rules.get("domains", [])
                if domain in cat_domains:
                    matched.append({"category": cat_name, "method": "domain"})

        # 2. Keyword matching
        title_lower = title.lower()
        for cat_name, cat_rules in self._categories.items():
            cat_keywords = cat_rules.get("keywords", [])
            for keyword in cat_keywords:
                if keyword.lower() in title_lower:
                    # Avoid duplicate category from same rule type
                    if not any(
                        m["category"] == cat_name and m["method"] == "keyword"
                        for m in matched
                    ):
                        matched.append({"category": cat_name, "method": "keyword"})
                    break

        # 3. HN type mapping
        mapped_category = self._hn_type_mappings.get(hn_type)
        if mapped_category:
            matched.append({"category": mapped_category, "method": "hn_type"})

        # Default if nothing matched
        if not matched:
            matched.append({"category": self._default_category, "method": "uncategorized"})

        # Persist to categories table
        now_iso = datetime.now(timezone.utc).isoformat()
        for entry in matched:
            try:
                self.db_conn.execute(
                    """INSERT INTO categories (story_id, category, method, categorized_at)
                       VALUES (?, ?, ?, ?)""",
                    (story_id, entry["category"], entry["method"], now_iso),
                )
            except sqlite3.Error:
                logger.exception(
                    "categorizer: failed to insert category %s for story %d",
                    entry["category"],
                    story_id,
                )
        try:
            self.db_conn.commit()
        except sqlite3.Error:
            logger.exception("categorizer: failed to commit categories for story %d", story_id)

        # Publish to category channel
        await self.publish(
            CHANNEL_CATEGORY,
            {
                "story_id": story_id,
                "categories": matched,
            },
            msg_type="category",
        )

        category_names = [m["category"] for m in matched]
        logger.info(
            "categorizer: story %d -> %s",
            story_id,
            ", ".join(category_names),
        )
