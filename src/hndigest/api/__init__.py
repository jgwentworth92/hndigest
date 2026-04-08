"""FastAPI application factory for hndigest.

Server mode is a passive backend — no agents auto-run. The API provides:
- Read endpoints: query stories, digests, categories, config
- Action endpoints: trigger collect, score, fetch, summarize, digest on demand
- WebSocket: live events during pipeline runs

The lifespan only initializes the database. Agents are instantiated
per-request when action endpoints are called.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from hndigest.bus import MessageBus
from hndigest.db import init_db

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize database on startup, close on exit.

    No agents or supervisor are started. The API is a passive backend
    that runs agents on demand via action endpoints.
    """
    from dotenv import load_dotenv

    from hndigest.paths import ENV_FILE
    load_dotenv(ENV_FILE)

    db_path = os.environ.get("HNDIGEST_DB_PATH", "hndigest.db")
    db_conn = init_db(db_path)
    bus = MessageBus()

    app.state.db_conn = db_conn
    app.state.bus = bus
    app.state.db_path = db_path
    app.state.supervisor = None
    app.state.active_runs = {}  # dict[str, RunEntry] — see api/routes/actions.py
    app.state.started_at_monotonic = time.monotonic()

    logger.info("FastAPI lifespan: database initialized at %s", db_path)
    yield

    db_conn.close()
    logger.info("FastAPI lifespan: database closed")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Sets up CORS middleware, mounts read + action + WebSocket routers.

    Returns:
        A fully configured FastAPI application instance.
    """
    app = FastAPI(
        title="hndigest",
        description="Multi-agent Hacker News daily digest API — on-demand agent execution",
        version="0.1.0",
        lifespan=lifespan,
    )

    # CORS
    origins = os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(",")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount routers
    from hndigest.api.routes import actions, agents, config, digests, stories
    from hndigest.api.websocket import router as ws_router

    app.include_router(digests.router, prefix="/api")
    app.include_router(stories.router, prefix="/api")
    app.include_router(agents.router, prefix="/api")
    app.include_router(config.router, prefix="/api")
    app.include_router(actions.router, prefix="/api")
    app.include_router(ws_router, prefix="/api")

    return app


app = create_app()
