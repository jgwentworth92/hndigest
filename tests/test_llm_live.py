"""Live end-to-end test: real HN story -> real article fetch -> Gemini summary -> validation.

Writes a detailed artifact to tests/artifacts/ so results can be inspected after the run.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp
import pytest

from hndigest.mcp.hn_mcp import fetch_top_stories, fetch_item
from hndigest.mcp.llm_mcp import LLMAdapter

_CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
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


class TestFullPipelineLive:
    """Fetch a real HN story, extract article, summarize with Gemini, validate."""

    async def test_real_story_summary_and_validation(self) -> None:
        """End-to-end: HN API -> article fetch -> Gemini summary -> validation."""
        artifact: dict[str, Any] = {
            "test": "test_real_story_summary_and_validation",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stages": {},
        }

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

            artifact["stages"]["hn_story"] = {
                "id": story.get("id"),
                "title": story.get("title"),
                "url": story.get("url"),
                "score": story.get("score", 0),
                "comments": story.get("descendants", 0),
                "author": story.get("by"),
            }

            # 3. Fetch article text (basic extraction)
            try:
                timeout = aiohttp.ClientTimeout(total=10)
                async with session.get(story["url"], timeout=timeout) as resp:
                    if resp.status != 200:
                        pytest.fail(f"Failed to fetch article: HTTP {resp.status}")
                    html = await resp.text()
            except Exception as exc:
                pytest.fail(f"Failed to fetch article URL: {exc}")

            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text).strip()
            article_text = text[:8000]

            assert len(article_text) > 100, f"Article text too short ({len(article_text)} chars)"

            artifact["stages"]["article_fetch"] = {
                "raw_html_chars": len(html),
                "extracted_text_chars": len(article_text),
                "first_500_chars": article_text[:500],
            }

        # 4. Summarize with Gemini
        adapter = LLMAdapter(config_path=_CONFIG_DIR / "llm.yaml")
        try:
            summary = await adapter.generate_summary(article_text, story["title"])
            assert len(summary) > 20, f"Summary too short: {summary!r}"

            artifact["stages"]["summarization"] = {
                "provider": adapter.provider,
                "model": adapter.model,
                "summary": summary,
                "summary_chars": len(summary),
            }

            # 5. Validate the summary against source
            validation = await adapter.validate_summary(summary, article_text)
            assert "result" in validation
            assert validation["result"] in ("pass", "fail")

            artifact["stages"]["validation"] = {
                "result": validation["result"],
                "claims": validation.get("details", []),
            }

            artifact["overall_result"] = "PASS"
        except Exception as exc:
            artifact["overall_result"] = "FAIL"
            artifact["error"] = str(exc)
            raise
        finally:
            await adapter.close()
            # Always write artifact, even on failure
            artifact_path = _write_artifact("pipeline_live", artifact)
            print(f"\n--- Artifact written to: {artifact_path}")
            print(f"--- Story: {artifact['stages'].get('hn_story', {}).get('title', '?')}")
            summary_stage = artifact["stages"].get("summarization", {})
            print(f"--- Provider: {summary_stage.get('provider', '?')} / {summary_stage.get('model', '?')}")
            print(f"--- Summary: {summary_stage.get('summary', '?')}")
            val_stage = artifact["stages"].get("validation", {})
            print(f"--- Validation: {val_stage.get('result', '?')}")
            for claim in val_stage.get("claims", [])[:5]:
                found = claim.get("found_in_source", "?")
                claim_text = claim.get("claim", "?")
                print(f"    [{'PASS' if found else 'FAIL'}] {claim_text[:120]}")
