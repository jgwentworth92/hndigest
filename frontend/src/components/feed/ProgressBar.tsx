"use client";

import type { PipelineProgress } from "@/lib/types";
import { Progress } from "@/components/ui/progress";

interface ProgressBarProps {
  progress: PipelineProgress | null;
  status: string;
}

export function ProgressBar({ progress, status }: ProgressBarProps) {
  if (!progress || progress.total_stories === 0) {
    return (
      <div className="text-sm text-muted-foreground">
        {status === "idle"
          ? "No active pipeline run."
          : `Status: ${status}`}
      </div>
    );
  }

  const total = progress.total_stories;
  const stages = [
    { label: "Collected", value: progress.collected },
    { label: "Scored", value: progress.scored },
    { label: "Categorized", value: progress.categorized },
    { label: "Fetched", value: progress.fetched },
    { label: "Summarized", value: progress.summarized },
    { label: "Validated", value: progress.validated },
  ];

  const avg =
    stages.reduce((sum, s) => sum + s.value, 0) / (stages.length * total);
  const pct = Math.min(Math.round(avg * 100), 100);

  return (
    <div>
      <div className="flex items-center gap-3 mb-2">
        <Progress value={pct} className="flex-1 h-4" />
        <span className="text-sm font-medium text-muted-foreground w-12 text-right">
          {pct}%
        </span>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {stages.map((s) => (
          <span key={s.label}>
            {s.label}: {s.value}/{total}
          </span>
        ))}
      </div>
    </div>
  );
}
