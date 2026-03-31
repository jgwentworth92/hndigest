# ADR-002: Phase 2 Content Pipeline — Fetcher, Categorizer, Report Builder

## Status: ACCEPTED
## Date: 2026-03-27

---

## Problem

Phase 1 established the infrastructure (bus, supervisor, database) and two agents (collector, scorer). Stories are collected and scored, but the pipeline stops there. Three agents are needed to complete the deterministic content pipeline before LLM integration in Phase 3:

1. **Fetcher** — retrieves article text from story URLs so the summarizer (Phase 3) has source material
2. **Categorizer** — assigns topic categories so the digest is organized by subject
3. **Report Builder** — assembles the daily digest from scores, categories, and (eventually) summaries

These three agents are entirely deterministic. No LLM calls. They complete the pipeline from "story collected" to "digest assembled" using only rule-based logic.

---

## Decisions

### Fetcher: article text extraction library

| Option | Pros | Cons |
|---|---|---|
| trafilatura | Best extraction quality, handles boilerplate removal, supports multiple formats | External dependency, larger install |
| readability-lxml | Well-known, used by Firefox Reader View | Lower extraction quality, less maintained |
| Raw HTML regex stripping | No dependency | Terrible quality, includes nav/footer/scripts |
| Both with fallback | Best coverage | Two dependencies, more complexity |

**Decision:** trafilatura as primary extractor. It handles the widest range of sites, strips boilerplate well, and is actively maintained. If trafilatura fails to extract meaningful text, mark the story as `fetch_failed`. No fallback to a second library — simplicity over coverage.

### Fetcher: concurrency model

| Option | Pros | Cons |
|---|---|---|
| Sequential fetch per story | Simple, no rate limiting concerns | Slow — 500 stories at 2s each = 16 minutes |
| Semaphore-bounded parallel (10 concurrent) | Fast, spec-compliant | Slightly more complex |
| Unbounded parallel | Fastest | Will hit rate limits, exhaust connections |

**Decision:** asyncio.Semaphore with max 10 concurrent fetches, per SPEC-000 section 5.6. Each fetch is an independent async task within the agent's process method.

### Categorizer: rule engine design

| Option | Pros | Cons |
|---|---|---|
| Flat YAML with keyword lists per category | Simple, easy to edit | No priority or conflict resolution |
| Ordered rules with first-match-wins | Predictable, debuggable | Order matters, harder to reason about |
| Multi-match with all applicable categories | Richer categorization | Stories get many categories, dilutes signal |

**Decision:** Multi-match. A story can belong to multiple categories (e.g., a Rust security CVE gets both "languages" and "security"). This matches the spec which says "assign topic categories" (plural). Three rule types are checked in order: domain mapping, keyword matching, HN type mapping. If no rules match, assign "uncategorized". All rules defined in `config/categories.yaml`.

### Report builder: digest trigger

| Option | Pros | Cons |
|---|---|---|
| Scheduled timer (every 6 hours) | Automated, spec-compliant default | Needs timer management in agent |
| On-demand only (CLI/API trigger) | Simple | No automatic digests |
| Both scheduled + on-demand | Full flexibility | Slightly more complex |

**Decision:** Both. The report builder runs a timer for scheduled digests (default every 6 hours, configurable) and also listens for on-demand trigger messages on a dedicated channel. For Phase 2, the CLI `digest --now` command publishes a trigger message.

### Web MCP: scope for Phase 2

The `web-mcp` module (`src/hndigest/mcp/web_mcp.py`) provides two tools per SPEC-000 section 8: `fetch_url(url, timeout)` and `extract_article_text(html)`. For Phase 2, these are plain async functions (same pattern as `hn_mcp.py` in Phase 1). Full MCP server wrapping is deferred.

---

## Phase 2 Implementation Plan

### Step 1: Category rules config

- [ ] Create `config/categories.yaml` with the full taxonomy from SPEC-000 section 5.7
- [ ] Domain mappings: github.com -> tools, arxiv.org -> research, etc.
- [ ] Keyword lists per category: ai-ml, web-dev, devops-infra, languages, tools, security, career, research, business, culture
- [ ] HN type mappings: show -> tools, job -> career, ask -> culture

### Step 2: Web MCP module

- [ ] Implement `fetch_url(session, url, timeout)` in `src/hndigest/mcp/web_mcp.py`
- [ ] Implement `extract_article_text(html)` using trafilatura
- [ ] Handle failures: timeouts, 404s, paywalls, empty responses
- [ ] Return extracted text or error status string

### Step 3: Fetcher agent

- [ ] Implement fetcher in `src/hndigest/agents/fetcher.py`
- [ ] Subscribe to story channel, publish to article channel
- [ ] For stories with no URL (Ask HN), use the HN text field instead
- [ ] Use asyncio.Semaphore for max 10 concurrent fetches
- [ ] Compute SHA-256 hash of extracted text
- [ ] Persist to articles table with fetch_status
- [ ] Do not retry failed fetches

### Step 4: Categorizer agent

- [ ] Implement categorizer in `src/hndigest/agents/categorizer.py`
- [ ] Load rules from `config/categories.yaml`
- [ ] Subscribe to story channel, publish to category channel
- [ ] Run three rule types: domain mapping, keyword matching, HN type mapping
- [ ] Assign multiple categories if multiple rules match
- [ ] Assign "uncategorized" if no rules match
- [ ] Persist each category assignment to categories table with method

### Step 5: Report builder agent

- [ ] Implement report builder in `src/hndigest/agents/report_builder.py`
- [ ] Runs on a configurable timer (default 6 hours) and on-demand via trigger message
- [ ] Query stories from current period, join with categories and scores
- [ ] Group by category, rank by composite score within each category
- [ ] Apply limits: top N stories per category (default 5)
- [ ] Format as structured JSON and rendered markdown
- [ ] Persist to digests table
- [ ] Publish to digest channel
- [ ] Phase 2 digests have no summaries — show "No summary available" placeholder

### Step 6: Register agents with supervisor

- [ ] Add fetcher, categorizer, and report builder to CLI start command
- [ ] Verify all five agents boot, emit heartbeats, and respond to shutdown

### Step 7: CLI extensions

- [ ] `hndigest digest --now`: publish trigger message to report builder
- [ ] `hndigest digest --latest`: query and display most recent digest
- [ ] `hndigest categories`: show category breakdown for today

### Step 8: End-to-end tests

- [ ] Test: seed story, run fetcher against real URL, verify article text in DB
- [ ] Test: seed story, run categorizer, verify correct categories assigned
- [ ] Test: seed stories + scores + categories, run report builder, verify digest output
- [ ] Test: full pipeline — collector -> fetcher -> categorizer -> scorer -> report builder with live HN data
- [ ] Write artifacts for all tests that hit external APIs
- [ ] Add trafilatura to pyproject.toml dependencies

---

## Consequences

- trafilatura becomes a new external dependency. It's listed as allowed in CLAUDE.md.
- The fetcher will be the slowest agent due to network I/O. The semaphore prevents overload but fetching 500 stories still takes minutes. This is acceptable for a 10-minute poll cycle.
- Categories are multi-match, so a single story may appear in multiple category sections of the digest. The report builder must handle deduplication in display (show story once in its highest-scored category, with other categories as tags).
- Digests in Phase 2 will have scores and categories but no summaries. The "No summary available" placeholder is replaced in Phase 3 when the summarizer and validator come online.
- The report builder's on-demand trigger introduces a new message pattern: the CLI publishes to the bus rather than just an agent. This is implemented by having the CLI start a minimal supervisor with just the report builder for the `digest --now` command.

---

## Amendments (added post-implementation)

### Amendment 1: Fetcher rewired to fetch_request channel (Phase 3)

The fetcher was originally implemented subscribing to the story channel per this ADR. In Phase 3 (ADR-003, orchestrator agent), the fetcher was rewired to subscribe to `fetch_request` instead of `story`. The orchestrator now controls which stories are dispatched for fetching. The fetcher's internal logic is unchanged — only its subscription channel changed.

### Amendment 2: Payload key mismatch discovered in production (post-Phase 4)

A `KeyError: 'id'` crash was found when running the system live. The collector publishes `story_id` in the message payload, but the scorer and orchestrator expected `id`. This bug was not caught by tests because all tests constructed their own payloads using the same wrong key, effectively testing against themselves.

**Root cause:** No integration test verified that the actual collector output could be processed by downstream agents. All tests used hand-crafted payloads.

**Fix:** Changed scorer and orchestrator to read `payload["story_id"]`. Fixed all test payloads. Added integration tests (`tests/test_integration.py`) that run the real collector, capture its actual bus output, and feed it through every downstream agent — verifying zero processing errors.

**Lesson:** Seeded/hand-crafted test payloads must be validated against the real publisher's output. Integration tests that use actual agent output as input are required for every inter-agent message contract.
