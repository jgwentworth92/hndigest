"use client";

import { useCallback } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
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
        <div
          key={key}
          className="border border-gray-200 rounded-lg p-3 bg-white text-center"
        >
          <div className="text-lg font-bold text-blue-600">
            {components[key].toFixed(2)}
          </div>
          <div className="text-xs text-gray-500 mt-1">{label}</div>
        </div>
      ))}
    </div>
  );
}

export function StoryDetailView({ storyId }: { storyId: number }) {
  const fetcher = useCallback(() => api.getStory(storyId), [storyId]);
  const { data: story, error, loading } = useApi<StoryDetail>(fetcher);

  if (loading) return <p className="text-gray-500">Loading story...</p>;
  if (error) return <p className="text-red-600">Error: {error}</p>;
  if (!story) return <p className="text-gray-500">Story not found.</p>;

  const hnUrl = `https://news.ycombinator.com/item?id=${story.id}`;

  return (
    <div className="space-y-6">
      {/* Title and links */}
      <div>
        <h1 className="text-2xl font-bold">{story.title}</h1>
        <div className="flex flex-wrap items-center gap-4 mt-2 text-sm text-gray-500">
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
          <span className="px-2 py-0.5 bg-gray-100 rounded text-xs text-gray-700">
            {story.hn_type}
          </span>
        </div>
      </div>

      {/* Score breakdown */}
      <div>
        <h2 className="text-lg font-semibold mb-3">Score Breakdown</h2>
        <div className="mb-3">
          <span className="text-2xl font-bold text-blue-600">
            {story.composite_score !== null
              ? story.composite_score.toFixed(2)
              : "N/A"}
          </span>
          <span className="text-sm text-gray-500 ml-2">composite</span>
        </div>
        {story.score_components ? (
          <ScoreGrid components={story.score_components} />
        ) : (
          <p className="text-gray-400 text-sm italic">Not scored yet.</p>
        )}
      </div>

      {/* Categories */}
      <div>
        <h2 className="text-lg font-semibold mb-3">Categories</h2>
        {story.categories.length > 0 ? (
          <div className="flex flex-wrap gap-2">
            {story.categories.map((cat) => (
              <span
                key={cat}
                className="px-3 py-1 bg-blue-100 text-blue-800 rounded-full text-sm"
              >
                {cat}
              </span>
            ))}
          </div>
        ) : (
          <p className="text-gray-400 text-sm italic">Not categorized yet.</p>
        )}
      </div>

      {/* Summary */}
      <div>
        <h2 className="text-lg font-semibold mb-3">Summary</h2>
        {story.summary_text ? (
          <div>
            <p className="text-gray-700 leading-relaxed">{story.summary_text}</p>
            {story.validation_result && (
              <p className="mt-2 text-sm">
                Validation:{" "}
                <span
                  className={
                    story.validation_result === "pass"
                      ? "text-green-600 font-medium"
                      : "text-red-600 font-medium"
                  }
                >
                  {story.validation_result}
                </span>
              </p>
            )}
          </div>
        ) : (
          <p className="text-gray-400 text-sm italic">
            {story.summary_status || "No summary yet."}
          </p>
        )}
      </div>

      {/* Article text */}
      <div>
        <h2 className="text-lg font-semibold mb-3">Article Text</h2>
        {story.article_text ? (
          <details className="border border-gray-200 rounded-lg bg-white">
            <summary className="cursor-pointer px-4 py-3 text-sm text-gray-600 hover:bg-gray-50">
              Show article text ({story.article_text.length.toLocaleString()}{" "}
              chars)
            </summary>
            <pre className="px-4 py-3 text-sm text-gray-700 whitespace-pre-wrap border-t border-gray-200 max-h-96 overflow-y-auto">
              {story.article_text}
            </pre>
          </details>
        ) : (
          <p className="text-gray-400 text-sm italic">
            {story.article_fetch_status || "Not fetched yet."}
          </p>
        )}
      </div>
    </div>
  );
}
