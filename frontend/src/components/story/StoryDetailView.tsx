"use client";

import { useCallback } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import type { StoryDetail, ScoreComponents } from "@/lib/types";

function ScoreGrid({ components }: { components: ScoreComponents }) {
  const items: { label: string; key: keyof ScoreComponents }[] = [
    { label: "Score Velocity", key: "score_velocity" },
    { label: "Comment Velocity", key: "comment_velocity" },
    { label: "Front Page Presence", key: "front_page_presence" },
    { label: "Recency", key: "recency" },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
      {items.map(({ label, key }) => (
        <Card key={key} size="sm">
          <CardContent className="text-center">
            <div className="text-lg font-bold text-blue-600">
              {components[key].toFixed(2)}
            </div>
            <div className="text-xs text-muted-foreground mt-1">{label}</div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

export function StoryDetailView({ storyId }: { storyId: number }) {
  const fetcher = useCallback(() => api.getStory(storyId), [storyId]);
  const { data: story, error, loading } = useApi<StoryDetail>(fetcher);

  if (loading)
    return <p className="text-muted-foreground">Loading story...</p>;
  if (error)
    return <p className="text-destructive">Error: {error}</p>;
  if (!story)
    return <p className="text-muted-foreground">Story not found.</p>;

  const hnUrl = `https://news.ycombinator.com/item?id=${story.id}`;

  return (
    <div className="space-y-6">
      {/* Title and links */}
      <div>
        <h1 className="text-2xl font-bold">{story.title}</h1>
        <div className="flex flex-wrap items-center gap-3 mt-2 text-sm text-muted-foreground">
          {story.url && (
            <a
              href={story.url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-blue-700 hover:underline"
            >
              Source
            </a>
          )}
          <a
            href={hnUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="text-orange-600 hover:underline"
          >
            HN Discussion
          </a>
          <span>{story.score} points</span>
          <span>{story.comments} comments</span>
          <Badge variant="outline">{story.hn_type}</Badge>
        </div>
      </div>

      {/* Score breakdown */}
      <Card>
        <CardHeader>
          <CardTitle>Score Breakdown</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="mb-4">
            <span className="text-2xl font-bold text-blue-600">
              {story.composite_score !== null
                ? story.composite_score.toFixed(2)
                : "N/A"}
            </span>
            <span className="text-sm text-muted-foreground ml-2">
              composite
            </span>
          </div>
          {story.score_components ? (
            <ScoreGrid components={story.score_components} />
          ) : (
            <p className="text-muted-foreground text-sm italic">
              Not scored yet.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Categories */}
      <Card>
        <CardHeader>
          <CardTitle>Categories</CardTitle>
        </CardHeader>
        <CardContent>
          {story.categories.length > 0 ? (
            <div className="flex flex-wrap gap-2">
              {story.categories.map((cat) => (
                <Badge key={cat} variant="secondary">
                  {cat}
                </Badge>
              ))}
            </div>
          ) : (
            <p className="text-muted-foreground text-sm italic">
              Not categorized yet.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Summary */}
      <Card>
        <CardHeader>
          <CardTitle>Summary</CardTitle>
        </CardHeader>
        <CardContent>
          {story.summary_text ? (
            <div>
              <p className="text-foreground leading-relaxed">
                {story.summary_text}
              </p>
              {story.validation_result && (
                <div className="mt-3">
                  <span className="text-sm text-muted-foreground mr-2">
                    Validation:
                  </span>
                  <Badge
                    variant={
                      story.validation_result === "pass"
                        ? "default"
                        : "destructive"
                    }
                  >
                    {story.validation_result}
                  </Badge>
                </div>
              )}
            </div>
          ) : (
            <p className="text-muted-foreground text-sm italic">
              {story.summary_status || "No summary yet."}
            </p>
          )}
        </CardContent>
      </Card>

      {/* Article text */}
      <Card>
        <CardHeader>
          <CardTitle>Article Text</CardTitle>
        </CardHeader>
        <CardContent>
          {story.article_text ? (
            <details className="group">
              <summary className="cursor-pointer text-sm text-muted-foreground hover:text-foreground transition-colors">
                Show article text (
                {story.article_text.length.toLocaleString()} chars)
              </summary>
              <pre className="mt-3 text-sm text-foreground whitespace-pre-wrap max-h-96 overflow-y-auto rounded-lg bg-muted p-4">
                {story.article_text}
              </pre>
            </details>
          ) : (
            <p className="text-muted-foreground text-sm italic">
              {story.article_fetch_status || "Not fetched yet."}
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
