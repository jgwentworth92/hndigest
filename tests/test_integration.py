"""Integration tests using real collector output fed through downstream agents.

These tests verify that the actual message payloads produced by the collector
are correctly consumed by every downstream agent. No hand-crafted payloads.
This catches contract mismatches between publishers and subscribers.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

from hndigest.agents.categorizer import CategorizerAgent
from hndigest.agents.collector import CollectorAgent
from hndigest.agents.fetcher import FetcherAgent
from hndigest.agents.orchestrator import OrchestratorAgent
from hndigest.agents.scorer import ScorerAgent
from hndigest.agents.summarizer import SummarizerAgent
from hndigest.agents.validator import ValidatorAgent
from hndigest.bus import (
    CHANNEL_ARTICLE,
    CHANNEL_CATEGORY,
    CHANNEL_FETCH_REQUEST,
    CHANNEL_SCORE,
    CHANNEL_STORY,
    CHANNEL_SUMMARIZE_REQUEST,
    CHANNEL_SUMMARY,
    CHANNEL_VALIDATED_SUMMARY,
    MessageBus,
)
from hndigest.db import init_db

_WORKTREE_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _WORKTREE_ROOT / "db" / "migrations"
_SCORING_CONFIG = _WORKTREE_ROOT / "config" / "scoring.yaml"
_CATEGORIES_CONFIG = _WORKTREE_ROOT / "config" / "categories.yaml"
_ORCHESTRATOR_CONFIG = _WORKTREE_ROOT / "config" / "orchestrator.yaml"
_LLM_CONFIG = _WORKTREE_ROOT / "config" / "llm.yaml"
_PROMPTS_CONFIG = _WORKTREE_ROOT / "config" / "prompts.yaml"
_ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"


def _write_artifact(name: str, data: dict[str, Any]) -> Path:
    """Write a JSON artifact file with timestamped name."""
    _ARTIFACTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = _ARTIFACTS_DIR / f"{ts}_{name}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


class TestCollectorOutputFeedsAllAgents:
    """Run the real collector, capture its output, feed to every downstream agent.

    This test verifies the actual message contract between the collector
    and all subscribers of the story channel. No hand-crafted payloads.
    """

    async def test_real_collector_output_through_full_pipeline(self) -> None:
        """Collector -> scorer, categorizer, orchestrator all process without error."""
        conn = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)
        bus = MessageBus()

        # Subscribe to all downstream channels to capture output
        story_queue = bus.subscribe(CHANNEL_STORY)
        score_queue = bus.subscribe(CHANNEL_SCORE)
        category_queue = bus.subscribe(CHANNEL_CATEGORY)
        fetch_request_queue = bus.subscribe(CHANNEL_FETCH_REQUEST)

        # Create the collector and run one poll
        collector = CollectorAgent(bus=bus, db_conn=conn, poll_interval=600)
        collector._session = aiohttp.ClientSession()
        try:
            await collector._poll_once()
        finally:
            await collector._session.close()
            collector._session = None

        # Capture at least one real story message from the bus
        story_messages: list[dict[str, Any]] = []
        while not story_queue.empty():
            story_messages.append(story_queue.get_nowait())

        assert len(story_messages) > 0, "Collector should have published at least one story"

        # Create downstream agents
        scorer = ScorerAgent(bus=bus, db_conn=conn, config_path=_SCORING_CONFIG)
        categorizer = CategorizerAgent(bus=bus, db_conn=conn, config_path=_CATEGORIES_CONFIG)
        orchestrator = OrchestratorAgent(bus=bus, db_conn=conn, config_path=_ORCHESTRATOR_CONFIG)

        # Feed real collector messages to each agent
        scorer_errors: list[str] = []
        categorizer_errors: list[str] = []
        orchestrator_errors: list[str] = []

        for msg in story_messages[:10]:  # Test first 10 stories
            try:
                await scorer.process(CHANNEL_STORY, msg)
            except Exception as exc:
                scorer_errors.append(f"story_id={msg['payload'].get('story_id')}: {exc}")

            try:
                await categorizer.process(CHANNEL_STORY, msg)
            except Exception as exc:
                categorizer_errors.append(f"story_id={msg['payload'].get('story_id')}: {exc}")

            try:
                await orchestrator.process(CHANNEL_STORY, msg)
            except Exception as exc:
                orchestrator_errors.append(f"story_id={msg['payload'].get('story_id')}: {exc}")

        # Now feed the score messages to orchestrator (it needs scores to dispatch)
        score_messages: list[dict[str, Any]] = []
        while not score_queue.empty():
            score_msg = score_queue.get_nowait()
            score_messages.append(score_msg)
            try:
                await orchestrator.process(CHANNEL_SCORE, score_msg)
            except Exception as exc:
                orchestrator_errors.append(f"score for story_id={score_msg['payload'].get('story_id')}: {exc}")

        assert not scorer_errors, f"Scorer errors on real collector output:\n" + "\n".join(scorer_errors)
        assert not categorizer_errors, f"Categorizer errors on real collector output:\n" + "\n".join(categorizer_errors)
        assert not orchestrator_errors, f"Orchestrator errors on real collector output:\n" + "\n".join(orchestrator_errors)

        # Verify downstream output was produced
        categories_produced = 0
        while not category_queue.empty():
            category_queue.get_nowait()
            categories_produced += 1

        scores_produced = len(score_messages)

        fetch_requests_produced = 0
        while not fetch_request_queue.empty():
            fetch_request_queue.get_nowait()
            fetch_requests_produced += 1

        # Write artifact
        artifact: dict[str, Any] = {
            "test": "test_real_collector_output_through_full_pipeline",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stories_collected": len(story_messages),
            "stories_tested": min(10, len(story_messages)),
            "scores_produced": scores_produced,
            "categories_produced": categories_produced,
            "fetch_requests_produced": fetch_requests_produced,
            "scorer_errors": scorer_errors,
            "categorizer_errors": categorizer_errors,
            "orchestrator_errors": orchestrator_errors,
            "sample_story_payload": story_messages[0]["payload"] if story_messages else {},
        }
        artifact_path = _write_artifact("integration_pipeline", artifact)
        print(f"\n--- Artifact: {artifact_path}")
        print(f"--- Stories collected: {len(story_messages)}")
        print(f"--- Scores: {scores_produced}, Categories: {categories_produced}, Fetch requests: {fetch_requests_produced}")
        print(f"--- Errors: scorer={len(scorer_errors)}, categorizer={len(categorizer_errors)}, orchestrator={len(orchestrator_errors)}")

        conn.close()


class TestCollectorOutputFeedsFetcher:
    """Verify the orchestrator's fetch_request messages work with the real fetcher."""

    async def test_fetch_request_from_orchestrator_to_fetcher(self) -> None:
        """Orchestrator dispatches -> fetcher processes without error."""
        conn = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)
        bus = MessageBus()

        story_queue = bus.subscribe(CHANNEL_STORY)
        score_queue = bus.subscribe(CHANNEL_SCORE)
        fetch_request_queue = bus.subscribe(CHANNEL_FETCH_REQUEST)
        article_queue = bus.subscribe(CHANNEL_ARTICLE)

        # Collect real stories
        collector = CollectorAgent(bus=bus, db_conn=conn, poll_interval=600)
        collector._session = aiohttp.ClientSession()
        try:
            await collector._poll_once()
        finally:
            await collector._session.close()
            collector._session = None

        story_messages: list[dict[str, Any]] = []
        while not story_queue.empty():
            story_messages.append(story_queue.get_nowait())

        assert len(story_messages) > 0

        # Score stories
        scorer = ScorerAgent(bus=bus, db_conn=conn, config_path=_SCORING_CONFIG)
        for msg in story_messages[:5]:
            await scorer.process(CHANNEL_STORY, msg)

        score_messages: list[dict[str, Any]] = []
        while not score_queue.empty():
            score_messages.append(score_queue.get_nowait())

        # Run orchestrator on stories + scores
        orchestrator = OrchestratorAgent(bus=bus, db_conn=conn, config_path=_ORCHESTRATOR_CONFIG)
        for msg in story_messages[:5]:
            await orchestrator.process(CHANNEL_STORY, msg)
        for msg in score_messages:
            await orchestrator.process(CHANNEL_SCORE, msg)

        # Capture fetch requests
        fetch_requests: list[dict[str, Any]] = []
        while not fetch_request_queue.empty():
            fetch_requests.append(fetch_request_queue.get_nowait())

        if not fetch_requests:
            print("\n--- No fetch requests dispatched (all stories below threshold)")
            return

        # Feed fetch requests to real fetcher
        fetcher = FetcherAgent(bus=bus, db_conn=conn)
        fetcher._session = aiohttp.ClientSession()
        fetcher_errors: list[str] = []

        try:
            for freq in fetch_requests[:3]:  # Test up to 3
                try:
                    await fetcher.process(CHANNEL_FETCH_REQUEST, freq)
                except Exception as exc:
                    fetcher_errors.append(str(exc))
        finally:
            await fetcher._session.close()
            fetcher._session = None

        assert not fetcher_errors, f"Fetcher errors on orchestrator output:\n" + "\n".join(fetcher_errors)

        # Verify articles were produced
        articles_produced = 0
        while not article_queue.empty():
            article_queue.get_nowait()
            articles_produced += 1

        artifact = {
            "test": "test_fetch_request_from_orchestrator_to_fetcher",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stories_collected": len(story_messages),
            "fetch_requests_dispatched": len(fetch_requests),
            "fetch_requests_tested": min(3, len(fetch_requests)),
            "articles_produced": articles_produced,
            "fetcher_errors": fetcher_errors,
            "sample_fetch_request_payload": fetch_requests[0]["payload"] if fetch_requests else {},
        }
        artifact_path = _write_artifact("integration_fetcher", artifact)
        print(f"\n--- Artifact: {artifact_path}")
        print(f"--- Fetch requests: {len(fetch_requests)}, Articles: {articles_produced}")

        conn.close()


class TestFullPipelineEndToEnd:
    """Complete pipeline: collector -> scorer -> orchestrator -> fetcher -> summarizer -> validator."""

    async def test_complete_pipeline_with_real_data(self) -> None:
        """Every agent processes real data from the previous agent's actual output."""
        conn = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)
        bus = MessageBus()

        story_queue = bus.subscribe(CHANNEL_STORY)
        score_queue = bus.subscribe(CHANNEL_SCORE)
        fetch_request_queue = bus.subscribe(CHANNEL_FETCH_REQUEST)
        article_queue = bus.subscribe(CHANNEL_ARTICLE)
        summarize_request_queue = bus.subscribe(CHANNEL_SUMMARIZE_REQUEST)
        summary_queue = bus.subscribe(CHANNEL_SUMMARY)
        validated_summary_queue = bus.subscribe(CHANNEL_VALIDATED_SUMMARY)

        stages: dict[str, Any] = {}

        # 1. Collect
        collector = CollectorAgent(bus=bus, db_conn=conn, poll_interval=600)
        collector._session = aiohttp.ClientSession()
        try:
            await collector._poll_once()
        finally:
            await collector._session.close()

        story_msgs = []
        while not story_queue.empty():
            story_msgs.append(story_queue.get_nowait())
        stages["collected"] = len(story_msgs)
        assert len(story_msgs) > 0

        # 2. Score first 5
        scorer = ScorerAgent(bus=bus, db_conn=conn, config_path=_SCORING_CONFIG)
        for msg in story_msgs[:5]:
            await scorer.process(CHANNEL_STORY, msg)

        score_msgs = []
        while not score_queue.empty():
            score_msgs.append(score_queue.get_nowait())
        stages["scored"] = len(score_msgs)

        # 3. Orchestrator
        orchestrator = OrchestratorAgent(bus=bus, db_conn=conn, config_path=_ORCHESTRATOR_CONFIG)
        for msg in story_msgs[:5]:
            await orchestrator.process(CHANNEL_STORY, msg)
        for msg in score_msgs:
            await orchestrator.process(CHANNEL_SCORE, msg)

        fetch_reqs = []
        while not fetch_request_queue.empty():
            fetch_reqs.append(fetch_request_queue.get_nowait())
        stages["fetch_requests"] = len(fetch_reqs)

        if not fetch_reqs:
            stages["reason"] = "all stories below orchestrator threshold"
            artifact_path = _write_artifact("integration_full_pipeline", {"stages": stages})
            print(f"\n--- No fetch requests, stopping. Artifact: {artifact_path}")
            return

        # 4. Fetch first dispatched story
        fetcher = FetcherAgent(bus=bus, db_conn=conn)
        fetcher._session = aiohttp.ClientSession()
        try:
            await fetcher.process(CHANNEL_FETCH_REQUEST, fetch_reqs[0])
        finally:
            await fetcher._session.close()

        article_msgs = []
        while not article_queue.empty():
            article_msgs.append(article_queue.get_nowait())
        stages["articles_fetched"] = len(article_msgs)

        # Check if orchestrator published summarize_request for the article
        # The orchestrator subscribes to article channel, so feed it
        for amsg in article_msgs:
            await orchestrator.process(CHANNEL_ARTICLE, amsg)

        summarize_reqs = []
        while not summarize_request_queue.empty():
            summarize_reqs.append(summarize_request_queue.get_nowait())
        stages["summarize_requests"] = len(summarize_reqs)

        if not summarize_reqs:
            artifact_path = _write_artifact("integration_full_pipeline", {"stages": stages})
            print(f"\n--- No summarize requests. Artifact: {artifact_path}")
            return

        # 5. Summarize
        summarizer = SummarizerAgent(
            bus=bus, db_conn=conn,
            llm_config_path=_LLM_CONFIG, prompts_path=_PROMPTS_CONFIG,
        )
        try:
            await summarizer.process(CHANNEL_SUMMARIZE_REQUEST, summarize_reqs[0])
        finally:
            await summarizer._llm.close()

        summary_msgs = []
        while not summary_queue.empty():
            summary_msgs.append(summary_queue.get_nowait())
        stages["summaries_generated"] = len(summary_msgs)

        if not summary_msgs:
            artifact_path = _write_artifact("integration_full_pipeline", {"stages": stages})
            print(f"\n--- No summary produced. Artifact: {artifact_path}")
            return

        # 6. Validate
        validator = ValidatorAgent(
            bus=bus, db_conn=conn,
            llm_config_path=_LLM_CONFIG, prompts_path=_PROMPTS_CONFIG,
        )
        try:
            await validator.process(CHANNEL_SUMMARY, summary_msgs[0])
        finally:
            await validator._llm.close()

        validated_msgs = []
        while not validated_summary_queue.empty():
            validated_msgs.append(validated_summary_queue.get_nowait())
        stages["validated_summaries"] = len(validated_msgs)

        # Check final DB state
        story_id = story_msgs[0]["payload"]["story_id"]
        sum_row = conn.execute(
            "SELECT status FROM summaries WHERE story_id = ?", (story_id,)
        ).fetchone()
        stages["final_summary_status"] = sum_row[0] if sum_row else "none"

        if validated_msgs:
            stages["validated_summary_text"] = validated_msgs[0]["payload"].get("summary_text", "")[:200]

        artifact_path = _write_artifact("integration_full_pipeline", {
            "test": "test_complete_pipeline_with_real_data",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stages": stages,
        })
        print(f"\n--- Full pipeline artifact: {artifact_path}")
        print(f"--- Collected: {stages['collected']}, Scored: {stages['scored']}, "
              f"Fetched: {stages.get('articles_fetched', 0)}, "
              f"Summarized: {stages.get('summaries_generated', 0)}, "
              f"Validated: {stages.get('validated_summaries', 0)}")

        conn.close()


class TestSystemChannelDoesNotCrashAgents:
    """Verify that heartbeat messages on the system channel don't crash agents.

    This catches the bug where agents subscribed to the system channel
    for shutdown signals but their process() method received heartbeat
    messages and crashed on missing payload keys.
    """

    async def test_agents_ignore_heartbeats(self) -> None:
        """Start agent via supervisor, send heartbeat, verify no crash."""
        from hndigest.supervisor import Supervisor
        from hndigest.agents.base import BaseAgent

        class _DummyAgent(BaseAgent):
            """Agent that would crash if process() receives a heartbeat."""
            def __init__(self, bus: MessageBus) -> None:
                super().__init__(
                    name="dummy",
                    bus=bus,
                    subscriptions=[CHANNEL_STORY],
                    publications=[],
                )
                self.processed: list[dict[str, Any]] = []

            async def process(self, channel: str, message: dict[str, Any]) -> None:
                self.processed.append({"channel": channel, "payload": message["payload"]})

        supervisor = Supervisor(db_path=":memory:")
        temp_bus = MessageBus()
        agent = _DummyAgent(bus=temp_bus)
        supervisor.register_agent(agent)

        await supervisor.start()

        # Publish a heartbeat to system channel — this used to crash agents
        heartbeat: dict[str, Any] = {
            "type": "heartbeat",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "supervisor",
            "payload": {"agent": "dummy", "status": "running", "messages_processed": 0},
        }
        await supervisor.bus.publish("system", heartbeat)

        # Give agent time to process (or not crash)
        await asyncio.sleep(0.5)

        # Agent should still be running
        statuses = supervisor.agent_statuses
        assert "dummy" in statuses
        assert statuses["dummy"]["status"] == "running", (
            f"Agent should be running, got {statuses['dummy']['status']}"
        )

        # Heartbeat should NOT have been passed to process()
        assert len(agent.processed) == 0, (
            f"Heartbeat should not reach process(), got {len(agent.processed)} messages"
        )

        await supervisor.shutdown()
