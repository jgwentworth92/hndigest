"""In-process async message bus for inter-agent communication.

Dict of asyncio.Queue objects, one per channel. Agents publish/subscribe
by channel name. Fan-out: each subscriber gets its own Queue that receives
a copy of every message published to that channel.
"""

from __future__ import annotations

import asyncio
import logging

from hndigest.models import BusMessage

logger = logging.getLogger(__name__)

# Channel name constants (from SPEC-000 section 5.2)
CHANNEL_STORY: str = "story"
CHANNEL_FETCH_REQUEST: str = "fetch_request"
CHANNEL_ARTICLE: str = "article"
CHANNEL_SUMMARIZE_REQUEST: str = "summarize_request"
CHANNEL_CATEGORY: str = "category"
CHANNEL_SCORE: str = "score"
CHANNEL_SUMMARY: str = "summary"
CHANNEL_VALIDATED_SUMMARY: str = "validated_summary"
CHANNEL_DIGEST: str = "digest"
CHANNEL_CHAT_REQUEST: str = "chat_request"
CHANNEL_CHAT_RESPONSE: str = "chat_response"
CHANNEL_SYSTEM: str = "system"

ALL_CHANNELS: tuple[str, ...] = (
    CHANNEL_STORY,
    CHANNEL_FETCH_REQUEST,
    CHANNEL_ARTICLE,
    CHANNEL_SUMMARIZE_REQUEST,
    CHANNEL_CATEGORY,
    CHANNEL_SCORE,
    CHANNEL_SUMMARY,
    CHANNEL_VALIDATED_SUMMARY,
    CHANNEL_DIGEST,
    CHANNEL_CHAT_REQUEST,
    CHANNEL_CHAT_RESPONSE,
    CHANNEL_SYSTEM,
)


class MessageBus:
    """In-process async message bus using asyncio.Queue per channel.

    Supports fan-out: multiple subscribers on one channel each receive a
    copy of every published message. Channels are pre-created for all
    eight channels defined in SPEC-000 section 5.2, and additional
    channels can be created at runtime.

    Example::

        bus = MessageBus()
        queue = bus.subscribe("story")
        await bus.publish("story", BusMessage(
            type="new_story",
            timestamp=datetime.now(timezone.utc),
            source="collector",
            payload=StoryPayload(story_id=12345, title="Example"),
        ))
        msg = await queue.get()
    """

    def __init__(self) -> None:
        """Initialize the message bus with all standard channels."""
        self._channels: dict[str, list[asyncio.Queue[BusMessage]]] = {}
        for name in ALL_CHANNELS:
            self._channels[name] = []
        logger.info(
            "MessageBus initialized with channels: %s",
            ", ".join(ALL_CHANNELS),
        )

    def create_channel(self, name: str) -> None:
        """Create a new channel if it does not already exist.

        Args:
            name: The channel name to create.
        """
        if name not in self._channels:
            self._channels[name] = []
            logger.info("Created channel: %s", name)

    def subscribe(self, channel: str) -> asyncio.Queue[BusMessage]:
        """Subscribe to a channel, returning a Queue that receives future messages.

        Args:
            channel: The channel name to subscribe to.

        Returns:
            An asyncio.Queue that will receive a copy of each BusMessage
            published to the channel after this subscription.

        Raises:
            ValueError: If the channel does not exist.
        """
        if channel not in self._channels:
            raise ValueError(f"Channel does not exist: {channel!r}")
        queue: asyncio.Queue[BusMessage] = asyncio.Queue()
        self._channels[channel].append(queue)
        logger.debug(
            "New subscriber on channel %r (total: %d)",
            channel,
            len(self._channels[channel]),
        )
        return queue

    async def publish(self, channel: str, message: BusMessage) -> None:
        """Publish a BusMessage to all subscribers of a channel.

        Args:
            channel: The channel name to publish to.
            message: A validated BusMessage instance.

        Raises:
            ValueError: If the channel does not exist.
            TypeError: If message is not a BusMessage instance.
        """
        if channel not in self._channels:
            raise ValueError(f"Channel does not exist: {channel!r}")
        if not isinstance(message, BusMessage):
            raise TypeError(
                f"Message must be a BusMessage, got {type(message).__name__}"
            )
        subscribers = self._channels[channel]
        for queue in subscribers:
            await queue.put(message)
        logger.debug(
            "Published message type=%r to channel %r (%d subscribers)",
            message.type,
            channel,
            len(subscribers),
        )

    @property
    def channels(self) -> list[str]:
        """Return a sorted list of all channel names."""
        return sorted(self._channels.keys())

    def subscriber_count(self, channel: str) -> int:
        """Return the number of subscribers on a channel.

        Args:
            channel: The channel name to query.

        Returns:
            The number of active subscriber queues.

        Raises:
            ValueError: If the channel does not exist.
        """
        if channel not in self._channels:
            raise ValueError(f"Channel does not exist: {channel!r}")
        return len(self._channels[channel])
