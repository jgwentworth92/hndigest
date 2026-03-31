"""End-to-end verification tests for Phase 2 content pipeline.

Tests exercise the real web MCP, fetcher, categorizer, scorer, and report
builder with no mocking — real HTTP, real DB, real agents — per CLAUDE.md
testing conventions.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from hndigest.bus import (
    CHANNEL_ARTICLE,
    CHANNEL_CATEGORY,
    CHANNEL_SCORE,
    CHANNEL_STORY,
    MessageBus,
)
from hndigest.db import init_db
from hndigest.mcp import web_mcp
from hndigest.mcp.hn_mcp import fetch_item, fetch_top_stories
from hndigest.agents.categorizer import CategorizerAgent
from hndigest.agents.fetcher import FetcherAgent
from hndigest.agents.report_builder import ReportBuilderAgent
from hndigest.agents.scorer import ScorerAgent

_WORKTREE_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _WORKTREE_ROOT / "db" / "migrations"
_CATEGORIES_CONFIG = _WORKTREE_ROOT / "config" / "categories.yaml"
_SCORING_CONFIG = _WORKTREE_ROOT / "config" / "scoring.yaml"
_ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"


def _write_artifact(name: str, data: dict[str, Any]) -> Path:
    """Write a JSON artifact file with timestamped name.

    Args:
        name: Base name for the artifact file.
        data: Dict to serialize as JSON.

    Returns:
        Path to the written artifact file.
    """
    _ARTIFACTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = _ARTIFACTS_DIR / f"{ts}_{name}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _seed_story(
    conn: "sqlite3.Connection",
    story_id: int,
    title: str,
    url: str | None,
    score: int = 100,
    comments: int = 20,
    author: str = "testuser",
    hn_type: str = "story",
    hn_text: str | None = None,
    hours_ago: float = 2.0,
) -> str:
    """Insert a story row into the database and return the posted_at timestamp.

    Args:
        conn: An open sqlite3 connection.
        story_id: HN story ID.
        title: Story title.
        url: Story URL (None for Ask HN).
        score: HN upvote score.
        comments: Number of comments.
        author: Story author.
        hn_type: One of "story", "show", "ask", "job".
        hn_text: Optional HN text field for self-posts.
        hours_ago: How many hours ago the story was posted.

    Returns:
        ISO 8601 UTC posted_at timestamp.
    """
    import sqlite3

    now = datetime.now(timezone.utc)
    posted_at = (now - timedelta(hours=hours_ago)).isoformat()
    first_seen = (now - timedelta(hours=hours_ago - 0.1)).isoformat()
    last_updated = now.isoformat()

    conn.execute(
        """INSERT INTO stories
           (id, title, url, hn_text, score, comments, author,
            posted_at, hn_type, endpoints, first_seen, last_updated)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            story_id,
            title,
            url,
            hn_text,
            score,
            comments,
            author,
            posted_at,
            hn_type,
            json.dumps(["topstories"]),
            first_seen,
            last_updated,
        ),
    )
    conn.commit()
    return posted_at


# ------------------------------------------------------------------
# Test 1: web_mcp fetch and extract
# ------------------------------------------------------------------


class TestWebMcpFetchAndExtract:
    """Verify web_mcp can fetch a URL and extract article text."""

    async def test_web_mcp_fetch_and_extract(self) -> None:
        """Fetch https://example.com and extract article text."""
        artifact: dict[str, Any] = {
            "test": "test_web_mcp_fetch_and_extract",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        async with aiohttp.ClientSession() as session:
            url = "https://example.com"
            html, status = await web_mcp.fetch_url(session, url)

            assert status == "success", f"Expected status 'success', got '{status}'"
            assert len(html) > 0, "HTML content should not be empty"

            extracted = web_mcp.extract_article_text(html)
            assert isinstance(extracted, str), "Extracted text should be a string"
            # example.com has minimal content; trafilatura may or may not extract
            # text from it. We verify the function runs without error. For richer
            # pages the text will be non-empty.

            artifact["url"] = url
            artifact["fetch_status"] = status
            artifact["html_length"] = len(html)
            artifact["extracted_text_length"] = len(extracted)
            artifact["extracted_first_200"] = extracted[:200]

        artifact_path = _write_artifact("web_mcp_fetch", artifact)
        print(f"\n--- Artifact written to: {artifact_path}")


# ------------------------------------------------------------------
# Test 2: Categorizer with seeded stories
# ------------------------------------------------------------------


class TestCategorizerWithSeededStories:
    """Verify the categorizer assigns correct categories via rules."""

    async def test_categorizer_with_seeded_stories(self) -> None:
        """Seed stories and verify categorizer assigns expected categories."""
        conn = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)
        bus = MessageBus()

        category_queue = bus.subscribe(CHANNEL_CATEGORY)

        categorizer = CategorizerAgent(
            bus=bus,
            db_conn=conn,
            config_path=_CATEGORIES_CONFIG,
        )

        # --- Story A: GitHub URL with "rust compiler" in title ---
        story_a_id = 90001
        _seed_story(
            conn, story_a_id,
            title="New Rust Compiler optimization reduces build times",
            url="https://github.com/rust-lang/rust/pull/12345",
        )
        msg_a: dict[str, Any] = {
            "type": "story",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "test",
            "payload": {
                "story_id": story_a_id,
                "title": "New Rust Compiler optimization reduces build times",
                "url": "https://github.com/rust-lang/rust/pull/12345",
                "hn_type": "story",
            },
        }
        await categorizer.process(CHANNEL_STORY, msg_a)

        # --- Story B: arxiv.org URL with "transformer" in title ---
        story_b_id = 90002
        _seed_story(
            conn, story_b_id,
            title="Efficient Transformer Architectures for Long Sequences",
            url="https://arxiv.org/abs/2401.12345",
        )
        msg_b: dict[str, Any] = {
            "type": "story",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "test",
            "payload": {
                "story_id": story_b_id,
                "title": "Efficient Transformer Architectures for Long Sequences",
                "url": "https://arxiv.org/abs/2401.12345",
                "hn_type": "story",
            },
        }
        await categorizer.process(CHANNEL_STORY, msg_b)

        # --- Story C: job posting ---
        story_c_id = 90003
        _seed_story(
            conn, story_c_id,
            title="Acme Corp is hiring senior engineers",
            url="https://acme.com/careers",
            hn_type="job",
        )
        msg_c: dict[str, Any] = {
            "type": "story",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "test",
            "payload": {
                "story_id": story_c_id,
                "title": "Acme Corp is hiring senior engineers",
                "url": "https://acme.com/careers",
                "hn_type": "job",
            },
        }
        await categorizer.process(CHANNEL_STORY, msg_c)

        # --- Verify categories in DB ---

        # Story A should have "languages" (keywords: rust, compiler) and
        # potentially "tools" (keyword: compiler is not in tools, but let's check)
        rows_a = conn.execute(
            "SELECT category, method FROM categories WHERE story_id = ?",
            (story_a_id,),
        ).fetchall()
        cats_a = {row[0] for row in rows_a}
        assert "languages" in cats_a, (
            f"Story A should have 'languages' category, got {cats_a}"
        )
        # "rust" and "compiler" are both in "languages" keywords

        # Story B should have "ai-ml" (keyword: transformer, domain: arxiv.org)
        # and "research" (domain: arxiv.org)
        rows_b = conn.execute(
            "SELECT category, method FROM categories WHERE story_id = ?",
            (story_b_id,),
        ).fetchall()
        cats_b = {row[0] for row in rows_b}
        assert "ai-ml" in cats_b, (
            f"Story B should have 'ai-ml' category, got {cats_b}"
        )
        assert "research" in cats_b, (
            f"Story B should have 'research' category, got {cats_b}"
        )

        # Story C should have "career" (hn_type: job -> career, keyword: hiring)
        rows_c = conn.execute(
            "SELECT category, method FROM categories WHERE story_id = ?",
            (story_c_id,),
        ).fetchall()
        cats_c = {row[0] for row in rows_c}
        assert "career" in cats_c, (
            f"Story C should have 'career' category, got {cats_c}"
        )

        # Verify bus messages were published
        published = []
        while not category_queue.empty():
            published.append(category_queue.get_nowait())
        assert len(published) == 3, f"Expected 3 category messages, got {len(published)}"

        conn.close()


# ------------------------------------------------------------------
# Test 3: Report builder with seeded data
# ------------------------------------------------------------------


class TestReportBuilderWithSeededData:
    """Verify the report builder produces a correctly structured digest."""

    async def test_report_builder_with_seeded_data(self) -> None:
        """Seed stories, scores, and categories, then build a digest."""
        conn = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)
        bus = MessageBus()

        now = datetime.now(timezone.utc)

        # Seed three stories with varying scores
        stories = [
            (80001, "Rust 2.0 Released", "https://blog.rust-lang.org", "languages", 85.5),
            (80002, "GPT-5 Architecture Paper", "https://arxiv.org/abs/2402.99999", "ai-ml", 72.3),
            (80003, "Docker 30 Released", "https://docker.com/blog", "devops-infra", 60.1),
        ]

        for sid, title, url, category, composite in stories:
            posted_at = _seed_story(conn, sid, title=title, url=url, hours_ago=3.0)

            # Seed score
            conn.execute(
                """INSERT INTO scores
                   (story_id, score_velocity, comment_velocity,
                    front_page_presence, recency, composite, scored_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (sid, 50.0, 30.0, 60, 80.0, composite, now.isoformat()),
            )

            # Seed category
            conn.execute(
                """INSERT INTO categories
                   (story_id, category, method, categorized_at)
                   VALUES (?, ?, ?, ?)""",
                (sid, category, "keyword", now.isoformat()),
            )

        conn.commit()

        # Build digest
        builder = ReportBuilderAgent(
            bus=bus,
            db_conn=conn,
            stories_per_category=5,
        )

        digest = await builder._build_digest()

        # Verify structure
        assert "content_json" in digest, "Digest should have content_json"
        assert "content_md" in digest, "Digest should have content_md"
        assert "story_count" in digest, "Digest should have story_count"
        assert digest["story_count"] == 3, (
            f"Expected 3 stories, got {digest['story_count']}"
        )

        # Verify JSON content has categories
        content = json.loads(digest["content_json"])
        category_names = list(content.keys())
        assert "languages" in category_names, (
            f"Expected 'languages' in categories, got {category_names}"
        )
        assert "ai-ml" in category_names, (
            f"Expected 'ai-ml' in categories, got {category_names}"
        )
        assert "devops-infra" in category_names, (
            f"Expected 'devops-infra' in categories, got {category_names}"
        )

        # Verify stories are ranked by score within categories
        for cat_name, cat_stories in content.items():
            for i in range(len(cat_stories) - 1):
                assert cat_stories[i]["signal_score"] >= cat_stories[i + 1]["signal_score"], (
                    f"Stories in {cat_name} should be ranked by score descending"
                )

        # Verify markdown contains story titles
        md = digest["content_md"]
        assert "Rust 2.0 Released" in md, "Markdown should contain 'Rust 2.0 Released'"
        assert "GPT-5 Architecture Paper" in md, "Markdown should contain 'GPT-5 Architecture Paper'"
        assert "Docker 30 Released" in md, "Markdown should contain 'Docker 30 Released'"

        conn.close()


# ------------------------------------------------------------------
# Test 4: Fetcher live
# ------------------------------------------------------------------


class TestFetcherLive:
    """Verify the fetcher agent fetches a real URL and persists the article."""

    async def test_fetcher_live(self) -> None:
        """Seed a story with a real URL, run fetcher, verify article in DB."""
        conn = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)
        bus = MessageBus()

        article_queue = bus.subscribe(CHANNEL_ARTICLE)

        story_id = 70001
        url = "https://www.python.org/about/"
        _seed_story(conn, story_id, title="About Python", url=url)

        fetcher = FetcherAgent(bus=bus, db_conn=conn)

        # Open an HTTP session for the fetcher (normally done in start()).
        fetcher._session = aiohttp.ClientSession()

        story_message: dict[str, Any] = {
            "type": "story",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "test",
            "payload": {
                "story_id": story_id,
                "url": url,
            },
        }

        artifact: dict[str, Any] = {
            "test": "test_fetcher_live",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            await fetcher.process(CHANNEL_STORY, story_message)
        finally:
            await fetcher._session.close()
            fetcher._session = None

        # Verify article was persisted
        row = conn.execute(
            "SELECT story_id, fetch_status, text FROM articles WHERE story_id = ?",
            (story_id,),
        ).fetchone()
        assert row is not None, "Article row should exist in DB"
        assert row[0] == story_id
        assert row[1] == "success", f"Expected fetch_status='success', got '{row[1]}'"

        article_text = row[2]
        artifact["url"] = url
        artifact["fetch_status"] = row[1]
        artifact["text_length"] = len(article_text)
        artifact["extracted_first_200"] = article_text[:200]

        # Verify article was published to the bus
        article_msg = await asyncio.wait_for(article_queue.get(), timeout=2.0)
        assert article_msg["type"] == "article"
        assert article_msg["payload"]["story_id"] == story_id
        assert article_msg["payload"]["fetch_status"] == "success"

        artifact_path = _write_artifact("fetcher_live", artifact)
        print(f"\n--- Artifact written to: {artifact_path}")
        print(f"--- URL: {url}")
        print(f"--- Fetch status: {row[1]}")
        print(f"--- Text length: {len(article_text)}")

        conn.close()


# ------------------------------------------------------------------
# Test 5: Full pipeline Phase 2
# ------------------------------------------------------------------


class TestFullPipelinePhase2:
    """Live end-to-end: collector -> fetcher -> categorizer -> scorer -> report builder."""

    async def test_full_pipeline_phase2(self) -> None:
        """Fetch a real HN story, run it through the full Phase 2 pipeline."""
        conn = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)
        bus = MessageBus()

        artifact: dict[str, Any] = {
            "test": "test_full_pipeline_phase2",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stages": {},
        }

        # Subscribe to channels to observe pipeline output
        category_queue = bus.subscribe(CHANNEL_CATEGORY)
        score_queue = bus.subscribe(CHANNEL_SCORE)
        article_queue = bus.subscribe(CHANNEL_ARTICLE)

        async with aiohttp.ClientSession() as session:
            # --- Stage 1: Get a real story from HN ---
            story_ids = await fetch_top_stories(session)
            assert len(story_ids) > 0, "No stories returned from HN API"

            # Find a story with a URL
            story = None
            for sid in story_ids[:20]:
                item = await fetch_item(session, sid)
                if item and item.get("url"):
                    story = item
                    break

            assert story is not None, "No story with URL found in top 20"

            story_id = story["id"]
            title = story.get("title", "")
            url = story.get("url", "")
            hn_score = story.get("score", 0)
            comments = story.get("descendants", 0)
            author = story.get("by", "unknown")
            posted_at_ts = story.get("time", 0)
            posted_at = datetime.fromtimestamp(posted_at_ts, tz=timezone.utc).isoformat()
            hn_type = story.get("type", "story")

            artifact["stages"]["hn_story"] = {
                "id": story_id,
                "title": title,
                "url": url,
                "score": hn_score,
                "comments": comments,
            }

            # Insert the story into the database (simulating collector output)
            now = datetime.now(timezone.utc)
            conn.execute(
                """INSERT INTO stories
                   (id, title, url, score, comments, author,
                    posted_at, hn_type, endpoints, first_seen, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    story_id,
                    title,
                    url,
                    hn_score,
                    comments,
                    author,
                    posted_at,
                    hn_type,
                    json.dumps(["topstories"]),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            conn.commit()

            # --- Stage 2: Fetch article ---
            fetcher = FetcherAgent(bus=bus, db_conn=conn)
            fetcher._session = session

            story_message: dict[str, Any] = {
                "type": "story",
                "timestamp": now.isoformat(),
                "source": "test",
                "payload": {
                    "story_id": story_id,
                    "url": url,
                },
            }

            await fetcher.process(CHANNEL_STORY, story_message)
            # Do not close session here — we reuse the shared one.
            fetcher._session = None

            article_row = conn.execute(
                "SELECT fetch_status, text FROM articles WHERE story_id = ?",
                (story_id,),
            ).fetchone()
            assert article_row is not None, "Article should be in DB after fetcher"
            fetch_status = article_row[0]
            article_text = article_row[1]

            artifact["stages"]["fetcher"] = {
                "fetch_status": fetch_status,
                "text_length": len(article_text),
                "first_200": article_text[:200],
            }

        # --- Stage 3: Categorize ---
        categorizer = CategorizerAgent(
            bus=bus, db_conn=conn, config_path=_CATEGORIES_CONFIG,
        )

        cat_message: dict[str, Any] = {
            "type": "story",
            "timestamp": now.isoformat(),
            "source": "test",
            "payload": {
                "story_id": story_id,
                "title": title,
                "url": url,
                "hn_type": hn_type,
            },
        }
        await categorizer.process(CHANNEL_STORY, cat_message)

        cat_rows = conn.execute(
            "SELECT category, method FROM categories WHERE story_id = ?",
            (story_id,),
        ).fetchall()
        categories = [{"category": r[0], "method": r[1]} for r in cat_rows]
        assert len(categories) > 0, "At least one category should be assigned"

        artifact["stages"]["categorizer"] = {
            "categories": categories,
        }

        # --- Stage 4: Score ---
        scorer = ScorerAgent(bus=bus, db_conn=conn, config_path=_SCORING_CONFIG)

        score_message: dict[str, Any] = {
            "type": "story",
            "timestamp": now.isoformat(),
            "source": "test",
            "payload": {
                "story_id": story_id,
                "score": hn_score,
                "comments": comments,
                "posted_at": posted_at,
                "endpoints": json.dumps(["topstories"]),
            },
        }
        await scorer.process(CHANNEL_STORY, score_message)

        score_row = conn.execute(
            "SELECT composite FROM scores WHERE story_id = ?",
            (story_id,),
        ).fetchone()
        assert score_row is not None, "Score should be in DB after scorer"
        composite = score_row[0]
        assert composite > 0, f"Composite score should be > 0, got {composite}"

        artifact["stages"]["scorer"] = {
            "composite": composite,
        }

        # --- Stage 5: Build digest ---
        builder = ReportBuilderAgent(
            bus=bus, db_conn=conn, stories_per_category=10,
        )

        digest = await builder._build_digest()
        assert digest["story_count"] >= 1, (
            f"Digest should contain at least 1 story, got {digest['story_count']}"
        )

        # Verify the story appears in the digest content
        content = json.loads(digest["content_json"])
        found_story = False
        for cat_name, cat_stories in content.items():
            for s in cat_stories:
                if s.get("title") == title:
                    found_story = True
                    break
            if found_story:
                break
        assert found_story, f"Story '{title}' should appear in the digest"

        # Verify markdown contains the title
        assert title in digest["content_md"], (
            f"Story title '{title}' should appear in the markdown digest"
        )

        artifact["stages"]["report_builder"] = {
            "story_count": digest["story_count"],
            "categories_in_digest": list(content.keys()),
            "markdown_length": len(digest["content_md"]),
            "markdown_first_500": digest["content_md"][:500],
        }

        artifact["overall_result"] = "PASS"
        artifact_path = _write_artifact("pipeline_phase2", artifact)
        print(f"\n--- Artifact written to: {artifact_path}")
        print(f"--- Story: {title}")
        print(f"--- Fetch status: {fetch_status}")
        print(f"--- Categories: {[c['category'] for c in categories]}")
        print(f"--- Composite score: {composite}")
        print(f"--- Digest stories: {digest['story_count']}")

        conn.close()
