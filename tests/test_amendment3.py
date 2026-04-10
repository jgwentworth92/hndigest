"""End-to-end tests for ADR-006 Amendment 3: failure handling.

Tests cover:
- _finish_run marks runs as finished with error
- _cleanup_expired_runs removes old entries
- Failed runs retained in GET /api/runs with error field
- pipeline_failed event published on failure
- WebSocket broadcasts pipeline_failed events
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from hndigest.api.routes import actions
from hndigest.api.routes.actions import RunEntry, _finish_run, _cleanup_expired_runs, _RUN_TTL_SECONDS
from hndigest.api.schemas import PipelineProgress
from hndigest.api.websocket import router as ws_router
from hndigest.bus import CHANNEL_SYSTEM, MessageBus
from hndigest.db import init_db
from hndigest.models import BusMessage, PipelineProgressPayload

_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations"


def _create_test_app() -> tuple[FastAPI, MessageBus]:
    """Build a FastAPI app with actions + ws routers for testing."""
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
# _finish_run tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_finish_run_success() -> None:
    """_finish_run marks a run as completed with ended_at and no error."""
    app, _bus = _create_test_app()

    async def _noop() -> None:
        pass

    task = asyncio.create_task(_noop())
    await task

    entry = RunEntry(
        task=task,
        run_type="collect",
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    app.state.active_runs["r1"] = entry

    class FakeRequest:
        class app_cls:
            class state_cls:
                active_runs: dict = {}
            state = state_cls()
        app = app_cls()

    FakeRequest.app.state.active_runs = app.state.active_runs
    _finish_run(FakeRequest, "r1")  # type: ignore[arg-type]

    assert entry.ended_at is not None
    assert entry.error is None
    # Entry is still in active_runs (not removed)
    assert "r1" in app.state.active_runs


@pytest.mark.asyncio
async def test_finish_run_with_error() -> None:
    """_finish_run marks a run as failed with error message."""
    app, _bus = _create_test_app()

    async def _noop() -> None:
        pass

    task = asyncio.create_task(_noop())
    await task

    entry = RunEntry(
        task=task,
        run_type="pipeline",
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    app.state.active_runs["r2"] = entry

    class FakeRequest:
        class app_cls:
            class state_cls:
                active_runs: dict = {}
            state = state_cls()
        app = app_cls()

    FakeRequest.app.state.active_runs = app.state.active_runs
    _finish_run(FakeRequest, "r2", error="Connection timeout")  # type: ignore[arg-type]

    assert entry.ended_at is not None
    assert entry.error == "Connection timeout"
    assert "r2" in app.state.active_runs


# ---------------------------------------------------------------------------
# _cleanup_expired_runs tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cleanup_removes_expired_runs() -> None:
    """_cleanup_expired_runs removes entries older than TTL."""
    app, _bus = _create_test_app()

    async def _noop() -> None:
        pass

    task = asyncio.create_task(_noop())
    await task

    # Create an entry that ended 10 minutes ago (past TTL)
    old_time = (datetime.now(timezone.utc) - timedelta(seconds=_RUN_TTL_SECONDS + 60)).isoformat()
    app.state.active_runs["old"] = RunEntry(
        task=task, run_type="collect", started_at=old_time, ended_at=old_time,
    )
    # Create an entry that ended just now (within TTL)
    now = datetime.now(timezone.utc).isoformat()
    app.state.active_runs["recent"] = RunEntry(
        task=task, run_type="score", started_at=now, ended_at=now,
    )
    # Create a still-running entry
    app.state.active_runs["running"] = RunEntry(
        task=task, run_type="pipeline", started_at=now,
    )

    class FakeRequest:
        class app_cls:
            class state_cls:
                active_runs: dict = {}
            state = state_cls()
        app = app_cls()

    FakeRequest.app.state.active_runs = app.state.active_runs
    _cleanup_expired_runs(FakeRequest)  # type: ignore[arg-type]

    assert "old" not in app.state.active_runs
    assert "recent" in app.state.active_runs
    assert "running" in app.state.active_runs


# ---------------------------------------------------------------------------
# GET /api/runs with error field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_runs_includes_error() -> None:
    """GET /api/runs includes error field for failed runs."""
    app, _bus = _create_test_app()

    async def _noop() -> None:
        pass

    task = asyncio.create_task(_noop())
    await task

    app.state.active_runs["fail1"] = RunEntry(
        task=task,
        run_type="pipeline",
        started_at=datetime.now(timezone.utc).isoformat(),
        ended_at=datetime.now(timezone.utc).isoformat(),
        error="Fetch timeout on story 12345",
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/runs")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["status"] == "failed"
        assert data[0]["error"] == "Fetch timeout on story 12345"


@pytest.mark.asyncio
async def test_get_run_status_includes_error() -> None:
    """GET /api/runs/{id} includes error for failed run."""
    app, _bus = _create_test_app()

    async def _noop() -> None:
        pass

    task = asyncio.create_task(_noop())
    await task

    app.state.active_runs["fail2"] = RunEntry(
        task=task,
        run_type="score",
        started_at=datetime.now(timezone.utc).isoformat(),
        ended_at=datetime.now(timezone.utc).isoformat(),
        error="Database locked",
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/runs/fail2")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert data["error"] == "Database locked"


# ---------------------------------------------------------------------------
# WebSocket pipeline_failed event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_websocket_receives_pipeline_failed() -> None:
    """WebSocket broadcasts pipeline_failed with run_id at top level."""
    app, bus = _create_test_app()

    from starlette.testclient import TestClient

    with TestClient(app) as tc:
        with tc.websocket_connect("/api/events") as ws:
            await bus.publish(
                CHANNEL_SYSTEM,
                BusMessage(
                    type="pipeline_failed",
                    timestamp=datetime.now(timezone.utc),
                    source="api",
                    payload=PipelineProgressPayload(
                        run_id="fail789",
                        collected=5,
                        scored=5,
                        total_stories=10,
                    ),
                ),
            )

            await asyncio.sleep(0.1)

            data = ws.receive_json()
            assert data["event"] == "pipeline_failed"
            assert data["run_id"] == "fail789"
            assert data["data"]["collected"] == 5
            assert data["data"]["total_stories"] == 10
