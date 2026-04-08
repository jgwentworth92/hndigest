"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";

interface ActionPanelProps {
  onRunStarted: (runId: string, type: string) => void;
}

const ACTIONS = [
  { label: "Run Pipeline", action: () => api.runPipeline(), type: "pipeline", primary: true },
  { label: "Collect", action: () => api.collect(), type: "collect", primary: false },
  { label: "Score", action: () => api.score(), type: "score", primary: false },
  { label: "Categorize", action: () => api.categorize(), type: "categorize", primary: false },
  {
    label: "Generate Digest",
    action: () => api.generateDigest(),
    type: "digest",
    primary: false,
  },
] as const;

export function ActionPanel({ onRunStarted }: ActionPanelProps) {
  const [busy, setBusy] = useState(false);

  async function handleAction(
    action: () => Promise<{ run_id: string; status: string }>,
    type: string,
  ) {
    setBusy(true);
    try {
      const result = await action();
      onRunStarted(result.run_id, type);
    } catch {
      // error is visible in event log
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-wrap gap-2">
      {ACTIONS.map(({ label, action, type, primary }) => (
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
  );
}
