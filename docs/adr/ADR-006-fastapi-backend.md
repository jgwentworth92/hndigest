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
