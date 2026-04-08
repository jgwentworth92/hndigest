"use client";

import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  useCallback,
  type ReactNode,
} from "react";
import type { WsEvent, EventType } from "@/lib/events";
import { api } from "@/lib/api";

type Listener = (event: WsEvent) => void;

interface WsContextValue {
  connected: boolean;
  subscribe: (events: EventType[], listener: Listener) => () => void;
}

const WsContext = createContext<WsContextValue>({
  connected: false,
  subscribe: () => () => {},
});

export function useWebSocket() {
  return useContext(WsContext);
}

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const listenersRef = useRef<Map<EventType, Set<Listener>>>(new Map());
  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);

  const dispatch = useCallback((event: WsEvent) => {
    const listeners = listenersRef.current.get(event.event);
    if (listeners) {
      listeners.forEach((fn) => fn(event));
    }
  }, []);

  const connect = useCallback(() => {
    const wsUrl = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";
    const ws = new WebSocket(`${wsUrl}/api/events`);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      retriesRef.current = 0;
    };

    ws.onmessage = (msg) => {
      try {
        const event: WsEvent = JSON.parse(msg.data);
        if (event.event === "ping") return;
        dispatch(event);
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      setConnected(false);
      wsRef.current = null;
      // Exponential backoff: 1s, 2s, 4s, 8s, ..., max 30s
      const delay = Math.min(1000 * 2 ** retriesRef.current, 30000);
      retriesRef.current += 1;
      setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [dispatch]);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
  }, [connect]);

  // On reconnect, refetch current state
  useEffect(() => {
    if (!connected || retriesRef.current === 0) return;
    // Fire-and-forget REST refetch for reconnect recovery
    api.getRuns().catch(() => {});
    api.getHealth().catch(() => {});
    api.getLatestDigest().catch(() => {});
  }, [connected]);

  const subscribe = useCallback(
    (events: EventType[], listener: Listener) => {
      for (const event of events) {
        if (!listenersRef.current.has(event)) {
          listenersRef.current.set(event, new Set());
        }
        listenersRef.current.get(event)!.add(listener);
      }
      return () => {
        for (const event of events) {
          listenersRef.current.get(event)?.delete(listener);
        }
      };
    },
    [],
  );

  return (
    <WsContext.Provider value={{ connected, subscribe }}>
      {children}
    </WsContext.Provider>
  );
}
