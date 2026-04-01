"""Pydantic v2 response models for the hndigest REST API.

These models define the shape of JSON responses returned by API
endpoints.  They are distinct from the inter-agent payload models in
``hndigest.models`` -- those are internal bus messages, while these are
external-facing API contracts.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class DigestSummary(BaseModel):
    """Summary representation of a digest for list endpoints.

    Attributes:
        id: Database primary key.
        period_start: ISO 8601 UTC start of the digest period.
        period_end: ISO 8601 UTC end of the digest period.
        story_count: Number of stories included in this digest.
        created_at: ISO 8601 UTC timestamp when the digest was created.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    period_start: str
    period_end: str
    story_count: int
    created_at: str


class DigestDetail(BaseModel):
    """Full digest representation including rendered content.

    Attributes:
        id: Database primary key.
        period_start: ISO 8601 UTC start of the digest period.
        period_end: ISO 8601 UTC end of the digest period.
        story_count: Number of stories included in this digest.
        created_at: ISO 8601 UTC timestamp when the digest was created.
        content_json: Parsed structured content (categories and stories).
        content_md: Rendered markdown content of the digest.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    period_start: str
    period_end: str
    story_count: int
    created_at: str
    content_json: dict[str, Any] | list[Any]
    content_md: str


class StorySummary(BaseModel):
    """Summary representation of a story for list endpoints.

    Attributes:
        id: Hacker News item ID.
        title: Story title text.
        url: External URL, or None for self-posts.
        score: HN upvote score at last collection.
        comments: Comment (descendant) count at last collection.
        hn_type: Classified type: story, ask, show, or job.
        posted_at: ISO 8601 UTC timestamp of original posting.
        composite_score: Weighted composite score, or None if unscored.
        categories: List of assigned category slugs.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    url: str | None = None
    score: int = 0
    comments: int = 0
    hn_type: str = "story"
    posted_at: str = ""
    composite_score: float | None = None
    categories: list[str] = []


class StoryDetail(BaseModel):
    """Full story representation with article, summary, and scoring data.

    Attributes:
        id: Hacker News item ID.
        title: Story title text.
        url: External URL, or None for self-posts.
        score: HN upvote score at last collection.
        comments: Comment (descendant) count at last collection.
        hn_type: Classified type: story, ask, show, or job.
        posted_at: ISO 8601 UTC timestamp of original posting.
        composite_score: Weighted composite score, or None if unscored.
        categories: List of assigned category slugs.
        article_text: Extracted article text, or None if not fetched.
        article_fetch_status: Fetch outcome: success, failed, no_url, or None.
        summary_text: LLM-generated summary, or None if not summarized.
        summary_status: Summary pipeline status, or None.
        validation_result: Validation outcome: pass, fail, or None.
        score_components: Breakdown of individual scoring signals, or None.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    url: str | None = None
    score: int = 0
    comments: int = 0
    hn_type: str = "story"
    posted_at: str = ""
    composite_score: float | None = None
    categories: list[str] = []
    article_text: str | None = None
    article_fetch_status: str | None = None
    summary_text: str | None = None
    summary_status: str | None = None
    validation_result: str | None = None
    score_components: dict[str, Any] | None = None


class CategoryCount(BaseModel):
    """Category with its story count for breakdown endpoints.

    Attributes:
        category: Category slug (e.g. ai_ml, webdev).
        count: Number of stories in this category.
    """

    model_config = ConfigDict(from_attributes=True)

    category: str
    count: int


class AgentStatus(BaseModel):
    """Status snapshot for a single agent.

    Attributes:
        name: Agent name identifier.
        status: Current status: running, stopped, crashed, unhealthy, or failed.
        last_heartbeat_ago: Seconds since the last heartbeat was received.
        messages_processed: Total messages processed by this agent.
        restart_count: Number of times the supervisor has restarted this agent.
    """

    model_config = ConfigDict(from_attributes=True)

    name: str
    status: str
    last_heartbeat_ago: float
    messages_processed: int
    restart_count: int


class HealthResponse(BaseModel):
    """System health response including agent statuses and uptime.

    Attributes:
        status: Overall system status (e.g. healthy, degraded).
        uptime_seconds: Seconds since the supervisor started.
        agents: Mapping of agent name to its current status snapshot.
    """

    model_config = ConfigDict(from_attributes=True)

    status: str
    uptime_seconds: float
    agents: dict[str, AgentStatus]


class DigestGenerateResponse(BaseModel):
    """Response returned after triggering on-demand digest generation.

    Attributes:
        story_count: Number of stories included in the generated digest.
        content_md: Rendered markdown content of the digest.
        period_start: ISO 8601 UTC start of the digest period.
        period_end: ISO 8601 UTC end of the digest period.
    """

    model_config = ConfigDict(from_attributes=True)

    story_count: int
    content_md: str
    period_start: str
    period_end: str
