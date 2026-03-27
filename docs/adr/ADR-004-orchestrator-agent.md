# ADR-004: Orchestrator Agent — Priority Dispatch, Token Budget, Pipeline Rewiring

## Status: ACCEPTED
## Date: 2026-03-27

---

## Problem

The current pipeline routes stories directly from the collector to the fetcher, categorizer, and scorer via the story channel. Every collected story is fetched and (in Phase 4) summarized, regardless of quality or relevance. This is wasteful: the HN API returns 500 stories per endpoint, but most are low-signal. Fetching and summarizing all of them consumes network bandwidth, LLM tokens, and processing time without proportional value.

An orchestrator agent is needed to sit between the collector and downstream agents, making dispatch decisions based on priority thresholds and a daily token budget. This changes the pipeline flow and introduces new message bus channels.

---

## Context

Reference existing architecture:
- ADR-001 established the message bus with fan-out pub/sub and the supervisor lifecycle
- ADR-002 implemented the fetcher, categorizer, and report builder as direct subscribers to the story channel
- ADR-003 defined the summarizer-validator prompt chain, which will now receive work from the orchestrator via summarize_request instead of directly monitoring the article channel

Current flow (Phase 2):

```
collector -> story channel -> [fetcher, categorizer, scorer]
```

New flow (Phase 3+):

```
collector -> story channel -> [orchestrator, categorizer, scorer]
orchestrator -> fetch_request channel -> fetcher
orchestrator -> summarize_request channel -> summarizer
```

Categorizer and scorer continue to receive from story channel directly — they are cheap deterministic operations that should run on every story. The orchestrator only gates the expensive operations: fetching (network I/O) and summarizing (LLM tokens).

---

## Options Considered

### Where to gate the pipeline

| Option | Pros | Cons |
|---|---|---|
| Gate at fetcher (orchestrator dispatches fetch_request) | Prevents wasted network I/O for low-priority stories | Categorizer/scorer still process everything (acceptable since they're cheap) |
| Gate at collector (only publish high-priority stories) | Simplest, fewest changes | Loses data — low-priority stories not tracked at all, can't retroactively fetch |
| Gate at summarizer only (fetch everything, gate LLM) | All articles available for search/browse | Still wastes fetch bandwidth on low-priority stories |

**Decision:** Gate at fetcher. The orchestrator dispatches fetch_request messages only for stories above the priority threshold. Categorizer and scorer still process all stories, preserving full category/score data for analytics and the chat agent.

### Token budget tracking

| Option | Pros | Cons |
|---|---|---|
| Estimate tokens per article (fixed cost) | Simple, predictable | Inaccurate — articles vary in length |
| Track actual tokens from LLM responses | Accurate | Requires feedback loop from summarizer/validator back to orchestrator |
| Hybrid: estimate for budgeting, track actuals for reporting | Best of both | Slightly more complex |

**Decision:** Estimate for budgeting (fixed tokens_per_article config), track actuals for reporting when available. The orchestrator makes dispatch decisions based on estimates. Actual usage is logged for tuning the estimate over time.

### Optional LLM mode for ambiguous cases

| Option | Pros | Cons |
|---|---|---|
| Always rules-based | Deterministic, no token cost | Misses nuanced relevance signals in titles |
| Always LLM | Best relevance judgment | Expensive, adds latency to every decision |
| LLM only for ambiguous range | Best tradeoff — LLM used sparingly | Slightly more complex config |

**Decision:** LLM only for scores within the ambiguity_range around the threshold. Controlled by config flag `orchestrator.use_llm` (default: false). When enabled, stories with composite scores between `min_composite_score - ambiguity_range` and `min_composite_score + ambiguity_range` are sent to the LLM for a relevance check. All other stories use pure rules.

### Database migration approach

New table: orchestrator_decisions. This is a new migration file (002_orchestrator_decisions.sql) following the pattern established in ADR-001.

---

## Decision

Implement the orchestrator agent as described. This becomes Phase 3, pushing the current Phase 3 (LLM integration, ADR-003) to Phase 4. The summarizer (ADR-003) is updated to receive summarize_request from the orchestrator instead of subscribing to the article channel directly. ADR-003's implementation plan Step 2 is amended: summarizer subscribes to summarize_request channel.

---

## Phase impact

| Phase | ADR | Before | After |
|---|---|---|---|
| Phase 1 | ADR-001 | Foundation | No change (done) |
| Phase 2 | ADR-002 | Content pipeline | No change (done) |
| Phase 3 | **ADR-004** | LLM integration | **Orchestrator agent** (new) |
| Phase 4 | ADR-003 | Interface | **LLM integration** (moved from Phase 3) |
| Phase 5 | ADR-005 (future) | — | **Chat agent** (new) |
| Phase 6 | ADR-006 (future) | — | **Interface + Docker** (moved from Phase 4) |

---

## Phase 3 Implementation Plan

### Step 1: Orchestrator config

- [ ] Create `config/orchestrator.yaml` with priority thresholds, token budget, ambiguity range, and LLM mode flag
- [ ] Default values: min_composite_score=30, daily_token_budget=100000, tokens_per_article=1000, ambiguity_range=5, use_llm=false

### Step 2: Database migration

- [ ] Create `db/migrations/002_orchestrator_decisions.sql` with the orchestrator_decisions table
- [ ] Columns: id, story_id (FK), decision, reason, priority_score, budget_remaining, used_llm, decided_at
- [ ] Verify migration runner applies it correctly on startup

### Step 3: New message bus channels

- [ ] Add CHANNEL_FETCH_REQUEST and CHANNEL_SUMMARIZE_REQUEST constants to bus module
- [ ] Register both channels in the MessageBus constructor (add to ALL_CHANNELS)
- [ ] Verify existing tests still pass with the new channels

### Step 4: Implement orchestrator agent

- [ ] Create `src/hndigest/agents/orchestrator.py`
- [ ] Subscribe to story channel and score channel
- [ ] Maintain in-memory state: daily budget counter, story scores pending dispatch
- [ ] On story message: record story, wait for score before making dispatch decision
- [ ] On score message: evaluate priority against threshold
- [ ] Above threshold + budget available: publish fetch_request, deduct budget, log "dispatched"
- [ ] Below threshold: log "skipped" in orchestrator_decisions
- [ ] Budget exhausted: log "budget_exceeded"
- [ ] Reset budget counter at midnight UTC
- [ ] Persist all decisions to orchestrator_decisions table

### Step 5: Optional LLM mode

- [ ] If use_llm=true and score is within ambiguity_range of threshold: call LLM adapter for relevance check
- [ ] Add `orchestrator_relevance` prompt template to config/prompts.yaml
- [ ] Log whether LLM was consulted (used_llm column)

### Step 6: Rewire fetcher

- [ ] Update fetcher to subscribe to fetch_request channel instead of story channel
- [ ] Fetcher extracts story_id and priority from fetch_request message
- [ ] Fetcher still publishes to article channel (no change to output)
- [ ] Update existing tests to route through orchestrator or use fetch_request directly

### Step 7: Register orchestrator with supervisor

- [ ] Add orchestrator to CLI start command's agent list
- [ ] Add orchestrator to supervisor startup sequence
- [ ] Verify all agents boot correctly with new channel topology

### Step 8: End-to-end tests

- [ ] Test: seed stories with known scores, run orchestrator, verify high-score stories produce fetch_request messages
- [ ] Test: verify low-score stories are logged as "skipped" in orchestrator_decisions
- [ ] Test: verify budget tracking decrements and stops dispatching when exhausted
- [ ] Test: full pipeline collector -> orchestrator -> fetcher with live HN data
- [ ] Write artifacts for all tests

---

## Consequences

- The story channel no longer fans out to the fetcher directly. This is a breaking change to the Phase 2 flow. The fetcher must be updated to subscribe to fetch_request instead. Existing Phase 2 tests that route stories directly to the fetcher will need updating.
- Categorizer and scorer are unaffected — they continue to receive from story channel. This preserves full analytics coverage even for stories that aren't fetched or summarized.
- The orchestrator adds a decision point to the pipeline. If the orchestrator is down, no new stories are dispatched for fetching/summarizing, but collection, categorization, and scoring continue normally.
- Token budget is approximate. The fixed tokens_per_article estimate will need tuning based on actual usage data. The orchestrator_decisions table provides the data for this tuning.
- The optional LLM mode is off by default. The orchestrator works as a pure rules-based dispatcher until the user enables it.
