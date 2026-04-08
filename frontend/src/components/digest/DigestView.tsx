"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useApi } from "@/hooks/useApi";
import { StoryCard } from "./StoryCard";
import { DigestPicker } from "./DigestPicker";
import type { DigestDetail, DigestContent } from "@/lib/types";

export function DigestView() {
  const [digestId, setDigestId] = useState<number | null>(null);
  const fetcher = useCallback(
    () => (digestId ? api.getDigest(digestId) : api.getLatestDigest()),
    [digestId],
  );
  const { data: digest, error, loading, refetch } = useApi<DigestDetail>(fetcher);
  const { subscribe } = useWebSocket();

  // Auto-refresh on digest_ready
  useEffect(() => {
    return subscribe(["digest_ready"], () => {
      if (!digestId) refetch(); // only auto-refresh if viewing latest
    });
  }, [subscribe, digestId, refetch]);

  // Update digestId when latest loads
  useEffect(() => {
    if (digest && !digestId) {
      setDigestId(digest.id);
    }
  }, [digest, digestId]);

  if (loading) return <p className="text-gray-500">Loading digest...</p>;
  if (error) return <p className="text-red-600">Error: {error}</p>;
  if (!digest) return <p className="text-gray-500">No digests yet.</p>;

  // content_json is stored as a JSON string in the DB, parsed by the API
  let content: DigestContent;
  if (typeof digest.content_json === "string") {
    content = JSON.parse(digest.content_json);
  } else {
    content = digest.content_json as DigestContent;
  }

  const categories = Object.keys(content).sort();

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold">Daily Digest</h1>
          <p className="text-sm text-gray-500">
            {new Date(digest.period_start).toLocaleDateString()} &mdash;{" "}
            {new Date(digest.period_end).toLocaleDateString()} |{" "}
            {digest.story_count} stories
          </p>
        </div>
        <DigestPicker currentId={digestId} onSelect={setDigestId} />
      </div>

      {categories.length === 0 && (
        <p className="text-gray-500">No stories in this digest.</p>
      )}

      {categories.map((category) => (
        <div key={category} className="mb-8">
          <h2 className="text-lg font-semibold mb-3 capitalize">
            {category.replace(/_/g, " ")}
          </h2>
          <div className="space-y-3">
            {content[category].map((story, idx) => (
              <StoryCard key={idx} story={story} />
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
