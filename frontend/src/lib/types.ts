// --- Digest ---

export interface DigestSummary {
  id: number;
  period_start: string;
  period_end: string;
  story_count: number;
  created_at: string;
}

export interface DigestDetail extends DigestSummary {
  content_json: DigestContent;
  content_md: string;
}

// content_json is a dict of category -> story entries
export type DigestContent = Record<string, DigestStoryEntry[]>;

export interface DigestStoryEntry {
  title: string;
  url: string | null;
  hn_url: string;
  signal_score: number;
  categories: string[];
  summary: string | null;
  score: number;
  comments: number;
  posted_at: string;
}

// --- Story ---

export interface StorySummary {
  id: number;
  title: string;
  url: string | null;
  score: number;
  comments: number;
  hn_type: string;
  posted_at: string;
  composite_score: number | null;
  categories: string[];
}

export interface StoryDetail extends StorySummary {
  article_text: string | null;
  article_fetch_status: string | null;
  summary_text: string | null;
  summary_status: string | null;
  validation_result: string | null;
  score_components: ScoreComponents | null;
}

export interface ScoreComponents {
  score_velocity: number;
  comment_velocity: number;
  front_page_presence: number;
  recency: number;
}

// --- Category ---

export interface CategoryCount {
  category: string;
  count: number;
}

// --- Health ---

export interface HealthResponse {
  status: string;
  uptime_seconds: number;
  agents: Record<string, AgentStatus>;
  mode?: string;
  database?: string;
}

export interface AgentStatus {
  name: string;
  status: string;
  last_heartbeat_ago: number;
  messages_processed: number;
  restart_count: number;
}

// --- Runs ---

export interface PipelineProgress {
  collected: number;
  scored: number;
  categorized: number;
  fetched: number;
  summarized: number;
  validated: number;
  total_stories: number;
}

export interface RunStatus {
  run_id: string;
  type: string;
  status: string;
  started_at: string;
  progress: PipelineProgress | null;
  error: string | null;
}

// --- Action response (202) ---

export interface ActionResponse {
  run_id: string;
  status: string;
}

// --- Config ---

export type SystemConfig = Record<string, Record<string, unknown>>;
