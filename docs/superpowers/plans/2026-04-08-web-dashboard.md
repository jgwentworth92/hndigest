# Web Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Next.js dashboard that displays hndigest data from the FastAPI backend via REST + WebSocket.

**Architecture:** Separate Next.js app in `frontend/` consuming the existing FastAPI API on port 8000. Four views: Daily Digest, Story Detail, Live Feed, System. WebSocket provides real-time updates; REST provides initial data loads and reconnect recovery. No new backend logic.

**Tech Stack:** Next.js 14 (App Router), TypeScript, Tailwind CSS, React 18

**Source of truth:** ADR-007 (`docs/adr/ADR-007-web-dashboard.md`), ADR-006 + Amendments (`docs/adr/ADR-006-fastapi-backend.md`)

---

## File Structure

```
frontend/
├── Dockerfile
├── package.json
├── tsconfig.json
├── next.config.ts
├── tailwind.config.ts
├── postcss.config.js
├── .env.local                      # NEXT_PUBLIC_API_URL, NEXT_PUBLIC_WS_URL
├── src/
│   ├── lib/
│   │   ├── types.ts                # TypeScript types matching API response models
│   │   ├── api.ts                  # Typed fetch wrappers for every REST endpoint
│   │   └── events.ts               # WebSocket event type definitions + type guards
│   ├── hooks/
│   │   ├── useWebSocket.ts         # WebSocket connection, reconnect, event dispatch
│   │   └── useApi.ts               # SWR-like hooks wrapping lib/api.ts
│   ├── components/
│   │   ├── Nav.tsx                  # Top nav bar with links to all views
│   │   ├── ConnectionStatus.tsx    # WebSocket connection indicator
│   │   ├── digest/
│   │   │   ├── DigestView.tsx      # Main digest page content (category groups)
│   │   │   ├── StoryCard.tsx       # Single story in digest list
│   │   │   └── DigestPicker.tsx    # Date picker for historical digest browsing
│   │   ├── story/
│   │   │   └── StoryDetailView.tsx # Full pipeline data chain for one story
│   │   ├── feed/
│   │   │   ├── FeedView.tsx        # Live Feed page content
│   │   │   ├── ActionPanel.tsx     # Pipeline trigger buttons
│   │   │   ├── ProgressBar.tsx     # Pipeline progress bar
│   │   │   └── EventLog.tsx        # Scrolling event log
│   │   └── system/
│   │       ├── SystemView.tsx      # System page content
│   │       ├── AgentCard.tsx       # Single agent status card
│   │       └── CategoryTable.tsx   # Category breakdown table
│   └── app/
│       ├── layout.tsx              # Root layout: Nav, WebSocket provider, Tailwind
│       ├── page.tsx                # Daily Digest view (/)
│       ├── stories/
│       │   └── [id]/
│       │       └── page.tsx        # Story Detail view (/stories/:id)
│       ├── feed/
│       │   └── page.tsx            # Live Feed view (/feed)
│       └── system/
│           └── page.tsx            # System view (/system)
```

---

## Task 1: Project scaffold + types

**Files:**
- Create: `frontend/package.json`, `frontend/tsconfig.json`, `frontend/next.config.ts`, `frontend/tailwind.config.ts`, `frontend/postcss.config.js`, `frontend/.env.local`
- Create: `frontend/src/lib/types.ts`
- Create: `frontend/src/app/layout.tsx`, `frontend/src/app/page.tsx`

### Steps

- [ ] **Step 1: Initialize Next.js project**

```bash
cd frontend
npx create-next-app@latest . --typescript --tailwind --eslint --app --src-dir --no-import-alias --use-npm
```

Accept defaults. This creates the scaffold with App Router, TypeScript, and Tailwind.

- [ ] **Step 2: Set environment variables**

Create `frontend/.env.local`:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_WS_URL=ws://localhost:8000
```

- [ ] **Step 3: Write TypeScript types matching API response models**

Create `frontend/src/lib/types.ts`. These types mirror the Pydantic models in `src/hndigest/api/schemas.py` exactly:

```typescript
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
}

// --- Action response (202) ---

export interface ActionResponse {
  run_id: string;
  status: string;
}

// --- Config ---

export type SystemConfig = Record<string, Record<string, unknown>>;
```

- [ ] **Step 4: Create placeholder root layout and home page**

Create `frontend/src/app/layout.tsx`:

```tsx
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "hndigest",
  description: "Hacker News daily digest dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-gray-50 text-gray-900 min-h-screen">
        <main className="max-w-6xl mx-auto px-4 py-8">{children}</main>
      </body>
    </html>
  );
}
```

Create `frontend/src/app/page.tsx`:

```tsx
export default function Home() {
  return <h1 className="text-2xl font-bold">hndigest</h1>;
}
```

- [ ] **Step 5: Verify dev server starts**

```bash
cd frontend && npm run dev
```

Open http://localhost:3000, confirm "hndigest" renders.

- [ ] **Step 6: Commit**

```bash
git add frontend/
git commit -m "Scaffold Next.js frontend with TypeScript types for API models"
```

---

## Task 2: API client + WebSocket hook

**Files:**
- Create: `frontend/src/lib/api.ts`
- Create: `frontend/src/lib/events.ts`
- Create: `frontend/src/hooks/useWebSocket.ts`
- Create: `frontend/src/hooks/useApi.ts`
- Create: `frontend/src/components/ConnectionStatus.tsx`
- Modify: `frontend/src/app/layout.tsx`

### Steps

- [ ] **Step 1: Write typed API client**

Create `frontend/src/lib/api.ts`:

```typescript
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
  getLatestDigest: () => get<DigestDetail>("/api/digests/latest"),
  getDigest: (id: number) => get<DigestDetail>(`/api/digests/${id}`),

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
```

- [ ] **Step 2: Write WebSocket event types**

Create `frontend/src/lib/events.ts`:

```typescript
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
];
```

- [ ] **Step 3: Write WebSocket hook with reconnect**

Create `frontend/src/hooks/useWebSocket.ts`:

```typescript
"use client";

import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  useCallback,
  type ReactNode,
} from "react";
import type { WsEvent, EventType } from "@/lib/events";
import { api } from "@/lib/api";

type Listener = (event: WsEvent) => void;

interface WsContextValue {
  connected: boolean;
  subscribe: (events: EventType[], listener: Listener) => () => void;
}

const WsContext = createContext<WsContextValue>({
  connected: false,
  subscribe: () => () => {},
});

export function useWebSocket() {
  return useContext(WsContext);
}

export function WebSocketProvider({ children }: { children: ReactNode }) {
  const [connected, setConnected] = useState(false);
  const listenersRef = useRef<Map<EventType, Set<Listener>>>(new Map());
  const wsRef = useRef<WebSocket | null>(null);
  const retriesRef = useRef(0);

  const dispatch = useCallback((event: WsEvent) => {
    const listeners = listenersRef.current.get(event.event);
    if (listeners) {
      listeners.forEach((fn) => fn(event));
    }
  }, []);

  const connect = useCallback(() => {
    const wsUrl = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";
    const ws = new WebSocket(`${wsUrl}/api/events`);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      retriesRef.current = 0;
    };

    ws.onmessage = (msg) => {
      try {
        const event: WsEvent = JSON.parse(msg.data);
        if (event.event === "ping") return;
        dispatch(event);
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      setConnected(false);
      wsRef.current = null;
      // Exponential backoff: 1s, 2s, 4s, 8s, ..., max 30s
      const delay = Math.min(1000 * 2 ** retriesRef.current, 30000);
      retriesRef.current += 1;
      setTimeout(connect, delay);
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [dispatch]);

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
  }, [connect]);

  // On reconnect, refetch current state
  useEffect(() => {
    if (!connected || retriesRef.current === 0) return;
    // Fire-and-forget REST refetch for reconnect recovery
    api.getRuns().catch(() => {});
    api.getHealth().catch(() => {});
    api.getLatestDigest().catch(() => {});
  }, [connected]);

  const subscribe = useCallback(
    (events: EventType[], listener: Listener) => {
      for (const event of events) {
        if (!listenersRef.current.has(event)) {
          listenersRef.current.set(event, new Set());
        }
        listenersRef.current.get(event)!.add(listener);
      }
      return () => {
        for (const event of events) {
          listenersRef.current.get(event)?.delete(listener);
        }
      };
    },
    [],
  );

  return (
    <WsContext.Provider value={{ connected, subscribe }}>
      {children}
    </WsContext.Provider>
  );
}
```

- [ ] **Step 4: Write useApi hook**

Create `frontend/src/hooks/useApi.ts`:

```typescript
"use client";

import { useEffect, useState, useCallback } from "react";

interface UseApiResult<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  refetch: () => void;
}

export function useApi<T>(fetcher: () => Promise<T>): UseApiResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refetch = useCallback(() => {
    setLoading(true);
    setError(null);
    fetcher()
      .then(setData)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, [fetcher]);

  useEffect(() => {
    refetch();
  }, [refetch]);

  return { data, error, loading, refetch };
}
```

- [ ] **Step 5: Write ConnectionStatus component**

Create `frontend/src/components/ConnectionStatus.tsx`:

```tsx
"use client";

import { useWebSocket } from "@/hooks/useWebSocket";

export function ConnectionStatus() {
  const { connected } = useWebSocket();
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full ${connected ? "bg-green-500" : "bg-red-500"}`}
      title={connected ? "WebSocket connected" : "WebSocket disconnected"}
    />
  );
}
```

- [ ] **Step 6: Write Nav component**

Create `frontend/src/components/Nav.tsx`:

```tsx
"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ConnectionStatus } from "./ConnectionStatus";

const links = [
  { href: "/", label: "Digest" },
  { href: "/feed", label: "Live Feed" },
  { href: "/system", label: "System" },
];

export function Nav() {
  const pathname = usePathname();
  return (
    <nav className="flex items-center gap-6 border-b border-gray-200 pb-4 mb-8">
      <span className="text-lg font-bold">hndigest</span>
      <ConnectionStatus />
      <div className="flex gap-4 ml-4">
        {links.map(({ href, label }) => (
          <Link
            key={href}
            href={href}
            className={`text-sm ${
              pathname === href
                ? "text-blue-600 font-semibold"
                : "text-gray-600 hover:text-gray-900"
            }`}
          >
            {label}
          </Link>
        ))}
      </div>
    </nav>
  );
}
```

- [ ] **Step 7: Update layout with providers and nav**

Replace `frontend/src/app/layout.tsx`:

```tsx
import type { Metadata } from "next";
import { WebSocketProvider } from "@/hooks/useWebSocket";
import { Nav } from "@/components/Nav";
import "./globals.css";

export const metadata: Metadata = {
  title: "hndigest",
  description: "Hacker News daily digest dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="bg-gray-50 text-gray-900 min-h-screen">
        <WebSocketProvider>
          <div className="max-w-6xl mx-auto px-4 py-8">
            <Nav />
            {children}
          </div>
        </WebSocketProvider>
      </body>
    </html>
  );
}
```

- [ ] **Step 8: Verify dev server with nav**

```bash
cd frontend && npm run dev
```

Confirm nav renders at http://localhost:3000 with Digest, Live Feed, System links and connection indicator (red dot since backend may not be running).

- [ ] **Step 9: Commit**

```bash
git add frontend/
git commit -m "Add API client, WebSocket hook with reconnect, nav, and connection status"
```

---

## Task 3: Daily Digest view

**Files:**
- Create: `frontend/src/components/digest/StoryCard.tsx`
- Create: `frontend/src/components/digest/DigestPicker.tsx`
- Create: `frontend/src/components/digest/DigestView.tsx`
- Modify: `frontend/src/app/page.tsx`

### Steps

- [ ] **Step 1: Write StoryCard component**

Create `frontend/src/components/digest/StoryCard.tsx`:

```tsx
import type { DigestStoryEntry } from "@/lib/types";
import Link from "next/link";

function timeAgo(isoDate: string): string {
  const diff = Date.now() - new Date(isoDate).getTime();
  const hours = Math.floor(diff / 3600000);
  if (hours < 1) return "just now";
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function StoryCard({ story, storyId }: { story: DigestStoryEntry; storyId?: number }) {
  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white">
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
          {story.summary && (
            <p className="text-sm text-gray-600 mt-1">{story.summary}</p>
          )}
          {!story.summary && (
            <p className="text-sm text-gray-400 mt-1 italic">
              No summary available
            </p>
          )}
          <div className="flex flex-wrap items-center gap-3 mt-2 text-xs text-gray-500">
            <span>{story.score} points</span>
            <span>{story.comments} comments</span>
            <span>{timeAgo(story.posted_at)}</span>
            <a
              href={story.hn_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-orange-600 hover:underline"
            >
              HN
            </a>
            {storyId && (
              <Link
                href={`/stories/${storyId}`}
                className="text-blue-500 hover:underline"
              >
                detail
              </Link>
            )}
          </div>
          <div className="flex gap-1 mt-2">
            {story.categories.map((cat) => (
              <span
                key={cat}
                className="text-xs px-2 py-0.5 bg-gray-100 rounded text-gray-700"
              >
                {cat}
              </span>
            ))}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className="text-lg font-bold text-blue-600">
            {Math.round(story.signal_score)}
          </div>
          <div className="text-xs text-gray-400">score</div>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Write DigestPicker component**

Create `frontend/src/components/digest/DigestPicker.tsx`:

```tsx
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
```

- [ ] **Step 3: Write DigestView component**

Create `frontend/src/components/digest/DigestView.tsx`:

```tsx
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
    () =>
      digestId ? api.getDigest(digestId) : api.getLatestDigest(),
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
```

- [ ] **Step 4: Wire up the home page**

Replace `frontend/src/app/page.tsx`:

```tsx
import { DigestView } from "@/components/digest/DigestView";

export default function Home() {
  return <DigestView />;
}
```

- [ ] **Step 5: Test with running backend**

Start the backend (`docker compose up` or venv), then:

```bash
cd frontend && npm run dev
```

Open http://localhost:3000. If digests exist, they should render grouped by category. If no digests exist, "No digests yet." should appear.

- [ ] **Step 6: Commit**

```bash
git add frontend/
git commit -m "Add Daily Digest view with story cards and historical picker"
```

---

## Task 4: Story Detail view

**Files:**
- Create: `frontend/src/components/story/StoryDetailView.tsx`
- Create: `frontend/src/app/stories/[id]/page.tsx`

### Steps

- [ ] **Step 1: Write StoryDetailView component**

Create `frontend/src/components/story/StoryDetailView.tsx`:

```tsx
"use client";

import { useCallback } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import type { StoryDetail } from "@/lib/types";

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-6">
      <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
        {title}
      </h2>
      {children}
    </div>
  );
}

export function StoryDetailView({ storyId }: { storyId: number }) {
  const fetcher = useCallback(() => api.getStory(storyId), [storyId]);
  const { data: story, error, loading } = useApi<StoryDetail>(fetcher);

  if (loading) return <p className="text-gray-500">Loading story...</p>;
  if (error) return <p className="text-red-600">Error: {error}</p>;
  if (!story) return <p className="text-gray-500">Story not found.</p>;

  return (
    <div>
      <h1 className="text-2xl font-bold mb-1">{story.title}</h1>
      <div className="flex gap-3 text-sm text-gray-500 mb-6">
        {story.url && (
          <a
            href={story.url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-600 hover:underline"
          >
            source
          </a>
        )}
        <a
          href={`https://news.ycombinator.com/item?id=${story.id}`}
          target="_blank"
          rel="noopener noreferrer"
          className="text-orange-600 hover:underline"
        >
          HN discussion
        </a>
        <span>{story.score} points</span>
        <span>{story.comments} comments</span>
        <span>{story.hn_type}</span>
      </div>

      <Section title="Score Breakdown">
        {story.composite_score !== null ? (
          <div className="grid grid-cols-2 md:grid-cols-5 gap-4">
            <div className="bg-blue-50 rounded p-3 text-center">
              <div className="text-2xl font-bold text-blue-600">
                {Math.round(story.composite_score)}
              </div>
              <div className="text-xs text-gray-500">composite</div>
            </div>
            {story.score_components && (
              <>
                <div className="bg-gray-50 rounded p-3 text-center">
                  <div className="text-lg font-semibold">
                    {story.score_components.score_velocity.toFixed(1)}
                  </div>
                  <div className="text-xs text-gray-500">score velocity</div>
                </div>
                <div className="bg-gray-50 rounded p-3 text-center">
                  <div className="text-lg font-semibold">
                    {story.score_components.comment_velocity.toFixed(1)}
                  </div>
                  <div className="text-xs text-gray-500">comment velocity</div>
                </div>
                <div className="bg-gray-50 rounded p-3 text-center">
                  <div className="text-lg font-semibold">
                    {story.score_components.front_page_presence}
                  </div>
                  <div className="text-xs text-gray-500">front page</div>
                </div>
                <div className="bg-gray-50 rounded p-3 text-center">
                  <div className="text-lg font-semibold">
                    {story.score_components.recency.toFixed(2)}
                  </div>
                  <div className="text-xs text-gray-500">recency</div>
                </div>
              </>
            )}
          </div>
        ) : (
          <p className="text-gray-400 text-sm italic">Not scored yet</p>
        )}
      </Section>

      <Section title="Categories">
        {story.categories.length > 0 ? (
          <div className="flex gap-2">
            {story.categories.map((cat) => (
              <span
                key={cat}
                className="px-2 py-1 bg-gray-100 rounded text-sm"
              >
                {cat}
              </span>
            ))}
          </div>
        ) : (
          <p className="text-gray-400 text-sm italic">Not categorized yet</p>
        )}
      </Section>

      <Section title="Summary">
        {story.summary_text ? (
          <div>
            <p className="text-gray-800">{story.summary_text}</p>
            <div className="flex gap-3 mt-2 text-xs text-gray-500">
              <span>
                Status:{" "}
                <span className="font-medium">{story.summary_status}</span>
              </span>
              {story.validation_result && (
                <span>
                  Validation:{" "}
                  <span
                    className={`font-medium ${story.validation_result === "pass" ? "text-green-600" : "text-red-600"}`}
                  >
                    {story.validation_result}
                  </span>
                </span>
              )}
            </div>
          </div>
        ) : (
          <p className="text-gray-400 text-sm italic">No summary available</p>
        )}
      </Section>

      <Section title="Article Text">
        {story.article_text ? (
          <details>
            <summary className="cursor-pointer text-sm text-blue-600 hover:underline">
              Show article text ({story.article_text.length.toLocaleString()}{" "}
              chars)
            </summary>
            <pre className="mt-2 text-sm text-gray-700 whitespace-pre-wrap bg-gray-50 p-4 rounded max-h-96 overflow-y-auto">
              {story.article_text}
            </pre>
          </details>
        ) : (
          <p className="text-gray-400 text-sm italic">
            {story.article_fetch_status === "failed"
              ? "Fetch failed"
              : story.article_fetch_status === "no_url"
                ? "No URL (self-post)"
                : "Not fetched yet"}
          </p>
        )}
      </Section>
    </div>
  );
}
```

- [ ] **Step 2: Write page route**

Create `frontend/src/app/stories/[id]/page.tsx`:

```tsx
import { StoryDetailView } from "@/components/story/StoryDetailView";

export default async function StoryPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <StoryDetailView storyId={Number(id)} />;
}
```

- [ ] **Step 3: Verify with running backend**

Navigate to http://localhost:3000/stories/12345 (use a real story ID from your DB). Verify all sections render.

- [ ] **Step 4: Commit**

```bash
git add frontend/
git commit -m "Add Story Detail view with full pipeline data chain"
```

---

## Task 5: Live Feed view

**Files:**
- Create: `frontend/src/components/feed/ActionPanel.tsx`
- Create: `frontend/src/components/feed/ProgressBar.tsx`
- Create: `frontend/src/components/feed/EventLog.tsx`
- Create: `frontend/src/components/feed/FeedView.tsx`
- Create: `frontend/src/app/feed/page.tsx`

### Steps

- [ ] **Step 1: Write ActionPanel component**

Create `frontend/src/components/feed/ActionPanel.tsx`:

```tsx
"use client";

import { useState } from "react";
import { api } from "@/lib/api";

interface ActionPanelProps {
  onRunStarted: (runId: string, type: string) => void;
}

export function ActionPanel({ onRunStarted }: ActionPanelProps) {
  const [busy, setBusy] = useState<string | null>(null);

  async function trigger(
    label: string,
    action: () => Promise<{ run_id: string }>,
  ) {
    setBusy(label);
    try {
      const res = await action();
      onRunStarted(res.run_id, label);
    } catch (e) {
      console.error(`Action ${label} failed:`, e);
    } finally {
      setBusy(null);
    }
  }

  const actions = [
    { label: "Run Pipeline", fn: () => api.runPipeline() },
    { label: "Collect", fn: () => api.collect() },
    { label: "Score", fn: () => api.score() },
    { label: "Categorize", fn: () => api.categorize() },
    { label: "Generate Digest", fn: () => api.generateDigest() },
  ];

  return (
    <div className="flex flex-wrap gap-2">
      {actions.map(({ label, fn }) => (
        <button
          key={label}
          onClick={() => trigger(label, fn)}
          disabled={busy !== null}
          className="px-3 py-1.5 text-sm bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
        >
          {busy === label ? `${label}...` : label}
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Write ProgressBar component**

Create `frontend/src/components/feed/ProgressBar.tsx`:

```tsx
import type { PipelineProgress } from "@/lib/types";

export function ProgressBar({
  progress,
  status,
}: {
  progress: PipelineProgress | null;
  status: string;
}) {
  if (!progress || progress.total_stories === 0) {
    return (
      <div className="text-sm text-gray-500">
        {status === "running" ? "Starting..." : status}
      </div>
    );
  }

  const stages = [
    { label: "Collected", value: progress.collected },
    { label: "Scored", value: progress.scored },
    { label: "Categorized", value: progress.categorized },
    { label: "Fetched", value: progress.fetched },
    { label: "Summarized", value: progress.summarized },
    { label: "Validated", value: progress.validated },
  ];

  // Overall progress: average of all stages relative to total
  const total = progress.total_stories;
  const sum = stages.reduce((acc, s) => acc + s.value, 0);
  const pct = Math.round((sum / (total * stages.length)) * 100);

  return (
    <div>
      <div className="w-full bg-gray-200 rounded-full h-3 mb-2">
        <div
          className="bg-blue-600 h-3 rounded-full transition-all duration-300"
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="flex flex-wrap gap-3 text-xs text-gray-600">
        {stages.map((s) => (
          <span key={s.label}>
            {s.label}: {s.value}/{total}
          </span>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Write EventLog component**

Create `frontend/src/components/feed/EventLog.tsx`:

```tsx
import type { WsEvent } from "@/lib/events";

const MAX_EVENTS = 200;

export function EventLog({ events }: { events: WsEvent[] }) {
  const display = events.slice(-MAX_EVENTS);

  return (
    <div className="bg-gray-900 text-gray-100 rounded-lg p-4 font-mono text-xs max-h-96 overflow-y-auto">
      {display.length === 0 && (
        <p className="text-gray-500">Waiting for events...</p>
      )}
      {display.map((evt, idx) => {
        const time = evt.timestamp
          ? new Date(evt.timestamp).toLocaleTimeString()
          : "";
        const storyId = evt.data?.story_id ?? evt.data?.run_id ?? "";
        return (
          <div key={idx} className="py-0.5">
            <span className="text-gray-500">{time}</span>{" "}
            <span className="text-green-400">{evt.event}</span>{" "}
            {storyId && <span className="text-yellow-300">{String(storyId)}</span>}
          </div>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 4: Write FeedView component**

Create `frontend/src/components/feed/FeedView.tsx`:

```tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useApi } from "@/hooks/useApi";
import { api } from "@/lib/api";
import { STORY_EVENTS, PIPELINE_EVENTS } from "@/lib/events";
import type { WsEvent } from "@/lib/events";
import type { PipelineProgress, RunStatus } from "@/lib/types";
import { ActionPanel } from "./ActionPanel";
import { ProgressBar } from "./ProgressBar";
import { EventLog } from "./EventLog";

export function FeedView() {
  const { subscribe } = useWebSocket();
  const [events, setEvents] = useState<WsEvent[]>([]);
  const [activeRun, setActiveRun] = useState<{
    runId: string;
    type: string;
    status: string;
    progress: PipelineProgress | null;
  } | null>(null);

  // Fetch active runs on mount for reconnect recovery
  const runsFetcher = useCallback(() => api.getRuns(), []);
  const { data: runs } = useApi<RunStatus[]>(runsFetcher);

  useEffect(() => {
    if (runs && runs.length > 0) {
      const running = runs.find((r) => r.status === "running");
      if (running) {
        setActiveRun({
          runId: running.run_id,
          type: running.type,
          status: running.status,
          progress: running.progress,
        });
      }
    }
  }, [runs]);

  // Subscribe to story events for the event log
  useEffect(() => {
    return subscribe([...STORY_EVENTS, "digest_ready"], (evt) => {
      setEvents((prev) => [...prev, evt]);
    });
  }, [subscribe]);

  // Subscribe to pipeline events for progress
  useEffect(() => {
    return subscribe(PIPELINE_EVENTS, (evt) => {
      setEvents((prev) => [...prev, evt]);
      if (evt.event === "pipeline_started") {
        setActiveRun({
          runId: (evt.run_id ?? evt.data?.run_id as string) || "",
          type: "pipeline",
          status: "running",
          progress: null,
        });
      } else if (evt.event === "pipeline_progress" && evt.data) {
        setActiveRun((prev) =>
          prev
            ? {
                ...prev,
                progress: {
                  collected: (evt.data?.collected as number) ?? 0,
                  scored: (evt.data?.scored as number) ?? 0,
                  categorized: (evt.data?.categorized as number) ?? 0,
                  fetched: (evt.data?.fetched as number) ?? 0,
                  summarized: (evt.data?.summarized as number) ?? 0,
                  validated: (evt.data?.validated as number) ?? 0,
                  total_stories: (evt.data?.total_stories as number) ?? 0,
                },
              }
            : prev,
        );
      } else if (evt.event === "pipeline_completed") {
        setActiveRun((prev) => (prev ? { ...prev, status: "completed" } : prev));
      }
    });
  }, [subscribe]);

  function handleRunStarted(runId: string, type: string) {
    setActiveRun({ runId, type, status: "running", progress: null });
  }

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">Live Feed</h1>

      <div className="mb-6">
        <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
          Actions
        </h2>
        <ActionPanel onRunStarted={handleRunStarted} />
      </div>

      {activeRun && (
        <div className="mb-6 p-4 bg-white border border-gray-200 rounded-lg">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-sm font-medium">{activeRun.type}</span>
            <span className="text-xs text-gray-500">{activeRun.runId}</span>
            <span
              className={`text-xs px-2 py-0.5 rounded ${
                activeRun.status === "running"
                  ? "bg-blue-100 text-blue-700"
                  : activeRun.status === "completed"
                    ? "bg-green-100 text-green-700"
                    : "bg-red-100 text-red-700"
              }`}
            >
              {activeRun.status}
            </span>
          </div>
          <ProgressBar progress={activeRun.progress} status={activeRun.status} />
        </div>
      )}

      <div>
        <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-2">
          Event Log
        </h2>
        <EventLog events={events} />
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Write page route**

Create `frontend/src/app/feed/page.tsx`:

```tsx
import { FeedView } from "@/components/feed/FeedView";

export default function FeedPage() {
  return <FeedView />;
}
```

- [ ] **Step 6: Test with running backend**

Start backend, open http://localhost:3000/feed. Click "Run Pipeline". Verify progress bar updates and events appear in the log.

- [ ] **Step 7: Commit**

```bash
git add frontend/
git commit -m "Add Live Feed view with action triggers, progress bar, and event log"
```

---

## Task 6: System view

**Files:**
- Create: `frontend/src/components/system/AgentCard.tsx`
- Create: `frontend/src/components/system/CategoryTable.tsx`
- Create: `frontend/src/components/system/SystemView.tsx`
- Create: `frontend/src/app/system/page.tsx`

### Steps

- [ ] **Step 1: Write AgentCard component**

Create `frontend/src/components/system/AgentCard.tsx`:

```tsx
export function AgentCard({
  name,
  status,
  lastHeartbeat,
  messagesProcessed,
}: {
  name: string;
  status: string;
  lastHeartbeat: string | null;
  messagesProcessed: number;
}) {
  const statusColor =
    status === "running"
      ? "bg-green-100 text-green-700"
      : status === "stopped"
        ? "bg-gray-100 text-gray-600"
        : "bg-red-100 text-red-700";

  return (
    <div className="border border-gray-200 rounded-lg p-4 bg-white">
      <div className="flex items-center justify-between mb-2">
        <span className="font-medium">{name}</span>
        <span className={`text-xs px-2 py-0.5 rounded ${statusColor}`}>
          {status}
        </span>
      </div>
      <div className="text-xs text-gray-500 space-y-1">
        <div>Messages: {messagesProcessed.toLocaleString()}</div>
        {lastHeartbeat && (
          <div>
            Last heartbeat: {new Date(lastHeartbeat).toLocaleTimeString()}
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Write CategoryTable component**

Create `frontend/src/components/system/CategoryTable.tsx`:

```tsx
import type { CategoryCount } from "@/lib/types";

export function CategoryTable({
  categories,
}: {
  categories: CategoryCount[];
}) {
  if (categories.length === 0) {
    return <p className="text-gray-400 text-sm italic">No categories yet</p>;
  }

  const total = categories.reduce((sum, c) => sum + c.count, 0);

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="text-left text-gray-500 border-b">
          <th className="py-2 font-medium">Category</th>
          <th className="py-2 font-medium text-right">Count</th>
          <th className="py-2 font-medium text-right">%</th>
        </tr>
      </thead>
      <tbody>
        {categories.map((c) => (
          <tr key={c.category} className="border-b border-gray-100">
            <td className="py-2">{c.category.replace(/_/g, " ")}</td>
            <td className="py-2 text-right">{c.count}</td>
            <td className="py-2 text-right text-gray-500">
              {total > 0 ? Math.round((c.count / total) * 100) : 0}%
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
```

- [ ] **Step 3: Write SystemView component**

Create `frontend/src/components/system/SystemView.tsx`:

```tsx
"use client";

import { useState, useEffect, useCallback } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/hooks/useApi";
import { useWebSocket } from "@/hooks/useWebSocket";
import { AgentCard } from "./AgentCard";
import { CategoryTable } from "./CategoryTable";
import type {
  HealthResponse,
  CategoryCount,
  SystemConfig,
} from "@/lib/types";

interface AgentState {
  name: string;
  status: string;
  lastHeartbeat: string | null;
  messagesProcessed: number;
}

export function SystemView() {
  const healthFetcher = useCallback(() => api.getHealth(), []);
  const categoriesFetcher = useCallback(() => api.getCategories(), []);
  const configFetcher = useCallback(() => api.getConfig(), []);

  const { data: health } = useApi<HealthResponse>(healthFetcher);
  const { data: categories } = useApi<CategoryCount[]>(categoriesFetcher);
  const { data: config } = useApi<SystemConfig>(configFetcher);
  const { subscribe } = useWebSocket();

  const [agents, setAgents] = useState<Record<string, AgentState>>({});

  // Live-update agents from heartbeat events
  useEffect(() => {
    return subscribe(["agent_heartbeat"], (evt) => {
      const data = evt.data;
      if (!data?.agent) return;
      const name = data.agent as string;
      setAgents((prev) => ({
        ...prev,
        [name]: {
          name,
          status: (data.status as string) ?? "unknown",
          lastHeartbeat: evt.timestamp ?? null,
          messagesProcessed: (data.messages_processed as number) ?? 0,
        },
      }));
    });
  }, [subscribe]);

  const agentList = Object.values(agents);

  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">System</h1>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div>
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
            Health
          </h2>
          {health && (
            <div className="bg-white border border-gray-200 rounded-lg p-4 space-y-2 text-sm">
              <div>
                Status:{" "}
                <span
                  className={`font-medium ${health.status === "healthy" ? "text-green-600" : "text-yellow-600"}`}
                >
                  {health.status}
                </span>
              </div>
              <div>Uptime: {Math.round(health.uptime_seconds)}s</div>
            </div>
          )}
        </div>

        <div>
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
            Categories
          </h2>
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <CategoryTable categories={categories ?? []} />
          </div>
        </div>
      </div>

      <div className="mt-6">
        <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
          Agents
        </h2>
        {agentList.length > 0 ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
            {agentList.map((agent) => (
              <AgentCard key={agent.name} {...agent} />
            ))}
          </div>
        ) : (
          <p className="text-gray-400 text-sm italic">
            No agent heartbeats received yet. Agents emit heartbeats in CLI
            mode.
          </p>
        )}
      </div>

      {config && (
        <div className="mt-6">
          <h2 className="text-sm font-semibold text-gray-500 uppercase tracking-wide mb-3">
            Configuration
          </h2>
          <div className="bg-white border border-gray-200 rounded-lg p-4">
            <pre className="text-xs text-gray-700 overflow-x-auto">
              {JSON.stringify(config, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Write page route**

Create `frontend/src/app/system/page.tsx`:

```tsx
import { SystemView } from "@/components/system/SystemView";

export default function SystemPage() {
  return <SystemView />;
}
```

- [ ] **Step 5: Test with running backend**

Open http://localhost:3000/system. Verify health, categories, and config sections render.

- [ ] **Step 6: Commit**

```bash
git add frontend/
git commit -m "Add System view with health, categories, agents, and config"
```

---

## Task 7: Docker integration

**Files:**
- Create: `frontend/Dockerfile`
- Modify: `docker-compose.yaml` (project root)
- Create: `frontend/.dockerignore`

### Steps

- [ ] **Step 1: Create frontend Dockerfile**

Create `frontend/Dockerfile`:

```dockerfile
FROM node:20-slim AS builder

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM node:20-slim AS runner
WORKDIR /app
ENV NODE_ENV=production

COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

EXPOSE 3000
CMD ["node", "server.js"]
```

- [ ] **Step 2: Update next.config.ts for standalone output**

Ensure `frontend/next.config.ts` includes:

```typescript
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
};

export default nextConfig;
```

- [ ] **Step 3: Create frontend .dockerignore**

Create `frontend/.dockerignore`:

```
node_modules
.next
.env.local
```

- [ ] **Step 4: Update docker-compose.yaml**

Replace the project root `docker-compose.yaml`:

```yaml
version: "3.8"

services:
  backend:
    build: .
    ports:
      - "8000:8000"
    env_file:
      - .env
    volumes:
      - ./data:/app/data
      - ./config:/app/config
      - ./output:/app/output
    environment:
      - HNDIGEST_DB_PATH=/app/data/hndigest.db
      - CORS_ORIGINS=http://localhost:3000
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"]
      interval: 30s
      timeout: 5s
      retries: 3

  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    ports:
      - "3000:3000"
    environment:
      - NEXT_PUBLIC_API_URL=http://backend:8000
      - NEXT_PUBLIC_WS_URL=ws://backend:8000
    depends_on:
      backend:
        condition: service_healthy
    restart: unless-stopped
```

- [ ] **Step 5: Test Docker Compose**

```bash
docker compose build
docker compose up
```

Open http://localhost:3000. Verify dashboard loads and communicates with backend on port 8000.

- [ ] **Step 6: Commit**

```bash
git add frontend/Dockerfile frontend/.dockerignore docker-compose.yaml frontend/next.config.ts
git commit -m "Add frontend Docker container and update docker-compose for full stack"
```

---

## Task 8: End-to-end verification

This is a manual verification task, not automated tests. The CLAUDE.md testing rules apply to Python backend tests (no mocking, no skipping). The frontend is verified against the running full stack.

### Steps

- [ ] **Step 1: Full stack smoke test**

```bash
docker compose up --build
```

Verify:
1. http://localhost:3000 — Digest view loads (shows digest or "No digests yet")
2. http://localhost:3000/feed — Live Feed view loads, action buttons visible
3. http://localhost:3000/system — System view shows health and config
4. WebSocket indicator shows green (connected)

- [ ] **Step 2: Pipeline run test**

On the Live Feed page:
1. Click "Run Pipeline"
2. Verify progress bar appears and updates
3. Verify event log shows story_collected, story_scored, etc.
4. Verify pipeline_completed event appears
5. Navigate to Digest view — new digest should appear

- [ ] **Step 3: Story detail test**

From the Digest view, click a "detail" link on any story. Verify:
1. Score breakdown displays with all components
2. Categories display
3. Summary (if available) with validation status
4. Article text (collapsible) if fetched

- [ ] **Step 4: WebSocket reconnect test**

1. On Live Feed page, note the green connection indicator
2. Stop the backend container: `docker compose stop backend`
3. Verify indicator turns red
4. Restart backend: `docker compose start backend`
5. Verify indicator turns green (reconnect with backoff)

- [ ] **Step 5: Commit final state**

```bash
git add -A
git commit -m "Verify end-to-end full stack dashboard"
```
