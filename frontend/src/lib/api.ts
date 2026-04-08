import type {
  DigestSummary,
  DigestDetail,
  StorySummary,
  StoryDetail,
  CategoryCount,
  HealthResponse,
  RunStatus,
  ActionResponse,
  SystemConfig,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${path}`);
  }
  return res.json();
}

async function getOrNull<T>(path: string): Promise<T | null> {
  const res = await fetch(`${API_URL}${path}`);
  if (res.status === 404) return null;
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${path}`);
  }
  return res.json();
}

async function post<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, { method: "POST" });
  if (!res.ok) {
    throw new Error(`API ${res.status}: ${path}`);
  }
  return res.json();
}

export const api = {
  // Digests
  getDigests: (limit = 20) =>
    get<DigestSummary[]>(`/api/digests?limit=${limit}`),
  getLatestDigest: () => getOrNull<DigestDetail>("/api/digests/latest"),
  getDigest: (id: number) => getOrNull<DigestDetail>(`/api/digests/${id}`),

  // Stories
  getStories: (params?: {
    category?: string;
    min_score?: number;
    limit?: number;
  }) => {
    const sp = new URLSearchParams();
    if (params?.category) sp.set("category", params.category);
    if (params?.min_score) sp.set("min_score", String(params.min_score));
    if (params?.limit) sp.set("limit", String(params.limit));
    const qs = sp.toString();
    return get<StorySummary[]>(`/api/stories${qs ? `?${qs}` : ""}`);
  },
  getStory: (id: number) => get<StoryDetail>(`/api/stories/${id}`),

  // System
  getHealth: () => get<HealthResponse>("/api/health"),
  getCategories: () => get<CategoryCount[]>("/api/categories"),
  getConfig: () => get<SystemConfig>("/api/config"),

  // Runs
  getRuns: () => get<RunStatus[]>("/api/runs"),
  getRun: (runId: string) => get<RunStatus>(`/api/runs/${runId}`),

  // Actions (return 202 with run_id)
  collect: (maxStories = 10) =>
    post<ActionResponse>(`/api/collect?max_stories=${maxStories}`),
  score: () => post<ActionResponse>("/api/score"),
  categorize: () => post<ActionResponse>("/api/categorize"),
  orchestrate: () => post<ActionResponse>("/api/orchestrate"),
  fetchStory: (id: number) => post<ActionResponse>(`/api/fetch/${id}`),
  summarizeStory: (id: number) =>
    post<ActionResponse>(`/api/summarize/${id}`),
  runPipeline: (maxStories = 10) =>
    post<ActionResponse>(`/api/pipeline/run?max_stories=${maxStories}`),
  generateDigest: () => post<ActionResponse>("/api/digests/generate"),
};
