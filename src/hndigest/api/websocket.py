"""WebSocket endpoint for real-time event streaming.

Subscribes to the message bus story, digest, and score channels and
broadcasts each ``BusMessage`` as JSON to all connected WebSocket clients.
A keepalive ping is sent when no events arrive within the timeout window.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from hndigest.bus import CHANNEL_DIGEST, CHANNEL_SCORE, CHANNEL_STORY, MessageBus
from hndigest.models import BusMessage

logger = logging.getLogger(__name__)
router = APIRouter(tags=["websocket"])


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

    Subscribes to the story, digest, and score bus channels via the
    supervisor stored in ``app.state``.  Each ``BusMessage`` received
    from any channel is serialised to JSON and broadcast to all
    connected clients.  A keepalive ``{"event": "ping"}`` is sent when
    no bus events arrive within 30 seconds.

    Args:
        websocket: The incoming WebSocket connection.
    """
    await manager.connect(websocket)

    # Get supervisor from app state to access the bus
    supervisor = websocket.app.state.supervisor
    bus: MessageBus = supervisor.bus

    # Subscribe to channels
    story_queue: asyncio.Queue[BusMessage] = bus.subscribe(CHANNEL_STORY)
    digest_queue: asyncio.Queue[BusMessage] = bus.subscribe(CHANNEL_DIGEST)
    score_queue: asyncio.Queue[BusMessage] = bus.subscribe(CHANNEL_SCORE)

    try:
        while True:
            # Watch all queues with a timeout
            tasks = {
                asyncio.create_task(story_queue.get()): "story",
                asyncio.create_task(digest_queue.get()): "digest",
                asyncio.create_task(score_queue.get()): "score",
            }

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
                    # Serialize using Pydantic v2
                    await manager.broadcast(
                        {
                            "event": channel,
                            "type": message.type,
                            "source": message.source,
                            "timestamp": message.timestamp.isoformat(),
                            "payload": message.payload.model_dump(),
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
