# ADR-006: FastAPI Backend — REST API, WebSocket, Agent Lifecycle

## Status: ACCEPTED
## Date: 2026-03-31

---

## Problem

The agent system runs as a CLI process with no external interface. Data is only accessible via CLI commands or direct SQLite queries. A Next.js frontend needs a backend API to:

- Query digests, stories, categories, scores, and agent status
- Trigger on-demand digest generation
- Receive real-time updates when new stories are collected or digests are built
- Send chat queries (Phase 6, future) to the chat agent

The backend must run the agent supervisor alongside the API server in the same process, sharing the same asyncio event loop, message bus, and database connection.

---

## Options Considered

### Server architecture

| Option | Pros | Cons |
|---|---|---|
| FastAPI in same process as agents | Shared event loop, direct access to supervisor/bus, single deployment | Single point of failure |
| Separate API process querying DB only | Isolated, agents can crash without killing API | No live WebSocket from bus, polling only, two processes to manage |
| FastAPI with agents as background tasks | Standard FastAPI pattern | Same as option 1 but more explicit |

**Decision:** FastAPI in the same process. The supervisor starts in FastAPI's lifespan context. API handlers query the database directly. WebSocket broadcasts are wired to the message bus digest channel. This matches the spec (section 7: "Supervisor starts FastAPI server as a separate async task").

### API framework

| Option | Pros | Cons |
|---|---|---|
| FastAPI | Async-native, Pydantic integration, automatic OpenAPI docs, WebSocket support | External dependency |
| aiohttp server | Already a dependency | No automatic docs, manual routing, no Pydantic integration |
| Starlette | Lightweight, FastAPI is built on it | Less ergonomic than FastAPI |

**Decision:** FastAPI. It integrates with our Pydantic models natively (response models = our existing models), has WebSocket support, and generates OpenAPI docs automatically. Already listed as an allowed dependency in CLAUDE.md.

### Startup model

| Option | Pros | Cons |
|---|---|---|
| `uvicorn hndigest.api:app` replaces `hndigest start` | Standard, supports reload, production-ready | CLI start command changes |
| `hndigest start` spawns uvicorn internally | Backward compatible | Extra layer, harder to configure |
| Both: CLI wraps uvicorn, direct uvicorn also works | Flexible | Two entry points |

**Decision:** Both modes, explicitly selectable:
- `hndigest start --mode cli` — agents only, no API server (current behavior, for headless/cron use)
- `hndigest start --mode server` — agents + FastAPI API server (default)
- `hndigest start --mode server --host 0.0.0.0 --port 8000` — for Docker
- Direct `uvicorn hndigest.api:app` also works for production

Docker runs the server mode. The Dockerfile uses `CMD ["python", "-m", "hndigest", "start", "--mode", "server", "--host", "0.0.0.0"]`.

---

## API Design

### REST Endpoints

| Endpoint | Method | Description | Response Model |
|---|---|---|---|
| `/api/health` | GET | System health: agent statuses, uptime | dict |
| `/api/digests` | GET | List recent digests. Params: limit, since | list[DigestSummary] |
| `/api/digests/latest` | GET | Most recent digest | DigestDetail |
| `/api/digests/{id}` | GET | Specific digest by ID | DigestDetail |
| `/api/digests/generate` | POST | Trigger on-demand digest | DigestDetail |
| `/api/stories` | GET | Query stories. Params: category, min_score, since, limit | list[StorySummary] |
| `/api/stories/{id}` | GET | Full story detail | StoryDetail |
| `/api/categories` | GET | Category breakdown for current period | list[CategoryCount] |
| `/api/agents` | GET | Agent registry: status, heartbeat, throughput | dict[str, AgentStatus] |
| `/api/config` | GET | Current system configuration | dict |

### WebSocket

| Endpoint | Description |
|---|---|
| `/api/events` | Live stream. Broadcasts: new stories (from story channel), digest completions (from digest channel), agent status changes |

### Response Models

API response models extend from our existing Pydantic models or define new ones for DB query results. Stored in `src/hndigest/api/schemas.py`.

### CORS

Enabled for `http://localhost:3000` (Next.js dev server) and configurable via environment variable `CORS_ORIGINS`.

---

## File Structure

```
src/hndigest/api/
├── __init__.py      # FastAPI app factory, lifespan, CORS
├── routes/
│   ├── __init__.py
│   ├── digests.py   # /api/digests endpoints
│   ├── stories.py   # /api/stories endpoints
│   ├── agents.py    # /api/agents, /api/health
│   └── config.py    # /api/config
├── schemas.py       # API-specific Pydantic response models
└── websocket.py     # /api/events WebSocket handler
```

---

## Implementation Plan

### Step 1: FastAPI app with lifespan

- [ ] Install FastAPI + uvicorn
- [ ] Create `src/hndigest/api/__init__.py` with app factory
- [ ] Lifespan: start supervisor with all agents on startup, shutdown on exit
- [ ] Store supervisor reference in `app.state` for route handlers
- [ ] CORS middleware for Next.js dev server
- [ ] Update pyproject.toml with fastapi and uvicorn dependencies

### Step 2: API response schemas

- [ ] Create `src/hndigest/api/schemas.py` with response models
- [ ] DigestSummary, DigestDetail, StorySummary, StoryDetail, CategoryCount, AgentStatus
- [ ] Reuse existing Pydantic models where possible

### Step 3: Digest endpoints

- [ ] GET `/api/digests` — query digests table, return list
- [ ] GET `/api/digests/latest` — most recent digest
- [ ] GET `/api/digests/{id}` — specific digest
- [ ] POST `/api/digests/generate` — trigger report builder, return result

### Step 4: Story endpoints

- [ ] GET `/api/stories` — query with filters (category, min_score, since, limit)
- [ ] GET `/api/stories/{id}` — full detail with article, summary, validation, score

### Step 5: System endpoints

- [ ] GET `/api/health` — supervisor agent statuses, uptime
- [ ] GET `/api/agents` — detailed agent registry
- [ ] GET `/api/categories` — category breakdown
- [ ] GET `/api/config` — current system config

### Step 6: WebSocket events

- [ ] `/api/events` — subscribe to bus channels (story, digest) and broadcast to connected clients
- [ ] JSON messages with event type and payload
- [ ] Handle client connect/disconnect gracefully

### Step 7: Wire CLI start to FastAPI

- [ ] Update `hndigest start` to run uvicorn with the FastAPI app
- [ ] Support `--host`, `--port` CLI flags
- [ ] Direct `uvicorn hndigest.api:app` also works

### Step 8: Docker containerization

- [ ] Create `Dockerfile` — Python 3.12 slim, install deps, copy source, expose port 8000
- [ ] Create `docker-compose.yaml` — single service with volume mounts for DB and config
- [ ] `.env` passed via `env_file` in compose
- [ ] SQLite DB mounted as volume for persistence across container restarts
- [ ] Config directory mounted as volume for tuning without rebuild
- [ ] Health check: `curl http://localhost:8000/api/health`

### Step 9: End-to-end tests

- [ ] Test: start FastAPI app, verify `/api/health` returns agent statuses
- [ ] Test: POST `/api/digests/generate`, verify digest returned
- [ ] Test: GET `/api/stories` returns data from DB
- [ ] Test: WebSocket `/api/events` receives story events
- [ ] Test: Docker build succeeds and container starts
- [ ] Write artifacts for all API tests

---

## Consequences

- FastAPI + uvicorn become runtime dependencies (already listed as allowed in CLAUDE.md)
- The API server and agents share a single process and event loop. If the process dies, both die. This is acceptable for the current single-machine deployment model.
- `hndigest start` behavior changes: it now starts the API server too (on port 8000 by default). The CLI-only mode could be preserved with a `--no-api` flag if needed.
- WebSocket broadcasts are wired to the message bus, so any frontend connected to `/api/events` sees real-time updates as stories flow through the pipeline.
- API response models reuse Pydantic payload models where possible, keeping a single source of truth for data shapes.
- Docker provides the production deployment path. The container runs the server mode by default with SQLite and config as mounted volumes.
- Both CLI-only and server modes are preserved. CLI mode is useful for cron jobs, scripting, and environments where an API server isn't needed.

---

## Amendment 1: Async action endpoints with WebSocket progress (post-implementation)

The initial action endpoints (`/api/collect`, `/api/pipeline/run`, etc.) were synchronous — the HTTP request blocked until all agent work completed. This caused timeouts on long-running operations like the full pipeline (30-60 seconds for collect + score + fetch + summarize + validate + digest).

### Problem

Synchronous action endpoints are wrong for two reasons:
1. HTTP requests timeout on long-running work (especially `/api/pipeline/run`)
2. The frontend has no visibility into progress — it waits in the dark until the response arrives or times out

### Decision

Action endpoints become **fire-and-forget**: they spawn an `asyncio.Task`, return `202 Accepted` immediately with a run ID, and progress streams through the WebSocket.

**Pattern:**
```
1. Frontend connects to WebSocket /api/events
2. Frontend calls POST /api/pipeline/run → 202 {"run_id": "abc", "status": "started"}
3. WebSocket receives progress events as agents process:
   - pipeline_started, story_collected, story_scored, story_categorized,
     story_dispatched, article_fetched, summary_generated, summary_validated,
     story_skipped, digest_ready, pipeline_completed
4. Frontend updates UI in real-time from WebSocket events
```

**The message bus IS the progress channel.** Agents already publish typed messages as they work. The WebSocket handler subscribes to ALL data channels (not just story/digest/score) and broadcasts every event to connected clients. No new infrastructure needed.

### Action endpoint changes

| Endpoint | Before | After |
|---|---|---|
| POST /api/collect | Sync, returns when done | 202 + background task, progress via WebSocket |
| POST /api/score | Sync | 202 + background task |
| POST /api/categorize | Sync | 202 + background task |
| POST /api/orchestrate | Sync | 202 + background task |
| POST /api/fetch/{id} | Sync | 202 + background task |
| POST /api/summarize/{id} | Sync | 202 + background task |
| POST /api/pipeline/run | Sync (timeout risk) | 202 + background task, full progress stream |

### WebSocket event format

All events follow the same shape:
```json
{
  "event": "story_collected",
  "run_id": "abc123",
  "timestamp": "2026-04-02T12:00:00+00:00",
  "data": { ... payload fields ... }
}
```

### WebSocket channel subscriptions

The WebSocket handler subscribes to ALL data channels:
- story, fetch_request, article, summarize_request, score, category, summary, validated_summary, digest

Each bus message is translated to a frontend-friendly event name.

### Run tracking

Active pipeline runs are tracked in `app.state.active_runs: dict[str, asyncio.Task]`. The `GET /api/runs/{run_id}` endpoint returns the current status. Completed runs are cleaned up after a configurable TTL.

---

## Amendment 2: WebSocket reliability and frontend support

### Problem

Amendment 1 made the frontend WebSocket-driven, but left gaps that make the frontend fragile:

1. **No missed-event recovery.** If the WebSocket disconnects mid-pipeline, the frontend has no way to catch up. It shows stale state until manually refreshed.
2. **No active run discovery.** `GET /api/runs/{run_id}` exists but requires knowing the run ID. On reconnect the frontend doesn't know what runs are in flight.
3. **No aggregate pipeline progress.** The WebSocket emits per-story events during `/api/pipeline/run`, but no summary. The frontend must count individual events to show progress — error-prone and lost on reconnect.
4. **Agent heartbeats not broadcast.** The WebSocket subscribes to data channels but not the `system` channel. The frontend has to poll `GET /api/agents` for live status instead of receiving heartbeat events.

### Decisions

#### Recovery strategy: REST refetch on reconnect

| Option | Pros | Cons |
|---|---|---|
| Event log endpoint with ring buffer | Granular catch-up, no data loss for short disconnects | New infrastructure, memory management, TTL complexity |
| REST refetch on reconnect | No new infrastructure, REST endpoints already return current state | Loses granular progress for in-flight runs |

**Decision:** REST refetch on reconnect. When the WebSocket reconnects, the frontend re-fetches current state from REST endpoints (`/api/runs`, `/api/agents`, `/api/digests/latest`). This is simple, uses existing endpoints, and covers the common case (short disconnects during pipeline runs). The frontend does not need to replay individual events.

#### Active run list endpoint

Add `GET /api/runs` to return all active and recently completed runs:

| Endpoint | Method | Description | Response |
|---|---|---|---|
| `/api/runs` | GET | List active and recent runs | `list[RunStatus]` |

Response shape:
```json
[
  {
    "run_id": "abc123",
    "type": "pipeline",
    "status": "running",
    "started_at": "2026-04-07T12:00:00+00:00",
    "progress": {"collected": 45, "scored": 45, "fetched": 12, "summarized": 8}
  }
]
```

This lets the frontend recover state on reconnect without knowing run IDs in advance.

#### Pipeline progress events

During `POST /api/pipeline/run`, emit periodic `pipeline_progress` events on the WebSocket summarizing aggregate counts:

```json
{
  "event": "pipeline_progress",
  "run_id": "abc123",
  "timestamp": "2026-04-07T12:01:00+00:00",
  "data": {
    "collected": 45,
    "scored": 45,
    "categorized": 40,
    "fetched": 12,
    "summarized": 8,
    "validated": 6,
    "total_stories": 45
  }
}
```

Emitted after each stage completes and on a 5-second interval during long-running stages. The frontend can show a single progress bar from this without counting individual events.

#### System channel on WebSocket

Add the `system` bus channel to the WebSocket subscription list. The WebSocket now subscribes to:

- All data channels: story, fetch_request, article, summarize_request, score, category, summary, validated_summary, digest
- System channel: agent heartbeats, lifecycle events

Agent heartbeat events are broadcast as:
```json
{
  "event": "agent_heartbeat",
  "timestamp": "2026-04-07T12:00:30+00:00",
  "data": {
    "agent": "collector",
    "status": "running",
    "last_heartbeat": "2026-04-07T12:00:30+00:00",
    "messages_processed": 142
  }
}
```

This eliminates the need to poll `GET /api/agents` for the live Agents view.

#### Drop chat endpoint

The `POST /api/chat` endpoint is removed from ADR-006 scope. Chat functionality (chat agent, analytics-mcp, chat_sessions/chat_messages tables) is deferred to a future phase. The frontend is a read-only dashboard with action triggers, not a conversational interface.

### Updated endpoint summary

**Read endpoints** (return data from SQLite):

| Endpoint | Method | Description |
|---|---|---|
| `GET /api/health` | GET | System health: uptime, database status |
| `GET /api/digests` | GET | List recent digests. Params: limit, since |
| `GET /api/digests/latest` | GET | Most recent digest |
| `GET /api/digests/{id}` | GET | Specific digest |
| `GET /api/stories` | GET | Query stories. Params: category, min_score, since, limit |
| `GET /api/stories/{id}` | GET | Full story detail |
| `GET /api/categories` | GET | Category breakdown for current period |
| `GET /api/agents` | GET | Agent registry (fallback for when WebSocket unavailable) |
| `GET /api/config` | GET | Current configuration |
| `GET /api/runs` | GET | Active and recently completed runs |
| `GET /api/runs/{run_id}` | GET | Status of a specific run |

**Action endpoints** (202 + background task, progress via WebSocket):

| Endpoint | Method | Description |
|---|---|---|
| `POST /api/collect` | POST | Collect stories from HN API |
| `POST /api/score` | POST | Score all unscored stories |
| `POST /api/categorize` | POST | Categorize all uncategorized stories |
| `POST /api/orchestrate` | POST | Run orchestrator on pending stories |
| `POST /api/fetch/{id}` | POST | Fetch article for a specific story |
| `POST /api/summarize/{id}` | POST | Summarize and validate a specific story |
| `POST /api/pipeline/run` | POST | Full pipeline run |
| `POST /api/digests/generate` | POST | Build digest from current data |

**WebSocket:**

| Endpoint | Description |
|---|---|
| `/api/events` | Live event stream. Subscribes to all data channels AND system channel. Events include per-story pipeline events, pipeline_progress summaries, agent heartbeats, and digest completions. |

### Implementation additions

These items are added to ADR-006's implementation plan:

- [ ] Add `GET /api/runs` endpoint returning active and recent runs from `app.state.active_runs`
- [ ] Add `pipeline_progress` event emission during pipeline runs (after each stage + 5s interval)
- [ ] Subscribe WebSocket handler to system bus channel for agent heartbeats
- [ ] Translate agent heartbeat bus messages to `agent_heartbeat` WebSocket events
- [ ] Remove `POST /api/chat` from implementation scope
- [ ] Test: WebSocket reconnect → REST refetch recovers current state
- [ ] Test: `GET /api/runs` returns active runs during pipeline execution
- [ ] Test: `pipeline_progress` events emitted during `/api/pipeline/run`

---

## Amendment 3: Failure handling and frontend configurability

### Problem

Pipeline failures are invisible to the frontend. When an action endpoint's background task raises an exception:

1. The exception is logged server-side but no event is published to WebSocket
2. The run entry is immediately removed from `active_runs`
3. `GET /api/runs/{run_id}` returns `completed_or_unknown` — indistinguishable from success
4. The frontend progress bar freezes at its last known state with no error indication

Additionally, the frontend hardcodes `max_stories=10` for all pipeline and collect actions. The API accepts this parameter but the UI provides no control.

### Decisions

#### Failure events

When a background task fails, publish a `pipeline_failed` event to the system channel before removing the run:

```json
{
  "event": "pipeline_failed",
  "run_id": "abc123",
  "timestamp": "2026-04-09T12:00:00+00:00",
  "data": {
    "run_id": "abc123",
    "error": "Connection timeout fetching article",
    "stage": "fetch",
    "progress": { "collected": 10, "scored": 10, "fetched": 3, ... }
  }
}
```

This applies to ALL action endpoints, not just `/api/pipeline/run`. Each action's `_run()` catch block publishes the failure event.

#### Run retention with TTL

Failed and completed runs are kept in `active_runs` for 5 minutes instead of being immediately removed. This allows the frontend to query run status after the fact. A periodic cleanup task removes entries older than the TTL.

The `RunEntry` dataclass gains two fields:

- `ended_at: str | None` — set when the task completes or fails
- `error: str | None` — set on failure with the exception message

`RunStatus` response model gains a matching `error: str | None` field.

#### Frontend error handling

- `ActionPanel`: show error toast/banner when an API call fails (not silently swallowed)
- `FeedView`: subscribe to `pipeline_failed` event, update active run to error state
- `ProgressBar`: render error state (red bar, error message) when run status is "failed"
- `ActionPanel`: add number input for `max_stories` (default 10, range 1-100)

### Implementation plan

- [ ] Backend: add `pipeline_failed` to `PipelineProgressPayload` or create a new payload type with error field
- [ ] Backend: publish `pipeline_failed` event in all action endpoint catch blocks
- [ ] Backend: add `ended_at` and `error` fields to `RunEntry`, retain runs for 5 min TTL
- [ ] Backend: add `error` field to `RunStatus` schema
- [ ] Backend: add periodic cleanup of expired runs in lifespan or lazy cleanup on GET
- [ ] Frontend: add `pipeline_failed` to event types
- [ ] Frontend: handle `pipeline_failed` in FeedView, show error state on ProgressBar
- [ ] Frontend: show error feedback in ActionPanel when API calls fail
- [ ] Frontend: add max_stories input to ActionPanel
- [ ] WebSocket: route `pipeline_failed` events same as other pipeline events (with top-level run_id)
- [ ] Test: pipeline failure publishes `pipeline_failed` event
- [ ] Test: failed run retained in GET /api/runs with error field
- [ ] Test: frontend shows error state on pipeline failure
