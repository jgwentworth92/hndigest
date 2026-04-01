"""FastAPI application factory and lifespan management for hndigest.

Creates the FastAPI app with CORS middleware, mounts all route modules,
and manages the supervisor lifecycle (startup/shutdown) via the lifespan
context manager.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from hndigest.agents.categorizer import CategorizerAgent
from hndigest.agents.collector import CollectorAgent
from hndigest.agents.fetcher import FetcherAgent
from hndigest.agents.orchestrator import OrchestratorAgent
from hndigest.agents.report_builder import ReportBuilderAgent
from hndigest.agents.scorer import ScorerAgent
from hndigest.agents.summarizer import SummarizerAgent
from hndigest.agents.validator import ValidatorAgent
from hndigest.bus import MessageBus
from hndigest.db import init_db
from hndigest.supervisor import Supervisor

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start supervisor and agents on startup, shut down on exit.

    Loads environment variables from ``.env``, initializes the database,
    creates all agents with a placeholder bus (the supervisor replaces it
    during ``start()``), registers them with the supervisor, and starts
    the supervisor.  On exit, shuts down the supervisor and closes the
    initial database connection.

    Args:
        app: The FastAPI application instance.

    Yields:
        Control to the running application.
    """
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")

    db_path = os.environ.get("HNDIGEST_DB_PATH", "hndigest.db")
    db_conn = init_db(db_path)
    placeholder_bus = MessageBus()

    # Create all agents (mirrors cli cmd_start registration order)
    collector = CollectorAgent(bus=placeholder_bus, db_conn=db_conn)
    scorer = ScorerAgent(bus=placeholder_bus, db_conn=db_conn)
    orchestrator = OrchestratorAgent(bus=placeholder_bus, db_conn=db_conn)
    fetcher = FetcherAgent(bus=placeholder_bus, db_conn=db_conn)
    categorizer = CategorizerAgent(bus=placeholder_bus, db_conn=db_conn)
    summarizer = SummarizerAgent(bus=placeholder_bus, db_conn=db_conn)
    validator = ValidatorAgent(bus=placeholder_bus, db_conn=db_conn)
    report_builder = ReportBuilderAgent(bus=placeholder_bus, db_conn=db_conn)

    supervisor = Supervisor(db_path=db_path)
    for agent in [
        collector,
        scorer,
        orchestrator,
        fetcher,
        categorizer,
        summarizer,
        validator,
        report_builder,
    ]:
        supervisor.register_agent(agent)

    await supervisor.start()

    # Store in app.state for route handlers via dependencies
    app.state.supervisor = supervisor
    app.state.db_conn = db_conn
    app.state.started_at_monotonic = time.monotonic()

    logger.info("FastAPI lifespan: supervisor started with all agents")
    yield

    await supervisor.shutdown()
    db_conn.close()
    logger.info("FastAPI lifespan: supervisor shut down")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Sets up CORS middleware (origins from ``CORS_ORIGINS`` env var,
    defaulting to ``http://localhost:3000``), mounts all API routers
    under the ``/api`` prefix, and attaches the lifespan context manager.

    Returns:
        A fully configured FastAPI application instance.
    """
    app = FastAPI(
        title="hndigest",
        description="Multi-agent Hacker News daily digest API",
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
    from hndigest.api.routes import agents, config, digests, stories
    from hndigest.api.websocket import router as ws_router

    app.include_router(digests.router, prefix="/api")
    app.include_router(stories.router, prefix="/api")
    app.include_router(agents.router, prefix="/api")
    app.include_router(config.router, prefix="/api")
    app.include_router(ws_router, prefix="/api")

    return app


app = create_app()
