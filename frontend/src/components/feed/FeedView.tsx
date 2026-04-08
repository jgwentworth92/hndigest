"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import { api } from "@/lib/api";
import { useWebSocket } from "@/hooks/useWebSocket";
import { STORY_EVENTS, PIPELINE_EVENTS } from "@/lib/events";
import type { WsEvent } from "@/lib/events";
import type { PipelineProgress } from "@/lib/types";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { ActionPanel } from "./ActionPanel";
import { ProgressBar } from "./ProgressBar";
import { EventLog } from "./EventLog";

interface ActiveRun {
  runId: string;
  type: string;
  status: string;
  progress: PipelineProgress | null;
}

export function FeedView() {
  const [activeRun, setActiveRun] = useState<ActiveRun | null>(null);
  const [events, setEvents] = useState<WsEvent[]>([]);
  const { subscribe } = useWebSocket();
  const mountedRef = useRef(true);

  // Reconnect recovery: fetch existing runs on mount
  useEffect(() => {
    mountedRef.current = true;
    api
      .getRuns()
      .then((runs) => {
        if (!mountedRef.current) return;
        const running = runs.find(
          (r) => r.status === "running" || r.status === "started",
        );
        if (running) {
          setActiveRun({
            runId: running.run_id,
            type: running.type,
            status: running.status,
            progress: running.progress,
          });
        }
      })
      .catch(() => {});
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Subscribe to pipeline lifecycle events
  useEffect(() => {
    return subscribe(PIPELINE_EVENTS, (ev: WsEvent) => {
      setEvents((prev) => [...prev.slice(-199), ev]);

      if (ev.event === "pipeline_started") {
        setActiveRun({
          runId: ev.run_id ?? "",
          type: (ev.data?.type as string) ?? "pipeline",
          status: "running",
          progress: null,
        });
      } else if (ev.event === "pipeline_progress") {
        setActiveRun((prev) =>
          prev
            ? { ...prev, progress: ev.data as unknown as PipelineProgress }
            : prev,
        );
      } else if (ev.event === "pipeline_completed") {
        setActiveRun((prev) =>
          prev ? { ...prev, status: "completed" } : prev,
        );
      }
    });
  }, [subscribe]);

  // Subscribe to story-level events
  useEffect(() => {
    return subscribe(STORY_EVENTS, (ev: WsEvent) => {
      setEvents((prev) => [...prev.slice(-199), ev]);
    });
  }, [subscribe]);

  const handleRunStarted = useCallback((runId: string, type: string) => {
    setActiveRun({ runId, type, status: "started", progress: null });
  }, []);

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Live Feed</h1>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-lg">Actions</CardTitle>
        </CardHeader>
        <CardContent>
          <ActionPanel onRunStarted={handleRunStarted} />
        </CardContent>
      </Card>

      <Separator />

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-lg">Pipeline Progress</CardTitle>
        </CardHeader>
        <CardContent>
          <ProgressBar
            progress={activeRun?.progress ?? null}
            status={activeRun?.status ?? "idle"}
          />
        </CardContent>
      </Card>

      <Separator />

      <EventLog events={events} />
    </div>
  );
}
