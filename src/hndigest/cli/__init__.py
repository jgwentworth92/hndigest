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
    """Start the system in server or CLI-only mode.

    In **server** mode (the default), launches the FastAPI application
    via uvicorn.  The FastAPI lifespan context starts the supervisor and
    all agents automatically, so no manual agent setup is needed here.

    In **cli** mode, creates a ``Supervisor``, registers all agents,
    installs signal handlers for graceful shutdown (SIGINT, SIGTERM),
    and runs the event loop until shutdown completes.  This mode is
    intended for headless / cron use where no HTTP API is required.

    Args:
        args: Parsed CLI arguments. Expected attributes:
            ``db_path`` (str) -- path to the SQLite database file.
            ``mode`` (str) -- "server" or "cli".
            ``host`` (str) -- bind address for server mode.
            ``port`` (int) -- listen port for server mode.
    """
    mode: str = getattr(args, "mode", "server")

    if mode == "server":
        _start_server(args)
    else:
        _start_cli(args)


def _start_server(args: Any) -> None:
    """Start the FastAPI server with uvicorn.

    The supervisor and all agents are managed by the FastAPI lifespan
    context defined in ``hndigest.api``.

    Args:
        args: Parsed CLI arguments with ``host`` and ``port``.
    """
    import uvicorn

    host: str = getattr(args, "host", "127.0.0.1")
    port: int = getattr(args, "port", 8000)

    logger.info(
        "Starting server mode on %s:%d", host, port,
    )
    uvicorn.run(
        "hndigest.api:app",
        host=host,
        port=port,
        log_level="info",
    )


def _start_cli(args: Any) -> None:
    """Start agents in CLI-only mode (no HTTP server).

    Creates a ``Supervisor``, registers all agents, installs signal
    handlers for graceful shutdown, and blocks until a shutdown signal
    is received.

    Args:
        args: Parsed CLI arguments with ``db_path``.
    """
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

    db_path: str = getattr(args, "db_path", _DEFAULT_DB_PATH)
    logger.info("Initializing CLI mode with db_path=%s", db_path)

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
        orchestrator = OrchestratorAgent(bus=placeholder_bus, db_conn=db_conn)
        fetcher = FetcherAgent(bus=placeholder_bus, db_conn=db_conn)
        categorizer = CategorizerAgent(bus=placeholder_bus, db_conn=db_conn)
        summarizer = SummarizerAgent(bus=placeholder_bus, db_conn=db_conn)
        validator = ValidatorAgent(bus=placeholder_bus, db_conn=db_conn)
        report_builder = ReportBuilderAgent(bus=placeholder_bus, db_conn=db_conn)

        supervisor_local = Supervisor(db_path=db_path)
        supervisor_local.register_agent(collector)
        supervisor_local.register_agent(scorer)
        supervisor_local.register_agent(orchestrator)
        supervisor_local.register_agent(fetcher)
        supervisor_local.register_agent(categorizer)
        supervisor_local.register_agent(summarizer)
        supervisor_local.register_agent(validator)
        supervisor_local.register_agent(report_builder)

        await supervisor_local.start()
        logger.info("System is running (CLI mode). Press Ctrl+C to stop.")

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


def cmd_digest(args: Any) -> None:
    """Generate or display a digest.

    Two modes are supported:

    * ``--now``: Initialize the database and message bus, create a
      ``ReportBuilderAgent``, call its ``_build_digest`` method directly
      to generate a digest from the current data, print the rendered
      markdown, then exit.
    * ``--latest``: Query the digests table for the most recently
      created digest and print its ``content_md`` field.

    Args:
        args: Parsed CLI arguments. Expected attributes:
            ``db_path`` (str) -- path to the SQLite database file.
            ``now`` (bool) -- if True, generate a digest immediately.
            ``latest`` (bool) -- if True, display the most recent digest.
    """
    db_path: str = getattr(args, "db_path", _DEFAULT_DB_PATH)
    now_flag: bool = getattr(args, "now", False)
    latest_flag: bool = getattr(args, "latest", False)

    if now_flag:
        _digest_now(db_path)
    elif latest_flag:
        _digest_latest(db_path)
    else:
        print("Specify --now to generate a digest or --latest to view the most recent one.")


def _write_digest_file(content_md: str, created_at: str) -> Path:
    """Write digest markdown to output/digests/ and return the file path.

    Args:
        content_md: Rendered markdown content.
        created_at: ISO 8601 timestamp for the filename.

    Returns:
        Path to the written file.
    """
    output_dir = Path("output") / "digests"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Parse timestamp for filename, fall back to now
    try:
        dt = datetime.fromisoformat(created_at)
    except (ValueError, TypeError):
        dt = datetime.now(timezone.utc)

    filename = dt.strftime("%Y-%m-%d_%H%M%S") + ".md"
    filepath = output_dir / filename
    filepath.write_text(content_md, encoding="utf-8")
    return filepath


def _digest_now(db_path: str) -> None:
    """Generate a digest immediately, print markdown, and write to file.

    Initializes the database and a minimal bus/agent setup, calls the
    report builder's ``_build_digest`` method directly, prints the
    rendered markdown, and writes it to ``output/digests/``.

    Args:
        db_path: Filesystem path to the SQLite database file.
    """
    from hndigest.agents.report_builder import ReportBuilderAgent
    from hndigest.bus import MessageBus
    from hndigest.db import init_db

    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        return

    db_conn = init_db(db_path)
    bus = MessageBus()

    try:
        report_builder = ReportBuilderAgent(bus=bus, db_conn=db_conn)

        async def _run() -> dict | None:
            return await report_builder._build_digest()

        result = asyncio.run(_run())

        if result and result.get("story_count", 0) > 0:
            content_md = result["content_md"]
            created_at = result.get("period_end", datetime.now(timezone.utc).isoformat())

            print(content_md)

            filepath = _write_digest_file(content_md, created_at)
            print(f"\n--- Digest written to: {filepath}")
            print(f"--- Stories: {result['story_count']}")
        else:
            print("No stories available to build a digest.")
    except Exception as exc:
        logger.exception("Failed to generate digest")
        print(f"Error generating digest: {exc}")
    finally:
        db_conn.close()


def _digest_latest(db_path: str) -> None:
    """Display the most recent digest from the database and write to file.

    Args:
        db_path: Filesystem path to the SQLite database file.
    """
    if not Path(db_path).exists():
        print(f"Database not found: {db_path}")
        return

    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT content_md, created_at FROM digests "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()

        if row is None:
            print("No digests found in the database.")
            return

        content_md, created_at = row

        print(content_md)

        filepath = _write_digest_file(content_md, created_at)
        print(f"\n--- Digest from {created_at}")
        print(f"--- Written to: {filepath}")
    except sqlite3.OperationalError as exc:
        print(f"Error querying database: {exc}")
    finally:
        conn.close()


def cmd_categories(args: Any) -> None:
    """Show category breakdown for today's stories.

    Queries the categories table joined with stories to count how many
    stories fall into each category for the current UTC day.

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
        today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_start = today_str + "T00:00:00+00:00"

        rows = conn.execute(
            """
            SELECT c.category, COUNT(DISTINCT c.story_id) AS story_count
            FROM categories c
            JOIN stories s ON s.id = c.story_id
            WHERE s.posted_at >= ?
            GROUP BY c.category
            ORDER BY story_count DESC
            """,
            (today_start,),
        ).fetchall()

        if not rows:
            print("No categorized stories found for today.")
            return

        print(f"Category breakdown for {today_str} (UTC):\n")
        print(f"{'Category':<20}  {'Stories':>7}")
        print("-" * 30)

        total = 0
        for category, count in rows:
            print(f"{category:<20}  {count:>7}")
            total += count

        print("-" * 30)
        print(f"{'Total':<20}  {total:>7}")
    except sqlite3.OperationalError as exc:
        print(f"Error querying database: {exc}")
    finally:
        conn.close()
