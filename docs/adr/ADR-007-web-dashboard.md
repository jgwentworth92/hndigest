# ADR-007: Web Dashboard — Read-Only Frontend for hndigest

## Status: ACCEPTED
## Date: 2026-04-07

---

## Problem

The hndigest system has a full REST API and WebSocket event stream (ADR-006) but no visual interface. Users interact through CLI commands or raw API calls. A web dashboard would make the digest output, pipeline status, and story data accessible at a glance.

The dashboard is a **read-only display layer with action triggers**. It consumes existing API endpoints and WebSocket events. It does not add conversational AI, chat, or any new backend logic. All data comes from the FastAPI backend defined in ADR-006.

---

## Options Considered

### Framework

| Option | Pros | Cons |
|---|---|---|
| Next.js (React) | SSR, file-based routing, large ecosystem, spec already names it | Heavy for a dashboard, Node.js dependency |
| Vite + React SPA | Lightweight, fast builds, no SSR complexity | No SSR (acceptable for a dashboard) |
| FastAPI serves static HTML + HTMX | No JS build step, same server | Limited interactivity, no component model |
| Svelte / SvelteKit | Lightweight, reactive, good DX | Smaller ecosystem than React |

**Decision:** Next.js. The master spec (Section 9) specifies it. SSR is useful for the digest view (shareable URLs with content). The ecosystem has mature WebSocket and data-fetching libraries.

### Hosting model

| Option | Pros | Cons |
|---|---|---|
| Separate container (Next.js) + FastAPI container | Independent deploys, standard separation | Two containers, CORS required |
| Next.js API routes proxy to FastAPI | Single frontend URL, no CORS | Extra hop, hides the real API |
| FastAPI serves Next.js static export | Single container | Loses SSR, build coupling |

**Decision:** Separate container. Next.js runs on port 3000, FastAPI on port 8000. CORS is already configured in ADR-006. Docker Compose orchestrates both services. This keeps the frontend and backend independently deployable and testable.

---

## Dashboard Views

Four views, derived from SPEC-000 Section 9 (minus chat):

### 1. Daily Digest (main view, route: `/`)

The primary interface. Displays the most recent digest grouped by category, ranked by composite score within each category.

**Data sources:**
- Initial load: `GET /api/digests/latest`
- Historical browsing: `GET /api/digests` (list) + `GET /api/digests/{id}` (detail)
- Live update: WebSocket `digest_ready` event triggers refetch of latest digest

**Display per story:**
- Title (linked to source URL)
- HN discussion link
- Composite signal score with component breakdown on hover (score velocity, comment velocity, front page presence, recency)
- Category tags
- Summary (or "No summary available" if fetch failed or validation rejected)
- Metadata line: X points | Y comments | posted Z hours ago

**Features:**
- Date picker for historical digest browsing
- Category filter (show/hide categories)
- Score threshold slider (hide stories below N)
- Expand/collapse categories

### 2. Story Detail (route: `/stories/{id}`)

Full data chain for a single story. Shows every stage of the pipeline's output for one item.

**Data source:** `GET /api/stories/{id}`

**Display sections:**
- HN metadata: title, author, score, comments, posted time, HN type, endpoints
- Article text: extracted content (collapsible, long)
- Summary: generated summary text, validation status (pass/fail), validation details (per-claim citations)
- Score breakdown: composite score + each component with values and weights
- Category: assigned categories with method (domain, keyword, hn_type)
- Orchestrator decision: dispatched/skipped/budget_exceeded, reason, priority score at decision time

### 3. Live Feed (route: `/feed`)

Real-time view of pipeline activity. Purely WebSocket-driven.

**Data sources:**
- Primary: WebSocket events from `/api/events`
- Reconnect recovery: `GET /api/runs` to discover active runs, then REST endpoints for current state
- Action triggers: `POST /api/pipeline/run`, `POST /api/collect`, etc.

**Display:**
- Pipeline control panel: buttons for "Run Full Pipeline", "Collect Stories", "Score", "Categorize", "Generate Digest"
- When an action is triggered: show run ID, status, and progress bar driven by `pipeline_progress` WebSocket events
- Event log: scrolling list of real-time events (story collected, scored, fetched, summarized, etc.) with timestamps
- Per-story status indicators: collected → scored → categorized → fetched → summarized → validated (pipeline stage badges)

**Action trigger pattern:**
1. Frontend ensures WebSocket is connected
2. Frontend calls POST endpoint (e.g., `/api/pipeline/run`)
3. Receives 202 with `run_id`
4. Tracks progress via `pipeline_progress` events and individual story events
5. Shows completion on `pipeline_completed` event

### 4. System (route: `/system`)

Agent status, category overview, and configuration.

**Data sources:**
- Agents: WebSocket `agent_heartbeat` events (live), `GET /api/agents` (initial load and fallback)
- Categories: `GET /api/categories`
- Config: `GET /api/config`
- Health: `GET /api/health`

**Display:**
- Agent cards: name, status (running/stopped/error), last heartbeat time, messages processed. Live-updated from WebSocket heartbeat events.
- Category breakdown: bar chart or table showing story count per category for the current period
- System config: read-only display of current scoring weights, orchestrator thresholds, poll interval
- Health: uptime, database status

---

## WebSocket Strategy

### Connection lifecycle

1. App establishes WebSocket connection to `/api/events` on mount
2. Connection is maintained as long as the app is open
3. All views share a single WebSocket connection via a React context provider

### Reconnection

On disconnect:
1. Attempt reconnect with exponential backoff (1s, 2s, 4s, 8s, max 30s)
2. On successful reconnect, refetch current state from REST:
   - `GET /api/runs` — discover any in-flight pipeline runs
   - `GET /api/agents` — current agent statuses
   - `GET /api/digests/latest` — in case a digest completed while disconnected
3. Resume processing WebSocket events normally

No event replay. REST refetch provides sufficient recovery for a dashboard. Per ADR-006 Amendment 2.

### Event routing

A central WebSocket provider receives all events and dispatches to subscribers:

| Event type | Routed to |
|---|---|
| `story_collected`, `story_scored`, `story_categorized`, `story_dispatched`, `article_fetched`, `summary_generated`, `summary_validated`, `story_skipped` | Live Feed view |
| `pipeline_started`, `pipeline_progress`, `pipeline_completed` | Live Feed progress bar |
| `digest_ready` | Daily Digest view (triggers refetch) |
| `agent_heartbeat` | System view agent cards |

---

## Docker Integration

Added to existing `docker-compose.yaml` from ADR-006:

```
services:
  backend:
    # ... existing FastAPI service from ADR-006 ...

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
```

The frontend container builds the Next.js app and serves it. It depends on the backend being healthy (via the `/api/health` check already defined in ADR-006).

For local development: `npm run dev` in `frontend/` with environment variables pointing to `localhost:8000`.

---

## File Structure

```
frontend/
├── Dockerfile
├── package.json
├── next.config.js
├── public/
├── src/
│   ├── app/
│   │   ├── layout.tsx          # Root layout, WebSocket provider
│   │   ├── page.tsx            # Daily Digest view (/)
│   │   ├── stories/
│   │   │   └── [id]/
│   │   │       └── page.tsx    # Story Detail view
│   │   ├── feed/
│   │   │   └── page.tsx        # Live Feed view
│   │   └── system/
│   │       └── page.tsx        # System view
│   ├── components/
│   │   ├── digest/             # Digest display components
│   │   ├── story/              # Story card, detail components
│   │   ├── feed/               # Event log, progress bar, action buttons
│   │   └── system/             # Agent cards, category chart
│   ├── hooks/
│   │   ├── useWebSocket.ts     # WebSocket connection + reconnect logic
│   │   ├── useEvents.ts        # Subscribe to specific event types
│   │   └── useApi.ts           # REST fetch helpers
│   └── lib/
│       ├── api.ts              # API client (typed fetch wrappers)
│       ├── types.ts            # TypeScript types matching API response models
│       └── events.ts           # WebSocket event type definitions
```

---

## Implementation Plan

### Step 1: Project scaffold and WebSocket infrastructure

- [x] Initialize Next.js project in `frontend/` with TypeScript
- [x] Define TypeScript types matching ADR-006 API response models
- [x] Implement WebSocket provider (connection, reconnect with backoff, event dispatch)
- [x] Implement REST API client with typed fetch wrappers
- [x] Verify WebSocket connects to running backend and receives events

### Step 2: Daily Digest view

- [x] Fetch and display latest digest from `GET /api/digests/latest`
- [x] Group stories by category, rank by composite score
- [x] Story cards: title, summary, score, metadata, links
- [x] Date picker for historical digest browsing via `GET /api/digests`
- [x] Auto-refresh on WebSocket `digest_ready` event

### Step 3: Story Detail view

- [x] Fetch and display full story data from `GET /api/stories/{id}`
- [x] Display all pipeline stages: metadata, article text, summary, validation, score breakdown, category
- [x] Link from digest story cards to detail view

### Step 4: Live Feed view

- [x] Action buttons triggering POST endpoints (pipeline run, collect, score, categorize, generate digest)
- [x] Pipeline progress bar driven by `pipeline_progress` WebSocket events
- [x] Scrolling event log from WebSocket events
- [x] Reconnect recovery: refetch active runs from `GET /api/runs`
- [x] Max stories input for pipeline and collect actions
- [x] Error state display on pipeline failure

### Step 5: System view

- [x] Agent status cards with live heartbeat updates from WebSocket
- [x] Category breakdown from `GET /api/categories`
- [x] System config display from `GET /api/config`
- [x] Health status from `GET /api/health`

### Step 6: Docker and integration

- [x] Frontend Dockerfile (Node 20, standalone build + serve)
- [x] Add frontend service to `docker-compose.yaml`
- [x] Environment variable configuration for API/WS URLs
- [x] Verify full stack: `docker compose up` starts backend + frontend, dashboard loads and shows live data

### Step 7: End-to-end tests

- [x] Test: dashboard loads and displays digest from running backend
- [x] Test: triggering pipeline run shows progress in Live Feed
- [x] Test: story detail view shows all pipeline stages
- [x] Test: Docker Compose full stack boots and dashboard is accessible
- [ ] Test: automated Playwright E2E test suite

---

## Consequences

- Next.js becomes a project dependency (Node.js required for frontend development)
- The frontend is a separate container in Docker Compose, adding a second service
- No new backend logic is required — the frontend consumes existing ADR-006 endpoints
- The WebSocket reliability improvements (ADR-006 Amendment 2) make the frontend resilient to disconnects without needing event replay infrastructure
- Chat and conversational AI are explicitly out of scope. The dashboard is a display layer with action triggers.
- The frontend can be developed and deployed independently of the backend as long as the API contract (ADR-006) is stable
