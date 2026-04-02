"""Pydantic v2 payload models for all inter-agent messages.

Every model is frozen (immutable after creation) to prevent accidental
mutation as messages flow through the bus.  All agents publish and
consume these typed payloads instead of raw dicts.

See ADR-005 for the rationale and full scope table.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field


# ── Story (collector -> scorer, categorizer, orchestrator) ───────────


class StoryPayload(BaseModel):
    """Payload published by the collector when a new HN story is discovered.

    Attributes:
        story_id: The Hacker News item ID.
        title: Story title text.
        url: External URL, or None for self-posts (Ask HN, etc.).
        hn_text: HN-hosted body text for self-posts.
        score: Current upvote score at collection time.
        comments: Current comment (descendant) count.
        author: HN username of the poster.
        posted_at: ISO 8601 UTC timestamp of original posting.
        hn_type: Classified type: "story", "ask", "show", or "job".
        endpoints: HN API endpoints the story appeared on.
    """

    model_config = ConfigDict(frozen=True)

    story_id: int
    title: str
    url: str | None = None
    hn_text: str | None = None
    score: int = 0
    comments: int = 0
    author: str = ""
    posted_at: str = ""
    hn_type: str = "story"
    endpoints: list[str] = Field(default_factory=list)


# ── Score (scorer -> orchestrator) ───────────────────────────────────


class ScoreComponents(BaseModel):
    """Individual signal components that feed into the composite score.

    Attributes:
        score_velocity: Percentile-ranked upvote velocity.
        comment_velocity: Percentile-ranked comment velocity.
        front_page_presence: Scaled front-page endpoint count (0-100).
        recency: Recency decay score (0-100).
    """

    model_config = ConfigDict(frozen=True)

    score_velocity: float
    comment_velocity: float
    front_page_presence: int | float
    recency: float


class ScorePayload(BaseModel):
    """Payload published by the scorer after ranking a story.

    Attributes:
        story_id: The Hacker News item ID.
        composite: Weighted composite score (0-100).
        components: Breakdown of individual signal components.
    """

    model_config = ConfigDict(frozen=True)

    story_id: int
    composite: float
    components: ScoreComponents


# ── Category (categorizer -> downstream) ─────────────────────────────


class CategoryAssignment(BaseModel):
    """A single category matched to a story by a specific rule method.

    Attributes:
        category: The category name (e.g. "ai_ml", "webdev").
        method: How the category was assigned: "domain", "keyword",
            "hn_type", or "uncategorized".
    """

    model_config = ConfigDict(frozen=True)

    category: str
    method: str


class CategoryPayload(BaseModel):
    """Payload published by the categorizer after classifying a story.

    Attributes:
        story_id: The Hacker News item ID.
        categories: List of all matched category assignments.
    """

    model_config = ConfigDict(frozen=True)

    story_id: int
    categories: list[CategoryAssignment]


# ── Fetch request (orchestrator -> fetcher) ──────────────────────────


class FetchRequestPayload(BaseModel):
    """Payload published by the orchestrator to request article fetching.

    Attributes:
        story_id: The Hacker News item ID.
        url: The article URL to fetch (may be None for self-posts).
        hn_text: HN-hosted body text, used when url is None.
        title: Story title for logging / context.
        priority: Composite priority score that triggered dispatch.
    """

    model_config = ConfigDict(frozen=True)

    story_id: int
    url: str | None = None
    hn_text: str | None = None
    title: str = ""
    priority: float = 0.0


# ── Article (fetcher -> orchestrator) ────────────────────────────────


class ArticlePayload(BaseModel):
    """Payload published by the fetcher after retrieving article content.

    Attributes:
        story_id: The Hacker News item ID.
        text: Extracted plain-text article content (empty on failure).
        text_hash: SHA-256 hex digest of the text field.
        fetch_status: Outcome: "success", "failed", or "no_url".
    """

    model_config = ConfigDict(frozen=True)

    story_id: int
    text: str = ""
    text_hash: str = ""
    fetch_status: str = "success"


# ── Summarize request (orchestrator -> summarizer) ───────────────────


class SummarizeRequestPayload(BaseModel):
    """Payload published by the orchestrator to request summarization.

    Attributes:
        story_id: The Hacker News item ID.
        priority: Composite priority score at dispatch time.
    """

    model_config = ConfigDict(frozen=True)

    story_id: int
    priority: float = 0.0


# ── Summary (summarizer -> validator) ────────────────────────────────


class SummaryPayload(BaseModel):
    """Payload published by the summarizer after generating a summary.

    Attributes:
        story_id: The Hacker News item ID.
        summary_text: The LLM-generated summary (2-3 sentences).
        source_text_hash: SHA-256 hex digest of the source article text.
    """

    model_config = ConfigDict(frozen=True)

    story_id: int
    summary_text: str
    source_text_hash: str


# ── Validated summary (validator -> report builder) ──────────────────


class ValidatedSummaryPayload(BaseModel):
    """Payload published by the validator after checking a summary.

    Attributes:
        story_id: The Hacker News item ID.
        summary_text: The summary text (may be a retry replacement).
        validation_result: "pass" or "fail".
    """

    model_config = ConfigDict(frozen=True)

    story_id: int
    summary_text: str
    validation_result: str


# ── Orchestrator decision (internal logging payload) ─────────────────


class OrchestratorDecisionPayload(BaseModel):
    """Payload representing an orchestrator dispatch decision.

    Attributes:
        story_id: The Hacker News item ID.
        decision: One of "dispatched", "skipped", or "budget_exceeded".
        reason: Human-readable explanation of the decision.
        priority_score: The composite score at decision time.
        budget_remaining: Daily token budget remaining after decision.
    """

    model_config = ConfigDict(frozen=True)

    story_id: int
    decision: str
    reason: str
    priority_score: float
    budget_remaining: int


# ── Heartbeat (base agent -> system channel) ─────────────────────────


class HeartbeatPayload(BaseModel):
    """Payload for periodic agent liveness heartbeat messages.

    Attributes:
        agent: Name of the agent emitting the heartbeat.
        status: Current agent status (e.g. "running", "stopping").
        messages_processed: Total messages processed by the agent so far.
    """

    model_config = ConfigDict(frozen=True)

    agent: str
    status: str
    messages_processed: int = 0


# ── Digest (report builder -> WebSocket broadcast) ────────────────────


class DigestPayload(BaseModel):
    """Payload published by the report builder when a digest is assembled.

    Attributes:
        period_start: ISO 8601 UTC start of the digest period.
        period_end: ISO 8601 UTC end of the digest period.
        story_count: Number of stories included in the digest.
        content_json: Structured digest data as a JSON string.
        content_md: Rendered markdown digest.
    """

    model_config = ConfigDict(frozen=True)

    period_start: str
    period_end: str
    story_count: int
    content_json: str
    content_md: str


# ── Union of all payload types ───────────────────────────────────────

PayloadType = (
    StoryPayload
    | ScorePayload
    | CategoryPayload
    | FetchRequestPayload
    | ArticlePayload
    | SummarizeRequestPayload
    | SummaryPayload
    | ValidatedSummaryPayload
    | OrchestratorDecisionPayload
    | HeartbeatPayload
    | DigestPayload
)


# ── Bus message envelope ─────────────────────────────────────────────


class BusMessage(BaseModel):
    """Typed envelope for every message that passes through the bus.

    Wraps a typed payload with routing metadata.  The ``type`` field
    identifies which payload variant is carried (e.g. "story", "score",
    "heartbeat").  The ``payload`` field is a discriminated union of all
    known payload types.

    Attributes:
        type: Message type identifier matching the channel / purpose.
        timestamp: UTC datetime when the message was created.
        source: Name of the agent that published this message.
        payload: The typed payload object (one of the PayloadType variants).
    """

    model_config = ConfigDict(frozen=True)

    type: str
    timestamp: datetime
    source: str
    payload: PayloadType
