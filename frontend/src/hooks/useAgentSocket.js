import { useCallback, useEffect, useRef, useState } from "react";

const WS_BASE = (import.meta.env.VITE_API_URL || "http://localhost:8000").replace(
  /^http/,
  "ws"
);

/**
 * Manages the live progress-event WebSocket for one session.
 * Reconnects with exponential backoff on drop. Purely additive: if
 * the socket never connects, /chat (Step 11.2) still works — this
 * only feeds the live "what's happening right now" layer.
 */
export function useAgentSocket(sessionId, onEvent) {
  const [connected, setConnected] = useState(false);
  const socketRef = useRef(null);
  const retryRef = useRef(0);
  const closedByUserRef = useRef(false);
  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const connect = useCallback(() => {
    if (!sessionId) return;
    const ws = new WebSocket(`${WS_BASE}/ws/${sessionId}`);
    socketRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      retryRef.current = 0;
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        onEventRef.current?.(data);
      } catch {
        // Ignore malformed frames rather than crashing the UI.
      }
    };

    ws.onclose = () => {
      setConnected(false);
      if (closedByUserRef.current) return;
      const delay = Math.min(1000 * 2 ** retryRef.current, 10000);
      retryRef.current += 1;
      setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [sessionId]);

  useEffect(() => {
    closedByUserRef.current = false;
    connect();
    return () => {
      closedByUserRef.current = true;
      socketRef.current?.close();
    };
  }, [connect]);

  return { connected };
}
