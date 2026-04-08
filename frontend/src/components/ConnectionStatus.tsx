"use client";

import { useWebSocket } from "@/hooks/useWebSocket";
import { Badge } from "@/components/ui/badge";

export function ConnectionStatus() {
  const { connected } = useWebSocket();
  return (
    <Badge variant={connected ? "default" : "destructive"} className="text-xs">
      {connected ? "Connected" : "Disconnected"}
    </Badge>
  );
}
