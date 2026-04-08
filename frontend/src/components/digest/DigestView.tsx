"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useApi } from "@/hooks/useApi";
import { StoryCard } from "./StoryCard";
import { DigestPicker } from "./DigestPicker";
import { Separator } from "@/components/ui/separator";
import { Card, CardContent } from "@/components/ui/card";
import type { DigestDetail, DigestContent } from "@/lib/types";

export function DigestView() {
  const [digestId, setDigestId] = useState<number | null>(null);
  const fetcher = useCallback(
    () => (digestId ? api.getDigest(digestId) : api.getLatestDigest()),
    [digestId],
  );
  const {
    data: digest,
    error,
    loading,
    refetch,
  } = useApi<DigestDetail | null>(fetcher);
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

  if (loading)
    return <p className="text-muted-foreground">Loading digest...</p>;
  if (error)
    return <p className="text-destructive">Error: {error}</p>;

  if (!digest) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <h2 className="text-xl font-semibold mb-2">No digests yet</h2>
          <p className="text-muted-foreground">
            Run the pipeline to generate your first daily digest.
          </p>
        </CardContent>
      </Card>
    );
  }

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
          <p className="text-sm text-muted-foreground">
            {new Date(digest.period_start).toLocaleDateString()} &mdash;{" "}
            {new Date(digest.period_end).toLocaleDateString()} |{" "}
            {digest.story_count} stories
          </p>
        </div>
        <DigestPicker currentId={digestId} onSelect={setDigestId} />
      </div>

      {categories.length === 0 && (
        <p className="text-muted-foreground">No stories in this digest.</p>
      )}

      {categories.map((category, idx) => (
        <div key={category}>
          {idx > 0 && <Separator className="my-6" />}
          <div className="mb-6">
            <h2 className="text-lg font-semibold mb-3 capitalize">
              {category.replace(/_/g, " ")}
            </h2>
            <div className="space-y-3">
              {content[category].map((story, storyIdx) => (
                <StoryCard key={storyIdx} story={story} />
              ))}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
