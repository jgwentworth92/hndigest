"""Supervisor: process manager for hndigest agent lifecycle.

The Supervisor is NOT an agent. It creates the message bus, initializes
the database, starts agents as asyncio tasks, monitors their health via
heartbeats on the system channel, restarts failed agents with exponential
backoff, and orchestrates graceful shutdown.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hndigest.agents.base import BaseAgent
from hndigest.bus import CHANNEL_SYSTEM, MessageBus
from hndigest.db import init_db

logger = logging.getLogger(__name__)

_HEARTBEAT_TIMEOUT_SECONDS: float = 60.0
_SHUTDOWN_TIMEOUT_SECONDS: float = 10.0
_MAX_RESTARTS: int = 3
_BASE_BACKOFF_SECONDS: float = 1.0
_HEALTH_CHECK_INTERVAL_SECONDS: float = 15.0


class Supervisor:
    """Process manager for hndigest agents.

    Creates the shared infrastructure (message bus, database), starts each
    registered agent as an ``asyncio.Task``, monitors health via heartbeats
    on the system channel, and restarts crashed agents with exponential
    backoff.

    Args:
        db_path: Filesystem path for the SQLite database file.
            Defaults to ``"hndigest.db"``.

    Example::

        supervisor = Supervisor(db_path="hndigest.db")
        supervisor.register_agent(my_agent)
        await supervisor.start()
    """

    def __init__(self, db_path: str | Path = "hndigest.db") -> None:
        self._db_path = db_path
        self._agents: list[BaseAgent] = []
        self._registry: dict[str, dict[str, Any]] = {}
        self._running: bool = False
        self._health_task: asyncio.Task[None] | None = None
        self._system_queue: asyncio.Queue[dict[str, Any]] | None = None

        self.bus: MessageBus | None = None
        self.db: sqlite3.Connection | None = None

    def register_agent(self, agent: BaseAgent) -> None:
        """Add an agent to the supervisor before starting.

        Args:
            agent: A ``BaseAgent`` instance to manage. Must have a unique
                ``name`` attribute.

        Raises:
            ValueError: If an agent with the same name is already registered.
            RuntimeError: If the supervisor is already running.
        """
        if self._running:
            raise RuntimeError(
                "Cannot register agents after the supervisor has started."
            )
        for existing in self._agents:
            if existing.name == agent.name:
                raise ValueError(f"Agent already registered: {agent.name!r}")
        self._agents.append(agent)
        logger.info("Registered agent: %s", agent.name)

    async def start(self) -> None:
        """Execute the full startup sequence and enter the health monitoring loop.

        Startup sequence (per SPEC-000 section 7):
            1. Create the message bus (all channels).
            2. Initialize the SQLite database (run migrations).
            3. Start all registered agents as asyncio tasks.
            4. Subscribe to the system channel for heartbeat monitoring.
            5. Enter the health monitoring loop.
        """
        logger.info("Supervisor starting...")

        # 1. Create message bus
        self.bus = MessageBus()

        # 2. Initialize database
        self.db = init_db(self._db_path)
        logger.info("Database initialized at %s", self._db_path)

        # 3. Assign bus to agents that need it and initialize registry
        self._running = True
        for agent in self._agents:
            agent.bus = self.bus
            self._start_agent(agent)

        # 4. Subscribe to system channel for heartbeat monitoring
        self._system_queue = self.bus.subscribe(CHANNEL_SYSTEM)

        # 5. Start health monitoring loop
        self._health_task = asyncio.create_task(
            self._health_monitoring_loop(), name="supervisor-health-monitor"
        )

        logger.info(
            "Supervisor started with %d agent(s): %s",
            len(self._agents),
            ", ".join(a.name for a in self._agents),
        )

    def _start_agent(self, agent: BaseAgent) -> None:
        """Create an asyncio task for an agent and register it.

        Args:
            agent: The agent to start.
        """
        task = asyncio.create_task(agent.start(), name=f"agent-{agent.name}")
        task.add_done_callback(self._make_agent_done_callback(agent.name))

        self._registry[agent.name] = {
            "task": task,
            "status": "running",
            "last_heartbeat": time.monotonic(),
            "messages_processed": 0,
            "restart_count": self._registry.get(agent.name, {}).get(
                "restart_count", 0
            ),
        }
        logger.info("Started agent task: %s", agent.name)

    def _make_agent_done_callback(
        self, agent_name: str
    ) -> Any:
        """Create a done callback for an agent task.

        The callback detects agent crashes (exceptions) and schedules
        a restart with exponential backoff.

        Args:
            agent_name: The name of the agent this callback monitors.

        Returns:
            A callback function suitable for ``task.add_done_callback``.
        """

        def _on_done(task: asyncio.Task[None]) -> None:
            if not self._running:
                return

            if task.cancelled():
                logger.info("Agent %s task was cancelled.", agent_name)
                if agent_name in self._registry:
                    self._registry[agent_name]["status"] = "stopped"
                return

            exc = task.exception()
            if exc is not None:
                logger.error(
                    "Agent %s crashed with exception: %s", agent_name, exc
                )
                if agent_name in self._registry:
                    self._registry[agent_name]["status"] = "crashed"
                # Schedule restart
                asyncio.ensure_future(self._restart_agent(agent_name))
            else:
                logger.info("Agent %s exited cleanly.", agent_name)
                if agent_name in self._registry:
                    self._registry[agent_name]["status"] = "stopped"

        return _on_done

    async def _restart_agent(self, agent_name: str) -> None:
        """Restart a crashed agent with exponential backoff.

        Retries up to ``_MAX_RESTARTS`` times. Backoff doubles each time:
        1s, 2s, 4s. After exhausting retries, the agent is marked as
        ``"failed"`` and an alert is logged.

        Args:
            agent_name: The name of the agent to restart.
        """
        if not self._running:
            return

        entry = self._registry.get(agent_name)
        if entry is None:
            logger.error("Cannot restart unknown agent: %s", agent_name)
            return

        restart_count = entry["restart_count"]
        if restart_count >= _MAX_RESTARTS:
            logger.critical(
                "Agent %s has failed %d times, exceeding max restarts (%d). "
                "Marking as failed.",
                agent_name,
                restart_count,
                _MAX_RESTARTS,
            )
            entry["status"] = "failed"
            return

        backoff = _BASE_BACKOFF_SECONDS * (2 ** restart_count)
        entry["restart_count"] = restart_count + 1

        logger.warning(
            "Restarting agent %s in %.1fs (attempt %d/%d)...",
            agent_name,
            backoff,
            entry["restart_count"],
            _MAX_RESTARTS,
        )
        await asyncio.sleep(backoff)

        if not self._running:
            return

        # Find the agent instance
        agent = self._find_agent(agent_name)
        if agent is None:
            logger.error("Agent instance not found for restart: %s", agent_name)
            entry["status"] = "failed"
            return

        # Reset agent internal state for a fresh start
        agent._shutdown = False
        agent.status = "stopped"
        agent._queues = {}

        self._start_agent(agent)
        logger.info(
            "Agent %s restarted (attempt %d/%d).",
            agent_name,
            entry["restart_count"],
            _MAX_RESTARTS,
        )

    def _find_agent(self, name: str) -> BaseAgent | None:
        """Look up an agent instance by name.

        Args:
            name: The agent name to search for.

        Returns:
            The matching ``BaseAgent`` or ``None`` if not found.
        """
        for agent in self._agents:
            if agent.name == name:
                return agent
        return None

    async def _health_monitoring_loop(self) -> None:
        """Monitor agent heartbeats on the system channel.

        Runs continuously while the supervisor is active. Consumes
        heartbeat messages from the system channel and updates the
        registry. Flags agents that miss two consecutive heartbeat
        windows (60 seconds without a heartbeat) as ``"unhealthy"``.
        """
        assert self._system_queue is not None

        while self._running:
            # Drain all available heartbeat messages
            try:
                message = await asyncio.wait_for(
                    self._system_queue.get(),
                    timeout=_HEALTH_CHECK_INTERVAL_SECONDS,
                )
                self._process_system_message(message)
                # Drain any additional queued messages without blocking
                while True:
                    try:
                        message = self._system_queue.get_nowait()
                        self._process_system_message(message)
                    except asyncio.QueueEmpty:
                        break
            except asyncio.TimeoutError:
                pass

            # Check for agents that have missed heartbeats
            now = time.monotonic()
            for agent_name, entry in self._registry.items():
                if entry["status"] not in ("running", "unhealthy"):
                    continue
                elapsed = now - entry["last_heartbeat"]
                if elapsed > _HEARTBEAT_TIMEOUT_SECONDS:
                    if entry["status"] != "unhealthy":
                        logger.warning(
                            "Agent %s missed heartbeat window "
                            "(%.1fs since last heartbeat, threshold %.1fs). "
                            "Marking as unhealthy.",
                            agent_name,
                            elapsed,
                            _HEARTBEAT_TIMEOUT_SECONDS,
                        )
                        entry["status"] = "unhealthy"

    def _process_system_message(self, message: dict[str, Any]) -> None:
        """Handle a single message from the system channel.

        Updates agent registry with heartbeat data.

        Args:
            message: A message dict from the system channel.
        """
        if not isinstance(message, dict):
            return

        msg_type = message.get("type")
        if msg_type != "heartbeat":
            return

        payload = message.get("payload", {})
        agent_name = payload.get("agent")
        if agent_name is None or agent_name not in self._registry:
            return

        entry = self._registry[agent_name]
        entry["last_heartbeat"] = time.monotonic()
        entry["messages_processed"] = payload.get(
            "messages_processed", entry["messages_processed"]
        )
        # Restore healthy status if previously flagged
        if entry["status"] == "unhealthy":
            logger.info(
                "Agent %s heartbeat received, marking as running.", agent_name
            )
            entry["status"] = "running"

    async def shutdown(self) -> None:
        """Gracefully shut down all agents and close resources.

        Shutdown sequence:
            1. Publish a shutdown message to the system channel.
            2. Wait for agent tasks to finish (up to 10s timeout).
            3. Cancel any remaining tasks.
            4. Cancel the health monitoring task.
            5. Close the database connection.
        """
        if not self._running:
            logger.warning("Supervisor is not running; nothing to shut down.")
            return

        logger.info("Supervisor shutting down...")
        self._running = False

        # 1. Publish shutdown message
        if self.bus is not None:
            shutdown_message = {
                "type": "shutdown",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "supervisor",
                "payload": {},
            }
            await self.bus.publish(CHANNEL_SYSTEM, shutdown_message)
            logger.info("Published shutdown message to system channel.")

        # 2. Wait for agent tasks to finish with timeout
        tasks = [
            entry["task"]
            for entry in self._registry.values()
            if not entry["task"].done()
        ]
        if tasks:
            logger.info(
                "Waiting up to %.0fs for %d agent task(s) to finish...",
                _SHUTDOWN_TIMEOUT_SECONDS,
                len(tasks),
            )
            done, pending = await asyncio.wait(
                tasks, timeout=_SHUTDOWN_TIMEOUT_SECONDS
            )

            # 3. Cancel any remaining tasks
            if pending:
                logger.warning(
                    "%d agent task(s) did not finish in time, cancelling.",
                    len(pending),
                )
                for task in pending:
                    task.cancel()
                # Wait for cancellations to complete
                await asyncio.gather(*pending, return_exceptions=True)

        # 4. Cancel health monitoring task
        if self._health_task is not None and not self._health_task.done():
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
            self._health_task = None

        # 5. Close database connection
        if self.db is not None:
            self.db.close()
            logger.info("Database connection closed.")
            self.db = None

        logger.info("Supervisor shutdown complete.")

    @property
    def agent_statuses(self) -> dict[str, dict[str, Any]]:
        """Return a snapshot of all agent statuses for CLI/API consumption.

        Returns:
            A dict mapping agent name to a dict with keys: ``status``,
            ``last_heartbeat_ago`` (seconds since last heartbeat),
            ``messages_processed``, and ``restart_count``.
        """
        now = time.monotonic()
        result: dict[str, dict[str, Any]] = {}
        for name, entry in self._registry.items():
            result[name] = {
                "status": entry["status"],
                "last_heartbeat_ago": round(now - entry["last_heartbeat"], 1),
                "messages_processed": entry["messages_processed"],
                "restart_count": entry["restart_count"],
            }
        return result
