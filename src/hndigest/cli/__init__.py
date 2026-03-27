"""CLI command implementations for hndigest.

Each public function corresponds to a CLI subcommand and receives the
parsed ``argparse.Namespace`` object.  Output intended for the user goes
to stdout via ``print``; operational logging goes to stderr via stdlib
``logging``.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = "hndigest.db"


def cmd_start(args: Any) -> None:
    """Start the supervisor with Collector and Scorer agents.

    Creates a ``Supervisor``, registers ``CollectorAgent`` and
    ``ScorerAgent``, installs signal handlers for graceful shutdown
    (SIGINT, SIGTERM), and runs the event loop until shutdown completes.

    Args:
        args: Parsed CLI arguments. Expected attributes:
            ``db_path`` (str) -- path to the SQLite database file.
    """
    from hndigest.supervisor import Supervisor
    from hndigest.agents.collector import CollectorAgent
    from hndigest.agents.scorer import ScorerAgent

    from hndigest.bus import MessageBus
    from hndigest.db import init_db

    db_path: str = getattr(args, "db_path", _DEFAULT_DB_PATH)
    logger.info("Initializing system with db_path=%s", db_path)

    async def _run_system() -> None:
        """Full async lifecycle: init, run, shutdown on signal."""
        shutdown_event = asyncio.Event()

        loop = asyncio.get_running_loop()

        def _signal_handler() -> None:
            logger.info("Received shutdown signal")
            shutdown_event.set()

        # Install signal handlers (Unix-style; on Windows SIGTERM may
        # not be available via add_signal_handler, so we fall back).
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _signal_handler)
            except NotImplementedError:
                # Windows: add_signal_handler not supported for all signals
                signal.signal(sig, lambda s, f: _signal_handler())

        # Initialize database for agents.  The Supervisor will also call
        # init_db, producing a second connection to the same WAL-mode
        # database -- this is harmless and keeps agent db_conn refs stable.
        db_conn = init_db(db_path)

        # Create a placeholder bus for agent constructors.  The supervisor
        # replaces agent.bus with its own bus during start().
        placeholder_bus = MessageBus()

        collector = CollectorAgent(bus=placeholder_bus, db_conn=db_conn)
        scorer = ScorerAgent(bus=placeholder_bus, db_conn=db_conn)

        supervisor_local = Supervisor(db_path=db_path)
        supervisor_local.register_agent(collector)
        supervisor_local.register_agent(scorer)

        await supervisor_local.start()
        logger.info("System is running. Press Ctrl+C to stop.")

        # Wait for shutdown signal
        await shutdown_event.wait()

        logger.info("Shutting down...")
        await supervisor_local.shutdown()

        # Close the agent database connection
        db_conn.close()
        logger.info("Shutdown complete.")

    asyncio.run(_run_system())


def cmd_stop(args: Any) -> None:
    """Print instructions for stopping a running hndigest process.

    In Phase 1 there is no IPC mechanism to contact a running supervisor
    from a separate process. The running process handles SIGINT/SIGTERM
    directly.

    Args:
        args: Parsed CLI arguments (unused in Phase 1).
    """
    print(
        "To stop a running hndigest process, send SIGINT (Ctrl+C) or "
        "SIGTERM to the process.\n"
        "Example: kill -INT <pid>"
    )


def cmd_status(args: Any) -> None:
    """Display basic database status information.

    In Phase 1 there is no way to connect to a running supervisor from
    a separate CLI invocation, so this command queries the database
    directly and reports story and score counts.

    Args:
        args: Parsed CLI arguments. Expected attributes:
            ``db_path`` (str) -- path to the SQLite database file.
    """
    db_path: str = getattr(args, "db_path", _DEFAULT_DB_PATH)

    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    try:
        story_count = conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
        score_count = conn.execute("SELECT COUNT(*) FROM scores").fetchone()[0]

        latest_story_row = conn.execute(
            "SELECT last_updated FROM stories ORDER BY last_updated DESC LIMIT 1"
        ).fetchone()
        latest_story = latest_story_row[0] if latest_story_row else "n/a"

        latest_score_row = conn.execute(
            "SELECT scored_at FROM scores ORDER BY scored_at DESC LIMIT 1"
        ).fetchone()
        latest_score = latest_score_row[0] if latest_score_row else "n/a"

        print(f"Database: {db_path}")
        print(f"Stories collected: {story_count}")
        print(f"Scores computed:   {score_count}")
        print(f"Latest story update: {latest_story}")
        print(f"Latest score:        {latest_score}")
    except sqlite3.OperationalError as exc:
        print(f"Error querying database: {exc}")
    finally:
        conn.close()


def cmd_stories(args: Any) -> None:
    """Query and display stories from the database.

    With ``--today``, filters to stories posted today (UTC). Shows each
    story's id, title, score, comments, composite score (from the scores
    table), and posted_at timestamp.

    Args:
        args: Parsed CLI arguments. Expected attributes:
            ``db_path`` (str) -- path to the SQLite database file.
            ``today`` (bool) -- if True, filter to today's stories.
    """
    db_path: str = getattr(args, "db_path", _DEFAULT_DB_PATH)
    today_only: bool = getattr(args, "today", False)

    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    try:
        query = """
            SELECT s.id, s.title, s.score, s.comments,
                   sc.composite, s.posted_at
            FROM stories s
            LEFT JOIN scores sc ON sc.story_id = s.id
                AND sc.id = (
                    SELECT MAX(sc2.id) FROM scores sc2
                    WHERE sc2.story_id = s.id
                )
        """
        params: list[str] = []

        if today_only:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            query += " WHERE s.posted_at >= ?"
            params.append(today_str + "T00:00:00+00:00")

        query += " ORDER BY COALESCE(sc.composite, 0) DESC"

        rows = conn.execute(query, params).fetchall()

        if not rows:
            qualifier = " for today" if today_only else ""
            print(f"No stories found{qualifier}.")
            return

        # Header
        print(
            f"{'ID':>10}  {'Score':>5}  {'Cmts':>5}  "
            f"{'Composite':>9}  {'Posted':>20}  Title"
        )
        print("-" * 90)

        for row in rows:
            story_id, title, score, comments, composite, posted_at = row
            composite_str = f"{composite:.1f}" if composite is not None else "  --"
            # Truncate title to keep output readable
            title_display = title if len(title) <= 60 else title[:57] + "..."
            print(
                f"{story_id:>10}  {score:>5}  {comments:>5}  "
                f"{composite_str:>9}  {posted_at:>20}  {title_display}"
            )

        print(f"\n{len(rows)} story(ies) displayed.")
    except sqlite3.OperationalError as exc:
        print(f"Error querying database: {exc}")
    finally:
        conn.close()
