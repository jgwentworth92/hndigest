"""Entry point for ``python -m hndigest``.

Sets up logging (INFO to stderr) and dispatches CLI subcommands via
argparse to the corresponding handler in ``hndigest.cli``.
"""

from __future__ import annotations

import argparse
import logging
import sys


def _build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser with subcommands.

    Returns:
        A fully configured ``argparse.ArgumentParser``.
    """
    parser = argparse.ArgumentParser(
        prog="hndigest",
        description="Multi-agent Hacker News daily digest system.",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- start ---
    start_parser = subparsers.add_parser(
        "start", help="Start the supervisor and all agents."
    )
    start_parser.add_argument(
        "--db-path",
        dest="db_path",
        default="hndigest.db",
        help="Path to the SQLite database file (default: hndigest.db).",
    )
    start_parser.add_argument(
        "--mode",
        choices=["server", "cli"],
        default="server",
        help=(
            "Startup mode: 'server' runs agents + FastAPI API server "
            "(default), 'cli' runs agents only with no API server."
        ),
    )
    start_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host to bind the API server to (default: 127.0.0.1).",
    )
    start_parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Port for the API server (default: 8000).",
    )

    # --- stop ---
    subparsers.add_parser(
        "stop",
        help="Show instructions for stopping a running process.",
    )

    # --- status ---
    status_parser = subparsers.add_parser(
        "status", help="Show database status information."
    )
    status_parser.add_argument(
        "--db-path",
        dest="db_path",
        default="hndigest.db",
        help="Path to the SQLite database file (default: hndigest.db).",
    )

    # --- stories ---
    stories_parser = subparsers.add_parser(
        "stories", help="Query and display collected stories."
    )
    stories_parser.add_argument(
        "--today",
        action="store_true",
        default=False,
        help="Show only stories posted today (UTC).",
    )
    stories_parser.add_argument(
        "--db-path",
        dest="db_path",
        default="hndigest.db",
        help="Path to the SQLite database file (default: hndigest.db).",
    )

    # --- digest ---
    digest_parser = subparsers.add_parser(
        "digest", help="Generate or display a digest."
    )
    digest_group = digest_parser.add_mutually_exclusive_group()
    digest_group.add_argument(
        "--now",
        action="store_true",
        default=False,
        help="Generate a digest immediately from current data and print it.",
    )
    digest_group.add_argument(
        "--latest",
        action="store_true",
        default=False,
        help="Display the most recent digest from the database.",
    )
    digest_parser.add_argument(
        "--db-path",
        dest="db_path",
        default="hndigest.db",
        help="Path to the SQLite database file (default: hndigest.db).",
    )

    # --- categories ---
    categories_parser = subparsers.add_parser(
        "categories", help="Show category breakdown for today's stories."
    )
    categories_parser.add_argument(
        "--db-path",
        dest="db_path",
        default="hndigest.db",
        help="Path to the SQLite database file (default: hndigest.db).",
    )

    return parser


def main() -> None:
    """Parse arguments, configure logging, and dispatch to the command handler."""
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    from hndigest.cli import (
        cmd_categories,
        cmd_digest,
        cmd_start,
        cmd_status,
        cmd_stop,
        cmd_stories,
    )

    dispatch: dict[str, object] = {
        "start": cmd_start,
        "stop": cmd_stop,
        "status": cmd_status,
        "stories": cmd_stories,
        "digest": cmd_digest,
        "categories": cmd_categories,
    }

    handler = dispatch.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    handler(args)  # type: ignore[operator]


if __name__ == "__main__":
    main()
