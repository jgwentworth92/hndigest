"""End-to-end tests for ADR-006 Amendment 2: WebSocket reliability features.

Tests cover:
- GET /api/runs (list active runs)
- GET /api/runs/{run_id} (run status with RunEntry metadata)
- Pipeline progress events via WebSocket
- Agent heartbeat events via WebSocket
- _task_status helper for cancelled/failed/completed tasks
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from hndigest.api.routes import actions
from hndigest.api.schemas import PipelineProgress
from hndigest.api.routes.actions import RunEntry, _task_status
from hndigest.api.websocket import router as ws_router, CHANNEL_EVENT_MAP
from hndigest.bus import CHANNEL_SYSTEM, MessageBus
from hndigest.db import init_db
from hndigest.models import BusMessage, HeartbeatPayload, PipelineProgressPayload

_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"


def _create_test_app_with_actions() -> tuple[FastAPI, MessageBus]:
    """Build a FastAPI app with actions router and message bus for testing.

    Returns:
        A tuple of (FastAPI app, MessageBus instance).
    """
    conn = init_db(":memory:", migrations_dir=_MIGRATIONS_DIR)
    bus = MessageBus()
    app = FastAPI()
    app.state.db_conn = conn
    app.state.bus = bus
    app.state.supervisor = None
    app.state.active_runs = {}
    app.state.started_at_monotonic = time.monotonic()
    app.include_router(actions.router, prefix="/api")
    app.include_router(ws_router, prefix="/api")
    return app, bus


# ---------------------------------------------------------------------------
# _task_status helper tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_status_running() -> None:
    """_task_status returns 'running' for a task that hasn't completed."""
    event = asyncio.Event()

    async def _wait() -> None:
        await event.wait()

    task = asyncio.create_task(_wait())
    try:
        assert _task_status(task) == "running"
    finally:
        event.set()
        await task


@pytest.mark.asyncio
async def test_task_status_completed() -> None:
    """_task_status returns 'completed' for a successful task."""

    async def _noop() -> None:
        pass

    task = asyncio.create_task(_noop())
    await task
    assert _task_status(task) == "completed"


@pytest.mark.asyncio
async def test_task_status_failed() -> None:
    """_task_status returns 'failed' for a task that raised an exception."""

    async def _fail() -> None:
        raise RuntimeError("boom")

    task = asyncio.create_task(_fail())
    try:
        await task
    except RuntimeError:
        pass
    assert _task_status(task) == "failed"


@pytest.mark.asyncio
async def test_task_status_cancelled() -> None:
    """_task_status returns 'cancelled' for a cancelled task."""

    async def _hang() -> None:
        await asyncio.sleep(999)

    task = asyncio.create_task(_hang())
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert _task_status(task) == "cancelled"


# ---------------------------------------------------------------------------
# GET /api/runs tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_runs_empty() -> None:
    """GET /api/runs returns empty list when no runs are active."""
    app, _bus = _create_test_app_with_actions()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/runs")
        assert response.status_code == 200
        assert response.json() == []


@pytest.mark.asyncio
async def test_list_runs_with_active_run() -> None:
    """GET /api/runs returns active runs with metadata."""
    app, _bus = _create_test_app_with_actions()

    # Inject a fake running task into active_runs
    event = asyncio.Event()

    async def _wait() -> None:
        await event.wait()

    task = asyncio.create_task(_wait())
    started = datetime.now(timezone.utc).isoformat()
    app.state.active_runs["test123"] = RunEntry(
        task=task,
        run_type="pipeline",
        started_at=started,
        progress=PipelineProgress(collected=5, total_stories=10),
    )

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/runs")
            assert response.status_code == 200
            data = response.json()
            assert len(data) == 1

            run = data[0]
            assert run["run_id"] == "test123"
            assert run["type"] == "pipeline"
            assert run["status"] == "running"
            assert run["started_at"] == started
            assert run["progress"]["collected"] == 5
            assert run["progress"]["total_stories"] == 10
    finally:
        event.set()
        await task


@pytest.mark.asyncio
async def test_get_run_status_unknown() -> None:
    """GET /api/runs/{id} returns completed_or_unknown for missing runs."""
    app, _bus = _create_test_app_with_actions()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/runs/nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "nonexistent"
        assert data["status"] == "completed_or_unknown"
        assert data["type"] == "unknown"


@pytest.mark.asyncio
async def test_get_run_status_running() -> None:
    """GET /api/runs/{id} returns running status for active task."""
    app, _bus = _create_test_app_with_actions()

    event = asyncio.Event()

    async def _wait() -> None:
        await event.wait()

    task = asyncio.create_task(_wait())
    app.state.active_runs["abc"] = RunEntry(
        task=task,
        run_type="collect",
        started_at=datetime.now(timezone.utc).isoformat(),
    )

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/runs/abc")
            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "running"
            assert data["type"] == "collect"
    finally:
        event.set()
        await task


@pytest.mark.asyncio
async def test_list_runs_sorted_by_started_at() -> None:
    """GET /api/runs returns runs sorted by started_at descending."""
    app, _bus = _create_test_app_with_actions()

    event = asyncio.Event()

    async def _wait() -> None:
        await event.wait()

    task1 = asyncio.create_task(_wait())
    task2 = asyncio.create_task(_wait())

    app.state.active_runs["old"] = RunEntry(
        task=task1,
        run_type="collect",
        started_at="2026-04-07T10:00:00+00:00",
    )
    app.state.active_runs["new"] = RunEntry(
        task=task2,
        run_type="score",
        started_at="2026-04-07T12:00:00+00:00",
    )

    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/api/runs")
            data = response.json()
            assert len(data) == 2
            assert data[0]["run_id"] == "new"
            assert data[1]["run_id"] == "old"
    finally:
        event.set()
        await asyncio.gather(task1, task2)


# ---------------------------------------------------------------------------
# WebSocket event tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_websocket_receives_heartbeat() -> None:
    """WebSocket broadcasts agent_heartbeat when a heartbeat is published."""
    app, bus = _create_test_app_with_actions()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as _client:
        from starlette.testclient import TestClient

        # Use Starlette's sync TestClient for WebSocket testing
        with TestClient(app) as tc:
            with tc.websocket_connect("/api/events") as ws:
                # Publish a heartbeat to the system channel
                await bus.publish(
                    CHANNEL_SYSTEM,
                    BusMessage(
                        type="heartbeat",
                        timestamp=datetime.now(timezone.utc),
                        source="collector",
                        payload=HeartbeatPayload(
                            agent="collector",
                            status="running",
                            messages_processed=42,
                        ),
                    ),
                )

                # Give the event loop a moment to process
                await asyncio.sleep(0.1)

                data = ws.receive_json()
                assert data["event"] == "agent_heartbeat"
                assert data["data"]["agent"] == "collector"
                assert data["data"]["status"] == "running"
                assert data["data"]["messages_processed"] == 42


@pytest.mark.asyncio
async def test_websocket_receives_pipeline_progress() -> None:
    """WebSocket broadcasts pipeline_progress with top-level run_id."""
    app, bus = _create_test_app_with_actions()

    from starlette.testclient import TestClient

    with TestClient(app) as tc:
        with tc.websocket_connect("/api/events") as ws:
            await bus.publish(
                CHANNEL_SYSTEM,
                BusMessage(
                    type="pipeline_progress",
                    timestamp=datetime.now(timezone.utc),
                    source="api",
                    payload=PipelineProgressPayload(
                        run_id="test456",
                        collected=10,
                        scored=10,
                        categorized=8,
                        fetched=5,
                        summarized=3,
                        validated=2,
                        total_stories=10,
                    ),
                ),
            )

            await asyncio.sleep(0.1)

            data = ws.receive_json()
            assert data["event"] == "pipeline_progress"
            assert data["run_id"] == "test456"
            assert data["data"]["collected"] == 10
            assert data["data"]["total_stories"] == 10


@pytest.mark.asyncio
async def test_websocket_receives_pipeline_started_and_completed() -> None:
    """WebSocket broadcasts pipeline_started and pipeline_completed events."""
    app, bus = _create_test_app_with_actions()

    from starlette.testclient import TestClient

    with TestClient(app) as tc:
        with tc.websocket_connect("/api/events") as ws:
            # pipeline_started
            await bus.publish(
                CHANNEL_SYSTEM,
                BusMessage(
                    type="pipeline_started",
                    timestamp=datetime.now(timezone.utc),
                    source="api",
                    payload=PipelineProgressPayload(run_id="run789"),
                ),
            )

            await asyncio.sleep(0.1)
            data = ws.receive_json()
            assert data["event"] == "pipeline_started"
            assert data["run_id"] == "run789"

            # pipeline_completed
            await bus.publish(
                CHANNEL_SYSTEM,
                BusMessage(
                    type="pipeline_completed",
                    timestamp=datetime.now(timezone.utc),
                    source="api",
                    payload=PipelineProgressPayload(
                        run_id="run789",
                        collected=5,
                        scored=5,
                        total_stories=5,
                    ),
                ),
            )

            await asyncio.sleep(0.1)
            data = ws.receive_json()
            assert data["event"] == "pipeline_completed"
            assert data["run_id"] == "run789"
            assert data["data"]["collected"] == 5


# ---------------------------------------------------------------------------
# PipelineProgressPayload model test
# ---------------------------------------------------------------------------


def test_pipeline_progress_payload_is_frozen() -> None:
    """PipelineProgressPayload is immutable (frozen Pydantic model)."""
    payload = PipelineProgressPayload(run_id="x", collected=1)
    with pytest.raises(Exception):
        payload.collected = 2  # type: ignore[misc]


def test_pipeline_progress_payload_in_bus_message() -> None:
    """PipelineProgressPayload can be used as BusMessage payload."""
    msg = BusMessage(
        type="pipeline_progress",
        timestamp=datetime.now(timezone.utc),
        source="api",
        payload=PipelineProgressPayload(run_id="abc", collected=10),
    )
    assert msg.payload.run_id == "abc"  # type: ignore[union-attr]
