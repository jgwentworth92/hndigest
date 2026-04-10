"use client";

import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

interface ActionPanelProps {
  onRunStarted: (runId: string, type: string) => void;
}

export function ActionPanel({ onRunStarted }: ActionPanelProps) {
  const [busy, setBusy] = useState(false);
  const [maxStories, setMaxStories] = useState(10);
  const [error, setError] = useState<string | null>(null);

  // Auto-clear error after 5 seconds
  useEffect(() => {
    if (!error) return;
    const timer = setTimeout(() => setError(null), 5000);
    return () => clearTimeout(timer);
  }, [error]);

  async function handleAction(
    action: () => Promise<{ run_id: string; status: string }>,
    type: string,
  ) {
    setBusy(true);
    setError(null);
    try {
      const result = await action();
      onRunStarted(result.run_id, type);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  const actions = [
    {
      label: "Run Pipeline",
      action: () => api.runPipeline(maxStories),
      type: "pipeline",
      primary: true,
    },
    {
      label: "Collect",
      action: () => api.collect(maxStories),
      type: "collect",
      primary: false,
    },
    { label: "Score", action: () => api.score(), type: "score", primary: false },
    {
      label: "Categorize",
      action: () => api.categorize(),
      type: "categorize",
      primary: false,
    },
    {
      label: "Generate Digest",
      action: () => api.generateDigest(),
      type: "digest",
      primary: false,
    },
  ];

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <label htmlFor="max-stories" className="text-sm text-muted-foreground">
          Max stories:
        </label>
        <input
          id="max-stories"
          type="number"
          min={1}
          max={100}
          value={maxStories}
          onChange={(e) =>
            setMaxStories(Math.max(1, Math.min(100, Number(e.target.value) || 1)))
          }
          className="w-20 rounded-md border border-input bg-background px-2 py-1 text-sm"
        />
        {actions.map(({ label, action, type, primary }) => (
          <Button
            key={type}
            variant={primary ? "default" : "outline"}
            size="sm"
            disabled={busy}
            onClick={() => handleAction(action, type)}
          >
            {label}
          </Button>
        ))}
      </div>
      {error && (
        <Badge variant="destructive" className="text-sm">
          {error}
        </Badge>
      )}
    </div>
  );
}
