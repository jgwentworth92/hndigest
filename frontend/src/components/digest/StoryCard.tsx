import type { DigestStoryEntry } from "@/lib/types";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";

function timeAgo(isoDate: string): string {
  const diff = Date.now() - new Date(isoDate).getTime();
  const hours = Math.floor(diff / 3600000);
  if (hours < 1) return "just now";
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function StoryCard({ story }: { story: DigestStoryEntry }) {
  return (
    <Card size="sm">
      <CardContent>
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1 min-w-0">
            <h3 className="font-medium text-base">
              {story.url ? (
                <a
                  href={story.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-blue-700 hover:underline"
                >
                  {story.title}
                </a>
              ) : (
                story.title
              )}
            </h3>
            {story.summary ? (
              <p className="text-sm text-muted-foreground mt-1">
                {story.summary}
              </p>
            ) : (
              <p className="text-sm text-muted-foreground/60 mt-1 italic">
                No summary available
              </p>
            )}
            <div className="flex flex-wrap items-center gap-2 mt-3">
              {story.categories.map((cat) => (
                <Badge key={cat} variant="secondary">
                  {cat}
                </Badge>
              ))}
            </div>
            <div className="flex flex-wrap items-center gap-3 mt-2 text-xs text-muted-foreground">
              <span>{story.score} points</span>
              <span>{story.comments} comments</span>
              <span>{timeAgo(story.posted_at)}</span>
              <a
                href={story.hn_url}
                target="_blank"
                rel="noopener noreferrer"
                className="text-orange-600 hover:underline font-medium"
              >
                HN
              </a>
            </div>
          </div>
          <div className="text-right shrink-0">
            <div className="text-2xl font-bold text-blue-600">
              {Math.round(story.signal_score)}
            </div>
            <div className="text-xs text-muted-foreground">score</div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
