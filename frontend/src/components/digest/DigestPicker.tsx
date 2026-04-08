"use client";

import { useApi } from "@/hooks/useApi";
import { api } from "@/lib/api";
import type { DigestSummary } from "@/lib/types";
import { useCallback } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

export function DigestPicker({
  currentId,
  onSelect,
}: {
  currentId: number | null;
  onSelect: (id: number) => void;
}) {
  const fetcher = useCallback(() => api.getDigests(20), []);
  const { data: digests } = useApi<DigestSummary[]>(fetcher);

  if (!digests || digests.length === 0) return null;

  return (
    <div className="flex items-center gap-2 text-sm">
      <span className="text-muted-foreground">Digest:</span>
      <Select
        value={currentId ?? undefined}
        onValueChange={(val) => onSelect(Number(val))}
      >
        <SelectTrigger size="sm">
          <SelectValue placeholder="Select digest" />
        </SelectTrigger>
        <SelectContent>
          {digests.map((d) => (
            <SelectItem key={d.id} value={d.id}>
              {new Date(d.created_at).toLocaleDateString()} ({d.story_count}{" "}
              stories)
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
