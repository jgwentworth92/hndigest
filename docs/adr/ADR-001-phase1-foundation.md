# ADR-001: Phase 1 Foundation

## Status: ACCEPTED
## Date: 2026-03-27

---

## Problem

hndigest needs a working foundation before any agents can run: a database schema, a message bus for inter-agent communication, a supervisor to manage agent lifecycles, and at least one agent (the HN collector) to prove the architecture works end-to-end. This ADR covers the decisions and implementation plan for Phase 1 as defined in SPEC-000 section 10.

---

## Options Considered

### Database approach

| Option | Pros | Cons |
|---|---|---|
| SQLite via stdlib sqlite3 | Zero dependencies, single-file DB, good enough for single-process workload | No concurrent writers from separate processes |
| PostgreSQL | Full ACID, concurrent access | External dependency, overkill for single-process system |
| In-memory dicts with JSON persistence | Simplest possible | No query capability, fragile, no schema enforcement |

### Message bus approach

| Option | Pros | Cons |
|---|---|---|
| asyncio.Queue per channel (dict of queues) | Zero dependencies, natural fit for asyncio tasks, in-process | No persistence, no cross-process |
| Redis pub/sub | Persistent, cross-process | External dependency, unnecessary for Phase 1 |
| Direct function calls between agents | Simplest | Tight coupling, no decoupling of publish/subscribe |

### Supervisor approach

| Option | Pros | Cons |
|---|---|---|
| Custom supervisor managing asyncio.Task instances | Full control over lifecycle, heartbeat, restart logic | Must implement health monitoring ourselves |
| supervisord or systemd per agent | Battle-tested process management | Agents must be separate processes, complicates message bus |
| No supervisor, just asyncio.gather | Minimal code | No health monitoring, no restart on failure |

### Schema migration approach

| Option | Pros | Cons |
|---|---|---|
| Numbered SQL files executed in order | Simple, transparent, no dependencies | Manual ordering |
| Alembic | Auto-generates migrations | Requires SQLAlchemy, violates minimal dependency principle |
| Single schema.sql recreated on startup | Simplest | No incremental migration path |

---

## Decision

- **Database:** SQLite via stdlib sqlite3. Schema defined in numbered SQL migration files under `db/migrations/`. Migrations run in filename order on startup. The supervisor checks a `schema_version` table to skip already-applied migrations.

- **Message bus:** Dict of `asyncio.Queue` objects, one per channel. A `MessageBus` class provides `publish(channel, message)` and `subscribe(channel)` methods. Subscribe returns an `asyncio.Queue` that receives copies of all messages published to that channel. Multiple subscribers per channel are supported (fan-out). The bus is created by the supervisor and passed to agents at construction time.

- **Supervisor:** Custom Python class managing agents as `asyncio.Task` instances. Responsible for: creating the message bus, initializing the database, starting agents, monitoring heartbeats (30-second interval), restarting failed agents with exponential backoff (max 3 retries), and graceful shutdown via a `shutdown` message on the system channel. Agents drain their queues on shutdown before exiting.

- **Agent base class:** Abstract base providing the run loop structure: receive message, process, persist, publish, emit heartbeat. Concrete agents override the processing step. Each agent declares its subscriptions and publications at construction time.

- **HN collector as first agent:** Implements the collector from SPEC-000 section 5.5. Polls `/v0/topstories` only in Phase 1 (other endpoints added later). Fetches item details for new story IDs. Publishes to the `story` channel. Persists to the `stories` table. Deduplicates by HN item ID (primary key).

- **Scorer agent:** Implements scoring from SPEC-000 section 5.8. Subscribes to the `story` channel. Computes score velocity, comment velocity, front page presence, and recency. Publishes composite score to the `score` channel. Persists to the `scores` table. Scoring weights loaded from YAML config.

- **Schema migration approach:** Numbered SQL files. A `schema_version` table tracks which migrations have been applied. On startup the db module scans `db/migrations/`, compares against applied versions, and executes any new migrations in order within a transaction.

---

## Phase 1 Implementation Plan

### Step 1: SQLite schema and migration runner

- [ ] Create `db/migrations/001_initial_schema.sql` defining all seven tables from SPEC-000 section 6: stories, score_snapshots, articles, categories, scores, summaries, validations, digests
- [ ] Create `db/migrations/000_schema_version.sql` defining the schema_version tracking table
- [ ] Implement migration runner in `src/hndigest/db/` that scans migration files, checks schema_version, and applies new migrations in order
- [ ] Verify: migration runner creates a fresh database with all tables from a clean state

### Step 2: Message bus

- [ ] Implement `MessageBus` class in `src/hndigest/bus/` with publish, subscribe, and channel creation
- [ ] Support fan-out: multiple subscribers on one channel each get a copy of every message
- [ ] Define message format: dict with `type`, `timestamp`, `source` (agent name), and `payload` fields
- [ ] Define the eight channels from SPEC-000 section 5.2: story, article, category, score, summary, validated_summary, digest, system
- [ ] Verify: publish a message, confirm all subscribers receive it

### Step 3: Agent base class

- [ ] Implement abstract `BaseAgent` in `src/hndigest/agents/base.py`
- [ ] Run loop: await message from subscribed channel, call abstract process method, persist result, publish to output channel, emit heartbeat to system channel
- [ ] Heartbeat: agent publishes its name, status, and timestamp to the system channel every 30 seconds
- [ ] Shutdown handler: on receiving shutdown message on system channel, drain remaining messages from subscribed queues, then exit cleanly
- [ ] Each agent tracks messages_processed count for status reporting

### Step 4: Supervisor

- [ ] Implement `Supervisor` class in `src/hndigest/supervisor/`
- [ ] Startup sequence: create message bus, initialize database (run migrations), start agents as asyncio tasks
- [ ] Health monitoring loop: check for heartbeats on system channel, flag agents that miss two consecutive heartbeats
- [ ] Restart failed agents: exponential backoff (1s, 2s, 4s), max 3 retries per agent, then log alert
- [ ] Graceful shutdown: publish shutdown to system channel, wait for agents to finish (with timeout), cancel any remaining tasks
- [ ] Expose agent registry: name, status (running/stopped/failed), last heartbeat, messages processed

### Step 5: HN collector agent

- [ ] Implement collector in `src/hndigest/agents/collector.py`
- [ ] Implement hn-mcp server in `src/hndigest/mcp/hn_mcp.py` with `fetch_top_stories()` and `fetch_item(id)` tools
- [ ] Collector polls `/v0/topstories` on a configurable interval (default 10 minutes)
- [ ] For each new story ID, fetch item details and persist to stories table
- [ ] For existing stories, update score and comments columns and record a score_snapshot
- [ ] Publish new stories to the story channel
- [ ] Deduplicate by HN item ID as primary key (INSERT OR IGNORE)

### Step 6: Scorer agent

- [ ] Implement scorer in `src/hndigest/agents/scorer.py`
- [ ] Load scoring weights from `config/scoring.yaml`
- [ ] Subscribe to story channel, compute four signal components per SPEC-000 section 5.8
- [ ] Score velocity: points / hours since posted, normalized via percentile rank against trailing 7-day baseline from score_snapshots
- [ ] Comment velocity: comments / hours since posted, normalized same way
- [ ] Front page presence: count of endpoints, scaled per spec (1=20, 2=50, 3+=100)
- [ ] Recency: exponential decay (6h=100, 12h=70, 24h=40, 48h=10)
- [ ] Compute weighted composite (0-100), publish to score channel, persist to scores table

### Step 7: CLI (start, stop, status, stories)

- [ ] Implement CLI entry point in `src/hndigest/__main__.py` using argparse
- [ ] `hndigest start`: instantiate supervisor, run event loop
- [ ] `hndigest stop`: send SIGINT/SIGTERM handler that triggers supervisor graceful shutdown
- [ ] `hndigest status`: query supervisor agent registry, display agent statuses and last heartbeats
- [ ] `hndigest stories --today`: query stories table for today's stories, display with scores

### Step 8: End-to-end verification

- [ ] Write test: seed database with known story data, start collector and scorer via supervisor, verify scorer produces expected composite scores
- [ ] Write test: start supervisor with collector against live HN API (gated by env var), verify stories are collected and scored
- [ ] Write test: start and stop supervisor, verify all agents boot, emit heartbeats, and shut down cleanly
- [ ] Verify CLI commands work against a running system

---

## Consequences

- All seven database tables are created upfront even though Phase 1 only writes to stories, score_snapshots, and scores. This avoids schema changes when Phase 2 agents start writing to articles, categories, etc.
- The message bus is in-process only. If we later need cross-process communication, the abstract interface (publish/subscribe) allows swapping to Redis or NATS without changing agent code.
- The collector starts with topstories only. Adding new/best/show/ask/job endpoints is a configuration change in later phases.
- The scorer needs historical data for percentile normalization. On first run with no baseline, it falls back to raw velocity values until enough data accumulates (approximately 7 days).

---

## Amendments (added during Phase 1 implementation)

### Amendment 1: LLM adapter added to Phase 1 (originally Phase 3)

The configurable LLM adapter was pulled forward into Phase 1 to establish the provider abstraction early. This was not in the original Phase 1 plan.

| Decision | Detail |
|---|---|
| What was added | LLM adapter in `src/hndigest/mcp/llm_mcp.py` with pluggable provider support |
| Providers supported | Gemini, Claude (Anthropic), OpenAI, local OpenAI-compatible endpoints |
| Default provider | Gemini (`gemini-2.5-flash`) — fastest and cheapest |
| Default models | Gemini: `gemini-2.5-flash`, Claude: `claude-haiku-4-5-20251001`, OpenAI: `gpt-4.1-nano` |
| Config | `config/llm.yaml` for provider and model settings, `.env` for API keys |
| Why now | User decision: establish adapter pattern and verify LLM connectivity before building the summarizer and validator agents in Phase 3 |

### Amendment 2: Prompt templates externalized to YAML

All LLM prompts are defined in `config/prompts.yaml`, not hardcoded in Python.

| Decision | Detail |
|---|---|
| Config file | `config/prompts.yaml` |
| Template format | YAML with `system` and `user` keys per prompt category, using `{placeholder}` variables |
| Current categories | `summarizer` (system + user with `{title}`, `{article_text}`) and `validator` (system + user with `{summary}`, `{source_text}`) |
| Rationale | Enables prompt experimentation without code changes, consistent with the project principle that all tunable behavior lives in YAML config |

### Amendment 3: Test policy — no skipped tests

Original plan allowed env-gated test skipping. This was changed during implementation.

| Decision | Detail |
|---|---|
| Policy | No test may be skipped. If a test requires env vars, API keys, or external setup, the developer must ask for the required configuration before running tests. |
| Rationale | A skipped test is a failing test. Missing setup should be surfaced immediately, not silently bypassed. |
| Committed to | CLAUDE.md testing section |

### Amendment 4: Docker containerization goal

The system should be containerizable and runnable via Docker. This is a design constraint, not a Phase 1 deliverable.

| Decision | Detail |
|---|---|
| Target | Single-container deployment via Dockerfile and docker-compose.yaml |
| Architecture fit | Single-process async design, file-based SQLite (volume-mountable), YAML config (mountable or env-overridable) |
| Timeline | Dockerfile and compose file planned for Phase 4 alongside the FastAPI server |
