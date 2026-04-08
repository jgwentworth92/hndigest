"use client";

import { useState } from "react";
import { api } from "@/lib/api";

interface ActionPanelProps {
  onRunStarted: (runId: string, type: string) => void;
}

const ACTIONS = [
  { label: "Run Pipeline", action: () => api.runPipeline(), type: "pipeline" },
  { label: "Collect", action: () => api.collect(), type: "collect" },
  { label: "Score", action: () => api.score(), type: "score" },
  { label: "Categorize", action: () => api.categorize(), type: "categorize" },
  {
    label: "Generate Digest",
    action: () => api.generateDigest(),
    type: "digest",
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
      {ACTIONS.map(({ label, action, type }) => (
        <button
          key={type}
          disabled={busy}
          onClick={() => handleAction(action, type)}
          className="px-4 py-2 bg-blue-600 text-white text-sm rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {label}
        </button>
      ))}
    </div>
  );
}
