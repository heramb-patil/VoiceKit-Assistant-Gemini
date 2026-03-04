"""
WebSocket Notification Server

Real-time push notifications for task completion and follow-up questions.
Alternative to HTTP polling for better UX.
"""
import asyncio
import logging
from typing import Dict, Set
from fastapi import WebSocket, WebSocketDisconnect
import json

logger = logging.getLogger(__name__)


class NotificationWebSocketManager:
    """
    Manages WebSocket connections for real-time notifications.

    Clients connect via /gemini-live/notifications?user_identity=...
    and receive push notifications when:
    - Background tasks complete
    - Follow-up questions are asked
    - Other orchestration events occur
    """

    def __init__(self):
        # user_identity -> set of active WebSocket connections
        self.connections: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_identity: str, websocket: WebSocket):
        """Register a new WebSocket connection."""
        await websocket.accept()

        async with self._lock:
            if user_identity not in self.connections:
                self.connections[user_identity] = set()
            self.connections[user_identity].add(websocket)

        logger.info(f"WebSocket connected: {user_identity} (total: {len(self.connections[user_identity])})")

    async def disconnect(self, user_identity: str, websocket: WebSocket):
        """Unregister a WebSocket connection."""
        async with self._lock:
            if user_identity in self.connections:
                self.connections[user_identity].discard(websocket)
                if not self.connections[user_identity]:
                    del self.connections[user_identity]

        logger.info(f"WebSocket disconnected: {user_identity}")

    async def send_notification(self, user_identity: str, message: dict):
        """
        Send notification to all active connections for a user.

        Message format:
            {
                "type": "task_complete" | "followup_question" | "error",
                "task_id": str (optional),
                "result": str (optional),
                "question": str (optional),
                "error": str (optional)
            }
        """
        async with self._lock:
            connections = self.connections.get(user_identity, set()).copy()

        if not connections:
            logger.debug(f"No active WebSocket connections for {user_identity}")
            return

        # Send to all active connections
        dead_connections = set()
        message_json = json.dumps(message)

        for websocket in connections:
            try:
                await websocket.send_text(message_json)
                logger.debug(f"Sent notification to {user_identity}: {message['type']}")
            except Exception as e:
                logger.warning(f"Failed to send to WebSocket: {e}")
                dead_connections.add(websocket)

        # Cleanup dead connections
        if dead_connections:
            async with self._lock:
                if user_identity in self.connections:
                    self.connections[user_identity] -= dead_connections
                    if not self.connections[user_identity]:
                        del self.connections[user_identity]

    async def broadcast_notification(self, message: dict):
        """Broadcast notification to all connected users."""
        async with self._lock:
            all_users = list(self.connections.keys())

        for user_identity in all_users:
            await self.send_notification(user_identity, message)

    def get_connection_count(self, user_identity: str = None) -> int:
        """Get number of active connections (for a specific user or total)."""
        if user_identity:
            return len(self.connections.get(user_identity, set()))
        return sum(len(conns) for conns in self.connections.values())


# Global WebSocket manager instance
ws_manager = NotificationWebSocketManager()


class FollowUpChannelHTTP:
    """
    HTTP-based follow-up channel for frontend-backend communication.

    Replaces STT-based answer capture with HTTP callbacks.
    """

    def __init__(self, timeout_seconds: float = 30.0):
        self._timeout = timeout_seconds
        self._pending: Dict[str, asyncio.Future] = {}  # user_identity -> Future

    async def ask(self, user_identity: str, question: str) -> str:
        """
        Ask a follow-up question via WebSocket push.

        Blocks until user responds via POST /followup-response endpoint.
        Returns user's answer or empty string on timeout.
        """
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending[user_identity] = future

        # Notify frontend via WebSocket
        try:
            await ws_manager.send_notification(user_identity, {
                "type": "followup_question",
                "question": question
            })
        except Exception as e:
            logger.error(f"Failed to send follow-up question: {e}")

        # Wait for response
        try:
            return await asyncio.wait_for(future, timeout=self._timeout)
        except asyncio.TimeoutError:
            self._pending.pop(user_identity, None)
            logger.warning(f"Follow-up question timed out for {user_identity}")
            return ""

    def resolve(self, user_identity: str, response_text: str) -> bool:
        """
        Called by POST /followup-response endpoint when user answers.

        Returns True if there was a pending question, False otherwise.
        """
        future = self._pending.pop(user_identity, None)
        if future is not None and not future.done():
            future.set_result(response_text)
            logger.info(f"Follow-up question resolved for {user_identity}")
            return True
        return False


# Global follow-up channel instance
followup_channel = FollowUpChannelHTTP()
