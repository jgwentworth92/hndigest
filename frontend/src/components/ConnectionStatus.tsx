"use client";

import { useWebSocket } from "@/hooks/useWebSocket";

export function ConnectionStatus() {
  const { connected } = useWebSocket();
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full ${connected ? "bg-green-500" : "bg-red-500"}`}
      title={connected ? "WebSocket connected" : "WebSocket disconnected"}
    />
  );
}
