"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

interface AgentCardProps {
  name: string;
  status: string;
  lastHeartbeat: string;
  messagesProcessed: number;
}

function statusVariant(status: string): "default" | "secondary" | "destructive" {
  switch (status) {
    case "running":
      return "default";
    case "stopped":
      return "secondary";
    case "error":
    case "crashed":
      return "destructive";
    default:
      return "secondary";
  }
}

export function AgentCard({
  name,
  status,
  lastHeartbeat,
  messagesProcessed,
}: AgentCardProps) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-base capitalize">
            {name.replace(/_/g, " ")}
          </CardTitle>
          <Badge variant={statusVariant(status)}>{status}</Badge>
        </div>
      </CardHeader>
      <CardContent>
        <div className="text-sm text-muted-foreground space-y-1">
          <div>Messages: {messagesProcessed}</div>
          <div>Last heartbeat: {lastHeartbeat || "N/A"}</div>
        </div>
      </CardContent>
    </Card>
  );
}
