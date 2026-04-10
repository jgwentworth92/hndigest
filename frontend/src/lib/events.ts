// All WebSocket event types from ADR-006 Amendments 1 + 2

export type EventType =
  // Data channel events
  | "story_collected"
  | "story_scored"
  | "story_categorized"
  | "story_dispatched"
  | "article_fetched"
  | "summarize_requested"
  | "summary_generated"
  | "summary_validated"
  | "digest_ready"
  // Pipeline lifecycle events
  | "pipeline_started"
  | "pipeline_progress"
  | "pipeline_completed"
  | "pipeline_failed"
  // System events
  | "agent_heartbeat"
  // Keepalive
  | "ping";

export interface WsEvent {
  event: EventType;
  timestamp?: string;
  source?: string;
  run_id?: string;
  data?: Record<string, unknown>;
}

// Story-level pipeline events for the Live Feed
export const STORY_EVENTS: EventType[] = [
  "story_collected",
  "story_scored",
  "story_categorized",
  "story_dispatched",
  "article_fetched",
  "summarize_requested",
  "summary_generated",
  "summary_validated",
];

// Pipeline lifecycle events for the progress bar
export const PIPELINE_EVENTS: EventType[] = [
  "pipeline_started",
  "pipeline_progress",
  "pipeline_completed",
  "pipeline_failed",
];
