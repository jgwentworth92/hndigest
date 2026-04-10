"""WebSocket endpoint for real-time event streaming.

Subscribes to ALL data channels on the message bus and broadcasts each
``BusMessage`` as a structured JSON event to all connected WebSocket
clients. A keepalive ping is sent when no events arrive within the
timeout window.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from hndigest.bus import (
    CHANNEL_ARTICLE,
    CHANNEL_CATEGORY,
    CHANNEL_DIGEST,
    CHANNEL_FETCH_REQUEST,
    CHANNEL_SCORE,
    CHANNEL_STORY,
    CHANNEL_SUMMARIZE_REQUEST,
    CHANNEL_SUMMARY,
    CHANNEL_SYSTEM,
    CHANNEL_VALIDATED_SUMMARY,
    MessageBus,
)
from hndigest.models import BusMessage, HeartbeatPayload, PipelineProgressPayload

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])

# Map bus channels to human-readable frontend event names.
CHANNEL_EVENT_MAP: dict[str, str] = {
    CHANNEL_STORY: "story_collected",
    CHANNEL_SCORE: "story_scored",
    CHANNEL_CATEGORY: "story_categorized",
    CHANNEL_FETCH_REQUEST: "story_dispatched",
    CHANNEL_ARTICLE: "article_fetched",
    CHANNEL_SUMMARIZE_REQUEST: "summarize_requested",
    CHANNEL_SUMMARY: "summary_generated",
    CHANNEL_VALIDATED_SUMMARY: "summary_validated",
    CHANNEL_DIGEST: "digest_ready",
}


class ConnectionManager:
    """Manages active WebSocket connections and broadcasts bus events.

    Maintains a list of accepted WebSocket connections and provides
    helpers for accepting new clients, removing disconnected ones, and
    broadcasting a JSON payload to every active connection.
    """

    def __init__(self) -> None:
        """Initialize with an empty connection list."""
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket) -> None:
        """Accept a new WebSocket connection and track it.

        Args:
            websocket: The incoming WebSocket to accept.
        """
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        """Remove a WebSocket from the active connections list.

        Args:
            websocket: The WebSocket that disconnected.
        """
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, data: dict) -> None:
        """Send a JSON payload to all active connections.

        Connections that fail to receive are silently removed.

        Args:
            data: The JSON-serializable dict to send.
        """
        disconnected: list[WebSocket] = []
        for conn in self.active_connections:
            try:
                await conn.send_json(data)
            except Exception:
                disconnected.append(conn)
        for conn in disconnected:
            self.active_connections.remove(conn)


manager = ConnectionManager()


@router.websocket("/events")
async def websocket_events(websocket: WebSocket) -> None:
    """WebSocket endpoint for live event streaming.

    Subscribes to all data channels on the message bus and broadcasts
    each ``BusMessage`` as a structured JSON event to all connected
    clients. Events include a human-readable ``event`` name, the
    ``source`` agent, an ISO 8601 ``timestamp``, and the full payload
    data. A keepalive ``{"event": "ping"}`` is sent when no bus events
    arrive within 30 seconds.

    Args:
        websocket: The incoming WebSocket connection.
    """
    await manager.connect(websocket)

    # Get bus from app state (works for both supervisor and passive modes)
    bus: MessageBus
    supervisor = getattr(websocket.app.state, "supervisor", None)
    if supervisor is not None and hasattr(supervisor, "bus"):
        bus = supervisor.bus
    else:
        bus = websocket.app.state.bus

    # Subscribe to ALL data channels + system channel
    queues: dict[str, asyncio.Queue[BusMessage]] = {}
    for channel in CHANNEL_EVENT_MAP:
        queues[channel] = bus.subscribe(channel)
    queues[CHANNEL_SYSTEM] = bus.subscribe(CHANNEL_SYSTEM)

    try:
        while True:
            # Create a get-task for each subscribed queue
            tasks: dict[asyncio.Task[BusMessage], str] = {}
            for channel, queue in queues.items():
                tasks[asyncio.create_task(queue.get())] = channel

            done, pending = await asyncio.wait(
                tasks.keys(),
                timeout=30.0,
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            for task in done:
                channel = tasks[task]
                try:
                    message: BusMessage = task.result()

                    if channel == CHANNEL_SYSTEM:
                        # Translate system channel messages to frontend events
                        if message.type == "heartbeat" and isinstance(
                            message.payload, HeartbeatPayload
                        ):
                            await manager.broadcast(
                                {
                                    "event": "agent_heartbeat",
                                    "timestamp": message.timestamp.isoformat(),
                                    "data": {
                                        "agent": message.payload.agent,
                                        "status": message.payload.status,
                                        "last_heartbeat": message.timestamp.isoformat(),
                                        "messages_processed": message.payload.messages_processed,
                                    },
                                }
                            )
                        elif message.type in (
                            "pipeline_started",
                            "pipeline_progress",
                            "pipeline_completed",
                            "pipeline_failed",
                        ):
                            payload = message.payload
                            run_id = (
                                payload.run_id
                                if isinstance(payload, PipelineProgressPayload)
                                else ""
                            )
                            await manager.broadcast(
                                {
                                    "event": message.type,
                                    "run_id": run_id,
                                    "timestamp": message.timestamp.isoformat(),
                                    "source": message.source,
                                    "data": payload.model_dump(),
                                }
                            )
                        else:
                            # Other system events (shutdown, etc.)
                            await manager.broadcast(
                                {
                                    "event": f"system_{message.type}",
                                    "timestamp": message.timestamp.isoformat(),
                                    "source": message.source,
                                    "data": message.payload.model_dump(),
                                }
                            )
                    else:
                        event_name = CHANNEL_EVENT_MAP.get(channel, channel)
                        await manager.broadcast(
                            {
                                "event": event_name,
                                "timestamp": message.timestamp.isoformat(),
                                "source": message.source,
                                "data": message.payload.model_dump(),
                            }
                        )
                except Exception:
                    logger.exception("Error broadcasting WebSocket event")

            # If no events arrived, send a keepalive ping
            if not done:
                try:
                    await websocket.send_json({"event": "ping"})
                except Exception:
                    break

    except WebSocketDisconnect:
        manager.disconnect(websocket)
        logger.info("WebSocket client disconnected")
    except Exception:
        manager.disconnect(websocket)
        logger.exception("WebSocket error")
