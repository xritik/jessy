"""
WebSocket Connection Manager — Phase 10.

Bridges the Agent's synchronous on_event callback (agent/agent.py) and
voice/state.py's synchronous set_state() calls to live, asynchronous
WebSocket pushes, for any GUI client connected to a given session_id.

Why this needs a bridge at all:
    - FastAPI/Starlette's WebSocket.send_json() is a coroutine — it must
      be awaited on the asyncio event loop that owns the socket.
    - agent.run() executes synchronously and is now run inside a worker
      thread (via starlette.concurrency.run_in_threadpool) from the
      /chat and /voice endpoints in main.py, specifically so the main
      event loop stays free to actually deliver these WebSocket
      messages *while* the agent loop is still running, instead of
      queuing them up and only flushing them once the whole HTTP
      request has already finished.
    - Because agent.run()'s on_event fires from that worker thread (a
      different thread than the one running the event loop), we can't
      just `await` the send directly. asyncio.run_coroutine_threadsafe
      is the standard bridge: it schedules a coroutine onto a specific
      event loop from any other thread, thread-safely.

Session fanout:
    - Zero or more GUI tabs can be watching the same session_id at
      once (e.g. a page refresh briefly leaves two sockets open). Every
      event is broadcast to all currently-connected sockets for that
      session_id. If nobody is connected, events are silently dropped
      — the HTTP response from /chat or /voice is always the source of
      truth regardless of whether anyone was watching live.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("jessy.ws.manager")


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, set[WebSocket]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called once at app startup (main.py), so emit_from_thread()
        has a loop to schedule sends onto even when called from a
        worker thread."""
        self._loop = loop

    async def connect(self, session_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.setdefault(session_id, set()).add(websocket)
        logger.info("WebSocket connected for session %s", session_id)

    def disconnect(self, session_id: str, websocket: WebSocket) -> None:
        conns = self._connections.get(session_id)
        if conns is not None:
            conns.discard(websocket)
            if not conns:
                self._connections.pop(session_id, None)
        logger.info("WebSocket disconnected for session %s", session_id)

    async def _broadcast(self, session_id: str, message: dict[str, Any]) -> None:
        conns = list(self._connections.get(session_id, ()))
        if not conns:
            return
        dead: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(session_id, ws)

    def emit_from_thread(
        self, session_id: str, event_type: str, payload: dict[str, Any]
    ) -> None:
        """
        Thread-safe entry point: safe to call from agent.run()'s
        on_event callback (which executes inside a worker thread), or
        from any plain synchronous code such as voice/state.set_state().

        Silently no-ops if the event loop hasn't been bound yet, or if
        nobody is connected for this session — never raises into the
        caller, since a dropped live event must never break the actual
        HTTP response.
        """
        if self._loop is None:
            return
        message = {
            "type": event_type,
            "session_id": session_id,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            asyncio.run_coroutine_threadsafe(
                self._broadcast(session_id, message), self._loop
            )
        except Exception:
            logger.exception(
                "Failed to schedule WebSocket broadcast for session %s", session_id
            )


# Single process-wide instance, imported by main.py and voice/state.py.
manager = ConnectionManager()
