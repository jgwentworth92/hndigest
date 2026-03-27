"""Live end-to-end test: real HN story -> real article fetch -> Gemini summary -> validation."""

import asyncio
from pathlib import Path

import aiohttp
import pytest

from hndigest.mcp.hn_mcp import fetch_top_stories, fetch_item
from hndigest.mcp.llm_mcp import LLMAdapter

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


class TestFullPipelineLive:
    """Fetch a real HN story, extract article, summarize with Gemini, validate."""

    async def test_real_story_summary_and_validation(self) -> None:
        """End-to-end: HN API -> article fetch -> Gemini summary -> validation."""
        async with aiohttp.ClientSession() as session:
            # 1. Get top stories from HN
            story_ids = await fetch_top_stories(session)
            assert len(story_ids) > 0, "No stories returned from HN API"

            # 2. Find a story with a URL (skip Ask HN etc.)
            story = None
            for sid in story_ids[:20]:
                item = await fetch_item(session, sid)
                if item and item.get("url"):
                    story = item
                    break

            assert story is not None, "No story with URL found in top 20"
            print(f"\n--- Story: {story['title']}")
            print(f"--- URL: {story['url']}")
            print(f"--- Score: {story.get('score', 0)} | Comments: {story.get('descendants', 0)}")

            # 3. Fetch article text (basic extraction)
            try:
                timeout = aiohttp.ClientTimeout(total=10)
                async with session.get(story["url"], timeout=timeout) as resp:
                    if resp.status != 200:
                        pytest.fail(f"Failed to fetch article: HTTP {resp.status}")
                    html = await resp.text()
            except Exception as exc:
                pytest.fail(f"Failed to fetch article URL: {exc}")

            # Basic text extraction (strip HTML tags for now)
            import re
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text).strip()
            article_text = text[:8000]

            assert len(article_text) > 100, f"Article text too short ({len(article_text)} chars)"
            print(f"--- Article text: {len(article_text)} chars")
            print(f"--- First 200 chars: {article_text[:200]}...")

        # 4. Summarize with Gemini
        adapter = LLMAdapter(config_path=_CONFIG_DIR / "llm.yaml")
        try:
            summary = await adapter.generate_summary(article_text, story["title"])
            assert len(summary) > 20, f"Summary too short: {summary!r}"
            print(f"\n--- Summary ({len(summary)} chars):")
            print(f"    {summary}")

            # 5. Validate the summary against source
            validation = await adapter.validate_summary(summary, article_text)
            assert "result" in validation
            assert validation["result"] in ("pass", "fail")
            print(f"\n--- Validation result: {validation['result']}")
            if validation.get("details"):
                for claim in validation["details"][:3]:
                    found = claim.get("found_in_source", "?")
                    claim_text = claim.get("claim", "?")
                    print(f"    [{found}] {claim_text[:120]}")
        finally:
            await adapter.close()
