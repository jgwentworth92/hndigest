"use client";

import { useApi } from "@/hooks/useApi";
import { api } from "@/lib/api";
import type { DigestSummary } from "@/lib/types";
import { useCallback } from "react";

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
      <label htmlFor="digest-picker" className="text-gray-600">
        Digest:
      </label>
      <select
        id="digest-picker"
        value={currentId ?? ""}
        onChange={(e) => onSelect(Number(e.target.value))}
        className="border border-gray-300 rounded px-2 py-1 text-sm bg-white"
      >
        {digests.map((d) => (
          <option key={d.id} value={d.id}>
            {new Date(d.created_at).toLocaleDateString()} ({d.story_count}{" "}
            stories)
          </option>
        ))}
      </select>
    </div>
  );
}
