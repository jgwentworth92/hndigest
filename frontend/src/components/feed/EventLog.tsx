"use client";

import type { WsEvent } from "@/lib/events";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface EventLogProps {
  events: WsEvent[];
}

export function EventLog({ events }: EventLogProps) {
  const visible = events.slice(-200);

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-lg">Event Log</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="bg-gray-900 rounded-lg p-4 h-80 overflow-y-auto font-mono text-xs leading-relaxed">
          {visible.length === 0 && (
            <span className="text-gray-500">Waiting for events...</span>
          )}
          {visible.map((ev, i) => {
            const ts = ev.timestamp
              ? new Date(ev.timestamp).toLocaleTimeString()
              : "--:--:--";
            const storyOrRun =
              (ev.data?.story_id as string | number | undefined) ??
              ev.run_id ??
              "";
            return (
              <div key={i} className="whitespace-nowrap">
                <span className="text-gray-400">{ts}</span>{" "}
                <span className="text-green-400">{ev.event}</span>{" "}
                {storyOrRun && (
                  <span className="text-yellow-400">{String(storyOrRun)}</span>
                )}
              </div>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}
