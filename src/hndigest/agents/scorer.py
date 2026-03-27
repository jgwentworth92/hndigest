"""Scorer agent — ranks stories by measurable signal strength.

Computes a composite score (0-100) from score velocity, comment velocity,
front page presence, and recency. Scoring weights are loaded from a YAML
config file. See SPEC-000 section 5.8.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from hndigest.agents.base import BaseAgent
from hndigest.bus import CHANNEL_SCORE, CHANNEL_STORY, MessageBus

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "scoring.yaml"


class ScorerAgent(BaseAgent):
    """Scores stories using weighted signal components.

    Subscribes to the story channel, computes four signal components
    (score velocity, comment velocity, front page presence, recency),
    produces a weighted composite score, persists to the scores table,
    and publishes to the score channel.

    Args:
        bus: The shared message bus instance.
        db_conn: An open sqlite3 connection to the hndigest database.
        config_path: Path to the scoring YAML config file. Defaults to
            ``config/scoring.yaml`` relative to the project root.
    """

    def __init__(
        self,
        bus: MessageBus,
        db_conn: sqlite3.Connection,
        config_path: str | Path | None = None,
    ) -> None:
        super().__init__(
            name="scorer",
            bus=bus,
            subscriptions=[CHANNEL_STORY],
            publications=[CHANNEL_SCORE],
        )
        self.db_conn = db_conn
        self._config = self._load_config(config_path)
        logger.info(
            "ScorerAgent initialized with weights: %s",
            self._config["weights"],
        )

    def _load_config(self, config_path: str | Path | None) -> dict[str, Any]:
        """Load and return the scoring configuration from YAML.

        Args:
            config_path: Explicit path, or None to use the default.

        Returns:
            Parsed YAML config as a dict.

        Raises:
            FileNotFoundError: If the config file does not exist.
        """
        path = Path(config_path) if config_path else _DEFAULT_CONFIG_PATH
        logger.debug("Loading scoring config from %s", path)
        with open(path, "r", encoding="utf-8") as fh:
            config: dict[str, Any] = yaml.safe_load(fh)
        return config

    async def process(self, channel: str, message: dict[str, Any]) -> None:
        """Score a story received from the story channel.

        Extracts story data from the message payload, computes the four
        signal components, calculates the weighted composite, persists
        to the scores table, and publishes to the score channel.

        Args:
            channel: The channel the message arrived on.
            message: The bus message dict with a ``payload`` containing
                story fields (id, score, comments, posted_at, endpoints).
        """
        if channel != CHANNEL_STORY:
            return

        payload: dict[str, Any] = message["payload"]
        story_id: int = payload["id"]
        score: int = payload.get("score", 0)
        comments: int = payload.get("comments", 0)
        posted_at_str: str = payload["posted_at"]
        endpoints_raw = payload.get("endpoints", "[]")

        # Parse posted_at timestamp
        posted_at = datetime.fromisoformat(posted_at_str)
        if posted_at.tzinfo is None:
            posted_at = posted_at.replace(tzinfo=timezone.utc)

        now = datetime.now(timezone.utc)
        hours_since_posted = max(
            (now - posted_at).total_seconds() / 3600.0, 0.01
        )

        # Parse endpoints
        if isinstance(endpoints_raw, str):
            endpoints: list[str] = json.loads(endpoints_raw)
        elif isinstance(endpoints_raw, list):
            endpoints = endpoints_raw
        else:
            endpoints = []

        # Compute four signal components
        raw_score_velocity = score / hours_since_posted
        raw_comment_velocity = comments / hours_since_posted

        score_velocity = await self._compute_percentile_rank(
            raw_score_velocity, "score_velocity"
        )
        comment_velocity = await self._compute_percentile_rank(
            raw_comment_velocity, "comment_velocity"
        )
        front_page_presence = self._compute_front_page_presence(len(endpoints))
        recency = self._compute_recency(posted_at)

        # Weighted composite
        weights = self._config["weights"]
        composite = (
            weights["score_velocity"] * score_velocity
            + weights["comment_velocity"] * comment_velocity
            + weights["front_page_presence"] * front_page_presence
            + weights["recency"] * recency
        )
        composite = max(0.0, min(100.0, composite))

        # Persist to scores table
        scored_at = now.isoformat()
        self.db_conn.execute(
            """INSERT INTO scores
               (story_id, score_velocity, comment_velocity,
                front_page_presence, recency, composite, scored_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                story_id,
                round(score_velocity, 2),
                round(comment_velocity, 2),
                front_page_presence,
                round(recency, 2),
                round(composite, 2),
                scored_at,
            ),
        )
        self.db_conn.commit()

        logger.info(
            "Scored story %d: composite=%.2f "
            "(sv=%.2f cv=%.2f fp=%d rec=%.2f)",
            story_id,
            composite,
            score_velocity,
            comment_velocity,
            front_page_presence,
            recency,
        )

        # Publish to score channel
        await self.publish(
            CHANNEL_SCORE,
            {
                "story_id": story_id,
                "composite": round(composite, 2),
                "components": {
                    "score_velocity": round(score_velocity, 2),
                    "comment_velocity": round(comment_velocity, 2),
                    "front_page_presence": front_page_presence,
                    "recency": round(recency, 2),
                },
            },
            msg_type="score",
        )

    async def _compute_percentile_rank(
        self, value: float, component: str
    ) -> float:
        """Compute the percentile rank of a value against trailing baseline.

        Queries the score_snapshots table for the trailing N days (from
        config ``baseline_days``) and computes what percentile the given
        value falls at.  If insufficient baseline data exists, returns
        the raw value capped at 100.

        Args:
            value: The raw velocity value to rank.
            component: Which component this is for: ``"score_velocity"``
                or ``"comment_velocity"``.

        Returns:
            A normalized score between 0 and 100.
        """
        baseline_days: int = self._config.get("baseline_days", 7)
        now = datetime.now(timezone.utc)

        # Build cutoff timestamp by subtracting baseline_days
        cutoff_dt = now - timedelta(days=baseline_days)
        cutoff = cutoff_dt.isoformat()

        # Choose the column to compare against
        if component == "score_velocity":
            # Compute velocity from snapshots: score / hours_since_posted
            # We need to join with stories to get posted_at
            rows = self.db_conn.execute(
                """SELECT ss.score * 1.0 /
                          MAX(0.01,
                              (julianday(ss.snapshot_at) - julianday(s.posted_at)) * 24.0
                          ) AS velocity
                   FROM score_snapshots ss
                   JOIN stories s ON ss.story_id = s.id
                   WHERE ss.snapshot_at >= ?
                   ORDER BY velocity""",
                (cutoff,),
            ).fetchall()
        elif component == "comment_velocity":
            rows = self.db_conn.execute(
                """SELECT ss.comments * 1.0 /
                          MAX(0.01,
                              (julianday(ss.snapshot_at) - julianday(s.posted_at)) * 24.0
                          ) AS velocity
                   FROM score_snapshots ss
                   JOIN stories s ON ss.story_id = s.id
                   WHERE ss.snapshot_at >= ?
                   ORDER BY velocity""",
                (cutoff,),
            ).fetchall()
        else:
            return min(value, 100.0)

        if len(rows) < 10:
            # Not enough baseline data — fall back to raw value capped at 100
            logger.debug(
                "Insufficient baseline data for %s (%d rows), "
                "using raw value %.2f",
                component,
                len(rows),
                value,
            )
            return min(value, 100.0)

        velocities = [row[0] for row in rows]
        # Count how many baseline values are less than or equal to the given value
        count_below = sum(1 for v in velocities if v <= value)
        percentile = (count_below / len(velocities)) * 100.0
        return max(0.0, min(100.0, percentile))

    def _compute_front_page_presence(self, endpoint_count: int) -> float:
        """Scale endpoint count to a 0-100 score using config thresholds.

        Args:
            endpoint_count: Number of HN endpoints the story appears on.

        Returns:
            Scaled front page presence score.
        """
        scale: dict[int, int] = self._config["front_page_scale"]
        if endpoint_count <= 0:
            return 0.0
        # Find the matching or highest threshold
        max_key = max(scale.keys())
        if endpoint_count >= max_key:
            return float(scale[max_key])
        return float(scale.get(endpoint_count, 0))

    def _compute_recency(self, posted_at: datetime) -> float:
        """Compute recency score via linear interpolation between decay breakpoints.

        Uses the ``recency_decay`` config mapping hours-since-posted to
        score values.  Linearly interpolates between the configured
        breakpoints.

        Args:
            posted_at: When the story was originally posted (UTC).

        Returns:
            Recency score between 0 and 100.
        """
        now = datetime.now(timezone.utc)
        hours_old = max(0.0, (now - posted_at).total_seconds() / 3600.0)

        decay: dict[int, int] = self._config["recency_decay"]
        # Sort breakpoints by hours ascending
        breakpoints = sorted(decay.items(), key=lambda x: x[0])

        # If younger than the smallest breakpoint, return its score
        if hours_old <= breakpoints[0][0]:
            return float(breakpoints[0][1])

        # If older than the largest breakpoint, return its score
        if hours_old >= breakpoints[-1][0]:
            return float(breakpoints[-1][1])

        # Linear interpolation between surrounding breakpoints
        for i in range(len(breakpoints) - 1):
            h_lo, s_lo = breakpoints[i]
            h_hi, s_hi = breakpoints[i + 1]
            if h_lo <= hours_old <= h_hi:
                fraction = (hours_old - h_lo) / (h_hi - h_lo)
                return s_lo + fraction * (s_hi - s_lo)

        # Fallback (should not reach here)
        return float(breakpoints[-1][1])
