"""Abstract base class for all hndigest agents."""

import abc
import asyncio
import logging
import time
from datetime import datetime, timezone

from hndigest.bus import MessageBus

logger = logging.getLogger(__name__)

SYSTEM_CHANNEL = "system"
HEARTBEAT_INTERVAL_SECONDS = 30


class BaseAgent(abc.ABC):
    """Abstract base class that every hndigest agent inherits from.

    Provides the standard run loop, heartbeat emission, shutdown handling,
    and message publishing helpers. Concrete agents implement the
    ``process`` method with their domain-specific logic.

    Args:
        name: Human-readable agent identity (e.g. "collector", "scorer").
        bus: The shared message bus instance for inter-agent communication.
        subscriptions: Channel names this agent reads from.
        publications: Channel names this agent writes to.
    """

    def __init__(
        self,
        name: str,
        bus: MessageBus,
        subscriptions: list[str],
        publications: list[str],
    ) -> None:
        self.name = name
        self.bus = bus
        self.subscriptions = subscriptions
        self.publications = publications
        self.messages_processed: int = 0
        self.status: str = "stopped"
        self._shutdown: bool = False
        self._queues: dict[str, asyncio.Queue] = {}

    async def start(self) -> None:
        """Subscribe to declared channels plus the system channel, then run.

        Enters the main run loop which awaits messages from all subscribed
        channels, dispatches them to ``process``, and emits periodic
        heartbeats. Exits cleanly when a shutdown message is received.
        """
        all_channels = set(self.subscriptions) | {SYSTEM_CHANNEL}
        for channel in all_channels:
            queue = self.bus.subscribe(channel)
            self._queues[channel] = queue

        self.status = "running"
        logger.info("%s agent started, subscribed to %s", self.name, list(all_channels))

        try:
            await self._run_loop()
        finally:
            self.status = "stopped"
            logger.info("%s agent stopped", self.name)

    async def _run_loop(self) -> None:
        """Main loop: watch all subscribed queues, process messages, heartbeat."""
        last_heartbeat = time.monotonic()

        while not self._shutdown:
            # Build tasks for getting the next message from each queue
            get_tasks: dict[asyncio.Task, str] = {}
            for channel, queue in self._queues.items():
                task = asyncio.create_task(queue.get())
                get_tasks[task] = channel

            # Wait for either a message or heartbeat timeout
            time_since_heartbeat = time.monotonic() - last_heartbeat
            timeout = max(0.0, HEARTBEAT_INTERVAL_SECONDS - time_since_heartbeat)

            done, pending = await asyncio.wait(
                get_tasks.keys(),
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            # Cancel tasks that did not complete
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            # Process completed messages
            for task in done:
                channel = get_tasks[task]
                try:
                    message = task.result()
                except Exception:
                    logger.exception("%s: error retrieving message from %s", self.name, channel)
                    continue

                if channel == SYSTEM_CHANNEL and isinstance(message, dict):
                    if message.get("type") == "shutdown":
                        logger.info("%s received shutdown signal", self.name)
                        self._shutdown = True
                        self.status = "stopping"
                        break
                    # Skip all other system messages (heartbeats, etc.)
                    # — they are not data messages for agent processing.
                    continue

                try:
                    await self.process(channel, message)
                    self.messages_processed += 1
                except Exception:
                    logger.exception(
                        "%s: error processing message on channel %s",
                        self.name,
                        channel,
                    )

            # Emit heartbeat if interval elapsed
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_SECONDS:
                await self._emit_heartbeat()
                last_heartbeat = now

        # Drain remaining messages from all queues before exiting
        await self._drain_queues()

    async def _drain_queues(self) -> None:
        """Process any remaining messages sitting in subscribed queues."""
        for channel, queue in self._queues.items():
            while not queue.empty():
                try:
                    message = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break

                # Skip system messages during drain
                if channel == SYSTEM_CHANNEL:
                    continue

                try:
                    await self.process(channel, message)
                    self.messages_processed += 1
                except Exception:
                    logger.exception(
                        "%s: error processing message during drain on channel %s",
                        self.name,
                        channel,
                    )

        logger.info(
            "%s drained queues, total messages processed: %d",
            self.name,
            self.messages_processed,
        )

    async def _emit_heartbeat(self) -> None:
        """Publish a heartbeat message to the system channel."""
        heartbeat = {
            "type": "heartbeat",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": self.name,
            "payload": {
                "agent": self.name,
                "status": self.status,
                "messages_processed": self.messages_processed,
            },
        }
        await self.bus.publish(SYSTEM_CHANNEL, heartbeat)
        logger.debug("%s emitted heartbeat", self.name)

    @abc.abstractmethod
    async def process(self, channel: str, message: dict) -> None:
        """Handle a single inbound message.

        Concrete agents override this with their domain logic: persist
        results, publish outputs to downstream channels, etc.

        Args:
            channel: The channel the message arrived on.
            message: The message payload dict.
        """

    async def publish(self, channel: str, payload: dict, msg_type: str) -> None:
        """Build a properly formatted message and publish it to the bus.

        Args:
            channel: Target channel name.
            payload: The domain-specific data to include.
            msg_type: Message type identifier (e.g. "story", "score").
        """
        message = {
            "type": msg_type,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": self.name,
            "payload": payload,
        }
        await self.bus.publish(channel, message)
        logger.debug("%s published %s to %s", self.name, msg_type, channel)
