"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { useWebSocket } from "@/hooks/useWebSocket";
import type { WsEvent } from "@/lib/events";
import type {
  HealthResponse,
  CategoryCount,
  SystemConfig,
} from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { AgentCard } from "./AgentCard";
import { CategoryTable } from "./CategoryTable";

interface AgentInfo {
  name: string;
  status: string;
  lastHeartbeat: string;
  messagesProcessed: number;
}

export function SystemView() {
  const healthFetcher = useCallback(() => api.getHealth(), []);
  const categoriesFetcher = useCallback(() => api.getCategories(), []);
  const configFetcher = useCallback(() => api.getConfig(), []);

  const { data: health, error: healthErr, loading: healthLoading } =
    useApi<HealthResponse>(healthFetcher);
  const { data: categories } = useApi<CategoryCount[]>(categoriesFetcher);
  const { data: config } = useApi<SystemConfig>(configFetcher);

  const [agents, setAgents] = useState<Record<string, AgentInfo>>({});
  const { subscribe } = useWebSocket();

  // Seed agents from health response
  useEffect(() => {
    if (!health?.agents) return;
    const seeded: Record<string, AgentInfo> = {};
    for (const [name, a] of Object.entries(health.agents)) {
      seeded[name] = {
        name: a.name,
        status: a.status,
        lastHeartbeat: `${a.last_heartbeat_ago}s ago`,
        messagesProcessed: a.messages_processed,
      };
    }
    setAgents(seeded);
  }, [health]);

  // Update agents from heartbeat WebSocket events
  useEffect(() => {
    return subscribe(["agent_heartbeat"], (ev: WsEvent) => {
      const d = ev.data ?? {};
      const name = (d.agent as string) ?? ev.source ?? "unknown";
      setAgents((prev) => ({
        ...prev,
        [name]: {
          name,
          status: (d.status as string) ?? "running",
          lastHeartbeat: ev.timestamp
            ? new Date(ev.timestamp).toLocaleTimeString()
            : "just now",
          messagesProcessed: (d.messages_processed as number) ?? 0,
        },
      }));
    });
  }, [subscribe]);

  const agentList = Object.values(agents);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">System</h1>

      {/* Health + Categories grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Health card */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-lg">Health</CardTitle>
          </CardHeader>
          <CardContent>
            {healthLoading && (
              <p className="text-muted-foreground text-sm">Loading...</p>
            )}
            {healthErr && (
              <p className="text-destructive text-sm">Error: {healthErr}</p>
            )}
            {health && (
              <div className="text-sm space-y-1 text-muted-foreground">
                <div>
                  Status:{" "}
                  <span
                    className={
                      health.status === "ok"
                        ? "text-green-600 font-medium"
                        : "text-destructive font-medium"
                    }
                  >
                    {health.status}
                  </span>
                </div>
                <div>
                  Uptime: {Math.round(health.uptime_seconds / 60)} minutes
                </div>
                {health.mode && <div>Mode: {health.mode}</div>}
                {health.database && <div>Database: {health.database}</div>}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Category table */}
        <Card>
          <CardHeader className="pb-3">
            <CardTitle className="text-lg">Categories</CardTitle>
          </CardHeader>
          <CardContent>
            {categories && categories.length > 0 ? (
              <CategoryTable categories={categories} />
            ) : (
              <p className="text-muted-foreground text-sm italic">
                No category data yet.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <Separator />

      {/* Agent cards */}
      <div>
        <h2 className="text-lg font-semibold mb-3">Agents</h2>
        {agentList.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {agentList.map((a) => (
              <AgentCard
                key={a.name}
                name={a.name}
                status={a.status}
                lastHeartbeat={a.lastHeartbeat}
                messagesProcessed={a.messagesProcessed}
              />
            ))}
          </div>
        ) : (
          <p className="text-muted-foreground text-sm italic">
            No heartbeats yet.
          </p>
        )}
      </div>

      <Separator />

      {/* Config */}
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-lg">Configuration</CardTitle>
        </CardHeader>
        <CardContent>
          {config ? (
            <pre className="bg-muted rounded-lg p-4 text-xs overflow-x-auto">
              {JSON.stringify(config, null, 2)}
            </pre>
          ) : (
            <p className="text-muted-foreground text-sm italic">Loading config...</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
